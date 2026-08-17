from __future__ import annotations

import ctypes
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .training_queue import (
    FINAL_TEST_CASE_ID,
    TRAIN_PHASES,
    FinalTestExecutionDenied,
    assert_training_case,
    begin_phase,
    load_training_queue,
    mark_phase_failure,
    mark_phase_success,
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
            item = next_runnable_item(queue)
            if item is None:
                summary = queue_summary(queue)
                final_status = "completed_with_blocks" if summary["counts"]["blocked"] else "completed"
                return _write_state(runtime_dir, status=final_status, queue=summary, finished_at=now_iso())

            case_id = str(item["case_id"])
            # Second, dispatch-time hard guard against a hand-edited queue.
            if case_id == FINAL_TEST_CASE_ID:
                raise FinalTestExecutionDenied("Autopilot 永远禁止执行 2023A。")
            assert_training_case(case_id)
            phase = str(item["current_phase"])
            running_item, attempt = begin_phase(queue_path, case_id)
            run_id = f"{case_id}-{phase}-{attempt}-{uuid.uuid4().hex[:12]}"
            run_dir = paths["runs"] / case_id / phase / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
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
                phase_result = _invoke_executor(executor, case_id, phase, attempt, run_dir)
                write_json(run_dir / "executor-result.json", asdict(phase_result))
                updated = mark_phase_success(queue_path, case_id, phase)
                if updated["status"] == "completed":
                    completed_cases += 1
                _write_state(runtime_dir, last_completed={"case_id": case_id, "phase": phase, "run_id": run_id})
            except TransientPhaseError as exc:
                updated = mark_phase_failure(queue_path, case_id, phase, str(exc), transient=True)
                write_json(run_dir / "executor-error.json", {"kind": "transient", "message": str(exc), "at": now_iso()})
                _write_state(runtime_dir, last_error={"kind": "transient", "case_id": case_id, "phase": phase, "message": str(exc)})
                continue
            except CasePhaseError as exc:
                mark_phase_failure(queue_path, case_id, phase, str(exc), transient=False)
                write_json(run_dir / "executor-error.json", {"kind": "case", "message": str(exc), "at": now_iso()})
                _write_state(runtime_dir, last_error={"kind": "case", "case_id": case_id, "phase": phase, "message": str(exc)})
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
        model: str = "gpt-5.4",
        reasoning_effort: str = "xhigh",
    ) -> None:
        self.trainer_root = Path(trainer_root).resolve()
        self.codex_home = Path(codex_home).resolve()
        self.codex_command = codex_command
        self.model = model
        self.reasoning_effort = reasoning_effort

    def _prepare_or_reuse(self, case_id: str, phase: str) -> tuple[Path, Path, bool]:
        from .cases import find_case
        from .phases import prepare_phase
        from .state import load_state

        case_dir = find_case(self.trainer_root, case_id)
        state = load_state(case_dir)["state"]
        workspace = case_dir / "workspaces" / phase
        if state in self.COMPLETED_STATES[phase]:
            return case_dir, workspace, True
        if state == self.PREREQUISITES[phase]:
            workspace = prepare_phase(self.trainer_root, case_id, phase)
        elif state not in self.READY_STATES[phase]:
            raise SystemAutopilotError(f"队列与案例状态不一致：{case_id}/{phase}: {state}")
        if not (workspace / "phase-lock.json").is_file():
            raise SystemAutopilotError(f"阶段工作区缺少 phase-lock.json：{workspace}")
        return case_dir, workspace, False

    def _finalize(self, case_dir: Path, case_id: str, phase: str) -> None:
        from .freeze import freeze_solution, verify_frozen
        from .phases import complete_phase

        if phase == "solve":
            freeze_solution(case_dir, "blind-v1")
            if verify_frozen(case_dir, "blind-v1")["status"] != "pass":
                raise SystemAutopilotError(f"blind-v1 冻结校验失败：{case_id}")
        elif phase == "audit":
            complete_phase(self.trainer_root, case_id, "audit")
        elif phase == "blind-revision":
            freeze_solution(case_dir, "blind-final")
            if verify_frozen(case_dir, "blind-final")["status"] != "pass":
                raise SystemAutopilotError(f"blind-final 冻结校验失败：{case_id}")
        elif phase == "reflection":
            complete_phase(self.trainer_root, case_id, "reflection")
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
        if already_complete:
            return PhaseResult(message="案例状态表明该阶段已完成；按幂等恢复跳过。")

        prompt = prompt_path.read_text(encoding="utf-8-sig")
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
        if session.get("status") != "completed" or session.get("exit_code") != 0:
            raise TransientPhaseError(f"Codex 阶段进程退出码为 {session.get('exit_code')}。")
        session_dir = Path(str(session["run_dir"]))
        if not (session_dir / "final-message.md").is_file():
            raise CasePhaseError("Codex 未生成 final-message.md。")
        self._finalize(case_dir, case_id, phase)
        return PhaseResult(message="Codex 阶段与外部收尾均成功。", metadata=dict(session))
