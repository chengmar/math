from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .util import now_iso, sha256_file, write_json


SESSION_SCHEMA_VERSION = 1
SUPPORTED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}
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
    """Build the immutable Codex CLI 0.142.0 stage-session command.

    The prompt is deliberately represented by ``-`` and must be supplied over
    stdin.  No caller-controlled extra-argument escape hatch is provided.
    """

    executable_text = _validate_nonempty("executable", str(executable))
    model_text = _validate_nonempty("model", model)
    effort = _validate_nonempty("reasoning_effort", reasoning_effort).lower()
    if effort not in SUPPORTED_REASONING_EFFORTS:
        supported = ", ".join(sorted(SUPPORTED_REASONING_EFFORTS))
        raise SessionRunnerError(f"reasoning_effort 必须是本机 0.142.0 支持值之一：{supported}")

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
        "workspace-write",
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
        raise SessionRunnerError(f"reasoning_effort 必须是本机 0.142.0 支持值之一：{supported}")
    _validate_nonempty("executable", str(executable))
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

    command = build_codex_exec_command(
        executable=executable,
        workspace=workspace,
        model=model,
        reasoning_effort=reasoning_effort,
        final_message_path=final_message_path,
    )
    auth_path = codex_home / "auth.json"
    auth_present = auth_path.is_file() and auth_path.stat().st_size > 0
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
        "codex_cli_contract": "0.142.0",
        "codex_home": str(codex_home),
        "auth_file_present": auth_present,
        "workspace": str(workspace),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "sandbox": "workspace-write",
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
    process_runner = runner or subprocess.run
    exit_code: int | None = None
    try:
        with events_path.open("w", encoding="utf-8", newline="\n") as stdout_handle, stderr_path.open(
            "w", encoding="utf-8", newline="\n"
        ) as stderr_handle:
            metadata["process_started"] = True
            write_json(metadata_path, metadata)
            completed = process_runner(
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
        status = "completed" if exit_code == 0 else "failed"
    except Exception as exc:  # Preserve a complete audit record for executor failures.
        status = "failed"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        with stderr_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(metadata["error"] + "\n")

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
        "run_dir": str(run_dir),
        "run_metadata": str(metadata_path),
        "output_manifest": str(output_manifest_path),
    }
