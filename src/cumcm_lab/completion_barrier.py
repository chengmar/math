from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .util import now_iso, read_json, write_json


BARRIER_SCHEMA_VERSION = 1
BARRIER_FILE_NAME = "case-completion-barrier.json"
TERMINAL_CASE_STATUSES = {
    "completed",
    "completed_with_caveats",
    "deferred_platform_safety",
}
REQUIRED_COMPLETION_CHECKS = (
    "blind_v1_frozen",
    "audit_completed",
    "blind_revision_completed",
    "revision_verification_completed",
    "blind_final_frozen",
    "blind_score_recorded",
    "reflection_completed",
    "reflection_references_cleaned",
    "candidate_updated",
    "training_memory_updated",
    "tests_passed",
    "regression_passed",
    "no_active_local_processes",
)
DEFERRED_COMPLETION_CHECKS = (
    "deferred_recorded",
    "reference_opened_false",
    "no_active_local_processes",
)


class CaseCompletionBarrierError(RuntimeError):
    """Raised when a later case would cross an unfinished case boundary."""


def barrier_path(runtime_dir: Path) -> Path:
    return Path(runtime_dir) / BARRIER_FILE_NAME


def load_case_completion_barrier(runtime_dir: Path) -> dict[str, Any] | None:
    path = barrier_path(runtime_dir)
    if not path.is_file():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != BARRIER_SCHEMA_VERSION:
        raise CaseCompletionBarrierError("案例完成屏障文件 schema 无效。")
    if payload.get("case_completion_barrier") not in {"locked", "released"}:
        raise CaseCompletionBarrierError("案例完成屏障状态无效。")
    allowed = payload.get("next_case_dispatch_allowed")
    if allowed is not (payload.get("case_completion_barrier") == "released"):
        raise CaseCompletionBarrierError("案例完成屏障与下一案例放行状态不一致。")
    if not payload.get("case_id"):
        raise CaseCompletionBarrierError("案例完成屏障缺少 case_id。")
    return payload


def lock_case_completion_barrier(
    runtime_dir: Path,
    case_id: str,
    *,
    writer_nonce: str,
) -> dict[str, Any]:
    """Create or retain the durable per-case barrier.

    A released earlier case may be replaced by the next case.  A locked barrier
    may only be re-entered for the same case and never while another formal
    write is recorded as active.
    """

    if not writer_nonce:
        raise CaseCompletionBarrierError("案例完成屏障需要非空 writer nonce。")
    existing = load_case_completion_barrier(runtime_dir)
    if existing is not None:
        if existing["case_completion_barrier"] == "locked":
            if existing["case_id"] != case_id:
                raise CaseCompletionBarrierError(
                    f"{existing['case_id']} 完成屏障仍锁定，禁止启动 {case_id}。"
                )
            if existing.get("active_formal_write"):
                raise CaseCompletionBarrierError(
                    f"{case_id} 已有活动正式写入，禁止启动第二个模型 Thread 或写入者。"
                )
            existing["writer_nonce"] = writer_nonce
            existing["recovered_at"] = now_iso()
            existing["updated_at"] = now_iso()
            write_json(barrier_path(runtime_dir), existing)
            return existing
        if existing["case_id"] == case_id:
            raise CaseCompletionBarrierError(f"{case_id} 完成屏障已释放，禁止重新启动正式阶段。")

    payload = {
        "schema_version": BARRIER_SCHEMA_VERSION,
        "case_id": case_id,
        "case_completion_barrier": "locked",
        "next_case_dispatch_allowed": False,
        "writer_nonce": writer_nonce,
        "active_formal_write": None,
        "completion_evidence": None,
        "locked_at": now_iso(),
        "updated_at": now_iso(),
    }
    write_json(barrier_path(runtime_dir), payload)
    return payload


def begin_formal_write(
    runtime_dir: Path,
    case_id: str,
    *,
    phase: str,
    run_id: str,
    writer_nonce: str,
    writer_pid: int,
) -> dict[str, Any]:
    barrier = load_case_completion_barrier(runtime_dir)
    if barrier is None or barrier.get("case_id") != case_id or barrier.get("case_completion_barrier") != "locked":
        raise CaseCompletionBarrierError(f"{case_id} 没有可用的锁定完成屏障。")
    if barrier.get("active_formal_write"):
        raise CaseCompletionBarrierError(
            f"{case_id} 已有活动模型 Thread 或正式阶段写入。"
        )
    barrier["writer_nonce"] = writer_nonce
    barrier["active_formal_write"] = {
        "case_id": case_id,
        "phase": phase,
        "run_id": run_id,
        "writer_nonce": writer_nonce,
        "writer_pid": int(writer_pid),
        "started_at": now_iso(),
    }
    barrier["updated_at"] = now_iso()
    write_json(barrier_path(runtime_dir), barrier)
    return barrier


def finish_formal_write(
    runtime_dir: Path,
    case_id: str,
    *,
    run_id: str,
    writer_nonce: str,
) -> dict[str, Any]:
    barrier = load_case_completion_barrier(runtime_dir)
    if barrier is None or barrier.get("case_id") != case_id:
        raise CaseCompletionBarrierError(f"{case_id} 完成屏障不存在或属于其他案例。")
    active = barrier.get("active_formal_write")
    if not isinstance(active, dict):
        return barrier
    if active.get("run_id") != run_id or active.get("writer_nonce") != writer_nonce:
        raise CaseCompletionBarrierError("正式写入结束标识与活动写入不一致。")
    barrier["active_formal_write"] = None
    barrier["last_formal_write"] = {
        **active,
        "finished_at": now_iso(),
    }
    barrier["updated_at"] = now_iso()
    write_json(barrier_path(runtime_dir), barrier)
    return barrier


def release_case_completion_barrier(
    runtime_dir: Path,
    case_id: str,
    *,
    terminal_status: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    barrier = load_case_completion_barrier(runtime_dir)
    if barrier is None or barrier.get("case_id") != case_id:
        raise CaseCompletionBarrierError(f"{case_id} 完成屏障不存在或属于其他案例。")
    if barrier.get("case_completion_barrier") == "released":
        return barrier
    if barrier.get("active_formal_write"):
        raise CaseCompletionBarrierError("仍有活动模型 Thread 或正式阶段写入，不能释放案例屏障。")
    if terminal_status not in TERMINAL_CASE_STATUSES:
        raise CaseCompletionBarrierError(f"案例终态不允许放行下一年份：{terminal_status}")
    required = DEFERRED_COMPLETION_CHECKS if terminal_status == "deferred_platform_safety" else REQUIRED_COMPLETION_CHECKS
    failed = [name for name in required if evidence.get(name) is not True]
    if failed:
        raise CaseCompletionBarrierError(f"案例完成证据不完整，屏障保持锁定：{', '.join(failed)}")
    barrier["case_completion_barrier"] = "released"
    barrier["next_case_dispatch_allowed"] = True
    barrier["terminal_status"] = terminal_status
    barrier["completion_evidence"] = dict(evidence)
    barrier["released_at"] = now_iso()
    barrier["updated_at"] = now_iso()
    write_json(barrier_path(runtime_dir), barrier)
    return barrier


def assert_case_dispatch_allowed(runtime_dir: Path, case_id: str) -> None:
    """Reject a different case while the durable prior-case barrier is locked."""

    barrier = load_case_completion_barrier(runtime_dir)
    if barrier is None:
        return
    if barrier["case_completion_barrier"] == "locked" and barrier["case_id"] != case_id:
        raise CaseCompletionBarrierError(
            f"{barrier['case_id']} 的本地门禁尚未原子完成，禁止启动 {case_id}。"
        )
    if barrier["case_completion_barrier"] == "locked" and barrier.get("active_formal_write"):
        raise CaseCompletionBarrierError(
            f"{barrier['case_id']} 已有活动模型 Thread 或正式写入。"
        )
