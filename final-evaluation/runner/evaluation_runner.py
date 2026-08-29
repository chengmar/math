from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


MODEL = "gpt-5.6-sol"
REASONING = "max"
SANDBOX = "workspace-write"
ALLOWED_PHASES = {"probe", "solve", "audit", "blind-revision", "judge", "reference-adjudication"}


class EvaluationRunnerError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def confined_file(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    root_resolved = root.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(root_resolved):
        raise EvaluationRunnerError(f"{label} must be a regular file inside the arm workspace")
    if resolved.is_symlink():
        raise EvaluationRunnerError(f"{label} must not be a symlink")
    return resolved


def confined_directory(path: Path, runtime_root: Path) -> Path:
    resolved = path.resolve(strict=True)
    allowed_root = runtime_root.resolve(strict=True)
    if not resolved.is_dir() or not resolved.is_relative_to(allowed_root):
        raise EvaluationRunnerError("workspace must be inside the registered evaluation runtime root")
    if resolved == allowed_root or resolved.is_symlink():
        raise EvaluationRunnerError("workspace must be a non-symlink child directory")
    return resolved


def resolve_executable(value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        if not candidate.is_file():
            raise EvaluationRunnerError("Codex executable not found")
        return str(candidate.resolve())
    located = shutil.which(value)
    if not located:
        raise EvaluationRunnerError("Codex executable not found")
    return located


def build_command(executable: str, workspace: Path, final_message: Path) -> list[str]:
    command = [
        executable,
        "exec",
        "-C",
        str(workspace),
        "-m",
        MODEL,
        "-c",
        f'model_reasoning_effort="{REASONING}"',
        "-c",
        'approval_policy="never"',
        "-s",
        SANDBOX,
        "--ignore-user-config",
        "--json",
        "-o",
        str(final_message),
        "--skip-git-repo-check",
        "--ephemeral",
        "-",
    ]
    if "--add-dir" in command or any("bypass" in item for item in command):
        raise AssertionError("unsafe CLI option in evaluation command")
    return command


def validate_catalog(executable: str, environment: dict[str, str]) -> dict[str, object]:
    version = subprocess.run(
        [executable, "--version"], env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    catalog = subprocess.run(
        [executable, "debug", "models"], env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60, check=False,
    )
    if version.returncode != 0 or catalog.returncode != 0:
        raise EvaluationRunnerError("Codex runtime/catalog validation failed")
    payload = json.loads(catalog.stdout)
    entry = next((item for item in payload.get("models", []) if item.get("slug") == MODEL), None)
    efforts = {str(item.get("effort")) for item in (entry or {}).get("supported_reasoning_levels", [])}
    if entry is None or REASONING not in efforts:
        raise EvaluationRunnerError("exact model/reasoning contract unavailable; fallback prohibited")
    return {"cli_version": version.stdout.strip(), "catalog_model": MODEL, "catalog_reasoning": REASONING}


def parse_events(events_path: Path) -> dict[str, object]:
    thread_id = None
    completed = 0
    errors: list[str] = []
    for line in events_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and not thread_id:
            thread_id = event.get("thread_id")
        elif event.get("type") == "turn.completed":
            completed += 1
        elif event.get("type") == "error":
            errors.append(str(event.get("message") or "Codex error"))
    return {"thread_id": thread_id, "turn_completed": completed, "errors": errors}


def run(args: argparse.Namespace) -> int:
    runtime_root = Path(args.runtime_root)
    workspace = confined_directory(Path(args.workspace), runtime_root)
    prompt = confined_file(Path(args.prompt), workspace, "prompt")
    run_dir = Path(args.run_dir).resolve(strict=False)
    if not run_dir.is_relative_to(workspace) or run_dir == workspace:
        raise EvaluationRunnerError("run-dir must be a child of the arm workspace")
    run_dir.mkdir(parents=True, exist_ok=False)
    if args.phase not in ALLOWED_PHASES:
        raise EvaluationRunnerError("unknown phase")
    codex_home = Path(args.codex_home).resolve(strict=True)
    executable = resolve_executable(args.codex_executable)
    final_message = run_dir / "final-message.txt"
    events = run_dir / "events.jsonl"
    stderr = run_dir / "stderr.txt"
    command = build_command(executable, workspace, final_message)
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(codex_home)
    environment["PYTHONUTF8"] = "1"
    catalog = validate_catalog(executable, environment)
    if not args.execute:
        print(json.dumps({"status": "dry_run", "command": command, "catalog": catalog}, ensure_ascii=False, indent=2))
        return 0
    started_at = now_iso()
    started = time.monotonic()
    with prompt.open("r", encoding="utf-8-sig") as stdin_handle, events.open("w", encoding="utf-8") as stdout_handle, stderr.open("w", encoding="utf-8") as stderr_handle:
        completed = subprocess.run(
            command,
            stdin=stdin_handle,
            stdout=stdout_handle,
            stderr=stderr_handle,
            cwd=workspace,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout_seconds,
            check=False,
        )
    event_summary = parse_events(events)
    valid = completed.returncode == 0 and event_summary["thread_id"] and event_summary["turn_completed"] >= 1 and not event_summary["errors"]
    metadata = {
        "schema_version": 1,
        "phase": args.phase,
        "status": "pass" if valid else "fail",
        "requested_model": MODEL,
        "actual_model": MODEL if valid and catalog["catalog_model"] == MODEL else None,
        "reasoning_effort": REASONING,
        "fallback": False,
        "ephemeral": True,
        "sandbox": SANDBOX,
        "approval_policy": "never",
        "thread_id": event_summary["thread_id"],
        "started_at": started_at,
        "finished_at": now_iso(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "exit_code": completed.returncode,
        "executor_identity": getpass.getuser(),
        "catalog": catalog,
        "event_summary": event_summary,
    }
    (run_dir / "session.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0 if valid else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--runtime-root", required=True)
    result.add_argument("--workspace", required=True)
    result.add_argument("--prompt", required=True)
    result.add_argument("--run-dir", required=True)
    result.add_argument("--codex-home", required=True)
    result.add_argument("--codex-executable", default="codex")
    result.add_argument("--phase", required=True, choices=sorted(ALLOWED_PHASES))
    result.add_argument("--timeout-seconds", type=int, default=10800)
    result.add_argument("--execute", action="store_true")
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(run(parser().parse_args()))
    except (EvaluationRunnerError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "fail", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)

