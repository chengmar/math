from __future__ import annotations

import pytest

from cumcm_lab.autopilot import request_autopilot_stop
from cumcm_lab.completion_barrier import (
    CaseCompletionBarrierError,
    REQUIRED_COMPLETION_CHECKS,
    assert_case_dispatch_allowed,
    begin_formal_write,
    finish_formal_write,
    load_case_completion_barrier,
    lock_case_completion_barrier,
    release_case_completion_barrier,
)
from cumcm_lab.training_queue import create_training_queue


def complete_evidence() -> dict[str, bool]:
    return {name: True for name in REQUIRED_COMPLETION_CHECKS}


def locked_barrier(tmp_path, case_id: str = "2016A"):
    runtime = tmp_path / "runtime"
    lock_case_completion_barrier(runtime, case_id, writer_nonce="writer-1")
    return runtime


def test_active_local_reproduction_blocks_next_case(tmp_path):
    runtime = locked_barrier(tmp_path)
    begin_formal_write(
        runtime,
        "2016A",
        phase="local-reproduction",
        run_id="reproduce-1",
        writer_nonce="writer-1",
        writer_pid=101,
    )
    with pytest.raises(CaseCompletionBarrierError, match="禁止启动 2017A"):
        assert_case_dispatch_allowed(runtime, "2017A")
    with pytest.raises(CaseCompletionBarrierError, match="活动模型 Thread"):
        release_case_completion_barrier(
            runtime,
            "2016A",
            terminal_status="completed",
            evidence=complete_evidence(),
        )


def test_missing_blind_final_keeps_barrier_locked(tmp_path):
    runtime = locked_barrier(tmp_path)
    evidence = complete_evidence()
    evidence["blind_final_frozen"] = False
    with pytest.raises(CaseCompletionBarrierError, match="blind_final_frozen"):
        release_case_completion_barrier(runtime, "2016A", terminal_status="completed", evidence=evidence)
    assert load_case_completion_barrier(runtime)["case_completion_barrier"] == "locked"


def test_incomplete_reflection_keeps_barrier_locked(tmp_path):
    runtime = locked_barrier(tmp_path)
    evidence = complete_evidence()
    evidence["reflection_completed"] = False
    with pytest.raises(CaseCompletionBarrierError, match="reflection_completed"):
        release_case_completion_barrier(runtime, "2016A", terminal_status="completed", evidence=evidence)


def test_missing_training_memory_update_keeps_barrier_locked(tmp_path):
    runtime = locked_barrier(tmp_path)
    evidence = complete_evidence()
    evidence["training_memory_updated"] = False
    with pytest.raises(CaseCompletionBarrierError, match="training_memory_updated"):
        release_case_completion_barrier(runtime, "2016A", terminal_status="completed", evidence=evidence)


def test_completed_with_caveats_releases_next_case(tmp_path):
    runtime = locked_barrier(tmp_path)
    released = release_case_completion_barrier(
        runtime,
        "2016A",
        terminal_status="completed_with_caveats",
        evidence=complete_evidence(),
    )
    assert released["next_case_dispatch_allowed"] is True
    assert_case_dispatch_allowed(runtime, "2017A")


def test_deferred_platform_safety_releases_without_formal_artifacts(tmp_path):
    runtime = locked_barrier(tmp_path, "2003A")
    released = release_case_completion_barrier(
        runtime,
        "2003A",
        terminal_status="deferred_platform_safety",
        evidence={
            "deferred_recorded": True,
            "reference_opened_false": True,
            "no_active_local_processes": True,
        },
    )
    assert released["case_completion_barrier"] == "released"
    assert_case_dispatch_allowed(runtime, "2004A")


def test_two_years_cannot_hold_active_model_threads(tmp_path):
    runtime = locked_barrier(tmp_path)
    begin_formal_write(
        runtime,
        "2016A",
        phase="reflection",
        run_id="thread-2016",
        writer_nonce="writer-1",
        writer_pid=101,
    )
    with pytest.raises(CaseCompletionBarrierError):
        begin_formal_write(
            runtime,
            "2017A",
            phase="solve",
            run_id="thread-2017",
            writer_nonce="writer-2",
            writer_pid=202,
        )


def test_two_formal_writers_cannot_enter_same_case(tmp_path):
    runtime = locked_barrier(tmp_path)
    begin_formal_write(
        runtime,
        "2016A",
        phase="reflection",
        run_id="run-1",
        writer_nonce="writer-1",
        writer_pid=101,
    )
    with pytest.raises(CaseCompletionBarrierError, match="第二个模型 Thread"):
        lock_case_completion_barrier(runtime, "2016A", writer_nonce="writer-2")


def test_user_stop_does_not_release_case_barrier(tmp_path):
    runtime = locked_barrier(tmp_path)
    queue_path = tmp_path / "queue.json"
    create_training_queue(["2016A", "2017A"], queue_path)
    request_autopilot_stop(runtime, queue_path)
    barrier = load_case_completion_barrier(runtime)
    assert barrier["case_completion_barrier"] == "locked"
    assert barrier["next_case_dispatch_allowed"] is False


def test_restart_preserves_locked_barrier_until_explicit_release(tmp_path):
    runtime = locked_barrier(tmp_path)
    reloaded = load_case_completion_barrier(runtime)
    assert reloaded["case_id"] == "2016A"
    assert reloaded["case_completion_barrier"] == "locked"
    with pytest.raises(CaseCompletionBarrierError, match="禁止启动 2017A"):
        assert_case_dispatch_allowed(runtime, "2017A")
    lock_case_completion_barrier(runtime, "2016A", writer_nonce="writer-after-restart")
    assert load_case_completion_barrier(runtime)["writer_nonce"] == "writer-after-restart"


def test_finished_formal_write_still_requires_all_completion_evidence(tmp_path):
    runtime = locked_barrier(tmp_path)
    begin_formal_write(
        runtime,
        "2016A",
        phase="reflection",
        run_id="run-1",
        writer_nonce="writer-1",
        writer_pid=101,
    )
    finish_formal_write(runtime, "2016A", run_id="run-1", writer_nonce="writer-1")
    evidence = complete_evidence()
    evidence["tests_passed"] = False
    with pytest.raises(CaseCompletionBarrierError, match="tests_passed"):
        release_case_completion_barrier(runtime, "2016A", terminal_status="completed", evidence=evidence)
