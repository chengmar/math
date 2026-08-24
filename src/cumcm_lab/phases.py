from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from .cases import find_case
from .freeze import verify_frozen
from .knowledge import retrieve_knowledge
from .state import load_state, transition
from .util import (
    ensure_empty_directory,
    load_lab_paths,
    now_iso,
    read_yaml,
    safe_copy_tree,
    sha256_file,
    write_json,
    write_yaml,
)


PHASE_SKILLS = {
    "solve": "$cumcm-a-solve",
    "audit": "$cumcm-a-audit",
    "blind-revision": "$cumcm-a-solve",
    "reflection": "$cumcm-a-reflect",
    "evaluation": "$cumcm-a-evaluate",
}

PHASE_SKILL_DIRS = {
    "solve": "cumcm-a-solve",
    "audit": "cumcm-a-audit",
    "blind-revision": "cumcm-a-solve",
    "reflection": "cumcm-a-reflect",
    "evaluation": "cumcm-a-evaluate",
}

PHASE_FORBIDDEN = {
    "solve": ["reference-vault", "exam-vault", "参考论文", "赛题讲评", "标准答案", "candidate knowledge"],
    "audit": ["reference-vault", "exam-vault", "参考论文", "修改冻结解答"],
    "blind-revision": ["reference-vault", "exam-vault", "参考论文", "赛题讲评"],
    "reflection": ["修改冻结盲解", "直接升级 verified"],
    "evaluation": ["reference-vault", "exam-vault", "参考答案", "candidate knowledge", "修改 Skills/knowledge/评分规则"],
}


def _copy_input(case_dir: Path, workspace: Path) -> None:
    source = case_dir / "input"
    destination = workspace / "input"
    destination.mkdir(parents=True, exist_ok=True)
    safe_copy_tree(source, destination)


def _copy_verified_knowledge(trainer_root: Path, case_meta: dict, workspace: Path, phase: str) -> list[dict[str, Any]]:
    query = {
        "problem_family": case_meta.get("problem_family"),
        "task_type": case_meta.get("task_types"),
        "data_type": case_meta.get("data_types"),
        "model_family": case_meta.get("model_family"),
        "objective": case_meta.get("objective"),
        "constraints": case_meta.get("constraints"),
        "validation_needed": case_meta.get("validation_needed"),
        "failure_modes": case_meta.get("failure_modes"),
    }
    log_path = workspace / "retrieval-log.json"
    results = retrieve_knowledge(trainer_root / "knowledge", query, phase=phase, limit=5, log_path=log_path)
    target = workspace / "knowledge"
    target.mkdir(parents=True, exist_ok=True)
    for result in results:
        source = Path(result["path"])
        if source.is_symlink():
            raise ValueError(f"拒绝复制符号链接知识卡：{source}")
        shutil.copy2(source, target / source.name)
    return results


def _phase_override(phase: str, skill_path: Path) -> str:
    skill = PHASE_SKILLS[phase]
    forbidden = "、".join(PHASE_FORBIDDEN[phase])
    return (
        f"# {phase} 阶段锁\n\n"
        f"本会话只允许显式调用 `{skill}`，不得调用其他阶段 Skill。\n"
        f"开始工作前必须完整读取并严格执行项目 Skill：`{skill_path}`。\n"
        f"只读取 `allowed-paths.json` 中列出的内容；禁止：{forbidden}。\n"
        "所有自动判断使用 pass、fail 或 needs_review；不得伪造复现、哈希或数学正确性。\n"
    )


def ensure_reflection_control_files(case_dir: Path, workspace: Path) -> None:
    """Install the authoritative external freeze evidence in a reflection copy."""

    manifest = case_dir / "frozen" / "FROZEN_BLIND_FINAL.json"
    target_root = workspace / "blind-final"
    if not manifest.is_file() or not target_root.is_dir():
        raise FileNotFoundError("Reflection 缺少外部 Blind Final 冻结清单或冻结副本。")
    report = verify_frozen(case_dir, "blind-final")
    if report["status"] != "pass":
        raise ValueError("Blind Final 外部冻结校验失败，拒绝准备 Reflection 控制材料。")
    shutil.copy2(manifest, target_root / "FROZEN_BLIND_FINAL.json")
    write_json(
        target_root / "REFLECTION-BOUNDARY.json",
        {
            "case_id": read_yaml(case_dir / "case.yaml").get("case_id"),
            "external_freeze_status": "pass",
            "external_freeze_manifest": "FROZEN_BLIND_FINAL.json",
            "external_freeze_verified_at": report.get("verified_at"),
            "audit_requirement": "completed",
            "audit_state": "audited",
            "self_report_frozen_field_authoritative": False,
            "note": "外部冻结发生在 Blind Revision 会话结束后；冻结清单是权威证据，解题会话内的 pre-freeze 自报字段不是冻结状态依据。",
        },
    )


def prepare_phase(trainer_root: Path, case_id: str, phase: str) -> Path:
    if phase not in PHASE_SKILLS:
        raise ValueError(f"未知阶段：{phase}")
    case_dir = find_case(trainer_root, case_id)
    state = load_state(case_dir)["state"]
    case_meta = read_yaml(case_dir / "case.yaml")
    workspace = case_dir / "workspaces" / phase
    ensure_empty_directory(workspace)
    skill_root = (trainer_root / ".agents" / "skills" / PHASE_SKILL_DIRS[phase]).resolve()
    skill_path = skill_root / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"项目阶段 Skill 不存在：{skill_path}")
    paths = load_lab_paths(trainer_root)
    reference_vault = Path(paths["reference_vault"])
    exam_vault = Path(paths["exam_vault"])

    if phase == "solve":
        if state != "initialized":
            raise ValueError(f"solve 只能从 initialized 准备，当前为 {state}。")
        _copy_input(case_dir, workspace)
        _copy_verified_knowledge(trainer_root, case_meta, workspace, "solve")
        safe_copy_tree(trainer_root / "templates" / "paper", workspace / "paper-template")
        next_states = ("solve_ready", "solving")
    elif phase == "audit":
        if state != "blind_v1_frozen":
            raise ValueError(f"audit 需要 blind_v1_frozen，当前为 {state}。")
        report = verify_frozen(case_dir, "blind-v1")
        if report["status"] != "pass":
            raise ValueError("blind-v1 冻结校验失败，拒绝进入审计。")
        _copy_input(case_dir, workspace)
        safe_copy_tree(case_dir / "frozen" / "blind-v1", workspace / "frozen-solution")
        # The audit Skill must independently verify the authoritative frozen
        # file list and SHA-256 values before reviewing mathematics.  Keep the
        # manifest beside the copied snapshot, inside the audit read boundary;
        # the manifest is external metadata and is intentionally not part of
        # the snapshot tree hash it authenticates.
        shutil.copy2(
            case_dir / "frozen" / "FROZEN_BLIND_V1.json",
            workspace / "frozen-solution" / "FROZEN_BLIND_V1.json",
        )
        next_states = ("audit_ready",)
    elif phase == "blind-revision":
        if state != "audited":
            raise ValueError(f"blind-revision 需要 audited，当前为 {state}。")
        report = verify_frozen(case_dir, "blind-v1")
        if report["status"] != "pass":
            raise ValueError("blind-v1 冻结校验失败，拒绝盲修订。")
        required = case_dir / "workspaces" / "audit" / "audit-report.md"
        if not required.exists():
            raise FileNotFoundError(f"缺少独立审计报告：{required}")
        _copy_input(case_dir, workspace)
        safe_copy_tree(case_dir / "frozen" / "blind-v1", workspace / "blind-v1")
        audit_source = case_dir / "workspaces" / "audit"
        audit_target = workspace / "audit"
        audit_target.mkdir(parents=True, exist_ok=True)
        for name in ("audit-report.md", "audit-findings.yaml", "reproduction-report.json", "counterexamples.md", "revision-plan.md"):
            source = audit_source / name
            if source.exists():
                shutil.copy2(source, audit_target / name)
        next_states = ("blind_revision_ready",)
    elif phase == "reflection":
        if state != "blind_final_frozen":
            raise ValueError(f"reflection 需要 blind_final_frozen，当前为 {state}。")
        if verify_frozen(case_dir, "blind-final")["status"] != "pass":
            raise ValueError("blind-final 冻结校验失败，拒绝进入复盘。")
        reference_ids = case_meta.get("reference_ids") or []
        if not reference_ids:
            current_reference_root = reference_vault / case_id
            if not current_reference_root.is_dir():
                raise ValueError(f"当前案例参考目录不存在：{case_id}")
            candidates = [
                path
                for path in sorted(current_reference_root.iterdir())
                if path.is_file() and not path.is_symlink()
            ]
            if len(candidates) < 2:
                raise ValueError(f"Reflection 需要当前案例 2 至 4 篇参考材料，实际仅 {len(candidates)} 篇。")
            reference_ids = [f"{case_id}/{path.name}" for path in candidates[:4]]
            case_meta["reference_ids"] = reference_ids
            write_yaml(case_dir / "case.yaml", case_meta)
        reference_sources: list[tuple[str, Path]] = []
        for reference_id in reference_ids:
            reference_path = Path(str(reference_id))
            if not reference_path.parts or reference_path.parts[0].casefold() != case_id.casefold():
                raise ValueError(f"参考材料必须来自当前案例目录 {case_id}：{reference_id}")
            source = (reference_vault / str(reference_id)).resolve()
            if not source.is_relative_to(reference_vault.resolve()) or not source.is_file() or source.is_symlink():
                raise ValueError(f"参考材料未通过 Vault 边界检查：{reference_id}")
            reference_sources.append((str(reference_id), source))
        safe_copy_tree(case_dir / "frozen" / "blind-final", workspace / "blind-final")
        ensure_reflection_control_files(case_dir, workspace)
        refs_target = workspace / "approved-references"
        refs_target.mkdir(parents=True, exist_ok=True)
        for reference_id, source in reference_sources:
            destination = refs_target / Path(str(reference_id)).name
            if destination.exists():
                raise FileExistsError(f"参考材料目标重名：{destination.name}")
            shutil.copy2(source, destination)
        next_states = ("reflection_ready",)
    else:
        if state != "initialized":
            raise ValueError(f"evaluation 只能从 initialized 准备，当前为 {state}。")
        if case_meta.get("split") not in {"dev", "exam", "dummy"}:
            raise ValueError("evaluation 只允许 dev、exam 或 dummy 案例。")
        _copy_input(case_dir, workspace)
        _copy_verified_knowledge(trainer_root, case_meta, workspace, "evaluation")
        safe_copy_tree(trainer_root / "templates" / "paper", workspace / "paper-template")
        next_states = ("evaluation_ready",)

    allowed = [
        str(path.resolve())
        for path in workspace.iterdir()
        if path.name not in {"allowed-paths.json", "forbidden-paths.json"}
    ]
    allowed.append(str(skill_root))
    forbidden = [str(reference_vault.resolve()), str(exam_vault.resolve()), *PHASE_FORBIDDEN[phase]]
    write_json(workspace / "allowed-paths.json", {"phase": phase, "paths": allowed})
    write_json(workspace / "forbidden-paths.json", {"phase": phase, "paths": forbidden})
    (workspace / "AGENTS.override.md").write_text(_phase_override(phase, skill_path), encoding="utf-8")
    write_json(
        workspace / "phase-lock.json",
        {
            "lock_id": str(uuid.uuid4()),
            "case_id": case_id,
            "phase": phase,
            "skill": PHASE_SKILLS[phase],
            "skill_path": str(skill_path),
            "created_at": now_iso(),
            "allowed_paths_sha256": sha256_file(workspace / "allowed-paths.json"),
            "forbidden_paths_sha256": sha256_file(workspace / "forbidden-paths.json"),
        },
    )
    current = state
    for target in next_states:
        transition(
            case_dir,
            target,
            command=f"prepare --phase {phase}",
            reason=f"准备隔离的 {phase} 工作区（上一状态 {current}）",
        )
        current = target
    return workspace


def complete_phase(trainer_root: Path, case_id: str, phase: str) -> dict:
    case_dir = find_case(trainer_root, case_id)
    state = load_state(case_dir)["state"]
    workspace = case_dir / "workspaces" / phase
    if phase == "audit":
        if state != "audit_ready":
            raise ValueError(f"完成 audit 需要 audit_ready，当前为 {state}。")
        required = ("audit-report.md", "audit-findings.yaml", "reproduction-report.json", "counterexamples.md", "revision-plan.md")
        target = "audited"
    elif phase == "reflection":
        if state != "reflection_ready":
            raise ValueError(f"完成 reflection 需要 reflection_ready，当前为 {state}。")
        required = ("comparison-matrix.md", "comparison-matrix.yaml", "reference-validation.md", "self-gap-analysis.md", "innovation-analysis.md")
        target = "reflected"
    elif phase == "evaluation":
        if state != "evaluation_ready":
            raise ValueError(f"完成 evaluation 需要 evaluation_ready，当前为 {state}。")
        required = ("evaluation-submission.json",)
        target = "evaluated"
    else:
        raise ValueError("complete 仅支持 audit、reflection 或 evaluation。")
    missing = [name for name in required if not (workspace / name).exists()]
    if missing:
        raise FileNotFoundError(f"阶段输出不完整：{missing}")
    return transition(case_dir, target, command=f"complete --phase {phase}", reason=f"{phase} 阶段必需输出已齐全")
