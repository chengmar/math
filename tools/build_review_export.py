from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


TRAINER_SRC = Path(__file__).resolve().parents[1] / "src"
if str(TRAINER_SRC) not in sys.path:
    sys.path.insert(0, str(TRAINER_SRC))

from cumcm_lab.autopilot import (  # noqa: E402
    _candidate_knowledge_statuses,
    _validate_candidate_proposals,
)


TEXT_SUFFIXES = {
    ".md", ".py", ".ps1", ".tex", ".yaml", ".yml", ".json", ".toml",
    ".txt", ".bib", ".cfg", ".ini", ".sty",
}
FRAMEWORK_DIRS = (
    ".agents/skills", "src", "tools", "scripts", "config", "schemas",
    "templates", "tests", "docs", "prompts", "knowledge",
)
ROOT_FILES = (
    "AGENTS.md", "CHANGELOG.md", "MODELING_STATE.md", "pyproject.toml",
    "requirements-core.txt", "requirements-modeling.txt",
)
SKIP_PARTS = {
    ".git", ".pytest_cache", "__pycache__", ".venv", "node_modules",
    "runtime", "logs", "corpus", "benchmarks", "cases", "reports",
}
MAX_TEXT_BYTES = 1_000_000
COMPLETED_CASES = tuple(f"{year}A" for year in range(2004, 2022))
INCOMPLETE_CASES: tuple[str, ...] = ()
ALL_TRAIN_CASES = tuple(f"{year}A" for year in range(2003, 2022))

ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:\\[^\r\n\"'<>|]*"),
    re.compile(r"(?i)\b[A-Z]:/[^\s\"'<>|]*"),
)
LONG_QUOTE_PATTERNS = (
    re.compile(r"“[^”\r\n]{100,}”"),
    re.compile(r'"[^"\r\n]{180,}"'),
)
BINARY_NAME_PATTERN = re.compile(
    r"(?i)(?:file-[0-9a-f]{10,}|[\w\-\u4e00-\u9fff ]{1,72})\.(?:pdf|docx?|xlsx?|xls|csv|rar|zip|7z|mdb|png|jpe?g|bmp)"
)
SECRET_PATTERNS = {
    "github_pat": re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    "github_classic_pat": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "openai_key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "bearer": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}"),
    "assigned_secret": re.compile(
        r"(?i)(?:access_token|refresh_token|api_key|password|cookie)\s*[:=]\s*[\"'][^<\s][^\"']{11,}[\"']"
    ),
}
MAGIC_PREFIXES = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip_or_office",
    b"\xd0\xcf\x11\xe0": "ole_office",
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpeg",
    b"GIF8": "gif",
    b"Rar!": "rar",
    b"7z\xbc\xaf\x27\x1c": "7z",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def remove_tree_force(path: Path) -> None:
    def make_writable(function: Any, target: str, _error: BaseException) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)
    shutil.rmtree(path, onexc=make_writable)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_yaml(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = "\n".join(line.rstrip() for line in text.splitlines())
    path.write_text(normalized.rstrip() + "\n", encoding="utf-8", newline="\n")


def write_json(path: Path, payload: Any) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def write_yaml(path: Path, payload: Any) -> None:
    write_text(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})


def git_value(root: Path, *args: str, default: str | None = None) -> str | None:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        return default
    return result.stdout.strip() or default


def normalize_path_text(text: str) -> str:
    replacements = (
        (r"<LAB_ROOT>", "<LAB_ROOT>"),
        (r"<RUNTIME_ROOT>", "<RUNTIME_ROOT>"),
        (r"<LAB_ROOT>", "<LAB_ROOT>"),
        (r"<VAULT_ROOT>", "<VAULT_ROOT>"),
        (r"<INTAKE_ROOT>", "<INTAKE_ROOT>"),
        (r"<USER_HOME>", "<USER_HOME>"),
        ("<LAB_ROOT>", "<LAB_ROOT>"),
        ("<RUNTIME_ROOT>", "<RUNTIME_ROOT>"),
        ("<LAB_ROOT>", "<LAB_ROOT>"),
        ("<VAULT_ROOT>", "<VAULT_ROOT>"),
        ("<INTAKE_ROOT>", "<INTAKE_ROOT>"),
        ("<USER_HOME>", "<USER_HOME>"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    text = text.replace("VAULT_ROOT_PLACEHOLDER", "VAULT_ROOT_PLACEHOLDER")
    text = text.replace("INTAKE_ROOT_PLACEHOLDER", "INTAKE_ROOT_PLACEHOLDER")
    text = re.sub(r"(?i)Users[\\/]<LOCAL_USER>", "<USER_HOME>", text)
    for pattern in ABSOLUTE_PATH_PATTERNS:
        text = pattern.sub("<ABSOLUTE_PATH>", text)
    text = re.sub(r"(?i)\blenovo\b", "<LOCAL_USER>", text)
    text = re.sub(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "<EMAIL_REDACTED>", text)
    return text


def sanitize_common(text: str) -> str:
    text = normalize_path_text(text)
    for pattern in LONG_QUOTE_PATTERNS:
        text = pattern.sub("<LONG_QUOTE_REDACTED>", text)
    return text


def redact_problem_sections(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    skip_level: int | None = None
    first_heading = True
    sensitive_heading = re.compile(r"问题重述|题目重述|问题背景|背景介绍|赛题背景|原题")
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            level = len(match.group(1))
            title = match.group(2)
            if first_heading:
                output.append(f"{match.group(1)} <CASE_TITLE_REDACTED>")
                first_heading = False
                skip_level = None
                continue
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if sensitive_heading.search(title):
                output.append(f"{match.group(1)} <PROBLEM_RESTATEMENT_REDACTED>")
                output.append("")
                output.append("本节因可能复述赛题正文而从公开审计快照中删除。")
                skip_level = level
                continue
        if skip_level is None:
            output.append(line)
    return "\n".join(output)


def sanitize_case_text(text: str, *, paper: bool = False, reflection: bool = False) -> str:
    text = sanitize_common(text)
    if paper:
        text = redact_problem_sections(text)
    text = BINARY_NAME_PATTERN.sub("<SOURCE_FILE_REDACTED>", text)
    text = re.sub(r"(?im)^(\s*title\s*:\s*).+$", r"\1<REFERENCE_TITLE_REDACTED>", text)
    text = re.sub(r"(?im)^(\s*path\s*:\s*)[\"']?approved-references/[^\r\n\"']+[\"']?\s*$", r"\1<REFERENCE_ID>", text)
    if reflection:
        text = re.sub(r"(?i)approved-references[/\\][^\s\"']+", "<REFERENCE_ID>", text)
        text = re.sub(r"(?im)^\s*sha256\s*:\s*[\"']?[0-9a-f]{64}[\"']?\s*$", "    sha256: <REFERENCE_HASH_REDACTED>", text)
    return text


def safe_source_text(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > MAX_TEXT_BYTES:
        raise ValueError(f"text too large: {path}")
    if b"\x00" in data:
        raise ValueError(f"binary content: {path}")
    return data.decode("utf-8-sig")


def copy_sanitized(
    src: Path,
    dst: Path,
    *,
    case_text: bool = False,
    paper: bool = False,
    reflection: bool = False,
    framework: bool = False,
) -> None:
    text = safe_source_text(src)
    if case_text:
        text = sanitize_case_text(text, paper=paper, reflection=reflection)
    elif framework:
        # Framework code is already protected by the repository leak and secret
        # gates.  Replacing long quoted literals with a bare marker can make
        # Python, PowerShell, YAML, or JSON syntactically invalid, so framework
        # files receive identity/path normalization only.
        text = normalize_path_text(text)
    else:
        text = sanitize_common(text)
    write_text(dst, text)


def copy_framework(trainer: Path, export: Path) -> list[str]:
    copied: list[str] = []
    for relative_root in FRAMEWORK_DIRS:
        source_root = trainer / relative_root
        if not source_root.is_dir():
            continue
        for source in sorted(source_root.rglob("*")):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(trainer)
            if any(part in SKIP_PARTS for part in relative.parts):
                continue
            if source.suffix.casefold() not in TEXT_SUFFIXES:
                continue
            if source.stat().st_size > MAX_TEXT_BYTES:
                continue
            destination = export / relative
            copy_sanitized(source, destination, framework=True)
            copied.append(relative.as_posix())
    for name in ROOT_FILES:
        source = trainer / name
        if source.is_file():
            copy_sanitized(source, export / name, framework=True)
            copied.append(name)
    source_readme = trainer / "README.md"
    if source_readme.is_file():
        copy_sanitized(source_readme, export / "docs" / "FRAMEWORK_README.md", framework=True)
        copied.append("docs/FRAMEWORK_README.md")
    return sorted(set(copied))


def queue_map(queue: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["case_id"]): item for item in queue.get("items", [])}


def report_status(path: Path) -> str | None:
    payload = read_json(path)
    return str(payload.get("status")) if isinstance(payload, dict) and payload.get("status") is not None else None


def state_times(case_dir: Path) -> tuple[str | None, str | None]:
    state = read_yaml(case_dir / "case-state.yaml", {}) or {}
    history = state.get("history") if isinstance(state, dict) else None
    if not isinstance(history, list) or not history:
        return None, None
    times = [item.get("timestamp") for item in history if isinstance(item, dict) and item.get("timestamp")]
    return (str(times[0]), str(times[-1])) if times else (None, None)


def session_summary(path: Path) -> dict[str, Any]:
    payload = read_json(path, {}) or {}
    return {
        "phase": payload.get("phase"),
        "run_id": payload.get("run_id") or payload.get("session_run_id"),
        "thread_id": payload.get("thread_id"),
        "status": payload.get("status"),
        "model": payload.get("model") or (payload.get("actual_session_contract") or {}).get("model"),
        "reasoning": payload.get("reasoning_effort") or (payload.get("actual_session_contract") or {}).get("reasoning_effort"),
        "fallback": payload.get("fallback") if "fallback" in payload else (payload.get("actual_session_contract") or {}).get("fallback"),
        "ephemeral": payload.get("ephemeral") if "ephemeral" in payload else (payload.get("actual_session_contract") or {}).get("ephemeral"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "exit_code": payload.get("exit_code") or payload.get("original_exit_code"),
        "completion_classification": payload.get("completion_classification"),
    }


def audit_counts(case_dir: Path) -> dict[str, int]:
    candidates = (
        case_dir / "frozen" / "blind-final" / "audit" / "audit-findings.yaml",
        case_dir / "workspaces" / "audit" / "audit-findings.yaml",
    )
    payload: Any = None
    for path in candidates:
        if path.is_file():
            payload = read_yaml(path, {})
            break
    findings = payload.get("findings") if isinstance(payload, dict) else []
    counts = Counter(
        str(item.get("severity", "unknown")).casefold()
        for item in findings or [] if isinstance(item, dict)
    )
    return {
        "critical": counts.get("critical", 0),
        "major": counts.get("major", 0),
        "minor": counts.get("minor", 0),
        "total": sum(counts.values()),
    }


def candidate_entries(lesson_root: Path, case_id: str) -> list[dict[str, Any]]:
    if not lesson_root.is_dir():
        return []
    index = read_yaml(lesson_root / "index.yaml")
    entries: list[dict[str, Any]] = []
    if isinstance(index, dict) and isinstance(index.get("proposals"), list):
        for position, card in enumerate(index["proposals"], 1):
            if not isinstance(card, dict):
                continue
            relative = str(card.get("file") or card.get("path") or "")
            path = lesson_root / relative
            payload = read_yaml(path, {}) if path.is_file() else {}
            text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
            entries.append({
                "card_id": str(card.get("id") or (payload or {}).get("id") or f"{case_id}-candidate-{position:02d}"),
                "status": str(card.get("proposal_state") or card.get("status") or index.get("default_proposal_state") or "candidate"),
                "source_case": case_id,
                "source_file": relative,
                "applicable_conditions_present": bool(re.search(r"适用|applicable|when_to_use", text, re.I)),
                "inapplicable_conditions_present": bool(re.search(r"不适用|inapplicable|when_not_to_use|failure", text, re.I)),
                "looks_case_specific": bool(re.search(r"case[-_ ]specific|具体事实|不迁移", relative + "\n" + text, re.I)),
            })
        return entries
    if isinstance(index, dict) and isinstance(index.get("cards"), list):
        for position, card in enumerate(index["cards"], 1):
            if not isinstance(card, dict):
                continue
            relative = str(card.get("path") or "")
            path = lesson_root / relative
            text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
            entries.append({
                "card_id": str(card.get("id") or f"{case_id}-candidate-{position:02d}"),
                "status": str(card.get("status") or "candidate"),
                "source_case": case_id,
                "source_file": relative,
                "applicable_conditions_present": bool(re.search(r"适用|applicable|when_to_use", text, re.I)),
                "inapplicable_conditions_present": bool(re.search(r"不适用|inapplicable|when_not_to_use|failure", text, re.I)),
                "looks_case_specific": bool(re.search(r"case[-_ ]specific|具体事实|不迁移", relative + "\n" + text, re.I)),
            })
        return entries
    sequence = 0
    for path in sorted((*lesson_root.glob("*.yaml"), *lesson_root.glob("*.yml"))):
        payload = read_yaml(path, {})
        statuses = _candidate_knowledge_statuses(payload)
        text = path.read_text(encoding="utf-8-sig")
        for status in statuses:
            sequence += 1
            entries.append({
                "card_id": f"{case_id}-{path.stem}-{sequence:02d}",
                "status": status,
                "source_case": case_id,
                "source_file": path.name,
                "applicable_conditions_present": bool(re.search(r"适用|applicable|when_to_use", text, re.I)),
                "inapplicable_conditions_present": bool(re.search(r"不适用|inapplicable|when_not_to_use|failure", text, re.I)),
                "looks_case_specific": bool(re.search(r"case[-_ ]specific|具体事实|不迁移", path.name + "\n" + text, re.I)),
            })
    return entries


def leakage_detected(case_dir: Path) -> bool | None:
    reports = list((case_dir / "reports").glob("leakage-*.json"))
    if not reports:
        return None
    return any(report_status(path) == "fail" for path in reports)


def metrics_for_case(case_id: str, item: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    completed = list(item.get("completed_phases") or [])
    solve_session = session_summary(case_dir / "logs" / "solve-session.json")
    v1 = (case_dir / "frozen" / "FROZEN_BLIND_V1.json").is_file()
    final = (case_dir / "frozen" / "FROZEN_BLIND_FINAL.json").is_file()
    score = read_yaml(case_dir / "reports" / "score-report.yaml", {}) or {}
    counts = audit_counts(case_dir)
    lesson_root = case_dir / "workspaces" / "reflection" / "lessons-proposed"
    proposal_validation = _validate_candidate_proposals(lesson_root, case_id)
    candidates = candidate_entries(lesson_root, case_id) if not proposal_validation["invalid"] else []
    retrieval = read_json(case_dir / "workspaces" / "solve" / "retrieval-log.json", {}) or {}
    cards = retrieval.get("cards") if isinstance(retrieval, dict) else []
    started, finished = state_times(case_dir)
    final_score = score.get("total") if final and isinstance(score, dict) else None
    reproduction_label = "blind-final" if final else "blind-v1"
    paper_label = "blind-final" if final else "blind-v1"
    reference_cleanup = read_json(case_dir / "reports" / "reflection-reference-cleanup.json", {}) or {}
    return {
        "case_id": case_id,
        "status": item.get("status"),
        "solve_model": solve_session.get("model"),
        "solve_reasoning": solve_session.get("reasoning"),
        "blind_v1_exists": v1,
        "blind_v1_score": None,
        "audit_completed": "audit" in completed,
        "audit_critical_count": counts["critical"],
        "audit_major_count": counts["major"],
        "audit_minor_count": counts["minor"],
        "blind_final_exists": final,
        "blind_final_score": final_score,
        "score_delta": None,
        "reproduction_pass": report_status(case_dir / "reports" / f"reproduction-{reproduction_label}.json"),
        "paper_lint_pass": report_status(case_dir / "reports" / f"paper-lint-{paper_label}.json"),
        "latex_compile_pass": report_status(case_dir / "reports" / f"tex-compile-{paper_label}.json"),
        "reflection_completed": "reflection" in completed,
        "candidate_count": sum(str(entry.get("status", "")).casefold() == "candidate" for entry in candidates),
        "machine_verified_count": 0,
        "verified_count": 0,
        "knowledge_cards_used_in_solve": len(cards) if isinstance(cards, list) else 0,
        "reference_opened_after_blind_final": bool("reflection" in completed and reference_cleanup),
        "reference_leakage_detected": leakage_detected(case_dir),
        "started_at": started,
        "completed_at": finished if item.get("status") == "completed" else None,
    }


def copy_selected_code(source_root: Path, destination_root: Path) -> list[str]:
    copied: list[str] = []
    if not source_root.is_dir():
        return copied
    for source in sorted(source_root.rglob("*")):
        if not source.is_file() or source.is_symlink():
            continue
        relative = source.relative_to(source_root)
        if any(part == "__pycache__" for part in relative.parts):
            continue
        if source.suffix.casefold() not in TEXT_SUFFIXES or source.stat().st_size > MAX_TEXT_BYTES:
            continue
        copy_sanitized(source, destination_root / relative, case_text=True)
        copied.append(relative.as_posix())
    return copied


def first_existing(paths: Iterable[Path]) -> Path | None:
    return next((path for path in paths if path.is_file()), None)


def export_completed_case(case_id: str, item: dict[str, Any], case_dir: Path, export: Path, metric: dict[str, Any]) -> None:
    out = export / "audit" / "cases" / case_id
    counts = audit_counts(case_dir)
    sessions = {}
    for phase in ("solve", "audit", "blind-revision", "reflection"):
        path = case_dir / "logs" / f"{phase}-session.json"
        if path.is_file():
            sessions[phase] = session_summary(path)
    summary = {
        "schema_version": 1,
        "case_id": case_id,
        "status": item.get("status"),
        "completed_phases": item.get("completed_phases"),
        "blind_v1_exists": metric["blind_v1_exists"],
        "blind_final_exists": metric["blind_final_exists"],
        "audit_findings": counts,
        "reflection_completed": metric["reflection_completed"],
        "candidate_count": metric["candidate_count"],
        "sessions": sessions,
        "score_boundary": {
            "blind_v1_score": None,
            "blind_final_score": metric["blind_final_score"],
            "score_delta": None,
            "note": "源系统未分别保存 Blind V1 与 Blind Final 的独立评分，禁止据此计算提升。",
        },
        "references_opened_only_after_blind_final": metric["reference_opened_after_blind_final"],
        "reference_leakage_detected": metric["reference_leakage_detected"],
    }
    write_json(out / "case-summary.json", summary)
    write_text(out / "case-summary.md", f"""# {case_id} 脱敏案例摘要

- 状态：`{item.get('status')}`
- Blind V1：{'存在' if metric['blind_v1_exists'] else '不存在'}
- Audit：{'完成' if metric['audit_completed'] else '未完成'}
- Blind Final：{'存在' if metric['blind_final_exists'] else '不存在'}
- Reflection：{'完成' if metric['reflection_completed'] else '未完成'}
- Candidate：{metric['candidate_count']}
- 参考资料提前泄漏：{'检测到' if metric['reference_leakage_detected'] else '未检测到'}

本快照没有导出题面、原始数据、参考论文、二进制结果或完整模型对话。源系统没有分别保存两个盲解版本的独立评分，因此不能从现有记录计算真实分数增益。
""")

    v1 = case_dir / "frozen" / "blind-v1"
    final = case_dir / "frozen" / "blind-final"
    for label, source in (("blind-v1", v1), ("blind-final", final)):
        destination = out / label
        for name in ("solution-report.yaml", "reproducibility.yaml"):
            path = source / name
            if path.is_file():
                copy_sanitized(path, destination / name, case_text=True)
        paper = source / "paper" / "paper.md"
        if paper.is_file():
            copy_sanitized(paper, destination / "paper.md", case_text=True, paper=True)
        copy_selected_code(source / "code", destination / "code")
    write_yaml(out / "blind-v1" / "score-report.yaml", {
        "status": "not_recorded",
        "total": None,
        "reason": "源系统没有保存版本专属 Blind V1 评分；不得回填或猜测。",
    })
    score = case_dir / "reports" / "score-report.yaml"
    if score.is_file():
        copy_sanitized(score, out / "blind-final" / "score-report.yaml", case_text=True)

    audit_source = final / "audit"
    for name in ("audit-findings.yaml", "audit-report.md", "revision-plan.md", "counterexamples.md", "reproduction-report.json"):
        path = audit_source / name
        if path.is_file():
            copy_sanitized(path, out / "audit" / name, case_text=True)
    revision = first_existing((
        final / "revision-response.yaml",
        final / "revision-response.md",
        final / "audit-response.md",
        case_dir / "workspaces" / "blind-revision" / "revision-response.yaml",
        case_dir / "workspaces" / "blind-revision" / "revision-response.md",
        case_dir / "workspaces" / "blind-revision" / "audit-response.md",
    ))
    if revision:
        destination_name = "revision-response" + revision.suffix.casefold()
        copy_sanitized(revision, out / "blind-revision" / destination_name, case_text=True)
        write_yaml(out / "blind-revision" / "revision-response.yaml", {
            "status": "exported_from_historical_record",
            "source_artifact": destination_name,
            "audit_finding_count": counts["total"],
            "resolved_ratio": None,
            "note": "不同年份的历史修订记录结构不统一，不能可靠自动计算解决比例。",
        })

    reflection_root = case_dir / "workspaces" / "reflection"
    for name in ("comparison-matrix.yaml", "comparison-matrix.md", "self-gap-analysis.md", "innovation-analysis.md"):
        path = reflection_root / name
        if path.is_file():
            copy_sanitized(path, out / "reflection" / name, case_text=True, reflection=True)
    lesson_root = reflection_root / "lessons-proposed"
    exported_lessons: list[dict[str, Any]] = []
    if lesson_root.is_dir():
        for path in sorted(lesson_root.iterdir()):
            if not path.is_file() or path.suffix.casefold() not in {".md", ".yaml", ".yml"}:
                continue
            destination = out / "reflection" / "lessons-proposed" / path.name
            copy_sanitized(path, destination, case_text=True, reflection=True)
            also = export / "knowledge" / "generated-candidates" / case_id / path.name
            copy_sanitized(path, also, case_text=True, reflection=True)
            exported_lessons.append({"file": path.name, "sha256": sha256_file(destination)})
    write_yaml(out / "generated-knowledge.yaml", {
        "case_id": case_id,
        "status": "candidate_only",
        "candidate_count": metric["candidate_count"],
        "machine_verified_count": 0,
        "verified_count": 0,
        "exported_files": exported_lessons,
    })


def gather_run_metadata(run_root: Path, case_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = run_root / case_id
    if not base.is_dir():
        return rows
    for path in sorted(base.rglob("run-metadata.json")):
        payload = read_json(path, {}) or {}
        row = session_summary(path)
        row["case_id"] = case_id
        row["blocked_reason"] = payload.get("blocked_reason")
        row["error_kind"] = (payload.get("error") or {}).get("kind") if isinstance(payload.get("error"), dict) else None
        rows.append(row)
    return rows


def export_incomplete_case(case_id: str, item: dict[str, Any], case_dir: Path, run_root: Path, export: Path) -> None:
    out = export / "audit" / "cases" / case_id
    state = read_yaml(case_dir / "case-state.yaml", {}) or {}
    metadata = gather_run_metadata(run_root, case_id)
    case_state = {
        "case_id": case_id,
        "status": item.get("status"),
        "lifecycle_status": item.get("lifecycle_status"),
        "current_phase": item.get("current_phase"),
        "completed_phases": item.get("completed_phases"),
        "attempts": item.get("attempts"),
        "blocked_reason": item.get("blocked_reason"),
        "blind_v1_exists": (case_dir / "frozen" / "FROZEN_BLIND_V1.json").is_file(),
        "blind_final_exists": (case_dir / "frozen" / "FROZEN_BLIND_FINAL.json").is_file(),
    }
    write_json(out / "case-state.json", case_state)
    write_json(out / "phase-state.json", {
        "case_id": case_id,
        "state": state.get("state") if isinstance(state, dict) else None,
        "history_event_count": len(state.get("history") or []) if isinstance(state, dict) else 0,
        "current_phase": item.get("current_phase"),
    })
    write_json(out / "non-sensitive-run-metadata.json", metadata)
    write_json(out / "recovery-point.json", {
        "case_id": case_id,
        "status": item.get("status"),
        "atomic_phase": item.get("current_phase"),
        "completed_phases": item.get("completed_phases"),
        "attempts": item.get("attempts"),
        "retry_or_resume_model_session": False,
        "next_action": "外部审计完成前保持暂停；不得自动启动模型。",
    })
    write_text(out / "failure-summary.md", f"""# {case_id} 未完成状态

- 队列状态：`{item.get('status')}`
- 生命周期：`{item.get('lifecycle_status')}`
- 当前阶段：`{item.get('current_phase')}`
- 阻塞原因：`{item.get('blocked_reason') or '未设置'}`
- 最近错误：`{sanitize_common(str(item.get('last_error') or '无'))}`

本目录只包含状态、断点和非敏感模型元数据。未完成输出没有被标记或导出为有效 Blind V1/Blind Final。
""")


def generate_effectiveness(export: Path, metrics: list[dict[str, Any]], knowledge_rows: list[dict[str, Any]]) -> None:
    root = export / "audit" / "training-effectiveness"
    metric_fields = list(metrics[0].keys()) if metrics else []
    write_csv(root / "case-metrics.csv", metrics, metric_fields)
    write_json(root / "case-metrics.json", metrics)

    improvements: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    reproduction_rows: list[dict[str, Any]] = []
    paper_rows: list[dict[str, Any]] = []
    usage_rows: list[dict[str, Any]] = []
    for metric in metrics:
        if metric["case_id"] == "2003A":
            continue
        improvements.append({
            "case_id": metric["case_id"],
            "blind_v1_score": metric["blind_v1_score"],
            "blind_final_score": metric["blind_final_score"],
            "score_delta": None,
            "accuracy_change": None,
            "method_quality_change": None,
            "paper_quality_change": None,
            "audit_findings": metric["audit_critical_count"] + metric["audit_major_count"] + metric["audit_minor_count"],
            "audit_resolved_ratio": None,
            "note": "缺少版本专属评分，无法把数学、方法与排版改进量化分离。",
        })
        audit_rows.append({
            "case_id": metric["case_id"],
            "audit_completed": metric["audit_completed"],
            "critical": metric["audit_critical_count"],
            "major": metric["audit_major_count"],
            "minor": metric["audit_minor_count"],
            "resolved_ratio": None,
        })
        reproduction_rows.append({
            "case_id": metric["case_id"],
            "blind_v1_exists": metric["blind_v1_exists"],
            "blind_final_exists": metric["blind_final_exists"],
            "latest_reproduction_status": metric["reproduction_pass"],
        })
        paper_rows.append({
            "case_id": metric["case_id"],
            "latest_paper_lint_status": metric["paper_lint_pass"],
            "latest_latex_compile_status": metric["latex_compile_pass"],
            "version_specific_quality_delta": None,
        })
        usage_rows.append({
            "case_id": metric["case_id"],
            "cards_used_in_formal_solve": metric["knowledge_cards_used_in_solve"],
            "observed_metric_change": None,
            "causal_claim_supported": False,
        })
    write_csv(root / "blind-improvement.csv", improvements, improvements[0].keys())
    write_csv(root / "audit-findings-summary.csv", audit_rows, audit_rows[0].keys())
    write_csv(root / "reproduction-summary.csv", reproduction_rows, reproduction_rows[0].keys())
    write_csv(root / "paper-quality-summary.csv", paper_rows, paper_rows[0].keys())
    write_csv(root / "knowledge-usage.csv", usage_rows, usage_rows[0].keys())
    knowledge_fields = list(knowledge_rows[0].keys()) if knowledge_rows else [
        "card_id", "status", "source_case", "source_case_count", "used_in_later_solve",
        "positive_cross_case_evidence", "negative_evidence", "applicable_conditions_present",
        "inapplicable_conditions_present", "looks_case_specific",
    ]
    write_csv(root / "knowledge-status.csv", knowledge_rows, knowledge_fields)

    status_counts = Counter(row.get("status") for row in knowledge_rows)
    candidate_total = status_counts.get("candidate", 0)
    cards_used = sum(bool(row.get("used_in_later_solve")) for row in knowledge_rows)
    missing_applicable = sum(not bool(row.get("applicable_conditions_present")) for row in knowledge_rows if row.get("status") == "candidate")
    missing_inapplicable = sum(not bool(row.get("inapplicable_conditions_present")) for row in knowledge_rows if row.get("status") == "candidate")
    case_specific = sum(bool(row.get("looks_case_specific")) for row in knowledge_rows if row.get("status") == "candidate")
    knowledge_summary = {
        "candidate_total": candidate_total,
        "machine_verified_total": status_counts.get("machine_verified", 0),
        "verified_total": status_counts.get("verified", 0),
        "deprecated_total": status_counts.get("deprecated", 0),
        "cards_with_one_source_case": candidate_total,
        "cards_with_multiple_source_cases": 0,
        "cards_used_in_later_solve": cards_used,
        "cards_with_positive_cross_case_evidence": 0,
        "cards_with_negative_evidence": 0,
        "cards_never_used": candidate_total - cards_used,
        "cards_missing_applicable_conditions": missing_applicable,
        "cards_missing_inapplicable_conditions": missing_inapplicable,
        "cards_that_look_case_specific": case_specific,
    }
    write_json(root / "knowledge-summary.json", knowledge_summary)
    completed_ids = [row["case_id"] for row in metrics if row.get("status") == "completed"]
    completed_label = "、".join(completed_ids) if completed_ids else "无"
    write_text(root / "learning-evidence.md", f"""# 学习效果证据分级

## 已被证据支持

- 已完成案例（{completed_label}）的磁盘状态包含 Blind V1、独立 Audit、Blind Final 和 Reflection。
- 已保存冻结清单、复现状态、论文 lint/编译状态以及阶段独立会话元数据。
- 当前生成了 {candidate_total} 条 candidate 记录，未被伪装为 verified。

## 部分被支持

- Audit 确实报告了实质问题，但历史修订响应结构不统一，无法可靠计算全部问题的解决比例。
- Blind V1 与 Blind Final 文件存在差异，但源系统没有两个版本的独立评分，无法把数学改进、方法改进和排版改进量化分离。

## 尚未被支持

- 当前没有 machine_verified 或 verified 知识卡。
- 当前检索日志中进入后续正式 Solve 的训练记忆卡数量为 {sum(row['knowledge_cards_used_in_solve'] for row in metrics)}。
- 当前系统尚未证明知识库提高了未见题解题能力。
- 现有完成案例不足以证明模型能力随年份提升。

## 被证据反驳

- “已有分数可以直接证明 Blind Final 优于 Blind V1”不成立：历史记录只有一个盲后评分边界，版本专属分数缺失。
- “candidate 已经具备跨题有效性”不成立：没有跨题正式使用与对照证据。

## 无法判断

- 训练是否提高真正未见题表现；缺少预注册评分、独立评分者、固定难度标尺和知识启用/禁用对照。
- 改进是否来自数学推理而非提示、工程修复或排版；现有证据粒度不足。
""")
    write_text(root / "limitations.md", """# 审计限制

- 本快照是严格白名单脱敏导出，不含题面、原始数据、参考论文、图片、PDF 和完整模型对话。
- 缺少 Blind V1 与 Blind Final 的版本专属独立评分，因此均值提升和分项提升不能计算。
- 评分主要由同一系统生成，存在共同方法偏差与自评偏高风险。
- candidate 尚未经过跨题正式使用、Shadow 对照或独立人工复核。
- 案例年份与难度不同，按年份比较分数不能直接解释为能力提升。
- 文件级泄漏扫描不能证明模型预训练阶段绝对未见过相似材料。
""")


def generate_root_docs(export: Path, queue: dict[str, Any], metrics: list[dict[str, Any]], source: dict[str, Any], knowledge_rows: list[dict[str, Any]]) -> None:
    completed = [row["case_id"] for row in metrics if row["status"] == "completed"]
    incomplete = [row["case_id"] for row in metrics if row["status"] != "completed"]
    candidate_total = sum(row.get("status") == "candidate" for row in knowledge_rows)
    write_text(export / "README.md", """# CUMCM A 题训练系统脱敏审计快照

这是供第三方独立检查流程、代码、测试、知识结构和既有训练证据的公开脱敏快照。

它不是原始题库，不含参考论文、原始附件、真实数据、PDF、Office 文件、图片、认证信息或完整模型对话。封存最终测试内容没有被导出。

本仓库不代表已经证明系统能力提升。现有证据必须与 `learning-evidence.md` 和 `limitations.md` 一起阅读。
""")
    write_text(export / "AUDIT_INDEX.md", """# 审计阅读顺序

1. `CURRENT_STATUS.md`
2. `audit/training-effectiveness/learning-evidence.md`
3. `audit/training-effectiveness/case-metrics.csv`
4. `audit/training-effectiveness/knowledge-status.csv`
5. `knowledge/`
6. `audit/cases/`
7. `audit/SELF_AUDIT.md`
8. `src/`
9. `tests/`
""")
    write_text(export / "CURRENT_STATUS.md", f"""# 当前真实状态

- 已完成案例：{', '.join(completed) if completed else '无'}
- 未完成或延期案例：{', '.join(incomplete)}
- 系统状态：training_complete_ready_for_final_test
- 当前正式模型：gpt-5.6-sol，reasoning=max，fallback=false
- 最近源项目测试：{source.get('test_result') or '未记录'}
- Candidate：{candidate_total}
- machine_verified：0
- verified：0
- 最终测试：sealed，consumed=false，content_exported=false
- 活动训练进程：0
- 训练已停止；禁止自动启动下一阶段、下一案例或最终测试
- 源分支：`{source['branch']}`
- 源提交：`{source['commit']}`
- 源工作树：{'有未提交修改' if source['dirty'] else '干净'}
""")
    write_text(export / "REVIEW_GUIDE.md", """# 外部审查指南

请重点判断：

1. 阶段状态与会话元数据能否证明盲解、审计、盲修订、参考复盘的先后顺序。
2. 隔离与泄漏守卫是否足以排除题面、答案、reference 或 candidate 提前进入 Solve。
3. Blind Final 相对 Blind V1 的变化是否属于数学/代码实质改进，还是只修复证据链或排版。
4. Audit 是否发现实质问题，修订是否逐项回应，历史响应结构是否可机器核对。
5. Candidate 是否可迁移、是否过于具体、是否包含适用与不适用条件。
6. 没有 machine_verified、没有正式跨题使用时，是否应继续积累 candidate。
7. 当前评分是否存在同模型自评、缺少版本独立评分或年份难度混杂。
8. 继续训练前，是否应先修评分、复现门禁、网络会话收尾和知识对照评测。
""")
    write_text(export / "EXCLUDED_CONTENT.md", """# 明确排除的内容

- 全部真实题面、题库副本、原始题目文本和官方附件。
- 全部原始数据、CSV、Excel、Access、压缩包、图片和二进制结果。
- 全部参考论文、参考图片、参考代码、标题和长段引用。
- 封存最终测试的题面、论文、标题、摘要、方法标签与运行材料。
- `auth.json`、Token、Cookie、API Key、PAT、`.env`、账号信息和私人邮箱。
- 完整绝对路径、本机用户名、PID、活动锁、运行缓存、stderr、JSONL 和完整模型对话。
- PDF、Word、Office、图片、LaTeX 临时文件和 Git 对象目录。
""")


def generate_self_audit(export: Path, metrics: list[dict[str, Any]], knowledge_rows: list[dict[str, Any]]) -> None:
    completed = [row for row in metrics if row["status"] == "completed"]
    full = [row for row in completed if row["blind_v1_exists"] and row["audit_completed"] and row["blind_final_exists"] and row["reflection_completed"]]
    candidate_total = sum(row.get("status") == "candidate" for row in knowledge_rows)
    used = sum(bool(row.get("used_in_later_solve")) for row in knowledge_rows)
    write_text(export / "audit" / "SELF_AUDIT.md", f"""# 训练系统内部审计报告

本报告是供第三方核对的内部陈述，不是外部审计结论。

1. 当前完成了 {len(completed)} 道真实题：{', '.join(row['case_id'] for row in completed)}。
2. 其中 {len(full)} 道同时存在 Blind V1、Audit、Blind Final 和 Reflection。
3. Blind Final 平均提高多少：无法计算；没有版本专属独立评分。
4. 提升来源：只能逐文件审查，当前不能量化区分数学结果、方法、代码证据链和排版。
5. 评分偏差：存在；评分与训练系统同源，且大量数学正确性项仍为 needs_review。
6. 评分过高风险：存在；总分不能替代独立数学复核，部分报告只验证结构和复现。
7. 后续正式 Solve 使用 knowledge：可核验使用次数为 {used}。
8. machine_verified：0；verified：0。
9. 能否证明 Codex 解题能力增强：不能。
10. 缺少证据：版本专属盲评分、独立评分者、固定难度标尺、知识启用/禁用对照、跨题正负证据。
11. 最大风险：训练闭环形式完整，但评分和知识验证不足；网络/CLI 故障还会造成阶段性失败。
12. 最值得保留：最小复制隔离、阶段独立会话、冻结哈希、独立 Audit、失败记录与泄漏守卫。
13. 最应停止或简化：在无跨题验证时继续堆积 candidate；把格式/证据链修复解释为能力提升。
14. 建议：保持训练停止与最终测试密封；如由用户另行授权评测，应保留版本独立评分、复现入口和知识启用/禁用对照。

当前 candidate 记录数：{candidate_total}。它们均保持 candidate 状态。
""")


def build_knowledge_rows(runtime_cases: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case_id in COMPLETED_CASES:
        lesson_root = runtime_cases / case_id / "workspaces" / "reflection" / "lessons-proposed"
        validation = _validate_candidate_proposals(lesson_root, case_id)
        if validation["invalid"]:
            continue
        for entry in candidate_entries(lesson_root, case_id):
            rows.append({
                "card_id": entry["card_id"],
                "status": entry["status"],
                "source_case": case_id,
                "source_case_count": 1,
                "used_in_later_solve": False,
                "positive_cross_case_evidence": False,
                "negative_evidence": False,
                "applicable_conditions_present": entry["applicable_conditions_present"],
                "inapplicable_conditions_present": entry["inapplicable_conditions_present"],
                "looks_case_specific": entry["looks_case_specific"],
            })
    return rows


def problem_fragments(runtime_cases: Path) -> set[str]:
    fragments: set[str] = set()
    for case_id in COMPLETED_CASES:
        roots = (
            runtime_cases / case_id / "workspaces" / "solve",
            runtime_cases / case_id / "frozen" / "blind-v1",
        )
        problem_file = None
        for root in roots:
            if not root.is_dir():
                continue
            candidates = sorted(path for path in root.rglob("*.txt") if "problem" in path.name.casefold())
            if candidates:
                problem_file = candidates[0]
                break
        if not problem_file:
            continue
        try:
            text = re.sub(r"\s+", "", problem_file.read_text(encoding="utf-8-sig", errors="ignore"))
        except OSError:
            continue
        for start in range(0, max(0, len(text) - 119), 40):
            fragment = text[start:start + 120]
            if len(fragment) == 120:
                fragments.add(fragment)
    return fragments


def validate_export(export: Path, runtime_cases: Path | None = None) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    keyword_mentions = 0
    checked_files = 0
    fragments = problem_fragments(runtime_cases) if runtime_cases else set()
    for path in sorted(export.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        checked_files += 1
        relative = path.relative_to(export).as_posix()
        if path.stat().st_size > MAX_TEXT_BYTES:
            findings.append({"category": "oversize", "path": relative})
            continue
        if path.suffix.casefold() == ".csv":
            if not relative.startswith("audit/training-effectiveness/"):
                findings.append({"category": "csv_outside_audit_metrics", "path": relative})
        elif path.suffix.casefold() not in TEXT_SUFFIXES and path.name not in {".gitignore", "EXPORT_FILES.sha256"}:
            findings.append({"category": "extension_not_allowed", "path": relative})
        data = path.read_bytes()
        for magic, label in MAGIC_PREFIXES.items():
            if data.startswith(magic):
                findings.append({"category": f"binary_magic_{label}", "path": relative})
        if b"\x00" in data:
            findings.append({"category": "nul_byte", "path": relative})
            continue
        text = data.decode("utf-8", errors="replace")
        for pattern in ABSOLUTE_PATH_PATTERNS:
            if pattern.search(text):
                findings.append({"category": "absolute_path", "path": relative})
        if re.search(r"(?i)CUMCM-A-(?:Intake|Vaults)|Users[\\/]|\blenovo\b", text):
            findings.append({"category": "local_identity_or_private_root", "path": relative})
        if path.name.casefold() in {"auth.json", ".env"}:
            findings.append({"category": "auth_file", "path": relative})
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append({"category": f"secret_{label}", "path": relative})
        keyword_mentions += len(re.findall(r"(?i)auth\.json|refresh_token|access_token|api_key|github_token|openai_api_key", text))
        if BINARY_NAME_PATTERN.search(text) and relative.startswith("audit/cases/"):
            findings.append({"category": "source_filename_in_case_evidence", "path": relative})
        if fragments and relative.startswith("audit/cases/"):
            normalized = re.sub(r"\s+", "", text)
            if any(fragment in normalized for fragment in fragments):
                findings.append({"category": "long_problem_text_overlap", "path": relative})
    report = {
        "status": "pass" if not findings else "fail",
        "checked_files": checked_files,
        "findings": findings,
        "secret_keyword_mentions_classified_as_code_or_documentation": keyword_mentions,
        "real_problem_files_exported": 0,
        "reference_papers_exported": 0,
        "raw_data_exported": 0,
        "test_2023_content_exported": 0,
        "auth_files_exported": 0,
        "limitation": "文件类型、秘密、路径、文件名和可用题面文本长片段扫描；不能证明模型预训练阶段绝对未见过相似内容。",
    }
    return report


def clean_export_caches(export: Path) -> None:
    export = export.resolve()
    cache_dirs = sorted(
        (path for path in export.rglob("*") if path.is_dir() and path.name in {"__pycache__", ".pytest_cache"}),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in cache_dirs:
        resolved = path.resolve()
        if not resolved.is_relative_to(export) or resolved == export:
            raise RuntimeError(f"refusing cache cleanup outside export: {resolved}")
        shutil.rmtree(resolved)
    for path in export.rglob("*.pyc"):
        resolved = path.resolve()
        if not resolved.is_relative_to(export):
            raise RuntimeError(f"refusing pyc cleanup outside export: {resolved}")
        resolved.unlink()


def refresh_integrity(export: Path, manifest_updates: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest_path = export / "PUBLISH_MANIFEST.json"
    manifest = read_json(manifest_path, {}) or {}
    if manifest_updates:
        manifest.update(manifest_updates)
    for _ in range(4):
        files = [path for path in sorted(export.rglob("*")) if path.is_file() and ".git" not in path.parts]
        lines = []
        for path in files:
            relative = path.relative_to(export).as_posix()
            if relative in {"PUBLISH_MANIFEST.json", "EXPORT_FILES.sha256"}:
                continue
            lines.append(f"{sha256_file(path)}  {relative}")
        write_text(export / "EXPORT_FILES.sha256", "\n".join(lines))
        files = [path for path in sorted(export.rglob("*")) if path.is_file() and ".git" not in path.parts]
        manifest["export_file_count"] = len(files)
        manifest["export_total_size"] = sum(path.stat().st_size for path in files)
        manifest["hash_manifest_scope"] = "all export files except PUBLISH_MANIFEST.json and EXPORT_FILES.sha256 (self-reference exclusion)"
        write_json(manifest_path, manifest)
    return manifest


def build(args: argparse.Namespace) -> int:
    trainer = args.trainer.resolve()
    lab_root = args.lab_root.resolve()
    runtime_cases = args.runtime_cases.resolve()
    export = args.export.resolve()
    expected = (lab_root / "review-export" / "math-audit").resolve()
    if export != expected:
        raise SystemExit(f"refusing unexpected export target: {export}")
    if export.exists():
        if not args.replace:
            raise SystemExit("export target already exists; pass --replace only after independent path verification")
        if export.parent != (lab_root / "review-export").resolve():
            raise SystemExit(f"refusing recursive replacement outside review-export: {export}")
        remove_tree_force(export)
    export.mkdir(parents=True)

    source = {
        "commit": git_value(trainer, "rev-parse", "HEAD"),
        "branch": git_value(trainer, "branch", "--show-current"),
        "dirty": bool(git_value(trainer, "status", "--porcelain", default="")),
        "test_result": args.source_test_result,
    }
    copied_framework = copy_framework(trainer, export)
    queue = read_json(trainer / "runtime" / "training-queue-state.json", {}) or {}
    items = queue_map(queue)
    metrics: list[dict[str, Any]] = []
    for case_id in ALL_TRAIN_CASES:
        item = items.get(case_id, {"case_id": case_id, "status": "missing"})
        metrics.append(metrics_for_case(case_id, item, runtime_cases / case_id))

    write_yaml(export / "audit" / "status" / "2003A.yaml", {
        "case_id": "2003A",
        "status": "deferred_platform_safety",
        "blind_v1": "not_completed",
        "reference_opened": False,
        "knowledge_generated": False,
    })
    write_yaml(export / "audit" / "status" / "final-test.yaml", {
        "case_id": "2023A",
        "status": "test_sealed",
        "consumed": False,
        "content_exported": False,
    })
    snapshot = trainer / "reports" / "knowledge-snapshot-before-2023.json"
    if snapshot.is_file():
        copy_sanitized(snapshot, export / "audit" / "status" / "knowledge-snapshot-before-2023.json")
    final_report = lab_root / "setup-reports" / "FULL-TRAINING-2016-2021-COMPLETION-REPORT.md"
    if final_report.is_file():
        copy_sanitized(final_report, export / "FINAL_TRAINING_REPORT.md")
    for case_id in COMPLETED_CASES:
        export_completed_case(case_id, items[case_id], runtime_cases / case_id, export, next(row for row in metrics if row["case_id"] == case_id))
    run_root = trainer / "runtime" / "autopilot-runs"
    for case_id in INCOMPLETE_CASES:
        export_incomplete_case(case_id, items[case_id], runtime_cases / case_id, run_root, export)

    knowledge_rows = build_knowledge_rows(runtime_cases)
    generate_effectiveness(export, metrics, knowledge_rows)
    generate_root_docs(export, queue, metrics, source, knowledge_rows)
    generate_self_audit(export, metrics, knowledge_rows)
    write_text(export / ".gitignore", """__pycache__/
.pytest_cache/
.venv/
*.py[cod]
*.pdf
*.doc
*.docx
*.xls
*.xlsx
*.png
*.jpg
*.jpeg
*.zip
*.rar
*.7z
*.log
auth.json
.env
""")
    write_json(export / "FRAMEWORK_EXPORT.json", {
        "schema_version": 1,
        "copied_framework_files": copied_framework,
        "whitelist_roots": list(FRAMEWORK_DIRS),
        "root_files": list(ROOT_FILES),
        "unknown_files_copied": 0,
    })
    scan = validate_export(export, runtime_cases)
    write_json(export / "audit" / "EXPORT_SCAN.json", scan)
    if scan["status"] != "pass":
        raise SystemExit(json.dumps(scan, ensure_ascii=False, indent=2))
    write_json(export / "PUBLISH_MANIFEST.json", {
        "generated_at": now_iso(),
        "source_git_commit": source["commit"],
        "source_git_branch": source["branch"],
        "source_worktree_dirty": source["dirty"],
        "export_file_count": 0,
        "export_total_size": 0,
        "test_result": args.source_test_result,
        "leak_scan_result": scan["status"],
        "secret_scan_result": scan["status"],
        "real_problem_files_exported": 0,
        "reference_papers_exported": 0,
        "raw_data_exported": 0,
        "test_2023_content_exported": 0,
        "auth_files_exported": 0,
        "publish_commit": None,
    })
    manifest = refresh_integrity(export)
    print(json.dumps({
        "status": "pass",
        "export": str(export),
        "file_count": manifest["export_file_count"],
        "total_size": manifest["export_total_size"],
        "candidate_count": sum(row.get("status") == "candidate" for row in knowledge_rows),
        "completed_cases": list(COMPLETED_CASES),
    }, ensure_ascii=False, indent=2))
    return 0


def validate_command(args: argparse.Namespace) -> int:
    export = args.export.resolve()
    clean_export_caches(export)
    report = validate_export(export, args.runtime_cases.resolve() if args.runtime_cases else None)
    write_json(export / "audit" / "EXPORT_SCAN.json", report)
    refresh_integrity(export, {
        "leak_scan_result": report["status"],
        "secret_scan_result": report["status"],
    })
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


def finalize(args: argparse.Namespace) -> int:
    clean_export_caches(args.export.resolve())
    updates: dict[str, Any] = {}
    if args.test_result is not None:
        updates["test_result"] = args.test_result
    if args.publish_commit is not None:
        updates["publish_commit"] = args.publish_commit
    manifest = refresh_integrity(args.export.resolve(), updates)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate a deterministic sanitized CUMCM-A-Lab review export.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("build")
    create.add_argument("--trainer", type=Path, required=True)
    create.add_argument("--lab-root", type=Path, required=True)
    create.add_argument("--runtime-cases", type=Path, required=True)
    create.add_argument("--export", type=Path, required=True)
    create.add_argument("--source-test-result", default="not_run")
    create.add_argument("--replace", action="store_true")
    check = sub.add_parser("validate")
    check.add_argument("--export", type=Path, required=True)
    check.add_argument("--runtime-cases", type=Path)
    finish = sub.add_parser("finalize")
    finish.add_argument("--export", type=Path, required=True)
    finish.add_argument("--test-result")
    finish.add_argument("--publish-commit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "build":
        return build(args)
    if args.command == "validate":
        return validate_command(args)
    return finalize(args)


if __name__ == "__main__":
    raise SystemExit(main())
