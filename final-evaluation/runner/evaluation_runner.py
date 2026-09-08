from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path


MODEL = "gpt-5.6-sol"
REASONING = "max"
SANDBOX = "workspace-write"
PERMISSION_PROFILE = "evaluation-workspace-write"
PHASE_TIMEOUTS = {
    "solve": 21600,
    "audit": 14400,
    "revision-correctness": 14400,
    "revision-paper-verification": 14400,
    "continuation": 14400,
    "judge": 14400,
    "reference-adjudication": 14400,
}
ALLOWED_PHASES = {"probe", *PHASE_TIMEOUTS}


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
        "--strict-config",
        "-C",
        str(workspace),
        "-m",
        MODEL,
        "-c",
        f'model_reasoning_effort="{REASONING}"',
        "-c",
        'approval_policy="never"',
        "-c",
        f'default_permissions="{PERMISSION_PROFILE}"',
        "--ignore-rules",
        "--json",
        "-o",
        str(final_message),
        "--skip-git-repo-check",
        "--ephemeral",
        "-",
    ]
    if "-s" in command or "--add-dir" in command or any("bypass" in item or "danger" in item for item in command):
        raise AssertionError("unsafe CLI option in evaluation command")
    return command


def validate_evaluation_config(codex_home: Path) -> dict[str, object]:
    config_path = codex_home / "config.toml"
    if not config_path.is_file():
        raise EvaluationRunnerError("evaluation CODEX_HOME is missing config.toml")
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    windows = config.get("windows") or {}
    permissions = (config.get("permissions") or {}).get(PERMISSION_PROFILE) or {}
    filesystem = permissions.get("filesystem") or {}
    network = permissions.get("network") or {}
    if config.get("model") != MODEL or config.get("model_reasoning_effort") != REASONING:
        raise EvaluationRunnerError("evaluation model/reasoning config mismatch")
    if config.get("approval_policy") != "never" or config.get("default_permissions") != PERMISSION_PROFILE:
        raise EvaluationRunnerError("evaluation approval/permission profile mismatch")
    if windows.get("sandbox") not in {"elevated", "unelevated"}:
        raise EvaluationRunnerError("native Windows sandbox implementation is not configured")
    if filesystem.get(":root") != "deny" or filesystem.get(":workspace_roots", {}).get(".") != "write":
        raise EvaluationRunnerError("workspace-write filesystem policy mismatch")
    if network.get("enabled") is not False:
        raise EvaluationRunnerError("command network must remain disabled")
    return {
        "permission_profile": PERMISSION_PROFILE,
        "windows_sandbox": windows.get("sandbox"),
        "private_desktop": bool(windows.get("sandbox_private_desktop")),
        "command_network": False,
    }


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
    warnings: list[str] = []
    fatal_errors: list[str] = []
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
            message = str(event.get("message") or "Codex error")
            if message.startswith("Reconnecting..."):
                warnings.append(message)
            else:
                fatal_errors.append(message)
    return {"thread_id": thread_id, "turn_completed": completed, "warnings": warnings, "fatal_errors": fatal_errors}


def terminate_exact_process_tree(process: subprocess.Popen[str]) -> dict[str, object]:
    """Terminate only the process tree started by this runner."""

    result: dict[str, object] = {
        "pid": process.pid,
        "requested": False,
        "method": None,
        "return_code": process.poll(),
    }
    if process.poll() is not None:
        return result
    result["requested"] = True
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
        result.update(
            {
                "method": "taskkill_exact_pid_tree",
                "taskkill_return_code": completed.returncode,
                "taskkill_stdout": completed.stdout[-2000:],
                "taskkill_stderr": completed.stderr[-2000:],
            }
        )
    else:
        result["method"] = "terminate_process_group"
        try:
            os.killpg(process.pid, 15)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=30)
        result["forced_parent_kill"] = True
    result["return_code"] = process.returncode
    return result


def resolve_runtime_tools(runtime_root: Path) -> dict[str, str | None]:
    """Resolve the fixed Python and portable XeLaTeX used by evaluation stages."""

    python_executable = Path(sys.executable).resolve(strict=True)
    lab_root = runtime_root.resolve(strict=True).parent
    portable_xelatex = (
        lab_root
        / "runtime"
        / "miktex-portable"
        / "texmfs"
        / "install"
        / "miktex"
        / "bin"
        / "x64"
        / "xelatex.exe"
    )
    located_xelatex = portable_xelatex if portable_xelatex.is_file() else None
    if located_xelatex is None:
        fallback = shutil.which("xelatex.exe") or shutil.which("xelatex")
        located_xelatex = Path(fallback).resolve() if fallback else None
    return {
        "python_executable": str(python_executable),
        "python_directory": str(python_executable.parent),
        "xelatex_executable": str(located_xelatex) if located_xelatex else None,
        "xelatex_directory": str(located_xelatex.parent) if located_xelatex else None,
    }


def prepare_environment(codex_home: Path, runtime_tools: dict[str, str | None]) -> dict[str, str]:
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(codex_home)
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["RUST_LOG"] = "codex_core::session::session=debug"
    path_entries = [runtime_tools["python_directory"], runtime_tools["xelatex_directory"], environment.get("PATH")]
    environment["PATH"] = os.pathsep.join(str(item) for item in path_entries if item)
    environment["CUMCM_PYTHON_EXECUTABLE"] = str(runtime_tools["python_executable"])
    if runtime_tools["xelatex_executable"]:
        environment["CUMCM_XELATEX_EXECUTABLE"] = str(runtime_tools["xelatex_executable"])
    return environment


def run_codex_process(
    command: list[str],
    *,
    prompt_text: str,
    workspace: Path,
    environment: dict[str, str],
    events_path: Path,
    stderr_path: Path,
    final_message_path: Path,
    timeout_seconds: int,
    terminal_grace_seconds: float,
) -> dict[str, object]:
    """Supervise a stage, distinguishing model timeout from a completed CLI hang."""

    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    with events_path.open("w", encoding="utf-8", newline="\n") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as stderr_handle:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=stdout_handle,
            stderr=stderr_handle,
            cwd=workspace,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            **popen_kwargs,
        )
        assert process.stdin is not None
        process.stdin.write(prompt_text)
        process.stdin.close()
        started = time.monotonic()
        terminal_seen_at: float | None = None
        while True:
            return_code = process.poll()
            summary = parse_events(events_path)
            if return_code is not None:
                return {
                    "pid": process.pid,
                    "return_code": int(return_code),
                    "terminal_recovered": False,
                    "timed_out": False,
                    "termination": None,
                    "event_summary": summary,
                }
            terminal_complete = (
                int(summary["turn_completed"]) >= 1
                and not summary["fatal_errors"]
                and final_message_path.is_file()
                and final_message_path.stat().st_size > 0
            )
            if terminal_complete:
                terminal_seen_at = terminal_seen_at or time.monotonic()
                if time.monotonic() - terminal_seen_at >= terminal_grace_seconds:
                    termination = terminate_exact_process_tree(process)
                    return {
                        "pid": process.pid,
                        "return_code": process.returncode,
                        "terminal_recovered": True,
                        "timed_out": False,
                        "termination": termination,
                        "event_summary": parse_events(events_path),
                    }
            else:
                terminal_seen_at = None
                if time.monotonic() - started >= timeout_seconds:
                    termination = terminate_exact_process_tree(process)
                    return {
                        "pid": process.pid,
                        "return_code": process.returncode,
                        "terminal_recovered": False,
                        "timed_out": True,
                        "termination": termination,
                        "event_summary": parse_events(events_path),
                    }
            time.sleep(1.0)


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
    expected_timeout = PHASE_TIMEOUTS.get(args.phase)
    if expected_timeout is not None and args.timeout_seconds != expected_timeout:
        raise EvaluationRunnerError(
            f"phase {args.phase} must use preregistered timeout {expected_timeout} seconds"
        )
    codex_home = Path(args.codex_home).resolve(strict=True)
    executable = resolve_executable(args.codex_executable)
    final_message = run_dir / "final-message.txt"
    events = run_dir / "events.jsonl"
    stderr = run_dir / "stderr.txt"
    command = build_command(executable, workspace, final_message)
    runtime_tools = resolve_runtime_tools(runtime_root)
    environment = prepare_environment(codex_home, runtime_tools)
    sandbox_config = validate_evaluation_config(codex_home)
    catalog = validate_catalog(executable, environment)
    if not args.execute:
        print(
            json.dumps(
                {"status": "dry_run", "command": command, "catalog": catalog, "runtime_tools": runtime_tools},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    started_at = now_iso()
    started = time.monotonic()
    outcome = run_codex_process(
        command,
        prompt_text=prompt.read_text(encoding="utf-8-sig"),
        workspace=workspace,
        environment=environment,
        events_path=events,
        stderr_path=stderr,
        final_message_path=final_message,
        timeout_seconds=args.timeout_seconds,
        terminal_grace_seconds=args.terminal_grace_seconds,
    )
    event_summary = outcome["event_summary"]
    stderr_text = stderr.read_text(encoding="utf-8-sig", errors="replace")
    configured_models = re.findall(r"Configuring session: model=([^;\s]+);", stderr_text)
    debug_model_mismatch = bool(configured_models) and configured_models != [MODEL]
    completed_process = outcome["return_code"] == 0 or outcome["terminal_recovered"]
    valid = bool(
        completed_process
        and not outcome["timed_out"]
        and event_summary["thread_id"]
        and event_summary["turn_completed"] >= 1
        and not event_summary["fatal_errors"]
        and final_message.is_file()
        and final_message.stat().st_size > 0
        and not debug_model_mismatch
    )
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
        "permission_profile": PERMISSION_PROFILE,
        "approval_policy": "never",
        "thread_id": event_summary["thread_id"],
        "started_at": started_at,
        "finished_at": now_iso(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "exit_code": outcome["return_code"],
        "executor_identity": getpass.getuser(),
        "catalog": catalog,
        "sandbox_config": sandbox_config,
        "runtime_tools": runtime_tools,
        "process_pid": outcome["pid"],
        "timed_out": outcome["timed_out"],
        "terminal_recovered": outcome["terminal_recovered"],
        "completion_classification": (
            "terminal_event_complete_cli_tree_reaped"
            if outcome["terminal_recovered"]
            else "hard_timeout"
            if outcome["timed_out"]
            else "normal_process_exit"
        ),
        "process_tree_termination": outcome["termination"],
        "configured_models_from_debug": configured_models,
        "actual_model_evidence": "debug_metadata" if configured_models else "exact_cli_contract_plus_catalog",
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
    result.add_argument("--timeout-seconds", type=int, required=True)
    result.add_argument("--terminal-grace-seconds", type=float, default=120.0)
    result.add_argument("--execute", action="store_true")
    return result


if __name__ == "__main__":
    try:
        raise SystemExit(run(parser().parse_args()))
    except (EvaluationRunnerError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "fail", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
