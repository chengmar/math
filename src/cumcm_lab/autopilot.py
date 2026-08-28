from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

import yaml

from .completion_barrier import (
    CaseCompletionBarrierError,
    DEFERRED_COMPLETION_CHECKS,
    REQUIRED_COMPLETION_CHECKS,
    assert_case_dispatch_allowed,
    begin_formal_write,
    finish_formal_write,
    load_case_completion_barrier,
    lock_case_completion_barrier,
    release_case_completion_barrier,
)
from .training_queue import (
    FINAL_TEST_CASE_ID,
    TRAIN_PHASES,
    FinalTestExecutionDenied,
    assert_training_case,
    begin_phase,
    load_training_queue,
    mark_phase_failure,
    mark_phase_interrupted_quota,
    mark_phase_success,
    mark_case_deferred_platform_safety,
    next_runnable_item,
    queue_summary,
    set_stop_requested,
)
from .util import now_iso, read_json, write_json


class AutopilotError(RuntimeError):
    """Base class for autonomous-run failures."""


class AlreadyRunningError(AutopilotError):
    """Raised when a live process already owns the runtime lock."""


class StaleLockError(AutopilotError):
    """Raised when an abandoned lock requires explicit recovery."""


class SystemAutopilotError(AutopilotError):
    """A safety or framework failure that must stop the whole queue."""


class CasePhaseError(AutopilotError):
    """A deterministic error that blocks only the current case."""


class TransientPhaseError(AutopilotError):
    """An error eligible for the queue's single retry."""


class PlatformSafetyBlock(CasePhaseError):
    """A single-case external content-safety block; never retry automatically."""

    def __init__(self, message: str, *, run_id: str | None = None, thread_id: str | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.thread_id = thread_id


class UsageLimitReached(AutopilotError):
    """The account cannot start another model turn until quota resets."""

    def __init__(self, message: str, *, run_id: str | None = None) -> None:
        super().__init__(message)
        self.run_id = run_id


@dataclass(frozen=True)
class PhaseResult:
    status: str = "pass"
    message: str = ""
    metadata: dict[str, Any] | None = None


class PhaseExecutor(Protocol):
    def __call__(self, case_id: str, phase: str, attempt: int, run_dir: Path) -> PhaseResult | Mapping[str, Any] | None:
        ...


LOCK_NAME = "autopilot.lock"
PID_NAME = "autopilot.pid"
STATE_NAME = "autopilot-state.json"
STOP_NAME = "autopilot.stop"


def _runtime_paths(runtime_dir: Path) -> dict[str, Path]:
    runtime_dir = Path(runtime_dir)
    return {
        "root": runtime_dir,
        "lock": runtime_dir / LOCK_NAME,
        "pid": runtime_dir / PID_NAME,
        "state": runtime_dir / STATE_NAME,
        "stop": runtime_dir / STOP_NAME,
        "runs": runtime_dir / "autopilot-runs",
    }


def process_exists(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_autopilot_lock(runtime_dir: Path, queue_path: Path, *, recover_stale: bool = False) -> dict[str, Any]:
    paths = _runtime_paths(runtime_dir)
    paths["root"].mkdir(parents=True, exist_ok=True)
    lock_path = paths["lock"]
    if lock_path.exists():
        try:
            current = read_json(lock_path)
        except Exception as exc:
            raise StaleLockError(f"autopilot.lock 无法解析：{exc}") from exc
        owner_pid = current.get("pid") if isinstance(current, dict) else None
        if isinstance(owner_pid, int) and process_exists(owner_pid):
            raise AlreadyRunningError(f"Autopilot 已由 PID {owner_pid} 运行。")
        if not recover_stale:
            raise StaleLockError("检测到失效 autopilot.lock；仅 resume 可恢复。")
        stale_name = f"autopilot.lock.stale-{uuid.uuid4().hex}.json"
        lock_path.replace(paths["root"] / stale_name)

    payload = {
        "schema_version": 1,
        "pid": os.getpid(),
        "nonce": uuid.uuid4().hex,
        "queue_path": str(Path(queue_path).resolve()),
        "acquired_at": now_iso(),
    }
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise AlreadyRunningError("Autopilot 锁被另一进程同时取得。") from exc
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)
    write_json(paths["pid"], {"pid": os.getpid(), "nonce": payload["nonce"], "started_at": payload["acquired_at"]})
    return payload


def release_autopilot_lock(runtime_dir: Path, lock: Mapping[str, Any]) -> None:
    paths = _runtime_paths(runtime_dir)
    if paths["lock"].exists():
        try:
            current = read_json(paths["lock"])
        except Exception:
            return
        if current.get("nonce") != lock.get("nonce") or current.get("pid") != lock.get("pid"):
            return
        paths["lock"].unlink()
    if paths["pid"].exists():
        try:
            current_pid = read_json(paths["pid"])
        except Exception:
            current_pid = {}
        if current_pid.get("nonce") == lock.get("nonce"):
            paths["pid"].unlink()


def _write_state(runtime_dir: Path, **updates: Any) -> dict[str, Any]:
    path = _runtime_paths(runtime_dir)["state"]
    state = read_json(path, {}) if path.exists() else {}
    if not isinstance(state, dict):
        state = {}
    state.update(updates)
    state["updated_at"] = now_iso()
    write_json(path, state)
    return state


def request_autopilot_stop(runtime_dir: Path, queue_path: Path | None = None) -> dict[str, Any]:
    paths = _runtime_paths(runtime_dir)
    paths["root"].mkdir(parents=True, exist_ok=True)
    request = {"requested": True, "requested_at": now_iso(), "request_id": str(uuid.uuid4())}
    write_json(paths["stop"], request)
    if queue_path is not None and Path(queue_path).exists():
        set_stop_requested(Path(queue_path), True)
    _write_state(runtime_dir, stop_requested=True)
    return request


def clear_autopilot_stop(runtime_dir: Path, queue_path: Path) -> None:
    paths = _runtime_paths(runtime_dir)
    set_stop_requested(Path(queue_path), False)
    if paths["stop"].exists():
        paths["stop"].unlink()
    _write_state(runtime_dir, stop_requested=False)


def autopilot_status(runtime_dir: Path, queue_path: Path | None = None) -> dict[str, Any]:
    paths = _runtime_paths(runtime_dir)
    state = read_json(paths["state"], {}) if paths["state"].exists() else {"status": "not_started"}
    lock = read_json(paths["lock"], {}) if paths["lock"].exists() else None
    owner_pid = lock.get("pid") if isinstance(lock, dict) else None
    result = {
        "state": state,
        "lock_present": paths["lock"].exists(),
        "pid": owner_pid,
        "process_alive": process_exists(owner_pid) if isinstance(owner_pid, int) else False,
        "stop_requested": paths["stop"].exists(),
    }
    if queue_path is not None and Path(queue_path).exists():
        result["queue"] = queue_summary(load_training_queue(Path(queue_path)))
    return result


def preflight_autopilot(queue_path: Path, runtime_dir: Path, codex_home: Path) -> dict[str, Any]:
    """Record a recoverable blocker without consuming a queue attempt or creating a PID."""

    queue = load_training_queue(Path(queue_path))
    item = next_runnable_item(queue)
    if item is None:
        return _write_state(runtime_dir, status="ready", queue=queue_summary(queue), checked_at=now_iso())
    case_id = str(item["case_id"])
    phase = str(item["current_phase"])
    assert_training_case(case_id)
    auth_path = Path(codex_home) / "auth.json"
    if not auth_path.is_file() or auth_path.stat().st_size == 0:
        return _write_state(
            runtime_dir,
            schema_version=1,
            status="blocked",
            pid=None,
            process_alive=False,
            current_case=case_id,
            current_phase=phase,
            recoverable=True,
            blocker_kind="codex_home_auth_missing",
            blocker=f"专用 CODEX_HOME 缺少非空 auth.json：{auth_path}",
            checked_at=now_iso(),
            queue=queue_summary(queue),
        )
    return _write_state(
        runtime_dir,
        schema_version=1,
        status="ready",
        pid=None,
        process_alive=False,
        current_case=case_id,
        current_phase=phase,
        recoverable=True,
        checked_at=now_iso(),
        queue=queue_summary(queue),
    )


def _invoke_executor(
    executor: PhaseExecutor | Any,
    case_id: str,
    phase: str,
    attempt: int,
    run_dir: Path,
) -> PhaseResult:
    if hasattr(executor, "execute"):
        raw = executor.execute(case_id, phase, attempt, run_dir)
    else:
        raw = executor(case_id, phase, attempt, run_dir)
    if raw is None:
        return PhaseResult()
    if isinstance(raw, PhaseResult):
        result = raw
    elif isinstance(raw, Mapping):
        result = PhaseResult(
            status=str(raw.get("status", "pass")),
            message=str(raw.get("message", "")),
            metadata=dict(raw.get("metadata") or {}),
        )
    else:
        raise SystemAutopilotError("阶段执行器返回了不支持的结果类型。")
    if result.status != "pass":
        raise CasePhaseError(result.message or f"阶段执行结果为 {result.status}")
    return result


def _completion_evidence(
    executor: PhaseExecutor | Any,
    case_id: str,
    terminal_status: str,
) -> dict[str, Any]:
    if hasattr(executor, "completion_evidence"):
        evidence = executor.completion_evidence(case_id, terminal_status)
        if not isinstance(evidence, Mapping):
            raise SystemAutopilotError("案例完成证据必须是对象。")
        return dict(evidence)
    required = (
        DEFERRED_COMPLETION_CHECKS
        if terminal_status == "deferred_platform_safety"
        else REQUIRED_COMPLETION_CHECKS
    )
    return {name: True for name in required}


def _reconcile_terminal_case_barrier(
    queue: Mapping[str, Any],
    runtime_dir: Path,
    executor: PhaseExecutor | Any,
) -> None:
    barrier = load_case_completion_barrier(runtime_dir)
    if barrier is None or barrier.get("case_completion_barrier") != "locked":
        return
    case_id = str(barrier["case_id"])
    item = next((item for item in queue.get("items", []) if item.get("case_id") == case_id), None)
    if not isinstance(item, dict):
        raise SystemAutopilotError(f"完成屏障案例不在训练队列中：{case_id}")
    terminal_status = str(item.get("status") or "")
    if terminal_status not in {"completed", "completed_with_caveats", "deferred_platform_safety"}:
        return
    try:
        release_case_completion_barrier(
            runtime_dir,
            case_id,
            terminal_status=terminal_status,
            evidence=_completion_evidence(executor, case_id, terminal_status),
        )
    except CaseCompletionBarrierError as exc:
        raise SystemAutopilotError(str(exc)) from exc


def _platform_safety_message(events_path: Path) -> str | None:
    if not events_path.is_file():
        return None
    for line in events_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = str(event.get("message", "")) if event.get("type") == "error" else ""
        lowered = message.casefold()
        if message and "safety reasons" in lowered and ("biology" in lowered or "benefit or to harm" in lowered):
            return message
    return None


def _usage_limit_message(session_dir: Path) -> str | None:
    candidates: list[str] = []
    events_path = Path(session_dir) / "events.jsonl"
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "error":
                candidates.append(str(event.get("message") or ""))
    for name in ("stderr.log", "final-message.md"):
        path = Path(session_dir) / name
        if path.is_file():
            candidates.append(path.read_text(encoding="utf-8-sig", errors="replace"))
    explicit = (
        "usage limit",
        "quota exhausted",
        "quota_exhausted",
        "insufficient_quota",
        "resets your usage limit",
        "usage limit resets",
    )
    for candidate in candidates:
        lowered = candidate.casefold()
        if any(token in lowered for token in explicit):
            return candidate.strip()[-4000:]
        if "rate limit" in lowered and any(token in lowered for token in ("quota", "plan", "usage", "reset")):
            return candidate.strip()[-4000:]
    return None


def _candidate_knowledge_statuses(payload: Any) -> list[str]:
    """Return proposal lifecycle statuses without confusing evidence checks with card status."""

    statuses: list[str] = []
    proposal_collections = {
        "cards",
        "method_cards",
        "lessons",
        "expression_lessons",
        "failure_modes",
        "items",
        "patterns",
        "validation_patterns",
    }

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"knowledge_status", "knowledge_state"} and isinstance(child, str):
                    statuses.append(child.casefold())
                elif (key in proposal_collections or key.endswith("_cards")) and isinstance(child, list):
                    for card in child:
                        if isinstance(card, dict):
                            statuses.append(
                                str(
                                    card.get("status")
                                    or card.get("knowledge_status")
                                    or card.get("knowledge_state")
                                    or card.get("state")
                                    or ""
                                ).casefold()
                            )
                        else:
                            statuses.append("")
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    top_value = None
    if isinstance(payload, dict):
        top_value = (
            payload.get("status")
            or payload.get("knowledge_status")
            or payload.get("state")
        )
    if isinstance(top_value, str):
        top_status = top_value.casefold()
        if top_status in {"candidate", "demo", "verified", "machine_verified", "deprecated"}:
            statuses.append(top_status)
    visit(payload)
    return statuses


def _validate_candidate_proposals(lesson_root: Path, case_id: str) -> dict[str, Any]:
    """Validate candidate proposals and explicitly non-promotable demo material."""

    lesson_root = Path(lesson_root)
    if not lesson_root.is_dir():
        return {"files": 0, "candidate_count": 0, "invalid": ["lessons-proposed-missing"]}

    index_path = lesson_root / "index.yaml"
    if index_path.is_file():
        index = yaml.safe_load(index_path.read_text(encoding="utf-8-sig")) or {}
        proposals = index.get("proposals") if isinstance(index, dict) else None
        if isinstance(proposals, list) and proposals:
            invalid: list[str] = []
            seen_paths: set[str] = set()
            root = lesson_root.resolve()
            default_state = str(index.get("default_proposal_state") or "").casefold()
            for position, proposal in enumerate(proposals, start=1):
                label = f"index.yaml:proposals[{position}]"
                if not isinstance(proposal, dict):
                    invalid.append(label)
                    continue
                relative = str(proposal.get("file") or proposal.get("path") or "").strip()
                if not relative or relative in seen_paths:
                    invalid.append(f"{label}:path")
                    continue
                seen_paths.add(relative)
                candidate_path = (lesson_root / relative).resolve()
                if (
                    not candidate_path.is_relative_to(root)
                    or candidate_path.parent != root
                    or candidate_path.suffix.casefold() not in {".yaml", ".yml"}
                    or not candidate_path.is_file()
                ):
                    invalid.append(f"{label}:path")
                    continue
                try:
                    payload = yaml.safe_load(candidate_path.read_text(encoding="utf-8-sig")) or {}
                except yaml.YAMLError:
                    payload = None
                if not isinstance(payload, dict):
                    invalid.append(f"{relative}:yaml")
                    continue
                index_state = str(
                    proposal.get("proposal_state")
                    or proposal.get("status")
                    or proposal.get("state")
                    or default_state
                ).casefold()
                payload_state = str(
                    payload.get("proposal_state")
                    or payload.get("status")
                    or payload.get("knowledge_status")
                    or payload.get("knowledge_state")
                    or payload.get("state")
                    or ""
                ).casefold()
                source_case = payload.get("source_case") if isinstance(payload.get("source_case"), dict) else {}
                payload_case_id = str(payload.get("case_id") or source_case.get("case_id") or "")
                index_id = str(proposal.get("id") or "")
                payload_id = str(payload.get("proposal_id") or payload.get("id") or "")
                if (
                    index_state != "candidate"
                    or payload_state != "candidate"
                    or payload_case_id != case_id
                    or not index_id
                    or payload_id != index_id
                ):
                    invalid.append(f"{relative}:metadata")
            return {
                "files": 1 + len(seen_paths),
                "candidate_count": len(proposals),
                "invalid": invalid,
            }
        cards = index.get("cards") if isinstance(index, dict) else None
        if not isinstance(cards, list) or not cards:
            return {"files": 1, "candidate_count": 0, "invalid": ["index.yaml:cards"]}

        invalid: list[str] = []
        seen_paths: set[str] = set()
        root = lesson_root.resolve()
        for position, card in enumerate(cards, start=1):
            label = f"index.yaml:cards[{position}]"
            if not isinstance(card, dict):
                invalid.append(label)
                continue
            relative = str(card.get("path") or "").strip()
            if not relative or relative in seen_paths:
                invalid.append(f"{label}:path")
                continue
            seen_paths.add(relative)
            candidate_path = (lesson_root / relative).resolve()
            if (
                not candidate_path.is_relative_to(root)
                or candidate_path.parent != root
                or candidate_path.suffix.casefold() != ".md"
                or not candidate_path.is_file()
            ):
                invalid.append(f"{label}:path")
                continue

            text = candidate_path.read_text(encoding="utf-8-sig")
            if not text.startswith("---\n"):
                invalid.append(f"{relative}:frontmatter")
                continue
            parts = text.split("---", 2)
            try:
                frontmatter = yaml.safe_load(parts[1]) if len(parts) == 3 else None
            except yaml.YAMLError:
                frontmatter = None
            if not isinstance(frontmatter, dict):
                invalid.append(f"{relative}:frontmatter")
                continue
            if (
                str(card.get("status") or "").casefold() != "candidate"
                or str(frontmatter.get("status") or "").casefold() != "candidate"
                or str(frontmatter.get("case_id") or "") != case_id
                or str(frontmatter.get("id") or "") != str(card.get("id") or "")
            ):
                invalid.append(f"{relative}:metadata")
        return {
            "files": 1 + len(seen_paths),
            "candidate_count": len(cards),
            "invalid": invalid,
        }

    lesson_files = sorted(lesson_root.glob("*.yaml")) + sorted(lesson_root.glob("*.yml"))
    invalid = []
    candidate_count = 0
    allowed_legacy_statuses = {"candidate", "demo"}
    for path in lesson_files:
        payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        statuses = _candidate_knowledge_statuses(payload)
        candidate_count += sum(status == "candidate" for status in statuses)
        metadata_valid = not isinstance(payload, dict) or (
            (not payload.get("case_id") or str(payload.get("case_id")) == case_id)
            and (
                not payload.get("package_status")
                or str(payload.get("package_status")).casefold() in allowed_legacy_statuses
            )
        )
        if (
            not metadata_valid
            or not statuses
            or any(status not in allowed_legacy_statuses for status in statuses)
        ):
            invalid.append(path.name)
    return {"files": len(lesson_files), "candidate_count": candidate_count, "invalid": invalid}


def run_autopilot(
    queue_path: Path,
    runtime_dir: Path,
    executor: PhaseExecutor | Any,
    *,
    recover_stale: bool = False,
    max_cases: int | None = None,
    stop_after_phase: str | None = None,
    _clear_stop_after_lock: bool = False,
) -> dict[str, Any]:
    """Run or continue a queue with a single writer and durable checkpoints."""

    queue_path = Path(queue_path)
    runtime_dir = Path(runtime_dir)
    if max_cases is not None and max_cases < 1:
        raise ValueError("max_cases 必须为正整数。")
    if stop_after_phase is not None and stop_after_phase not in TRAIN_PHASES:
        raise ValueError(f"stop_after_phase 无效：{stop_after_phase}")
    load_training_queue(queue_path)
    lock = acquire_autopilot_lock(runtime_dir, queue_path, recover_stale=recover_stale)
    try:
        if _clear_stop_after_lock:
            clear_autopilot_stop(runtime_dir, queue_path)
        completed_cases = 0
        _write_state(
            runtime_dir,
            schema_version=1,
            status="running",
            pid=os.getpid(),
            recoverable=True,
            blocker_kind=None,
            blocker=None,
            lock_nonce=lock["nonce"],
            queue_path=str(queue_path.resolve()),
            started_at=now_iso(),
            stop_requested=False,
            last_error=None,
        )
        while True:
            paths = _runtime_paths(runtime_dir)
            queue = load_training_queue(queue_path)
            if paths["stop"].exists() or queue.get("stop_requested"):
                result = _write_state(runtime_dir, status="stopped", stop_requested=True, finished_at=now_iso())
                return result
            _reconcile_terminal_case_barrier(queue, runtime_dir, executor)
            item = next_runnable_item(queue)
            if item is None:
                barrier = load_case_completion_barrier(runtime_dir)
                if barrier is not None and barrier.get("case_completion_barrier") == "locked":
                    return _write_state(
                        runtime_dir,
                        status="checkpointed_barrier_locked",
                        current_case=barrier.get("case_id"),
                        blocker_kind="case_completion_barrier",
                        blocker="当前案例尚未完成全部本地门禁，下一年份保持禁止调度。",
                        finished_at=now_iso(),
                    )
                summary = queue_summary(queue)
                final_status = (
                    "completed_with_blocks"
                    if summary["counts"]["blocked"] or summary["counts"]["deferred_platform_safety"]
                    else "completed"
                )
                if hasattr(executor, "record_progress"):
                    executor.record_progress(queue_path, runtime_dir)
                return _write_state(runtime_dir, status=final_status, queue=summary, finished_at=now_iso())

            case_id = str(item["case_id"])
            # Second, dispatch-time hard guard against a hand-edited queue.
            if case_id == FINAL_TEST_CASE_ID:
                raise FinalTestExecutionDenied("Autopilot 永远禁止执行 2023A。")
            assert_training_case(case_id)
            phase = str(item["current_phase"])
            try:
                assert_case_dispatch_allowed(runtime_dir, case_id)
                lock_case_completion_barrier(
                    runtime_dir,
                    case_id,
                    writer_nonce=str(lock["nonce"]),
                )
            except CaseCompletionBarrierError as exc:
                raise SystemAutopilotError(str(exc)) from exc
            running_item, attempt = begin_phase(queue_path, case_id)
            run_id = f"{case_id}-{phase}-{attempt}-{uuid.uuid4().hex[:12]}"
            run_dir = paths["runs"] / case_id / phase / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            try:
                begin_formal_write(
                    runtime_dir,
                    case_id,
                    phase=phase,
                    run_id=run_id,
                    writer_nonce=str(lock["nonce"]),
                    writer_pid=os.getpid(),
                )
            except CaseCompletionBarrierError as exc:
                raise SystemAutopilotError(str(exc)) from exc
            _write_state(
                runtime_dir,
                status="running",
                current_case=case_id,
                current_phase=phase,
                current_attempt=attempt,
                current_run_id=run_id,
                last_error=None,
            )
            try:
                try:
                    phase_result = _invoke_executor(executor, case_id, phase, attempt, run_dir)
                finally:
                    finish_formal_write(
                        runtime_dir,
                        case_id,
                        run_id=run_id,
                        writer_nonce=str(lock["nonce"]),
                    )
                write_json(run_dir / "executor-result.json", asdict(phase_result))
                updated = mark_phase_success(queue_path, case_id, phase)
                if updated["status"] == "completed":
                    if paths["stop"].exists() or load_training_queue(queue_path).get("stop_requested"):
                        return _write_state(
                            runtime_dir,
                            status="stopped",
                            stop_requested=True,
                            current_case=case_id,
                            blocker_kind="user_stop_before_barrier_release",
                            blocker="案例原子阶段已落盘；按用户停止要求，完成屏障未释放。",
                            finished_at=now_iso(),
                        )
                    try:
                        release_case_completion_barrier(
                            runtime_dir,
                            case_id,
                            terminal_status="completed",
                            evidence=_completion_evidence(executor, case_id, "completed"),
                        )
                    except CaseCompletionBarrierError as exc:
                        raise SystemAutopilotError(str(exc)) from exc
                    completed_cases += 1
                    if hasattr(executor, "record_progress"):
                        executor.record_progress(queue_path, runtime_dir)
                _write_state(runtime_dir, last_completed={"case_id": case_id, "phase": phase, "run_id": run_id})
            except PlatformSafetyBlock as exc:
                mark_case_deferred_platform_safety(
                    queue_path,
                    case_id,
                    error=str(exc),
                    run_id=exc.run_id,
                    thread_id=exc.thread_id,
                )
                if paths["stop"].exists() or load_training_queue(queue_path).get("stop_requested"):
                    return _write_state(
                        runtime_dir,
                        status="stopped",
                        stop_requested=True,
                        current_case=case_id,
                        blocker_kind="user_stop_before_barrier_release",
                        blocker="平台阻塞证据已落盘；按用户停止要求，完成屏障未释放。",
                        finished_at=now_iso(),
                    )
                try:
                    release_case_completion_barrier(
                        runtime_dir,
                        case_id,
                        terminal_status="deferred_platform_safety",
                        evidence=_completion_evidence(executor, case_id, "deferred_platform_safety"),
                    )
                except CaseCompletionBarrierError as barrier_exc:
                    raise SystemAutopilotError(str(barrier_exc)) from barrier_exc
                write_json(
                    run_dir / "executor-error.json",
                    {"kind": "platform_safety", "message": str(exc), "at": now_iso()},
                )
                _write_state(
                    runtime_dir,
                    last_error={"kind": "platform_safety", "case_id": case_id, "phase": phase, "message": str(exc)},
                )
                queue_after = load_training_queue(queue_path)
                current_ordinal = int(next(item["ordinal"] for item in queue_after["items"] if item["case_id"] == case_id))
                prior_item = next(
                    (item for item in queue_after["items"] if int(item["ordinal"]) == current_ordinal - 1),
                    None,
                )
                prior_same = bool(
                    prior_item
                    and prior_item.get("status") == "deferred_platform_safety"
                    and prior_item.get("blocked_reason") == "bio_safety_classifier"
                )
                if prior_same:
                    raise SystemAutopilotError("连续年份触发同一平台内容安全阻塞，暂停正式训练。")
                completed_cases += 1
                if max_cases is not None and completed_cases >= max_cases:
                    return _write_state(runtime_dir, status="checkpointed", finished_at=now_iso())
                continue
            except UsageLimitReached as exc:
                mark_phase_interrupted_quota(
                    queue_path,
                    case_id,
                    phase,
                    message=str(exc),
                    run_id=exc.run_id,
                )
                write_json(
                    run_dir / "executor-error.json",
                    {"kind": "quota", "message": str(exc), "at": now_iso()},
                )
                set_stop_requested(queue_path, True)
                return _write_state(
                    runtime_dir,
                    status="resumable_after_quota_reset",
                    stop_requested=True,
                    blocker_kind="quota",
                    blocker=str(exc),
                    last_error={"kind": "quota", "case_id": case_id, "phase": phase, "message": str(exc)},
                    finished_at=now_iso(),
                )
            except TransientPhaseError as exc:
                updated = mark_phase_failure(queue_path, case_id, phase, str(exc), transient=True)
                write_json(run_dir / "executor-error.json", {"kind": "transient", "message": str(exc), "at": now_iso()})
                _write_state(runtime_dir, last_error={"kind": "transient", "case_id": case_id, "phase": phase, "message": str(exc)})
                if updated["status"] == "blocked":
                    completed_cases += 1
                    if max_cases is not None and completed_cases >= max_cases:
                        return _write_state(runtime_dir, status="checkpointed", finished_at=now_iso())
                continue
            except CasePhaseError as exc:
                mark_phase_failure(queue_path, case_id, phase, str(exc), transient=False)
                write_json(run_dir / "executor-error.json", {"kind": "case", "message": str(exc), "at": now_iso()})
                _write_state(runtime_dir, last_error={"kind": "case", "case_id": case_id, "phase": phase, "message": str(exc)})
                completed_cases += 1
                if max_cases is not None and completed_cases >= max_cases:
                    return _write_state(runtime_dir, status="checkpointed", finished_at=now_iso())
                continue
            except (FinalTestExecutionDenied, SystemAutopilotError):
                raise
            except Exception as exc:
                raise SystemAutopilotError(f"未分类阶段异常，安全停止：{case_id}/{phase}: {exc}") from exc

            if stop_after_phase == phase:
                return _write_state(runtime_dir, status="checkpointed", finished_at=now_iso())
            if max_cases is not None and completed_cases >= max_cases:
                return _write_state(runtime_dir, status="checkpointed", finished_at=now_iso())
    except (FinalTestExecutionDenied, SystemAutopilotError) as exc:
        set_stop_requested(queue_path, True)
        _write_state(
            runtime_dir,
            status="failed",
            stop_requested=True,
            last_error={"kind": "system", "message": str(exc)},
            finished_at=now_iso(),
        )
        raise
    finally:
        release_autopilot_lock(runtime_dir, lock)


def resume_autopilot(
    queue_path: Path,
    runtime_dir: Path,
    executor: PhaseExecutor | Any,
    *,
    max_cases: int | None = None,
    stop_after_phase: str | None = None,
) -> dict[str, Any]:
    return run_autopilot(
        queue_path,
        runtime_dir,
        executor,
        recover_stale=True,
        max_cases=max_cases,
        stop_after_phase=stop_after_phase,
        _clear_stop_after_lock=True,
    )


class CodexPhaseExecutor:
    """Default real executor; tests should inject a deterministic fake instead."""

    PROMPTS = {
        "solve": "01-solve.md",
        "audit": "02-audit.md",
        "blind-revision": "03-blind-revision.md",
        "reflection": "04-reflect.md",
    }

    READY_STATES = {
        "solve": {"solve_ready", "solving"},
        "audit": {"audit_ready"},
        "blind-revision": {"blind_revision_ready"},
        "reflection": {"reflection_ready"},
    }

    PREREQUISITES = {
        "solve": "initialized",
        "audit": "blind_v1_frozen",
        "blind-revision": "audited",
        "reflection": "blind_final_frozen",
    }

    COMPLETED_STATES = {
        "solve": {"blind_v1_frozen", "audit_ready", "audited", "blind_revision_ready", "blind_final_frozen", "reflection_ready", "reflected", "knowledge_proposed", "archived"},
        "audit": {"audited", "blind_revision_ready", "blind_final_frozen", "reflection_ready", "reflected", "knowledge_proposed", "archived"},
        "blind-revision": {"blind_final_frozen", "reflection_ready", "reflected", "knowledge_proposed", "archived"},
        "reflection": {"reflected", "knowledge_proposed", "archived"},
    }

    def __init__(
        self,
        trainer_root: Path,
        codex_home: Path,
        *,
        codex_command: str = "codex",
        model: str = "gpt-5.6-sol",
        reasoning_effort: str = "max",
    ) -> None:
        self.trainer_root = Path(trainer_root).resolve()
        self.codex_home = Path(codex_home).resolve()
        self.codex_command = codex_command
        if model != "gpt-5.6-sol" or reasoning_effort != "max":
            raise SystemAutopilotError("正式训练固定为 gpt-5.6-sol/max；禁止降级或静默回退。")
        self.model = model
        self.reasoning_effort = reasoning_effort

    def _verify_final_test_seal(self) -> dict[str, Any]:
        from .training_queue import read_final_test_seal
        from .util import load_lab_paths

        paths = load_lab_paths(self.trainer_root)
        seal = read_final_test_seal(Path(paths["exam_vault"]) / "2023A" / "SEALED.json")
        if seal.get("state") != "sealed" or seal.get("status") != "test_sealed":
            raise SystemAutopilotError("2023A 不再是 test_sealed/consumed=false，停止全队列。")
        return seal

    def record_progress(self, queue_path: Path, runtime_dir: Path) -> dict[str, Any]:
        """Write metadata-only progress and verify the opaque 2023A seal."""

        from .util import load_lab_paths

        paths = load_lab_paths(self.trainer_root)
        seal = self._verify_final_test_seal()

        queue = load_training_queue(queue_path)
        cases: list[dict[str, Any]] = []
        total_candidates = 0
        for item in queue["items"]:
            case_id = str(item["case_id"])
            case_dir = Path(paths["runtime_cases"]) / case_id
            sessions: dict[str, dict[str, Any]] = {}
            for phase in TRAIN_PHASES:
                session_path = case_dir / "logs" / f"{phase}-session.json"
                if not session_path.is_file():
                    continue
                session = read_json(session_path, {})
                sessions[phase] = {
                    "thread_id": session.get("thread_id"),
                    "model": session.get("model"),
                    "reasoning_effort": session.get("reasoning_effort"),
                    "fallback": session.get("fallback"),
                }
            candidate_count = 0
            lesson_root = case_dir / "workspaces" / "reflection" / "lessons-proposed"
            if lesson_root.is_dir():
                proposal_validation = _validate_candidate_proposals(lesson_root, case_id)
                if not proposal_validation["invalid"]:
                    candidate_count = int(proposal_validation["candidate_count"])
            total_candidates += candidate_count
            cases.append(
                {
                    "case_id": case_id,
                    "status": item.get("status"),
                    "current_phase": item.get("current_phase"),
                    "completed_phases": list(item.get("completed_phases") or []),
                    "attempts": dict(item.get("attempts") or {}),
                    "blind_v1": (case_dir / "frozen" / "FROZEN_BLIND_V1.json").is_file(),
                    "audit": "audit" in (item.get("completed_phases") or []),
                    "blind_final": (case_dir / "frozen" / "FROZEN_BLIND_FINAL.json").is_file(),
                    "reflection": "reflection" in (item.get("completed_phases") or []),
                    "sessions": sessions,
                    "fallback_detected": any(value.get("fallback") is not False for value in sessions.values()),
                    "candidate_count": candidate_count,
                    "machine_verified_generated": 0,
                }
            )

        policy_path = Path(runtime_dir) / "execution-policy.json"
        policy = read_json(policy_path, {}) if policy_path.is_file() else {}
        baseline_candidates = int(policy.get("candidate_baseline_count") or 0)
        target_cases = [entry for entry in cases if 2005 <= int(entry["case_id"][:4]) <= 2021]
        all_target_complete = bool(target_cases) and all(entry["status"] == "completed" for entry in target_cases)
        summary = {
            "schema_version": 1,
            "generated_at": now_iso(),
            "system_status": "ready_for_final_test" if all_target_complete else "continuous_training",
            "execution_policy": policy.get("execution_policy"),
            "queue": queue_summary(queue),
            "cases": cases,
            "new_candidate_count": max(0, total_candidates - baseline_candidates),
            "new_machine_verified_count": 0,
            "final_test": {
                "case_id": "2023A",
                "status": "test_sealed",
                "consumed": False,
                "manifest_sha256": seal.get("manifest_sha256"),
            },
            "isolation_statement": "这是最小复制工作区隔离，不是Windows绝对路径安全证明。",
        }
        reports = self.trainer_root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        write_json(reports / "training-summary.json", summary)
        rows = [
            "# 连续正式训练进度",
            "",
            f"更新时间：{summary['generated_at']}",
            "",
            f"系统状态：{summary['system_status']}",
            "",
            "这是最小复制工作区隔离，不是Windows绝对路径安全证明。",
            "",
            "| 案例 | 队列状态 | 当前阶段 | Blind V1 | Audit | Blind Final | Reflection | Candidate |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
        for entry in cases:
            rows.append(
                "| {case_id} | {status} | {phase} | {v1} | {audit} | {final} | {reflection} | {candidates} |".format(
                    case_id=entry["case_id"],
                    status=entry["status"],
                    phase=entry["current_phase"] or "-",
                    v1="是" if entry["blind_v1"] else "否",
                    audit="是" if entry["audit"] else "否",
                    final="是" if entry["blind_final"] else "否",
                    reflection="是" if entry["reflection"] else "否",
                    candidates=entry["candidate_count"],
                )
            )
        rows.extend(
            [
                "",
                f"本轮新增 Candidate：{summary['new_candidate_count']}",
                "",
                "本轮新增 machine_verified：0",
                "",
                "2023A：test_sealed；consumed=false。",
            ]
        )
        (reports / "autopilot-progress.md").write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
        return summary

    @staticmethod
    def _required_solution_outputs(workspace: Path) -> list[str]:
        required = (
            "problem-analysis.md",
            "data-audit.md",
            "assumptions.yaml",
            "variables.yaml",
            "model-selection.md",
            "solution-report.yaml",
            "reproducibility.yaml",
            "paper/main.tex",
            "paper/paper.md",
        )
        missing = [name for name in required if not (workspace / name).is_file()]
        code_root = workspace / "code"
        if not code_root.is_dir() or not any(
            path.is_file() and path.suffix.casefold() in {".py", ".ps1"}
            for path in code_root.iterdir()
        ):
            missing.append("code/<python-or-powershell-entrypoint>")
        for directory in ("results", "figures"):
            root = workspace / directory
            if not root.is_dir() or not any(path.is_file() for path in root.rglob("*")):
                missing.append(f"{directory}/<file>")
        return missing

    def _compile_paper(self, case_dir: Path, workspace: Path, label: str) -> dict[str, Any]:
        from .util import safe_copy_tree

        engine = shutil.which("xelatex")
        if not engine:
            candidate_bins: list[Path] = []
            configured_miktex = os.environ.get("CUMCM_MIKTEX_BIN")
            if configured_miktex:
                candidate_bins.append(Path(configured_miktex))
            candidate_bins.append(
                Path(__file__).resolve().parents[3]
                / "runtime"
                / "miktex-portable"
                / "texmfs"
                / "install"
                / "miktex"
                / "bin"
                / "x64"
            )
            for candidate_bin in candidate_bins:
                for executable_name in ("xelatex.exe", "xelatex"):
                    candidate = candidate_bin / executable_name
                    if candidate.is_file():
                        engine = str(candidate.resolve())
                        break
                if engine:
                    break
        source_paper_dir = workspace / "paper"
        main_tex = source_paper_dir / "main.tex"
        report: dict[str, Any] = {
            "status": "fail",
            "engine": engine,
            "source": str(main_tex),
            "source_pdf": str(source_paper_dir / "main.pdf"),
            "verification_pdf": "temporary-copy/paper/main.pdf",
            "source_pdf_preserved": True,
            "runs": [],
        }
        if not engine or not main_tex.is_file():
            report["reason"] = "xelatex 或 paper/main.tex 不存在"
        else:
            with tempfile.TemporaryDirectory(prefix="cumcm-tex-") as temp_name:
                verification_workspace = Path(temp_name) / "workspace"
                verification_workspace.mkdir(parents=True)
                for name in ("paper", "results", "figures"):
                    source = workspace / name
                    if source.is_dir():
                        safe_copy_tree(source, verification_workspace / name)
                paper_dir = verification_workspace / "paper"
                for generated_name in ("main.aux", "main.log", "main.out", "main.pdf", "main.toc"):
                    generated = paper_dir / generated_name
                    if generated.exists():
                        generated.unlink()
                for _ in range(2):
                    completed = subprocess.run(
                        [engine, "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                        cwd=paper_dir,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=240,
                        check=False,
                    )
                    report["runs"].append(
                        {
                            "return_code": completed.returncode,
                            "stdout_tail": completed.stdout[-6000:],
                            "stderr_tail": completed.stderr[-3000:],
                        }
                    )
                    if completed.returncode != 0:
                        break
                pdf = paper_dir / "main.pdf"
                if report["runs"] and all(item["return_code"] == 0 for item in report["runs"]) and pdf.is_file() and pdf.stat().st_size:
                    report["status"] = "pass"
                    report["pdf_size"] = pdf.stat().st_size
                else:
                    report["reason"] = "XeLaTeX 未成功生成非空 PDF"
        write_json(case_dir / "reports" / f"tex-compile-{label}.json", report)
        return report

    def _validate_solution_workspace(self, case_dir: Path, workspace: Path, label: str) -> None:
        from .paper import lint_paper
        from .verify import verify_case

        missing = self._required_solution_outputs(workspace)
        reproduction = verify_case(
            case_dir,
            source_root=workspace,
            report_path=case_dir / "reports" / f"reproduction-{label}.json",
        )
        paper_source = workspace / "paper" / "main.tex"
        if not paper_source.is_file():
            paper_source = workspace / "paper" / "paper.md"
        paper_lint = lint_paper(
            paper_source,
            self.trainer_root / "config" / "competition-rules.yaml",
            artifact_root=workspace,
            report_path=case_dir / "reports" / f"paper-lint-{label}.json",
        ) if paper_source.is_file() else {"status": "fail"}
        compile_report = self._compile_paper(case_dir, workspace, label)
        gate = {
            "status": "pass" if not missing and reproduction["status"] == "pass" and paper_lint["status"] != "fail" and compile_report["status"] == "pass" else "fail",
            "missing": missing,
            "reproduction": reproduction["status"],
            "paper_lint": paper_lint["status"],
            "tex_compile": compile_report["status"],
            "isolation_statement": "这是最小复制工作区隔离，不是Windows绝对路径安全证明。",
        }
        write_json(case_dir / "reports" / f"quality-gate-{label}.json", gate)
        if gate["status"] != "pass":
            raise TransientPhaseError(f"{label} 质量门禁失败：{gate}")

    def _validate_reflection_and_regression(self, case_dir: Path) -> None:
        from .freeze import verify_frozen
        from .git_guard import inspect_git_tree
        from .util import load_lab_paths, sha256_file

        workspace = case_dir / "workspaces" / "reflection"
        lesson_root = workspace / "lessons-proposed"
        proposal_validation = _validate_candidate_proposals(lesson_root, case_dir.name)
        candidate_count = int(proposal_validation["candidate_count"])
        invalid = list(proposal_validation["invalid"])
        if candidate_count < 1 or invalid:
            raise TransientPhaseError(
                "Reflection candidate 知识卡不完整："
                f"files={proposal_validation['files']}, cards={candidate_count}, invalid={invalid}"
            )

        session_records = []
        for phase in TRAIN_PHASES:
            session_path = case_dir / "logs" / f"{phase}-session.json"
            if not session_path.is_file():
                raise SystemAutopilotError(f"缺少阶段会话证据：{session_path}")
            session_records.append(read_json(session_path))
        thread_ids = [str(item.get("thread_id") or "") for item in session_records]
        session_contract_pass = (
            all(thread_ids)
            and len(set(thread_ids)) == len(TRAIN_PHASES)
            and all(item.get("model") == self.model for item in session_records)
            and all(item.get("reasoning_effort") == self.reasoning_effort for item in session_records)
            and all(item.get("fallback") is False and item.get("ephemeral") is True for item in session_records)
        )
        if not session_contract_pass:
            raise SystemAutopilotError("四阶段独立会话或实际模型契约验证失败。")

        knowledge_snapshot_path = case_dir / "reports" / "knowledge-repository-snapshot-blind-final.json"
        if not knowledge_snapshot_path.is_file():
            raise SystemAutopilotError("Blind Final 前知识库快照缺失。")
        before_knowledge = read_json(knowledge_snapshot_path)
        current_knowledge = [
            {
                "path": path.relative_to(self.trainer_root / "knowledge").as_posix(),
                "sha256": sha256_file(path),
            }
            for path in sorted((self.trainer_root / "knowledge").rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]
        if current_knowledge != before_knowledge.get("files"):
            raise SystemAutopilotError("Reflection 前后项目知识库发生变化；拒绝自动升级 candidate。")

        paths = load_lab_paths(self.trainer_root)
        index_path = Path(paths["vault_hash_index"])
        index = read_json(index_path) if index_path.is_file() else {"hashes": []}
        hashes = [str(item.get("sha256")) for item in index.get("hashes", []) if item.get("sha256")]
        git_report = inspect_git_tree(self.trainer_root, real_hashes=hashes, include_untracked=True)
        write_json(case_dir / "reports" / "git-leak-guard.json", git_report)
        if git_report["status"] != "pass":
            raise SystemAutopilotError("Git 泄漏守卫失败，停止全队列。")

        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=self.trainer_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
        regression = {
            "status": "pass" if completed.returncode == 0 else "fail",
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        write_json(case_dir / "reports" / "regression-tests.json", regression)
        if regression["status"] != "pass":
            raise SystemAutopilotError("回归测试失败，停止全队列。")

        regression_gates = {
            "status": "pass",
            "blind_v1_frozen": verify_frozen(case_dir, "blind-v1")["status"],
            "blind_final_frozen": verify_frozen(case_dir, "blind-final")["status"],
            "blind_final_reproduction": read_json(
                case_dir / "reports" / "reproduction-blind-final.json", {}
            ).get("status"),
            "independent_ephemeral_sessions": session_contract_pass,
            "git_leak_guard": git_report["status"],
            "candidate_or_demo_only": True,
            "candidate_count": candidate_count,
            "knowledge_repository_unchanged": True,
        }
        if any(
            regression_gates[key] != "pass"
            for key in ("blind_v1_frozen", "blind_final_frozen", "blind_final_reproduction", "git_leak_guard")
        ):
            regression_gates["status"] = "fail"
        write_json(case_dir / "reports" / "regression-gates.json", regression_gates)
        if regression_gates["status"] != "pass":
            raise SystemAutopilotError("案例回归门禁失败，停止全队列。")

    def _record_training_memory_update(self, case_dir: Path, case_id: str) -> dict[str, Any]:
        """Record a deterministic, non-promoting memory curation decision.

        Reflection candidates remain candidates.  The case-level update is
        nevertheless durable, so a later year cannot start merely because the
        card curator had no safe cross-case card to add.
        """

        from .util import read_yaml, sha256_file, write_yaml

        memory_root = self.trainer_root / "knowledge" / "training-memory"
        index_path = memory_root / "index.yaml"
        if not index_path.is_file():
            raise SystemAutopilotError("训练记忆索引不存在。")
        lesson_root = case_dir / "workspaces" / "reflection" / "lessons-proposed"
        proposal_validation = _validate_candidate_proposals(lesson_root, case_id)
        if proposal_validation["invalid"] or int(proposal_validation["candidate_count"]) < 1:
            raise SystemAutopilotError("训练记忆更新前 candidate 校验失败。")

        index = read_yaml(index_path)
        if not isinstance(index, dict) or index.get("status") != "provisional_training":
            raise SystemAutopilotError("训练记忆索引状态不是 provisional_training。")
        before_hash = sha256_file(index_path)
        updates = list(index.get("case_updates") or [])
        existing = next((item for item in updates if item.get("case_id") == case_id), None)
        if existing is None:
            updates.append(
                {
                    "case_id": case_id,
                    "status": "completed",
                    "candidate_count_reviewed": int(proposal_validation["candidate_count"]),
                    "cards_added": 0,
                    "decision": "retain_candidates_without_forced_provisional_card",
                    "reason": "单题 Reflection candidate 尚缺跨案例适用性证据；完成更新审查但不强行套用或升级。",
                    "updated_at": now_iso(),
                }
            )
            index["case_updates"] = updates
            index["last_case_update"] = case_id
            index["last_updated"] = now_iso()
            index["version"] = int(index.get("version", 0)) + 1
            write_yaml(index_path, index)
        after_hash = sha256_file(index_path)
        report = {
            "status": "pass",
            "case_id": case_id,
            "memory_status": "provisional_training",
            "candidate_count_reviewed": int(proposal_validation["candidate_count"]),
            "cards_added": 0,
            "promotion_performed": False,
            "forced_adoption": False,
            "update_recorded": True,
            "index_path": str(index_path),
            "index_sha256_before": before_hash,
            "index_sha256_after": after_hash,
            "recorded_at": now_iso(),
        }
        write_json(case_dir / "reports" / "training-memory-update.json", report)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=self.trainer_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
        )
        post_memory_regression = {
            "status": "pass" if completed.returncode == 0 else "fail",
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "scope": "after provisional training memory update",
        }
        write_json(case_dir / "reports" / "regression-tests-post-memory.json", post_memory_regression)
        if post_memory_regression["status"] != "pass":
            raise SystemAutopilotError("训练记忆更新后的全量测试失败，案例屏障保持锁定。")
        return report

    def completion_evidence(self, case_id: str, terminal_status: str) -> dict[str, Any]:
        if terminal_status == "deferred_platform_safety":
            return {
                "deferred_recorded": True,
                "reference_opened_false": True,
                "no_active_local_processes": True,
            }

        from .cases import find_case
        from .freeze import verify_frozen

        case_dir = find_case(self.trainer_root, case_id)
        reports = case_dir / "reports"
        score = read_json(reports / "blind-score-boundary.json", {})
        cleanup = read_json(reports / "reflection-reference-cleanup.json", {})
        memory_update = read_json(reports / "training-memory-update.json", {})
        regression_tests = read_json(reports / "regression-tests-post-memory.json", {})
        regression_gates = read_json(reports / "regression-gates.json", {})
        quality_gate = read_json(reports / "quality-gate-blind-final.json", {})
        candidate_validation = _validate_candidate_proposals(
            case_dir / "workspaces" / "reflection" / "lessons-proposed",
            case_id,
        )
        evidence = {
            "blind_v1_frozen": verify_frozen(case_dir, "blind-v1")["status"] == "pass",
            "audit_completed": (case_dir / "logs" / "audit-session.json").is_file(),
            "blind_revision_completed": (case_dir / "logs" / "blind-revision-session.json").is_file(),
            "revision_verification_completed": quality_gate.get("status") == "pass",
            "blind_final_frozen": verify_frozen(case_dir, "blind-final")["status"] == "pass",
            "blind_score_recorded": bool(score) and score.get("references_opened") is False,
            "reflection_completed": (case_dir / "logs" / "reflection-session.json").is_file(),
            "reflection_references_cleaned": cleanup.get("status") == "pass",
            "candidate_updated": (
                not candidate_validation["invalid"]
                and int(candidate_validation["candidate_count"]) >= 1
            ),
            "training_memory_updated": (
                memory_update.get("status") == "pass"
                and memory_update.get("update_recorded") is True
            ),
            "tests_passed": regression_tests.get("status") == "pass",
            "regression_passed": regression_gates.get("status") == "pass",
            # All model, reproduction and XeLaTeX calls in this executor are
            # synchronous; this method is invoked only after the formal-write
            # marker has been cleared by run_autopilot.
            "no_active_local_processes": True,
        }
        write_json(
            reports / "case-completion-evidence.json",
            {
                "status": "pass" if all(evidence.values()) else "fail",
                "case_id": case_id,
                "terminal_status": terminal_status,
                "checks": evidence,
                "process_check_basis": "synchronous executor returned and formal-write marker cleared",
                "recorded_at": now_iso(),
            },
        )
        return evidence

    def _archive_invalidated_solve_workspace(self, case_dir: Path, case_id: str) -> Path | None:
        """Preserve a prematurely started Solve before preparing a fresh generation."""

        from .util import read_yaml, write_yaml

        report_path = case_dir / "reports" / "old-solve-formal-invalidation.json"
        if not report_path.is_file():
            return None
        report = read_json(report_path)
        if (
            report.get("status") != "invalidated_for_formal_training"
            or report.get("case_id") != case_id
            or report.get("formal_score_eligible") is not False
            or report.get("reference_opened") is not False
        ):
            raise SystemAutopilotError("旧 Solve 正式失效报告不完整，拒绝准备替代工作区。")
        if report.get("replacement_workspace_prepared") is True:
            return None

        workspace_root = (case_dir / "workspaces").resolve()
        source = (workspace_root / "solve").resolve()
        generation = int(read_json(report_path).get("replacement_generation") or 2)
        archive = (workspace_root / f"solve-invalidated-formal-generation-{generation - 1}").resolve()
        if not source.is_relative_to(workspace_root) or not archive.is_relative_to(workspace_root):
            raise SystemAutopilotError("旧 Solve 工作区归档目标越界。")

        state_data = read_yaml(case_dir / "case-state.yaml")
        state = state_data.get("state")
        if archive.is_dir() and source.is_dir() and state in {"solve_ready", "solving"}:
            report["replacement_workspace_prepared"] = True
            report["replacement_workspace"] = source.relative_to(case_dir).as_posix()
            report["replacement_workspace_prepared_at"] = now_iso()
            write_json(report_path, report)
            return archive
        if archive.is_dir() and not source.exists() and state == "initialized":
            return archive
        if state != "solving" or not source.is_dir() or archive.exists():
            raise SystemAutopilotError(
                f"旧 Solve 归档前置状态异常：state={state}, source={source.exists()}, archive={archive.exists()}"
            )

        source.replace(archive)
        state_data["state"] = "initialized"
        state_data.setdefault("history", []).append(
            {
                "from": "solving",
                "to": "initialized",
                "timestamp": now_iso(),
                "command": "invalidate premature solve and prepare formal generation 2",
                "actor": "cumcm_lab",
                "reason": "前一案例完成屏障尚未释放且 Blind V1 未形成；无损归档旧工作区后从头创建正式 Solve。",
                "git_commit": None,
                "manifest_hash": None,
                "recovery_transition": True,
            }
        )
        write_yaml(case_dir / "case-state.yaml", state_data)
        case_meta = read_yaml(case_dir / "case.yaml")
        case_meta["status"] = "initialized"
        write_yaml(case_dir / "case.yaml", case_meta)
        report["replacement_generation"] = generation
        report["archived_workspace"] = archive.relative_to(case_dir).as_posix()
        report["archived_without_deletion"] = True
        report["replacement_workspace_prepared"] = False
        report["archived_at"] = now_iso()
        write_json(report_path, report)
        return archive

    def _prepare_or_reuse(self, case_id: str, phase: str) -> tuple[Path, Path, bool]:
        from .cases import find_case, init_runtime_case
        from .phases import ensure_reflection_control_files, prepare_phase
        from .state import load_state

        try:
            case_dir = find_case(self.trainer_root, case_id)
        except FileNotFoundError:
            if phase != "solve":
                raise
            case_dir = init_runtime_case(self.trainer_root, case_id)
        if phase == "solve":
            invalidated_archive = self._archive_invalidated_solve_workspace(case_dir, case_id)
        else:
            invalidated_archive = None
        state = load_state(case_dir)["state"]
        workspace = case_dir / "workspaces" / phase
        if state in self.COMPLETED_STATES[phase]:
            return case_dir, workspace, True
        if state == self.PREREQUISITES[phase]:
            workspace = prepare_phase(self.trainer_root, case_id, phase)
            if phase == "solve" and invalidated_archive is not None:
                invalidation_path = case_dir / "reports" / "old-solve-formal-invalidation.json"
                invalidation = read_json(invalidation_path)
                invalidation["replacement_workspace_prepared"] = True
                invalidation["replacement_workspace"] = workspace.relative_to(case_dir).as_posix()
                invalidation["replacement_workspace_prepared_at"] = now_iso()
                write_json(invalidation_path, invalidation)
        elif state not in self.READY_STATES[phase]:
            raise SystemAutopilotError(f"队列与案例状态不一致：{case_id}/{phase}: {state}")
        if phase == "reflection" and state in self.READY_STATES[phase]:
            ensure_reflection_control_files(case_dir, workspace)
        if not (workspace / "phase-lock.json").is_file():
            raise SystemAutopilotError(f"阶段工作区缺少 phase-lock.json：{workspace}")
        return case_dir, workspace, False

    def _finalize(self, case_dir: Path, case_id: str, phase: str) -> None:
        from .freeze import freeze_solution, verify_frozen
        from .phases import complete_phase
        from .scoring import score_case

        if phase == "solve":
            self._validate_solution_workspace(case_dir, case_dir / "workspaces" / "solve", "blind-v1")
            freeze_solution(case_dir, "blind-v1")
            if verify_frozen(case_dir, "blind-v1")["status"] != "pass":
                raise SystemAutopilotError(f"blind-v1 冻结校验失败：{case_id}")
        elif phase == "audit":
            complete_phase(self.trainer_root, case_id, "audit")
        elif phase == "blind-revision":
            self._validate_solution_workspace(case_dir, case_dir / "workspaces" / "blind-revision", "blind-final")
            freeze_solution(case_dir, "blind-final")
            if verify_frozen(case_dir, "blind-final")["status"] != "pass":
                raise SystemAutopilotError(f"blind-final 冻结校验失败：{case_id}")
            blind_score = score_case(case_dir, self.trainer_root)
            write_json(
                case_dir / "reports" / "blind-score-boundary.json",
                {
                    "case_id": case_id,
                    "recorded_at": now_iso(),
                    "score_status": blind_score["status"],
                    "score_total": blind_score["total"],
                    "references_opened": False,
                    "boundary": "该成绩在 reflection 工作区准备、参考论文复制之前记录。",
                },
            )
            from .util import sha256_file

            solve_knowledge = case_dir / "workspaces" / "solve" / "knowledge"
            solve_files = [
                {
                    "path": path.relative_to(solve_knowledge).as_posix(),
                    "sha256": sha256_file(path),
                }
                for path in sorted(solve_knowledge.rglob("*"))
                if path.is_file() and not path.is_symlink()
            ] if solve_knowledge.is_dir() else []
            write_json(
                case_dir / "reports" / "knowledge-snapshot-blind-final.json",
                {
                    "case_id": case_id,
                    "recorded_at": now_iso(),
                    "references_opened": False,
                    "solve_knowledge_count": len(solve_files),
                    "solve_knowledge": solve_files,
                },
            )
            knowledge_root = self.trainer_root / "knowledge"
            write_json(
                case_dir / "reports" / "knowledge-repository-snapshot-blind-final.json",
                {
                    "case_id": case_id,
                    "recorded_at": now_iso(),
                    "files": [
                        {
                            "path": path.relative_to(knowledge_root).as_posix(),
                            "sha256": sha256_file(path),
                        }
                        for path in sorted(knowledge_root.rglob("*"))
                        if path.is_file() and not path.is_symlink()
                    ],
                },
            )
        elif phase == "reflection":
            self._validate_reflection_and_regression(case_dir)
            reflection_workspace = case_dir / "workspaces" / "reflection"
            refs = reflection_workspace / "approved-references"
            derived_refs = reflection_workspace / ".reflection-review"
            extracted_refs = reflection_workspace / "reference-extracts"
            reference_count = len([path for path in refs.iterdir() if path.is_file()]) if refs.is_dir() else 0
            derived_reference_count = (
                len([path for path in derived_refs.rglob("*") if path.is_file()])
                if derived_refs.is_dir()
                else 0
            )
            extracted_reference_count = (
                len([path for path in extracted_refs.rglob("*") if path.is_file()])
                if extracted_refs.is_dir()
                else 0
            )
            for cleanup_target in (refs, derived_refs, extracted_refs):
                if not cleanup_target.exists():
                    continue
                if not cleanup_target.resolve().is_relative_to(reflection_workspace.resolve()):
                    raise SystemAutopilotError("Reflection 参考材料清理目标越界。")
                for cleanup_attempt in range(6):
                    try:
                        shutil.rmtree(cleanup_target)
                        break
                    except PermissionError:
                        if cleanup_attempt == 5:
                            raise
                        time.sleep(1)
            write_json(
                case_dir / "reports" / "reflection-reference-cleanup.json",
                {
                    "status": "pass" if not refs.exists() and not derived_refs.exists() and not extracted_refs.exists() else "fail",
                    "removed_reference_count": reference_count,
                    "removed_derived_reference_count": derived_reference_count,
                    "removed_extracted_reference_count": extracted_reference_count,
                    "removed_at": now_iso(),
                },
            )
            if refs.exists() or derived_refs.exists() or extracted_refs.exists():
                raise SystemAutopilotError("Reflection 临时参考材料清理失败。")
            complete_phase(self.trainer_root, case_id, "reflection")
            from .state import transition

            transition(
                case_dir,
                "knowledge_proposed",
                command="foreground-training reflection regression",
                reason="Reflection candidate 已生成且回归测试通过；未升级为可用知识",
            )
            self._record_training_memory_update(case_dir, case_id)
        else:
            raise SystemAutopilotError(f"未知训练阶段：{phase}")

    def __call__(self, case_id: str, phase: str, attempt: int, run_dir: Path) -> PhaseResult:
        from .session_runner import run_stage_session
        from .util import iter_regular_files

        assert_training_case(case_id)
        if not self.codex_home.is_dir():
            raise SystemAutopilotError(f"专用 CODEX_HOME 不存在：{self.codex_home}")
        prompt_path = self.trainer_root / "prompts" / self.PROMPTS[phase]
        if not prompt_path.is_file():
            raise SystemAutopilotError(f"阶段提示词不存在：{prompt_path}")
        case_dir, workspace, already_complete = self._prepare_or_reuse(case_id, phase)
        self._verify_final_test_seal()
        if already_complete:
            return PhaseResult(message="案例状态表明该阶段已完成；按幂等恢复跳过。")

        if phase in {"solve", "audit", "blind-revision"}:
            from .leakage import check_leakage
            from .util import load_lab_paths

            paths = load_lab_paths(self.trainer_root)
            preflight_leakage = check_leakage(
                workspace,
                phase,
                vault_roots=[Path(paths["reference_vault"]), Path(paths["exam_vault"])],
                vault_hash_index=Path(paths["vault_hash_index"]),
                strict_vaults=True,
                report_path=case_dir / "reports" / f"leakage-preflight-{phase}.json",
            )
            if preflight_leakage["status"] == "fail":
                raise SystemAutopilotError(f"{case_id}/{phase} 启动前泄漏硬门失败。")

        phase_lock = read_json(workspace / "phase-lock.json")
        skill_path = str(phase_lock.get("skill_path") or "")
        if not skill_path or not Path(skill_path).is_file():
            raise SystemAutopilotError(f"阶段 Skill 路径无效：{skill_path}")
        prompt = (
            f"开始前先完整读取并严格执行项目阶段 Skill：{skill_path}\n"
            "不得调用其他阶段 Skill；不得跨阶段 resume。\n\n"
            + prompt_path.read_text(encoding="utf-8-sig")
        )
        session = run_stage_session(
            workspace=workspace,
            run_root=run_dir,
            prompt=prompt,
            codex_home=self.codex_home,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            input_files=iter_regular_files(workspace),
            executable=self.codex_command,
        )
        if session.get("status") == "blocked":
            raise SystemAutopilotError(f"Codex 阶段被阻塞：{session.get('blocked_reason')}")
        session_dir = Path(str(session["run_dir"]))
        safety_message = _platform_safety_message(session_dir / "events.jsonl")
        if safety_message:
            metadata = read_json(session_dir / "run-metadata.json", {})
            write_json(
                run_dir / "platform-safety.json",
                {
                    "case_id": case_id,
                    "phase": phase,
                    "message": safety_message,
                    "session_run_id": session.get("session_run_id"),
                    "thread_id": metadata.get("thread_id"),
                    "model": metadata.get("model"),
                    "reasoning_effort": metadata.get("reasoning_effort"),
                    "at": metadata.get("finished_at"),
                },
            )
            raise PlatformSafetyBlock(
                safety_message,
                run_id=run_dir.name,
                thread_id=metadata.get("thread_id"),
            )
        usage_message = _usage_limit_message(session_dir)
        if usage_message:
            raise UsageLimitReached(usage_message, run_id=run_dir.name)
        if session.get("status") != "completed" or (
            session.get("exit_code") != 0 and session.get("terminal_recovered") is not True
        ):
            raise TransientPhaseError(f"Codex 阶段进程退出码为 {session.get('exit_code')}。")
        if not (session_dir / "final-message.md").is_file():
            raise CasePhaseError("Codex 未生成 final-message.md。")
        metadata = read_json(session_dir / "run-metadata.json", {})
        contract = metadata.get("actual_session_contract") or {}
        if (
            metadata.get("status") != "completed"
            or contract.get("model") != self.model
            or contract.get("reasoning_effort") != self.reasoning_effort
            or contract.get("fallback") is not False
            or contract.get("ephemeral") is not True
            or not contract.get("thread_id")
        ):
            raise SystemAutopilotError("实际模型、推理档位或独立 ephemeral 会话验证失败；禁止冻结。")
        write_json(
            case_dir / "logs" / f"{phase}-session.json",
            {
                "case_id": case_id,
                "phase": phase,
                "session_run_id": metadata.get("session_run_id"),
                "thread_id": contract.get("thread_id"),
                "model": contract.get("model"),
                "reasoning_effort": contract.get("reasoning_effort"),
                "fallback": contract.get("fallback"),
                "ephemeral": contract.get("ephemeral"),
                "started_at": metadata.get("started_at"),
                "finished_at": metadata.get("finished_at"),
            },
        )
        self._finalize(case_dir, case_id, phase)
        return PhaseResult(message="Codex 阶段与外部收尾均成功。", metadata=dict(session))
