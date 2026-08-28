from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .util import now_iso, sha256_file, write_json


SESSION_SCHEMA_VERSION = 1
MINIMUM_CODEX_VERSION = (0, 144, 0)
SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
FORBIDDEN_CODEX_ARGS = {
    "resume",
    "--add-dir",
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
}


class ProcessResult(Protocol):
    returncode: int


ProcessRunner = Callable[..., ProcessResult]


class SessionRunnerError(ValueError):
    """Raised when a stage-session request violates the fixed CLI contract."""


def _event_stream_summary(events_path: Path) -> dict[str, Any]:
    thread_id = None
    turn_completed = 0
    errors: list[str] = []
    if not events_path.is_file():
        return {"thread_id": None, "turn_completed": 0, "errors": errors}
    for line in events_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # The producer may still be flushing the final JSONL record.
            continue
        event_type = event.get("type")
        if event_type == "thread.started" and not thread_id:
            thread_id = event.get("thread_id")
        elif event_type == "turn.completed":
            turn_completed += 1
        elif event_type == "error":
            errors.append(str(event.get("message") or "Codex error event"))
    return {"thread_id": thread_id, "turn_completed": turn_completed, "errors": errors}


def _terminate_started_process_tree(process: subprocess.Popen[str]) -> dict[str, Any]:
    """Terminate only the exact process tree created by this runner."""

    result: dict[str, Any] = {"pid": process.pid, "requested": False, "method": None, "return_code": None}
    if process.poll() is not None:
        result["return_code"] = process.returncode
        return result
    result["requested"] = True
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
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


def _run_native_codex_process(
    command: Sequence[str],
    *,
    prompt: str,
    workspace: Path,
    environment: Mapping[str, str],
    events_path: Path,
    stderr_path: Path,
    final_message_path: Path,
    terminal_grace_seconds: float = 45.0,
) -> dict[str, Any]:
    """Supervise Codex and recover a completed turn whose CLI never exits."""

    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    with events_path.open("w", encoding="utf-8", newline="\n") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as stderr_handle:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            cwd=str(workspace),
            env=dict(environment),
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            **popen_kwargs,
        )
        assert process.stdin is not None
        process.stdin.write(prompt)
        process.stdin.close()
        terminal_seen_at: float | None = None
        while True:
            return_code = process.poll()
            if return_code is not None:
                return {
                    "pid": process.pid,
                    "return_code": int(return_code),
                    "terminal_recovered": False,
                    "termination": None,
                    "event_summary": _event_stream_summary(events_path),
                }
            summary = _event_stream_summary(events_path)
            terminal_complete = (
                summary["turn_completed"] >= 1
                and not summary["errors"]
                and final_message_path.is_file()
                and final_message_path.stat().st_size > 0
            )
            if terminal_complete:
                terminal_seen_at = terminal_seen_at or time.monotonic()
                if time.monotonic() - terminal_seen_at >= terminal_grace_seconds:
                    termination = _terminate_started_process_tree(process)
                    return {
                        "pid": process.pid,
                        "return_code": int(process.returncode) if process.returncode is not None else None,
                        "terminal_recovered": True,
                        "termination": termination,
                        "event_summary": _event_stream_summary(events_path),
                    }
            else:
                terminal_seen_at = None
            time.sleep(1.0)


def resolve_codex_executable(executable: str | Path = "codex") -> str:
    """Resolve the native Codex binary so Windows never executes an npm shim."""

    requested = _validate_nonempty("executable", str(executable))
    explicit = Path(requested).expanduser()
    if explicit.is_absolute() or explicit.parent != Path("."):
        if not explicit.is_file():
            raise SessionRunnerError(f"显式 Codex 可执行文件不存在：{explicit}")
        return str(explicit.resolve())
    if Path(requested).name.casefold() not in {"codex", "codex.exe", "codex.cmd", "codex.ps1"}:
        return requested
    appdata = os.environ.get("APPDATA")
    if appdata:
        native = (
            Path(appdata)
            / "npm"
            / "node_modules"
            / "@openai"
            / "codex"
            / "node_modules"
            / "@openai"
            / "codex-win32-x64"
            / "vendor"
            / "x86_64-pc-windows-msvc"
            / "bin"
            / "codex.exe"
        )
        if native.is_file():
            return str(native)
    located = shutil.which("codex.exe") or shutil.which("codex")
    if located:
        return located
    return requested


def validate_codex_runtime(
    executable: str | Path,
    *,
    codex_home: Path,
    model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    """Fail closed unless the installed CLI catalog supports the exact contract."""

    executable_text = resolve_codex_executable(executable)
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(Path(codex_home).resolve())
    version_result = subprocess.run(
        [executable_text, "--version"],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    version_text = (version_result.stdout or version_result.stderr).strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version_text)
    if version_result.returncode != 0 or not match:
        raise SessionRunnerError(f"无法验证 Codex CLI 版本：{version_text or '无输出'}")
    version = tuple(int(part) for part in match.groups())
    if version < MINIMUM_CODEX_VERSION:
        minimum = ".".join(str(part) for part in MINIMUM_CODEX_VERSION)
        raise SessionRunnerError(f"Codex CLI {version_text} 低于正式训练下限 {minimum}。")

    models_result = subprocess.run(
        [executable_text, "debug", "models"],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=60,
    )
    if models_result.returncode != 0:
        raise SessionRunnerError(f"Codex 模型目录验证失败：{models_result.stderr.strip()}")
    try:
        catalog = json.loads(models_result.stdout)
    except json.JSONDecodeError as exc:
        raise SessionRunnerError("Codex 模型目录不是有效 JSON。") from exc
    entry = next((item for item in catalog.get("models", []) if item.get("slug") == model), None)
    if entry is None:
        raise SessionRunnerError(f"当前 Codex CLI 不支持精确模型 {model}；禁止回退。")
    efforts = {str(item.get("effort")) for item in entry.get("supported_reasoning_levels", [])}
    if reasoning_effort not in efforts:
        raise SessionRunnerError(f"模型 {model} 不支持推理档位 {reasoning_effort}；禁止回退。")
    return {
        "executable": executable_text,
        "cli_version": version_text,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "catalog_confirmed": True,
    }


def verify_saved_session_contract(
    codex_home: Path,
    thread_id: str,
    *,
    model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    """Read the CLI rollout metadata and reject any model or effort fallback."""

    matches = list((Path(codex_home) / "sessions").rglob(f"*{thread_id}.jsonl"))
    if len(matches) != 1:
        raise SessionRunnerError(f"无法唯一定位会话元数据：{thread_id}")
    rollout = matches[0]
    context = None
    for line in rollout.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn_context":
            context = event.get("payload") or {}
            break
    if not isinstance(context, dict):
        raise SessionRunnerError(f"会话元数据缺少 turn_context：{thread_id}")
    actual_model = context.get("model")
    actual_effort = context.get("effort")
    if actual_model != model or actual_effort != reasoning_effort:
        raise SessionRunnerError(
            f"模型元数据不一致：期望 {model}/{reasoning_effort}，实际 {actual_model}/{actual_effort}；禁止冻结。"
        )
    return {
        "thread_id": thread_id,
        "rollout": str(rollout.resolve()),
        "model": actual_model,
        "reasoning_effort": actual_effort,
        "metadata_confirmed": True,
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_nonempty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise SessionRunnerError(f"{name} 不能为空。")
    return normalized


def build_codex_exec_command(
    *,
    executable: str | Path,
    workspace: Path,
    model: str,
    reasoning_effort: str,
    final_message_path: Path,
) -> list[str]:
    """Build the immutable Codex CLI 0.144+ stage-session command.

    The prompt is deliberately represented by ``-`` and must be supplied over
    stdin.  No caller-controlled extra-argument escape hatch is provided.
    """

    executable_text = _validate_nonempty("executable", str(executable))
    model_text = _validate_nonempty("model", model)
    effort = _validate_nonempty("reasoning_effort", reasoning_effort).lower()
    if effort not in SUPPORTED_REASONING_EFFORTS:
        supported = ", ".join(sorted(SUPPORTED_REASONING_EFFORTS))
        raise SessionRunnerError(f"reasoning_effort 必须是活动训练支持值之一：{supported}")

    command = [
        executable_text,
        "exec",
        "-C",
        str(Path(workspace).resolve()),
        "-m",
        model_text,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "-c",
        'approval_policy="never"',
        "-s",
        "danger-full-access",
        "--json",
        "-o",
        str(Path(final_message_path).resolve()),
        "--skip-git-repo-check",
        "--ephemeral",
        "-",
    ]
    forbidden = FORBIDDEN_CODEX_ARGS.intersection(command)
    if forbidden:
        raise AssertionError(f"固定命令意外包含禁止参数：{sorted(forbidden)}")
    return command


def _input_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_symlink():
            raise SessionRunnerError(f"输入文件不得是符号链接：{path}")
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise SessionRunnerError(f"输入文件不存在：{path}") from exc
        if not resolved.is_file():
            raise SessionRunnerError(f"输入清单只接受普通文件：{resolved}")
        if resolved in seen:
            continue
        seen.add(resolved)
        records.append(
            {
                "path": str(resolved),
                "size": resolved.stat().st_size,
                "sha256": sha256_file(resolved),
            }
        )
    return sorted(records, key=lambda item: item["path"].casefold())


def _artifact_record(path: Path, run_dir: Path) -> dict[str, Any]:
    exists = path.is_file()
    record: dict[str, Any] = {
        "path": path.relative_to(run_dir).as_posix(),
        "exists": exists,
    }
    if exists:
        record["size"] = path.stat().st_size
        record["sha256"] = sha256_file(path)
    else:
        record["size"] = None
        record["sha256"] = None
    return record


def _write_output_manifest(
    *,
    run_dir: Path,
    session_run_id: str,
    status: str,
    exit_code: int | None,
    paths: Sequence[Path],
) -> Path:
    manifest_path = run_dir / "output-manifest.json"
    write_json(
        manifest_path,
        {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_run_id": session_run_id,
            "status": status,
            "exit_code": exit_code,
            "generated_at": now_iso(),
            "files": [_artifact_record(path, run_dir) for path in paths],
        },
    )
    return manifest_path


def run_stage_session(
    *,
    workspace: Path,
    run_root: Path,
    prompt: str,
    codex_home: Path,
    model: str,
    reasoning_effort: str = "xhigh",
    input_files: Iterable[Path] = (),
    executable: str | Path = "codex",
    runner: ProcessRunner | None = None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one new, ephemeral, non-interactive Codex stage session.

    A missing or empty ``CODEX_HOME/auth.json`` is recorded as an objective
    blocked result.  In that case the process runner is never called.
    """

    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise SessionRunnerError(f"阶段工作区不存在或不是目录：{workspace}")
    prompt = str(prompt)
    if not prompt.strip():
        raise SessionRunnerError("prompt 不能为空。")
    model = _validate_nonempty("model", model)
    reasoning_effort = _validate_nonempty("reasoning_effort", reasoning_effort).lower()
    if reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
        supported = ", ".join(sorted(SUPPORTED_REASONING_EFFORTS))
        raise SessionRunnerError(f"reasoning_effort 必须是活动训练支持值之一：{supported}")
    executable = resolve_codex_executable(executable)
    input_records = _input_records(input_files)

    codex_home = Path(codex_home).resolve()
    run_root = Path(run_root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    session_run_id = str(uuid.uuid4())
    run_dir = run_root / session_run_id
    run_dir.mkdir(parents=False, exist_ok=False)

    prompt_sha256 = _sha256_text(prompt)
    prompt_hash_path = run_dir / "prompt-hash.txt"
    prompt_hash_path.write_text(prompt_sha256 + "\n", encoding="utf-8", newline="\n")

    input_manifest_path = run_dir / "input-manifest.json"
    write_json(
        input_manifest_path,
        {
            "schema_version": SESSION_SCHEMA_VERSION,
            "session_run_id": session_run_id,
            "workspace": str(workspace),
            "prompt_sha256": prompt_sha256,
            "prompt_size_bytes": len(prompt.encode("utf-8")),
            "files": input_records,
        },
    )

    final_message_path = run_dir / "final-message.md"
    events_path = run_dir / "events.jsonl"
    stderr_path = run_dir / "stderr.log"
    metadata_path = run_dir / "run-metadata.json"
    events_path.touch(exist_ok=False)
    stderr_path.touch(exist_ok=False)

    auth_path = codex_home / "auth.json"
    auth_present = auth_path.is_file() and auth_path.stat().st_size > 0
    runtime_contract = None
    if runner is None and auth_present:
        runtime_contract = validate_codex_runtime(
            executable,
            codex_home=codex_home,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    command = build_codex_exec_command(
        executable=executable,
        workspace=workspace,
        model=model,
        reasoning_effort=reasoning_effort,
        final_message_path=final_message_path,
    )
    started_at = now_iso()
    metadata: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_run_id": session_run_id,
        "status": "running" if auth_present else "blocked",
        "blocked_reason": None if auth_present else "codex_home_auth_missing",
        "process_started": False,
        "started_at": started_at,
        "finished_at": None,
        "exit_code": None,
        "codex_cli_contract": ">=0.144.0",
        "runtime_contract": runtime_contract,
        "codex_home": str(codex_home),
        "auth_file_present": auth_present,
        "workspace": str(workspace),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "sandbox": "danger-full-access",
        "isolation_statement": "这是最小复制工作区隔离，不是Windows绝对路径安全证明。",
        "approval_policy": "never",
        "ephemeral": True,
        "prompt_transport": "stdin",
        "prompt_sha256": prompt_sha256,
        "command": command,
        "error": None,
    }

    artifact_paths = [
        prompt_hash_path,
        input_manifest_path,
        metadata_path,
        events_path,
        stderr_path,
        final_message_path,
    ]
    if not auth_present:
        metadata["finished_at"] = now_iso()
        write_json(metadata_path, metadata)
        output_manifest_path = _write_output_manifest(
            run_dir=run_dir,
            session_run_id=session_run_id,
            status="blocked",
            exit_code=None,
            paths=artifact_paths,
        )
        return {
            "session_run_id": session_run_id,
            "status": "blocked",
            "blocked_reason": "codex_home_auth_missing",
            "exit_code": None,
            "run_dir": str(run_dir),
            "run_metadata": str(metadata_path),
            "output_manifest": str(output_manifest_path),
        }

    write_json(metadata_path, metadata)
    environment = dict(os.environ if base_env is None else base_env)
    environment["CODEX_HOME"] = str(codex_home)
    python_dir = str(Path(sys.executable).resolve().parent)
    inherited_path = environment.get("PATH", "")
    environment["PATH"] = os.pathsep.join(
        item for item in (python_dir, inherited_path) if item
    )
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    metadata["session_runtime"] = {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_path_prepend": python_dir,
        "python_utf8": environment["PYTHONUTF8"],
        "python_io_encoding": environment["PYTHONIOENCODING"],
    }
    write_json(metadata_path, metadata)
    if runner is None:
        environment["RUST_LOG"] = "codex_core::session::session=debug"
    exit_code: int | None = None
    terminal_recovered = False
    termination: dict[str, Any] | None = None
    try:
        metadata["process_started"] = True
        write_json(metadata_path, metadata)
        if runner is None:
            outcome = _run_native_codex_process(
                command,
                prompt=prompt,
                workspace=workspace,
                environment=environment,
                events_path=events_path,
                stderr_path=stderr_path,
                final_message_path=final_message_path,
            )
            exit_code = outcome["return_code"]
            terminal_recovered = bool(outcome["terminal_recovered"])
            termination = outcome["termination"]
            metadata["process_pid"] = outcome["pid"]
        else:
            with events_path.open("w", encoding="utf-8", newline="\n") as stdout_handle, stderr_path.open(
                "w", encoding="utf-8", newline="\n"
            ) as stderr_handle:
                completed = runner(
                    command,
                    input=prompt,
                    cwd=str(workspace),
                    env=environment,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    shell=False,
                )
            exit_code = int(completed.returncode)
        status = "completed" if exit_code == 0 or terminal_recovered else "failed"
    except Exception as exc:  # Preserve a complete audit record for executor failures.
        status = "failed"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        with stderr_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(metadata["error"] + "\n")

    event_summary = _event_stream_summary(events_path)
    thread_id = event_summary["thread_id"]
    metadata["thread_id"] = thread_id
    metadata["terminal_turn_completed_events"] = event_summary["turn_completed"]
    metadata["event_error_count"] = len(event_summary["errors"])
    metadata["terminal_recovered"] = terminal_recovered
    metadata["completion_classification"] = (
        "terminal_event_complete_cli_tree_reaped" if terminal_recovered else "normal_process_exit"
    )
    metadata["process_tree_termination"] = termination
    if status == "completed" and not thread_id:
        status = "failed"
        metadata["error"] = "Codex 事件流缺少 thread.started；无法证明独立新会话。"
    if runtime_contract is not None:
        debug_text = stderr_path.read_text(encoding="utf-8-sig", errors="replace")
        configured = re.findall(r"Configuring session: model=([^;\s]+);", debug_text)
        if configured != [model]:
            status = "failed"
            metadata["error"] = (
                f"ephemeral 会话模型元数据不一致：期望唯一 {model}，实际 {configured}；禁止冻结。"
            )
        else:
            metadata["actual_session_contract"] = {
                "thread_id": thread_id,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "model_debug_metadata_confirmed": True,
                "reasoning_evidence": "精确 CLI 配置参数与当前模型目录共同确认",
                "fallback": False,
                "ephemeral": True,
            }
    metadata["status"] = status
    metadata["exit_code"] = exit_code
    metadata["finished_at"] = now_iso()
    write_json(metadata_path, metadata)
    output_manifest_path = _write_output_manifest(
        run_dir=run_dir,
        session_run_id=session_run_id,
        status=status,
        exit_code=exit_code,
        paths=artifact_paths,
    )
    return {
        "session_run_id": session_run_id,
        "status": status,
        "blocked_reason": None,
        "exit_code": exit_code,
        "terminal_recovered": terminal_recovered,
        "run_dir": str(run_dir),
        "run_metadata": str(metadata_path),
        "output_manifest": str(output_manifest_path),
    }
