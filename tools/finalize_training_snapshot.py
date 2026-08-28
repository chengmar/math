from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.autopilot import CodexPhaseExecutor, _validate_candidate_proposals  # noqa: E402
from cumcm_lab.training_queue import (  # noqa: E402
    load_training_queue,
    queue_summary,
    read_final_test_seal,
    set_stop_requested,
)
from cumcm_lab.util import load_lab_paths, write_json  # noqa: E402


TRAIN_CASES = tuple(f"{year}A" for year in range(2004, 2022))
FINAL_STATUS = "training_complete_ready_for_final_test"
PHASES = ("solve", "audit", "blind-revision", "reflection")
SKILLS = ("cumcm-a-solve", "cumcm-a-audit", "cumcm-a-reflect", "cumcm-a-evaluate")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def summarize_usage(runtime_cases: Path) -> dict[str, Any]:
    by_case: list[dict[str, Any]] = []
    decisions: Counter[str] = Counter()
    usage_years: dict[str, list[str]] = {}
    positive_records = 0
    negative_records = 0
    for case_id in TRAIN_CASES:
        log_path = runtime_cases / case_id / "workspaces" / "solve" / "retrieval-log.json"
        payload = read_json(log_path, {}) or {}
        cards = payload.get("cards") if isinstance(payload, dict) else []
        rows: list[dict[str, str]] = []
        for position, card in enumerate(cards or [], start=1):
            if not isinstance(card, dict):
                continue
            card_id = str(card.get("id") or card.get("card_id") or f"unidentified-{position}")
            decision = str(card.get("decision") or "retrieved_only").casefold()
            rows.append({"card_id": card_id, "decision": decision})
            decisions[decision] += 1
            usage_years.setdefault(card_id, []).append(case_id)
            positive = card.get("positive_evidence") or card.get("observed_positive_evidence")
            negative = card.get("negative_evidence") or card.get("observed_negative_evidence")
            positive_records += len(positive) if isinstance(positive, list) else int(bool(positive))
            negative_records += len(negative) if isinstance(negative, list) else int(bool(negative))
        by_case.append({"case_id": case_id, "cards": rows})
    for decision in ("adopt", "adapt", "reject", "retrieved_only"):
        decisions.setdefault(decision, 0)
    return {
        "by_case": by_case,
        "decision_counts": dict(sorted(decisions.items())),
        "usage_years_by_card": dict(sorted(usage_years.items())),
        "positive_cross_case_evidence_records": positive_records,
        "negative_cross_case_evidence_records": negative_records,
    }


def candidate_summary(runtime_cases: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}
    invalid_cases: list[str] = []
    for case_id in TRAIN_CASES:
        root = runtime_cases / case_id / "workspaces" / "reflection" / "lessons-proposed"
        result = _validate_candidate_proposals(root, case_id)
        if result["invalid"]:
            invalid_cases.append(case_id)
            counts[case_id] = 0
        else:
            counts[case_id] = int(result["candidate_count"])
    return {
        "by_case": counts,
        "total_valid_candidates": sum(counts.values()),
        "round_2016_2021_valid_candidates": sum(counts[f"{year}A"] for year in range(2016, 2022)),
        "legacy_invalid_candidate_packages": invalid_cases,
    }


def memory_summary(trainer: Path, usage: dict[str, Any]) -> dict[str, Any]:
    memory_root = trainer / "knowledge" / "training-memory"
    index = yaml.safe_load((memory_root / "index.yaml").read_text(encoding="utf-8-sig")) or {}
    cards: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    for card_id in index.get("cards") or []:
        payload = yaml.safe_load((memory_root / "cards" / f"{card_id}.yaml").read_text(encoding="utf-8-sig")) or {}
        status = str(payload.get("status") or "unknown")
        statuses[status] += 1
        cards.append({
            "id": str(card_id),
            "status": status,
            "source_cases": list(payload.get("source_cases") or []),
            "actual_usage_years": list(usage["usage_years_by_card"].get(str(card_id), [])),
        })
    return {
        "version": index.get("version"),
        "active_card_limit": index.get("active_card_limit"),
        "current_active_count": len(cards),
        "status_counts": dict(sorted(statuses.items())),
        "provisional_at_risk_count": statuses.get("provisional_at_risk", 0),
        "machine_verified_count": statuses.get("machine_verified", 0),
        "verified_count": statuses.get("verified", 0),
        "cards": cards,
        "tree_sha256": tree_sha256(memory_root),
    }


def session_contract_summary(runtime_cases: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for year in range(2016, 2022):
        case_id = f"{year}A"
        for phase in PHASES:
            payload = read_json(runtime_cases / case_id / "logs" / f"{phase}-session.json", {}) or {}
            records.append({
                "case_id": case_id,
                "phase": phase,
                "model": payload.get("model"),
                "reasoning": payload.get("reasoning_effort"),
                "fallback": payload.get("fallback"),
                "ephemeral": payload.get("ephemeral"),
            })
    return {
        "requested_model": "gpt-5.6-sol",
        "actual_models": sorted({str(row["model"]) for row in records}),
        "reasoning_values": sorted({str(row["reasoning"]) for row in records}),
        "fallback_detected": any(row["fallback"] is not False for row in records),
        "all_ephemeral": all(row["ephemeral"] is True for row in records),
        "session_count": len(records),
    }


def command_output(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {command[0]}")
    return completed.stdout.strip()


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    trainer = args.trainer.resolve()
    runtime_cases = args.runtime_cases.resolve()
    runtime = trainer / "runtime"
    queue_path = runtime / "training-queue-state.json"
    if (runtime / "autopilot.lock").exists() or (runtime / "autopilot.pid").exists():
        raise RuntimeError("active or stale autopilot lock/pid must be cleared before finalization")

    queue = load_training_queue(queue_path)
    items = {str(item["case_id"]): item for item in queue["items"]}
    if items.get("2003A", {}).get("status") != "deferred_platform_safety":
        raise RuntimeError("2003A platform-safety deferral is not preserved")
    bad = [case_id for case_id in TRAIN_CASES if items.get(case_id, {}).get("status") != "completed"]
    if bad:
        raise RuntimeError(f"training cases are not complete: {bad}")

    set_stop_requested(queue_path, True)
    queue = load_training_queue(queue_path)
    completed_at = now_iso()
    policy_path = runtime / "execution-policy.json"
    policy = read_json(policy_path, {}) or {}
    execution_policy = dict(policy.get("execution_policy") or {})
    execution_policy.update({
        "continuous_mode": False,
        "training_paused": True,
        "new_model_sessions_allowed": False,
        "auto_start_next_phase": False,
        "auto_start_next_case": False,
        "current_case": None,
        "current_phase": None,
        "stop_after_current_case": True,
        "stop_before_2023": True,
    })
    policy["execution_policy"] = execution_policy
    policy["training_status"] = FINAL_STATUS
    policy["completed_at"] = completed_at
    write_json(policy_path, policy)

    paths = load_lab_paths(trainer)
    seal = read_final_test_seal(Path(paths["exam_vault"]) / "2023A" / "SEALED.json")
    if seal.get("state") != "sealed" or seal.get("status") != "test_sealed":
        raise RuntimeError("final test is not sealed")

    state = {
        "schema_version": 1,
        "status": FINAL_STATUS,
        "pid": None,
        "process_alive": False,
        "lock_nonce": None,
        "current_case": None,
        "current_phase": None,
        "current_attempt": None,
        "current_run_id": None,
        "recoverable": False,
        "blocker_kind": None,
        "blocker": None,
        "last_error": None,
        "queue": queue_summary(queue),
        "stop_requested": True,
        "training_paused": True,
        "new_model_sessions_allowed": False,
        "auto_start_next_phase": False,
        "auto_start_next_case": False,
        "case_completion_barrier": "released",
        "next_case_dispatch_allowed": False,
        "finished_at": completed_at,
        "updated_at": completed_at,
    }
    write_json(runtime / "autopilot-state.json", state)

    executor = CodexPhaseExecutor(trainer, Path(args.codex_home).resolve())
    progress = executor.record_progress(queue_path, runtime)
    progress["system_status"] = FINAL_STATUS
    progress["execution_policy"] = execution_policy
    write_json(trainer / "reports" / "training-summary.json", progress)
    progress_path = trainer / "reports" / "autopilot-progress.md"
    progress_text = progress_path.read_text(encoding="utf-8")
    progress_text = progress_text.replace("系统状态：ready_for_final_test", f"系统状态：{FINAL_STATUS}")
    progress_path.write_text(progress_text, encoding="utf-8", newline="\n")

    usage = summarize_usage(runtime_cases)
    candidates = candidate_summary(runtime_cases)
    memory = memory_summary(trainer, usage)
    session_contract = session_contract_summary(runtime_cases)
    if session_contract["actual_models"] != ["gpt-5.6-sol"]:
        raise RuntimeError("unexpected actual model in formal sessions")
    if session_contract["reasoning_values"] != ["max"] or session_contract["fallback_detected"]:
        raise RuntimeError("formal session reasoning/fallback contract failed")

    skill_hashes = {
        skill: sha256_file(trainer / ".agents" / "skills" / skill / "SKILL.md")
        for skill in SKILLS
    }
    codex = shutil.which(args.codex_executable) or args.codex_executable
    snapshot = {
        "schema_version": 1,
        "snapshot": "knowledge-snapshot-before-2023",
        "status": FINAL_STATUS,
        "generated_at": completed_at,
        "source": {
            "git_commit": command_output(["git", "rev-parse", "HEAD"], trainer),
            "git_branch": command_output(["git", "branch", "--show-current"], trainer),
            "git_worktree_dirty": bool(command_output(["git", "status", "--porcelain"], trainer)),
            "codex_cli_version": command_output([codex, "--version"], trainer),
        },
        "integrity": {
            "AGENTS.md": sha256_file(trainer / "AGENTS.md"),
            "skills": skill_hashes,
            "training_memory_tree_sha256": memory["tree_sha256"],
            "model_policy_sha256": sha256_file(policy_path),
        },
        "case_status": {
            "completed": list(TRAIN_CASES),
            "completed_with_caveats": [],
            "deferred_platform_safety": ["2003A"],
            "incomplete": [],
        },
        "candidates": candidates,
        "training_memory": memory,
        "usage": usage,
        "session_contract": session_contract,
        "quality_gates": {
            "pytest": args.pytest_result,
            "regression": args.regression_result,
            "git_leak_guard": args.git_leak_result,
            "secret_scan": args.secret_scan_result,
        },
        "runtime": {
            "active_training_processes": 0,
            "pid_present": False,
            "lock_present": False,
            "nonce_present": False,
            "queue_stop_requested": True,
        },
        "final_test": {
            "status": "test_sealed",
            "consumed": False,
            "content_accessed": False,
            "content_exported": False,
        },
    }
    snapshot_path = trainer / "reports" / "knowledge-snapshot-before-2023.json"
    write_json(snapshot_path, snapshot)
    return {
        "status": FINAL_STATUS,
        "snapshot": str(snapshot_path),
        "snapshot_sha256": sha256_file(snapshot_path),
        "candidate_total": candidates["total_valid_candidates"],
        "provisional_training": memory["status_counts"].get("provisional_training", 0),
        "queue": queue_summary(queue),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze completed training and create the pre-final-test knowledge snapshot.")
    parser.add_argument("--trainer", type=Path, default=TRAINER_ROOT)
    parser.add_argument("--runtime-cases", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--pytest-result", required=True)
    parser.add_argument("--regression-result", required=True)
    parser.add_argument("--git-leak-result", required=True)
    parser.add_argument("--secret-scan-result", required=True)
    args = parser.parse_args(argv)
    try:
        result = finalize(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"finalization failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
