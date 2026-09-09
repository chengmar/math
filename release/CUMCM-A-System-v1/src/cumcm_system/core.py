from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
import uuid


MODEL = "gpt-5.6-sol"
REASONING = "max"
FORMAL_STAGES = ["solve", "audit", "revision-correctness", "revision-paper-verification"]
TIMEOUTS = {"solve": 21600, "audit": 14400, "revision-correctness": 14400, "revision-paper-verification": 14400, "reflection": 14400}


class SystemError(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_executable(env_name: str, names: tuple[str, ...], local_candidates: tuple[Path, ...] = ()) -> str | None:
    configured = os.environ.get(env_name)
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    for candidate in local_candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    for name in names:
        located = shutil.which(name)
        if located:
            return str(Path(located).resolve())
    return None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _parse_codex_events(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"thread_id": None, "turn_completed": 0, "fatal_errors": [], "warnings": []}
    if not path.is_file():
        return summary
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and not summary["thread_id"]:
            summary["thread_id"] = event.get("thread_id")
        elif event.get("type") == "turn.completed":
            summary["turn_completed"] += 1
        elif event.get("type") == "error":
            message = str(event.get("message") or "Codex error")
            target = "warnings" if message.startswith("Reconnecting...") else "fatal_errors"
            summary[target].append(message)
    return summary


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(root: Path) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    tree = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise SystemError(f"冻结目录禁止符号链接：{path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest = sha256_file(path)
        size = path.stat().st_size
        records.append({"path": relative, "size": size, "sha256": digest})
        tree.update(f"{relative}\0{digest}\0{size}\n".encode("utf-8"))
    return records, tree.hexdigest()


def freeze_tree(source: Path, destination: Path, manifest: Path, label: str) -> dict[str, Any]:
    if destination.exists() or manifest.exists():
        raise SystemError(f"冻结目标已存在，拒绝覆盖：{destination}")
    if not source.is_dir():
        raise SystemError(f"冻结源不存在：{source}")
    source_files, source_tree = inventory(source)
    if not source_files:
        raise SystemError("拒绝冻结空提交")
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    frozen_files, frozen_tree = inventory(destination)
    if source_files != frozen_files or source_tree != frozen_tree:
        raise SystemError("冻结复制后哈希不一致")
    payload = {"schema_version": 1, "status": "frozen", "label": label, "file_count": len(frozen_files), "tree_sha256": frozen_tree, "files": frozen_files}
    write_json(manifest, payload)
    return payload


def verify_freeze(snapshot: Path, manifest: Path) -> dict[str, Any]:
    expected = read_json(manifest)
    files, tree = inventory(snapshot)
    passed = files == expected.get("files") and tree == expected.get("tree_sha256")
    return {"status": "pass" if passed else "fail", "file_count": len(files), "tree_sha256": tree}


def system_root() -> Path:
    return Path(__file__).resolve().parents[2]


def cases_root(root: Path) -> Path:
    config = read_json(root / "config" / "system.json")
    configured = config.get("cases_root", "user-cases")
    path = Path(str(configured))
    return path if path.is_absolute() else root / path


def validate_case_id(case_id: str) -> str:
    if not case_id or len(case_id) > 64 or not case_id[0].isalnum() or any(not (char.isalnum() or char in "_-") for char in case_id):
        raise SystemError("案例 ID 仅允许 1-64 位字母、数字、下划线和连字符")
    return case_id


def case_dir(root: Path, case_id: str) -> Path:
    return cases_root(root) / validate_case_id(case_id)


def new_case(root: Path, case_id: str, problem_files: list[Path], data_files: list[Path]) -> dict[str, Any]:
    destination = case_dir(root, case_id)
    if destination.exists():
        raise SystemError(f"案例已存在，拒绝覆盖：{case_id}")
    if not problem_files:
        raise SystemError("至少需要一个题面文件")
    validated_inputs: list[tuple[str, int, Path]] = []
    for role, paths in (("problem", problem_files), ("data", data_files)):
        for index, source in enumerate(paths, 1):
            source = source.expanduser()
            if source.is_symlink():
                raise SystemError(f"输入禁止符号链接：{source}")
            resolved = source.resolve(strict=True)
            if not resolved.is_file():
                raise SystemError(f"输入必须是普通文件：{resolved}")
            validated_inputs.append((role, index, resolved))
    for relative in ("input/problem", "input/data", "resources", "scripts", "submission", "frozen", "audit", "runs", "reports", "export"):
        (destination / relative).mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    for role, index, source in validated_inputs:
        target = destination / "input" / role / f"{role}-{index:02d}{source.suffix.lower()}"
        shutil.copy2(source, target)
        copied.append({"role": role, "name": target.name, "size": target.stat().st_size, "sha256": sha256_file(target)})
    mode = read_json(root / "config" / "system.json")["mode"]
    shutil.copytree(root / "templates", destination / "resources" / "paper-template", dirs_exist_ok=True, copy_function=shutil.copy2)
    shutil.copy2(root / "config" / "AGENTS.md", destination / "resources" / "AGENTS.md")
    for skill_name in ("cumcm-a-solve", "cumcm-a-audit"):
        shutil.copytree(root / "skills" / skill_name, destination / "resources" / "skills" / skill_name, copy_function=shutil.copy2)
    shutil.copy2(root / "scripts" / "case-tools" / "check_phase.py", destination / "scripts" / "check_phase.py")
    if mode == "full-trained":
        memory_target = destination / "resources" / "training-memory"
        memory_target.mkdir(parents=True)
        shutil.copy2(root / "knowledge-snapshot" / "index.yaml", memory_target / "index.yaml")
        shutil.copytree(root / "knowledge-snapshot" / "cards", memory_target / "cards", copy_function=shutil.copy2)
    state = {"schema_version": 1, "case_id": case_id, "state": "initialized", "paused": False, "mode": mode, "reference_accessed": False, "history": [], "inputs": copied}
    write_json(destination / "case-state.json", state)
    return state


def _append_history(case: Path, state: dict[str, Any], event: str, **extra: Any) -> None:
    state.setdefault("history", []).append({"event": event, "time": time.time(), **extra})
    write_json(case / "case-state.json", state)


def _copy_inputs(case: Path, target: Path) -> None:
    shutil.copytree(case / "input", target / "input", dirs_exist_ok=True, copy_function=shutil.copy2)


def _fake_stage(case: Path, stage: str, mode: str) -> None:
    if stage == "solve":
        target = case / "submission" / "blind-v1"
        (target / "code").mkdir(parents=True, exist_ok=True)
        (target / "results").mkdir(parents=True, exist_ok=True)
        (target / "paper").mkdir(parents=True, exist_ok=True)
        (target / "code" / "generate.py").write_text("import json\njson.dump({'dummy_metric': 1.0}, open('../results/results.json','w'))\n", encoding="utf-8")
        write_json(target / "results" / "results.json", {"dummy_metric": 1.0, "synthetic": True})
        (target / "paper" / "main.tex").write_text("\\documentclass{article}\\begin{document}Synthetic lifecycle test.\\end{document}\n", encoding="utf-8")
        write_json(target / "reproducibility.json", {"executor": "fake", "status": "pass", "command": "synthetic"})
        write_json(target / "solution-report.json", {"status": "complete", "synthetic": True})
        write_json(target / "stage-status.json", {"status": "complete", "synthetic": True})
        if mode == "full-trained":
            write_json(
                target / "training-memory-selection.json",
                {"schema_version": 1, "retrieved": [], "adopted": [], "zero_adoption_allowed": True, "synthetic": True},
            )
            (target / "training-memory-usage.md").write_text(
                "# Synthetic training-memory usage\n\nNo cards were adopted.\n", encoding="utf-8"
            )
    elif stage == "audit":
        write_json(case / "audit" / "findings.json", {"findings": [], "synthetic": True})
        (case / "audit" / "findings.md").write_text("# Synthetic findings\n\nNo findings.\n", encoding="utf-8")
        (case / "audit" / "counterexamples").mkdir(parents=True, exist_ok=True)
        (case / "audit" / "revision-plan.md").write_text("# Synthetic revision plan\n\nNo critical findings.\n", encoding="utf-8")
        write_json(case / "audit" / "reproduction-report.json", {"status": "pass", "synthetic": True})
        write_json(case / "audit" / "stage-status.json", {"status": "complete", "synthetic": True})
    elif stage == "revision-correctness":
        source = case / "frozen" / "blind-v1"
        target = case / "submission" / "revision-work"
        shutil.copytree(source, target, copy_function=shutil.copy2)
        write_json(target / "revision-progress.json", {"status": "complete", "findings": [], "synthetic": True})
        write_json(target / "correctness-stage-status.json", {"status": "complete", "synthetic": True})
    elif stage == "revision-paper-verification":
        source = case / "submission" / "revision-work"
        target = case / "submission" / "blind-final"
        shutil.copytree(source, target, copy_function=shutil.copy2)
        write_json(target / "finding-map.json", {"status": "pass", "findings": [], "synthetic": True})
        write_json(target / "stage-status.json", {"status": "complete", "synthetic": True})
    else:
        raise SystemError(f"未知阶段：{stage}")


def validate_stage_contract(case: Path, stage: str, mode: str) -> dict[str, Any]:
    expected: dict[str, list[Path]] = {
        "solve": [
            case / "submission" / "blind-v1" / "code",
            case / "submission" / "blind-v1" / "results",
            case / "submission" / "blind-v1" / "paper",
            case / "submission" / "blind-v1" / "stage-status.json",
        ],
        "audit": [
            case / "audit" / "findings.json",
            case / "audit" / "findings.md",
            case / "audit" / "counterexamples",
            case / "audit" / "revision-plan.md",
            case / "audit" / "reproduction-report.json",
            case / "audit" / "stage-status.json",
        ],
        "revision-correctness": [
            case / "submission" / "revision-work" / "revision-progress.json",
            case / "submission" / "revision-work" / "correctness-stage-status.json",
        ],
        "revision-paper-verification": [
            case / "submission" / "blind-final" / "code",
            case / "submission" / "blind-final" / "results",
            case / "submission" / "blind-final" / "paper",
            case / "submission" / "blind-final" / "finding-map.json",
            case / "submission" / "blind-final" / "stage-status.json",
        ],
    }
    required = list(expected[stage])
    if stage == "solve" and mode == "full-trained":
        required.extend(
            [
                case / "submission" / "blind-v1" / "training-memory-selection.json",
                case / "submission" / "blind-v1" / "training-memory-usage.md",
            ]
        )
    missing = [str(path.relative_to(case)) for path in required if not path.exists()]
    if stage == "solve" and mode == "workflow-only" and (case / "resources" / "training-memory").exists():
        missing.append("workflow-only case unexpectedly contains resources/training-memory")
    report = {"schema_version": 1, "stage": stage, "status": "pass" if not missing else "fail", "missing": missing}
    write_json(case / "reports" / f"{stage}-contract-verification.json", report)
    if missing:
        raise SystemError(f"阶段输出合同不完整：{stage}；缺失 {missing}")
    return report


def _write_stage_guard(case: Path, stage: str, mode: str) -> None:
    read_paths = {
        "solve": ["input", "resources/AGENTS.md", "resources/skills/cumcm-a-solve/SKILL.md", "resources/paper-template"],
        "audit": ["input", "frozen/blind-v1", "frozen/FROZEN_BLIND_V1.json", "resources/skills/cumcm-a-audit/SKILL.md"],
        "revision-correctness": ["input", "frozen/blind-v1", "frozen/FROZEN_BLIND_V1.json", "audit", "resources/skills/cumcm-a-solve/SKILL.md"],
        "revision-paper-verification": ["input", "frozen/blind-v1", "frozen/FROZEN_BLIND_V1.json", "audit", "submission/revision-work", "resources/paper-template", "resources/skills/cumcm-a-solve/SKILL.md"],
    }[stage]
    if stage == "solve" and mode == "full-trained":
        read_paths.append("resources/training-memory")
    write_paths = {
        "solve": ["submission/blind-v1", "runs", "runtime-supervisor.json"],
        "audit": ["audit", "runs", "runtime-supervisor.json"],
        "revision-correctness": ["submission/revision-work", "runs", "runtime-supervisor.json"],
        "revision-paper-verification": ["submission/blind-final", "reports", "runs", "runtime-supervisor.json"],
    }[stage]
    write_json(case / "phase-lock.json", {
        "schema_version": 1,
        "case_id": case.name,
        "active_phase": stage,
        "mode": mode,
        "outside_workspace_search_forbidden": True,
    })
    write_json(case / "allowed-paths.json", {
        "schema_version": 1,
        "case_id": case.name,
        "phase": stage,
        "read": read_paths + ["phase-lock.json", "allowed-paths.json", "scripts/check_phase.py"],
        "write": write_paths,
        "forbidden": ["references", "other-cases", "parent-directories", "network", "existing-answers"],
    })


def _codex_prompt(root: Path, case: Path, stage: str, mode: str, recovery: bool = False) -> str:
    skill_note = "本案例为 workflow-only；禁止定位或加载 training-memory。"
    if mode == "full-trained":
        skill_note = (
            "本案例为 full-trained；仅 Solve 可读取 resources/training-memory/index.yaml，最多检索 5 张卡，"
            "逐张记录 retrieved、adopt/adapt/reject、decision_influenced、validation_added、"
            "complexity_added、observed_risks；允许采用 0 张。其他阶段不得扩大读取范围。"
        )
    stage_contracts = {
        "solve": (
            "读取 resources/AGENTS.md、resources/skills/cumcm-a-solve/SKILL.md、input/ 与 resources/paper-template/。"
            "把盲解持续写入 submission/blind-v1，至少包含 code、results、figures、paper、"
            "reproducibility 和机器可读 stage-status；不得自行创建冻结标记。"
        ),
        "audit": (
            "只读核查 frozen/blind-v1，读取 resources/skills/cumcm-a-audit/SKILL.md，"
            "把 findings、counterexamples、revision-plan 和 reproduction-report 写入 audit/；"
            "不得修改 frozen/blind-v1。"
        ),
        "revision-correctness": (
            "以 frozen/blind-v1 和 audit/ 为只读依据，只在 submission/revision-work 中修复 critical/high 数学、"
            "代码、公式、单位、约束、边界、稳定性、关键数字和反例；持续更新 revision-progress.json，"
            "本阶段不做非必要论文润色。"
        ),
        "revision-paper-verification": (
            "只在 submission/blind-final 中完成论文整合、图表、摘要、符号、结果一致性、复现说明和 finding-map；"
            "运行确定性复现/论文构建并写 stage-status。不得修改 frozen/blind-v1 或 audit/，不得自行冻结。"
        ),
    }
    return (
        f"你正在本地 CUMCM-A-System 中执行唯一阶段 {stage}。只读取当前案例，不联网查答案，不读取其他案例、"
        f"现成答案或尚未授权的参考资料。模型合同固定为 {MODEL}、reasoning={REASONING}、"
        "fallback=false、当前调用为全新 ephemeral Thread。\n\n"
        f"第一条命令必须是当前 Python 执行 scripts/check_phase.py --workspace . --phase {stage}；"
        "若门禁缺失或失败，立即停止，绝对不得到父目录或工作区外寻找替代脚本。\n\n"
        + ("这是同一未完成阶段的恢复调用；保留现有部分输出，只完成明确缺口，不重新设计已完成内容。\n\n" if recovery else "")
        + f"{skill_note}\n\n{stage_contracts[stage]}\n\n"
        "把实质进度持续写盘；关键数字由代码生成。结束前检查必需输出并给出简洁终答；终答不替代磁盘交付。"
    )


def _prepare_real_stage(case: Path, stage: str) -> bool:
    recovery = False
    if stage == "solve":
        target = case / "submission" / "blind-v1"
        if target.exists() and any(target.iterdir()):
            recovery = True
        target.mkdir(parents=True, exist_ok=True)
    elif stage == "audit":
        recovery = (case / "audit").exists() and any((case / "audit").iterdir())
        (case / "audit").mkdir(parents=True, exist_ok=True)
    elif stage == "revision-correctness":
        source = case / "frozen" / "blind-v1"
        target = case / "submission" / "revision-work"
        if target.exists():
            recovery = True
        else:
            shutil.copytree(source, target, copy_function=shutil.copy2)
            shutil.copytree(case / "audit", target / "audit", copy_function=shutil.copy2)
    elif stage == "revision-paper-verification":
        source = case / "submission" / "revision-work"
        target = case / "submission" / "blind-final"
        if target.exists():
            recovery = True
        else:
            shutil.copytree(source, target, copy_function=shutil.copy2)
    else:
        raise SystemError(f"未知阶段：{stage}")
    return recovery


def _terminate_exact_process_tree(process: subprocess.Popen[str]) -> dict[str, Any]:
    if process.poll() is not None:
        return {"requested": False, "pid": process.pid, "return_code": process.returncode}
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        process.wait(timeout=30)
        return {
            "requested": True,
            "pid": process.pid,
            "method": "taskkill_exact_pid_tree",
            "taskkill_exit_code": completed.returncode,
            "return_code": process.returncode,
        }
    process.terminate()
    process.wait(timeout=30)
    return {"requested": True, "pid": process.pid, "method": "terminate", "return_code": process.returncode}


def _run_codex_supervised(
    *,
    command: list[str],
    prompt: str,
    case: Path,
    run_dir: Path,
    events: Path,
    stderr: Path,
    final: Path,
    phase: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    nonce = uuid.uuid4().hex
    started = time.time()
    deadline = started + timeout_seconds
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    with events.open("w", encoding="utf-8", newline="\n") as out, stderr.open(
        "w", encoding="utf-8", newline="\n"
    ) as err:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            cwd=case,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            **popen_kwargs,
        )
        assert process.stdin is not None
        process.stdin.write(prompt)
        process.stdin.close()
        (case / "runtime-supervisor.pid").write_text(f"{process.pid}\n", encoding="ascii")
        terminal_seen: float | None = None
        termination: dict[str, Any] | None = None
        timed_out = False
        terminal_recovered = False
        while True:
            summary = _parse_codex_events(events)
            heartbeat = {
                "schema_version": 1,
                "status": "running",
                "case_id": case.name,
                "phase": phase,
                "run_id": run_dir.name,
                "thread_id": summary["thread_id"],
                "child_pid": process.pid,
                "nonce": nonce,
                "events_path": str(events),
                "final_message_path": str(final),
                "started_at_epoch": started,
                "deadline_epoch": deadline,
                "last_event_time_epoch": events.stat().st_mtime if events.exists() else None,
                "heartbeat_at_epoch": time.time(),
            }
            write_json(case / "runtime-supervisor.json", heartbeat)
            write_json(
                case / "runtime-supervisor-heartbeat.json",
                {"child_pid": process.pid, "nonce": nonce, "heartbeat_at_epoch": heartbeat["heartbeat_at_epoch"]},
            )
            return_code = process.poll()
            if return_code is not None:
                break
            terminal_complete = bool(
                summary["turn_completed"] >= 1
                and not summary["fatal_errors"]
                and final.is_file()
                and final.stat().st_size > 0
            )
            if terminal_complete:
                terminal_seen = terminal_seen or time.time()
                if time.time() - terminal_seen >= 120:
                    termination = _terminate_exact_process_tree(process)
                    terminal_recovered = True
                    return_code = process.returncode
                    break
            else:
                terminal_seen = None
            if time.time() >= deadline:
                termination = _terminate_exact_process_tree(process)
                timed_out = True
                return_code = process.returncode
                break
            time.sleep(1)
    summary = _parse_codex_events(events)
    final_state = {
        "schema_version": 1,
        "status": "completed" if not timed_out else "timed_out",
        "case_id": case.name,
        "phase": phase,
        "run_id": run_dir.name,
        "thread_id": summary["thread_id"],
        "child_pid": process.pid,
        "nonce": nonce,
        "events_path": str(events),
        "final_message_path": str(final),
        "started_at_epoch": started,
        "finished_at_epoch": time.time(),
        "return_code": return_code,
        "timed_out": timed_out,
        "terminal_recovered": terminal_recovered,
        "termination": termination,
    }
    write_json(run_dir / "runtime-supervisor-final.json", final_state)
    write_json(case / "runtime-supervisor.json", {**final_state, "status": "idle", "child_pid": None, "nonce": None})
    (case / "runtime-supervisor.pid").unlink(missing_ok=True)
    (case / "runtime-supervisor-heartbeat.json").unlink(missing_ok=True)
    return {**final_state, "event_summary": summary}


def _real_stage(root: Path, case: Path, stage: str, mode: str) -> None:
    codex = resolve_executable("CUMCM_CODEX", ("codex.exe", "codex"))
    if not codex:
        raise SystemError("未找到 Codex CLI")
    recovery = _prepare_real_stage(case, stage)
    run_dir = case / "runs" / f"{stage}-{int(time.time())}"
    run_dir.mkdir(parents=True)
    final = run_dir / "final-message.txt"
    events = run_dir / "events.jsonl"
    stderr = run_dir / "stderr.txt"
    command = [
        codex, "exec", "--strict-config", "-C", str(case), "-m", MODEL,
        "-c", f'model_reasoning_effort="{REASONING}"', "-c", 'approval_policy="never"',
        "-s", "workspace-write", "--json", "-o", str(final), "--skip-git-repo-check", "--ephemeral", "-",
    ]
    outcome = _run_codex_supervised(
        command=command,
        prompt=_codex_prompt(root, case, stage, mode, recovery),
        case=case,
        run_dir=run_dir,
        events=events,
        stderr=stderr,
        final=final,
        phase=stage,
        timeout_seconds=TIMEOUTS[stage],
    )
    summary = outcome["event_summary"]
    timed_out = bool(outcome["timed_out"])
    exit_code = outcome["return_code"]
    debug_text = stderr.read_text(encoding="utf-8-sig", errors="replace")
    configured_models = re.findall(r"Configuring session: model=([^;\s]+);", debug_text)
    model_mismatch = bool(configured_models) and configured_models != [MODEL]
    valid = bool(
        not timed_out
        and (exit_code == 0 or outcome["terminal_recovered"])
        and summary["thread_id"]
        and summary["turn_completed"] >= 1
        and not summary["fatal_errors"]
        and final.is_file()
        and final.stat().st_size > 0
        and not model_mismatch
    )
    metadata = {
        "schema_version": 1,
        "stage": stage,
        "status": "pass" if valid else "fail",
        "requested_model": MODEL,
        "actual_model": MODEL if valid else None,
        "reasoning": REASONING,
        "fallback": False,
        "ephemeral": True,
        "thread_id": summary["thread_id"],
        "turn_completed": summary["turn_completed"],
        "fatal_errors": summary["fatal_errors"],
        "warnings": summary["warnings"],
        "elapsed_seconds": round(float(outcome["finished_at_epoch"]) - float(outcome["started_at_epoch"]), 3),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "terminal_recovered": outcome["terminal_recovered"],
        "process_tree_termination": outcome["termination"],
        "configured_models_from_debug": configured_models,
    }
    write_json(run_dir / "run-metadata.json", metadata)
    if not valid:
        reason = "模型或推理合同不匹配" if model_mismatch else "调用未形成完整终止事件和 final-message"
        raise SystemError(f"Codex 阶段失败：{stage}；{reason}；证据已保存在 {run_dir}")


def _completed_codex_stage(case: Path, stage: str) -> dict[str, Any] | None:
    candidates = sorted((case / "runs").glob(f"{stage}-*/run-metadata.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for metadata_path in candidates:
        try:
            metadata = read_json(metadata_path)
        except (OSError, json.JSONDecodeError):
            continue
        if (
            metadata.get("stage") == stage
            and metadata.get("status") == "pass"
            and metadata.get("requested_model") == MODEL
            and metadata.get("actual_model") == MODEL
            and metadata.get("reasoning") == REASONING
            and metadata.get("fallback") is False
            and metadata.get("ephemeral") is True
            and int(metadata.get("turn_completed") or 0) >= 1
        ):
            return metadata
    return None


def _freeze_or_verify(source: Path, snapshot: Path, manifest: Path, label: str) -> dict[str, Any]:
    if snapshot.exists() or manifest.exists():
        if not snapshot.is_dir() or not manifest.is_file():
            raise SystemError(f"冻结现场不完整，拒绝覆盖：{label}")
        verified = verify_freeze(snapshot, manifest)
        if verified["status"] != "pass":
            raise SystemError(f"既有冻结件校验失败，拒绝覆盖：{label}")
        return verified
    return freeze_tree(source, snapshot, manifest, label)


def deterministic_revision_verification(case: Path) -> dict[str, Any]:
    candidate = case / "submission" / "blind-final"
    required_groups = {
        "code": candidate / "code",
        "results": candidate / "results",
        "paper": candidate / "paper",
    }
    missing = [name for name, path in required_groups.items() if not path.is_dir()]
    paper_sources = [path for path in (candidate / "paper").glob("*.tex")] if (candidate / "paper").is_dir() else []
    machine_results = [path for path in (candidate / "results").rglob("*") if path.is_file()] if (
        candidate / "results"
    ).is_dir() else []
    code_files = [path for path in (candidate / "code").rglob("*") if path.is_file()] if (
        candidate / "code"
    ).is_dir() else []
    symlinks = [str(path.relative_to(candidate)) for path in candidate.rglob("*") if path.is_symlink()] if candidate.exists() else []
    checks = {
        "required_groups": "pass" if not missing else "fail",
        "paper_source": "pass" if paper_sources else "fail",
        "machine_results": "pass" if machine_results else "fail",
        "code": "pass" if code_files else "fail",
        "symlink_guard": "pass" if not symlinks else "fail",
        "finding_map": "pass" if (candidate / "finding-map.json").is_file() else "needs_review",
        "stage_status": "pass" if (candidate / "stage-status.json").is_file() else "needs_review",
    }
    blocking = any(checks[name] == "fail" for name in ("required_groups", "paper_source", "machine_results", "code", "symlink_guard"))
    records, tree = inventory(candidate) if candidate.is_dir() and not symlinks else ([], "")
    report = {
        "schema_version": 1,
        "status": "fail" if blocking else "pass",
        "checks": checks,
        "missing": missing,
        "symlinks": symlinks,
        "file_count": len(records),
        "tree_sha256": tree,
        "additional_model_calls": 0,
    }
    write_json(case / "reports" / "deterministic-revision-verification.json", report)
    return report


def _acquire_case_lock(case: Path) -> tuple[Path, str]:
    lock = case / "run.lock"
    if lock.exists():
        try:
            current = read_json(lock)
        except (OSError, json.JSONDecodeError):
            raise SystemError("案例运行锁损坏；请先用 Case-Status 检查并人工保全现场")
        owner_pid = int(current.get("pid") or 0)
        if _pid_exists(owner_pid):
            raise SystemError(f"案例已有运行锁，活动 PID={owner_pid}")
        stale = case / "reports" / f"stale-run-lock-{int(time.time())}.json"
        shutil.copy2(lock, stale)
        lock.unlink()
    nonce = uuid.uuid4().hex
    payload = {"schema_version": 1, "case_id": case.name, "pid": os.getpid(), "nonce": nonce, "created_at": time.time()}
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise SystemError("案例已有运行锁") from error
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return lock, nonce


def run_case(root: Path, case_id: str, executor: str, max_stages: int | None = None) -> dict[str, Any]:
    case = case_dir(root, case_id).resolve(strict=True)
    state_path = case / "case-state.json"
    state = read_json(state_path)
    if state.get("paused"):
        return state
    lock, nonce = _acquire_case_lock(case)
    _append_history(case, state, "run_started", pid=os.getpid(), nonce=nonce, executor=executor)
    completed_count = 0
    try:
        while state["state"] != "blind_final_frozen":
            if (case / "pause.requested").exists():
                state["paused"] = True
                _append_history(case, state, "paused_at_boundary")
                break
            if max_stages is not None and completed_count >= max_stages:
                break
            current = state["state"]
            if current == "initialized":
                stage, next_state = "solve", "blind_v1_frozen"
            elif current == "blind_v1_frozen":
                stage, next_state = "audit", "audited"
            elif current == "audited":
                stage, next_state = "revision-correctness", "correctness_revised"
            elif current == "correctness_revised":
                stage, next_state = "revision-paper-verification", "blind_final_frozen"
            else:
                raise SystemError(f"无法从状态继续：{current}")
            _write_stage_guard(case, stage, state["mode"])
            if executor == "fake":
                _fake_stage(case, stage, state["mode"])
            elif executor == "codex":
                completed = _completed_codex_stage(case, stage)
                if completed is None:
                    _real_stage(root, case, stage, state["mode"])
                else:
                    _append_history(case, state, "completed_stage_recovered_without_model_call", stage=stage, thread_id=completed.get("thread_id"))
            else:
                raise SystemError("executor 必须是 fake 或 codex")
            validate_stage_contract(case, stage, state["mode"])
            if stage == "solve":
                _freeze_or_verify(case / "submission" / "blind-v1", case / "frozen" / "blind-v1", case / "frozen" / "FROZEN_BLIND_V1.json", "FROZEN_BLIND_V1")
            elif stage == "revision-paper-verification":
                gate = deterministic_revision_verification(case)
                if gate["status"] != "pass":
                    raise SystemError("确定性 Revision Verification 失败，拒绝冻结 Blind Final")
                _freeze_or_verify(case / "submission" / "blind-final", case / "frozen" / "blind-final", case / "frozen" / "FROZEN_BLIND_FINAL.json", "FROZEN_BLIND_FINAL")
                if verify_freeze(case / "frozen" / "blind-final", case / "frozen" / "FROZEN_BLIND_FINAL.json")["status"] != "pass":
                    raise SystemError("Blind Final 确定性冻结复验失败")
            state["state"] = next_state
            _append_history(case, state, "stage_completed", stage=stage, executor=executor)
            completed_count += 1
    finally:
        lock.unlink(missing_ok=True)
    return read_json(state_path)


def pause_case(root: Path, case_id: str) -> dict[str, Any]:
    case = case_dir(root, case_id).resolve(strict=True)
    (case / "pause.requested").write_text("pause at next safe stage boundary\n", encoding="utf-8")
    state = read_json(case / "case-state.json")
    state["paused"] = True
    _append_history(case, state, "pause_requested")
    return state


def resume_case(root: Path, case_id: str, executor: str) -> dict[str, Any]:
    case = case_dir(root, case_id).resolve(strict=True)
    state = read_json(case / "case-state.json")
    state["paused"] = False
    (case / "pause.requested").unlink(missing_ok=True)
    _append_history(case, state, "resumed")
    return run_case(root, case_id, executor)


def case_status(root: Path, case_id: str) -> dict[str, Any]:
    case = case_dir(root, case_id).resolve(strict=True)
    state = read_json(case / "case-state.json")
    lock = case / "run.lock"
    lock_state: dict[str, Any] | None = None
    if lock.is_file():
        try:
            lock_state = read_json(lock)
            lock_state["pid_active"] = _pid_exists(int(lock_state.get("pid") or 0))
        except (OSError, ValueError, json.JSONDecodeError):
            lock_state = {"status": "damaged", "pid_active": None}
    runs = sorted((case / "runs").glob("*/run-metadata.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    state["runtime"] = {
        "lock": lock_state,
        "supervisor": read_json(case / "runtime-supervisor.json") if (case / "runtime-supervisor.json").is_file() else None,
        "latest_run_metadata": read_json(runs[0]) if runs else None,
        "pause_requested": (case / "pause.requested").is_file(),
    }
    return state


def _run_reflection(root: Path, case: Path, executor: str) -> dict[str, Any]:
    workspace = case / "workspaces" / f"reflection-{int(time.time())}"
    workspace.mkdir(parents=True)
    shutil.copytree(case / "frozen" / "blind-final", workspace / "blind-final", copy_function=shutil.copy2)
    shutil.copytree(case / "references", workspace / "approved-references", copy_function=shutil.copy2)
    shutil.copytree(root / "skills" / "cumcm-a-reflect", workspace / "skill", copy_function=shutil.copy2)
    (workspace / "scripts").mkdir()
    shutil.copy2(root / "scripts" / "case-tools" / "check_phase.py", workspace / "scripts" / "check_phase.py")
    manifest = read_json(case / "frozen" / "FROZEN_BLIND_FINAL.json")
    write_json(
        workspace / "reflection-contract.json",
        {
            "schema_version": 1,
            "case_id": case.name,
            "blind_final_tree_sha256": manifest["tree_sha256"],
            "output": "reference-reflection.md",
            "frozen_solution_modification_forbidden": True,
            "training_memory_modification_forbidden": True,
        },
    )
    write_json(workspace / "phase-lock.json", {"schema_version": 1, "case_id": case.name, "active_phase": "reflection", "outside_workspace_search_forbidden": True})
    write_json(workspace / "allowed-paths.json", {
        "schema_version": 1,
        "case_id": case.name,
        "phase": "reflection",
        "read": ["blind-final", "approved-references", "skill/SKILL.md", "reflection-contract.json", "phase-lock.json", "allowed-paths.json", "scripts/check_phase.py"],
        "write": ["reference-reflection.md", "lessons-proposed"],
        "forbidden": ["parent-directories", "network", "system-skills", "training-memory"],
    })
    if executor == "fake":
        (workspace / "reference-reflection.md").write_text(
            "# Synthetic reference reflection\n\nBlind Final remained immutable.\n", encoding="utf-8"
        )
        thread_id = "fake-reflection-thread"
    elif executor == "codex":
        codex = resolve_executable("CUMCM_CODEX", ("codex.exe", "codex"))
        if not codex:
            raise SystemError("未找到 Codex CLI")
        run_dir = case / "runs" / f"reflection-{int(time.time())}"
        run_dir.mkdir(parents=True)
        events = run_dir / "events.jsonl"
        stderr = run_dir / "stderr.txt"
        final = run_dir / "final-message.txt"
        prompt = (
            "这是 Blind Final 后的独立 Reflection 阶段。第一条命令必须用当前 Python执行 "
            "scripts/check_phase.py --workspace . --phase reflection；失败即停止且不得向工作区外搜索。"
            "随后读取 skill/SKILL.md 和 reflection-contract.json。只比较当前工作区的 blind-final/ 与 approved-references/；不得修改 blind-final，不得访问其他案例，"
            "不得修改系统 Skills 或训练记忆。把复盘写到 reference-reflection.md，指出关键结果/方法/"
            "小问覆盖、共同错误、参考资料自身风险和可供未来用户理解的结论。模型固定为 "
            f"{MODEL}、reasoning={REASONING}、fallback=false、新 ephemeral Thread。"
        )
        command = [
            codex, "exec", "--strict-config", "-C", str(workspace), "-m", MODEL,
            "-c", f'model_reasoning_effort="{REASONING}"', "-c", 'approval_policy="never"',
            "-s", "workspace-write", "--json", "-o", str(final), "--skip-git-repo-check", "--ephemeral", "-",
        ]
        outcome = _run_codex_supervised(
            command=command,
            prompt=prompt,
            case=case,
            run_dir=run_dir,
            events=events,
            stderr=stderr,
            final=final,
            phase="reflection",
            timeout_seconds=TIMEOUTS["reflection"],
        )
        summary = outcome["event_summary"]
        completed_process = outcome["return_code"] == 0 or outcome["terminal_recovered"]
        if not completed_process or outcome["timed_out"] or summary["turn_completed"] < 1 or summary["fatal_errors"] or not final.is_file():
            raise SystemError(f"Reflection 未完整结束；证据保存在 {run_dir}")
        thread_id = str(summary["thread_id"])
    else:
        raise SystemError("executor 必须是 fake 或 codex")
    report = workspace / "reference-reflection.md"
    if not report.is_file() or report.stat().st_size == 0:
        raise SystemError("Reflection 未生成 reference-reflection.md")
    shutil.copy2(report, case / "reports" / "reference-reflection.md")
    return {"status": "complete", "thread_id": thread_id, "workspace": str(workspace), "report": str(report)}


def add_references(root: Path, case_id: str, references: list[Path], executor: str) -> dict[str, Any]:
    case = case_dir(root, case_id).resolve(strict=True)
    state = read_json(case / "case-state.json")
    if state.get("case_id") != case_id:
        raise SystemError("案例 ID 与状态文件不一致")
    if state["state"] != "blind_final_frozen" or verify_freeze(case / "frozen" / "blind-final", case / "frozen" / "FROZEN_BLIND_FINAL.json")["status"] != "pass":
        raise SystemError("只有 Blind Final 冻结并复验通过后才能导入参考论文")
    if not 2 <= len(references) <= 4:
        raise SystemError("参考论文数量必须为 2-4 篇")
    target = case / "references"
    target.mkdir(exist_ok=False)
    identities: list[dict[str, Any]] = []
    for index, source in enumerate(references, 1):
        source = source.expanduser()
        if source.is_symlink():
            raise SystemError(f"参考资料禁止符号链接：{source}")
        source = source.resolve(strict=True)
        if not source.is_file():
            raise SystemError(f"参考资料必须是普通文件：{source}")
        destination = target / f"reference-{index:02d}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        identities.append({"name": destination.name, "size": destination.stat().st_size, "sha256": sha256_file(destination)})
    state["reference_accessed"] = True
    state["state"] = "reference_imported_reflection_in_progress"
    write_json(case / "references" / "REFERENCE-MANIFEST.json", {"schema_version": 1, "case_id": case_id, "files": identities})
    _append_history(case, state, "references_imported", executor=executor, reference_count=len(references))
    before = verify_freeze(case / "frozen" / "blind-final", case / "frozen" / "FROZEN_BLIND_FINAL.json")
    reflection = _run_reflection(root, case, executor)
    after = verify_freeze(case / "frozen" / "blind-final", case / "frozen" / "FROZEN_BLIND_FINAL.json")
    if before["status"] != "pass" or after["status"] != "pass" or before["tree_sha256"] != after["tree_sha256"]:
        raise SystemError("Reflection 前后 Blind Final 哈希变化，已停止")
    state = read_json(case / "case-state.json")
    state["state"] = "reflected"
    write_json(
        case / "reports" / "reference-reflection.json",
        {
            "status": "complete",
            "case_id": case_id,
            "reference_count": len(references),
            "references": identities,
            "blind_final_immutable": True,
            "blind_final_tree_sha256": after["tree_sha256"],
            "thread_id": reflection["thread_id"],
            "training_memory_modified": False,
        },
    )
    _append_history(case, state, "reflection_completed", executor=executor, thread_id=reflection["thread_id"])
    return state


def export_submission(root: Path, case_id: str, destination: Path) -> dict[str, Any]:
    case = case_dir(root, case_id).resolve(strict=True)
    state = read_json(case / "case-state.json")
    if state["state"] not in {"blind_final_frozen", "reflected"}:
        raise SystemError("案例尚无可导出的 Blind Final")
    destination = destination.resolve(strict=False)
    if destination.exists():
        raise SystemError("导出目标已存在，拒绝覆盖")
    destination.mkdir(parents=True)
    shutil.copytree(case / "frozen" / "blind-final", destination / "submission", copy_function=shutil.copy2)
    shutil.copy2(case / "frozen" / "FROZEN_BLIND_FINAL.json", destination / "FROZEN_BLIND_FINAL.json")
    report = {"status": "pass", "case_id": case_id, "destination": str(destination), "freeze": verify_freeze(destination / "submission", destination / "FROZEN_BLIND_FINAL.json")}
    write_json(destination / "EXPORT-REPORT.json", report)
    return report


def set_mode(root: Path, mode: str) -> dict[str, Any]:
    if mode not in {"workflow-only", "full-trained"}:
        raise SystemError("模式必须是 workflow-only 或 full-trained")
    config_path = root / "config" / "system.json"
    config = read_json(config_path)
    config["mode"] = mode
    write_json(config_path, config)
    return config


def verify_system(root: Path) -> dict[str, Any]:
    required = [
        "README-USER.md", "START-HERE.md", "QUICKSTART.md", "USER-MANUAL.md", "TROUBLESHOOTING.md",
        "LIMITATIONS.md", "EVALUATION-SUMMARY.md", "recommended-mode.json", "RELEASE-MANIFEST.json", "RELEASE-FILES.sha256", "config/system.json",
        "Launch-CUMCM-A-System.ps1", "src/cumcm_system/core.py", "scripts/case-tools/check_phase.py", "scripts/Verify-System.ps1", "scripts/New-Case.ps1",
        "scripts/Run-Case.ps1", "scripts/Pause-Case.ps1", "scripts/Resume-Case.ps1",
        "scripts/Case-Status.ps1", "scripts/Add-References-And-Reflect.ps1",
        "scripts/Export-Submission.ps1", "scripts/Open-Latest-Report.ps1", "scripts/Set-Mode.ps1",
        "skills", "templates", "knowledge-snapshot", "demo",
    ]
    missing = [item for item in required if not (root / item).exists()]
    manifest = root / "RELEASE-FILES.sha256"
    mismatches: list[str] = []
    if manifest.is_file():
        for line in manifest.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            digest, relative = line.split("  ", 1)
            path = root / relative
            if not path.is_file() or sha256_file(path) != digest:
                mismatches.append(relative)
    codex = resolve_executable("CUMCM_CODEX", ("codex.exe", "codex"))
    xelatex = resolve_executable(
        "CUMCM_XELATEX",
        ("xelatex.exe", "xelatex"),
        (
            root / "runtime" / "miktex" / "xelatex.exe",
            root.parent.parent / "runtime" / "miktex-portable" / "texmfs" / "install" / "miktex" / "bin" / "x64" / "xelatex.exe",
        ),
    )
    git = resolve_executable("CUMCM_GIT", ("git.exe", "git"))
    configured = read_json(root / "config" / "system.json")
    runtime_ready = bool(codex and xelatex and git)
    status = "pass" if not missing and not mismatches and runtime_ready else "fail"
    return {
        "status": status,
        "version": "1.0.0",
        "mode": configured["mode"],
        "recommended_mode": configured["recommended_mode"],
        "missing": missing,
        "hash_mismatches": mismatches,
        "python": os.environ.get("CUMCM_PYTHON") or sys.executable,
        "codex_cli": codex,
        "codex_home": os.environ.get("CODEX_HOME") or "user-default",
        "model": MODEL,
        "reasoning": REASONING,
        "fallback": False,
        "xelatex": xelatex,
        "git": git,
        "cases_root": str(cases_root(root)),
        "runtime_ready": runtime_ready,
        "note": "结构与哈希必须通过；真实模型运行还要求 Codex CLI、XeLaTeX 与 Git 可定位。",
    }
