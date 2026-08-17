from __future__ import annotations

import json
import os

import pytest

from cumcm_lab.autopilot import (
    AlreadyRunningError,
    CasePhaseError,
    PhaseResult,
    StaleLockError,
    SystemAutopilotError,
    TransientPhaseError,
    acquire_autopilot_lock,
    autopilot_status,
    release_autopilot_lock,
    preflight_autopilot,
    request_autopilot_stop,
    resume_autopilot,
    run_autopilot,
)
from cumcm_lab.training_queue import TRAIN_PHASES, create_training_queue, load_training_queue


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, case_id, phase, attempt, run_dir):
        self.calls.append((case_id, phase, attempt, run_dir))
        (run_dir / "fake.txt").write_text("dummy only", encoding="utf-8")
        return PhaseResult(metadata={"fake": True})


def test_autopilot_completes_with_distinct_stage_runs(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A"], queue_path)
    executor = RecordingExecutor()
    result = run_autopilot(queue_path, runtime, executor)
    assert result["status"] == "completed"
    assert [(case, phase, attempt) for case, phase, attempt, _ in executor.calls] == [
        ("2003A", phase, 1) for phase in TRAIN_PHASES
    ]
    assert len({str(run_dir) for *_, run_dir in executor.calls}) == len(TRAIN_PHASES)
    assert load_training_queue(queue_path)["items"][0]["status"] == "completed"
    status = autopilot_status(runtime, queue_path)
    assert status["lock_present"] is False
    assert status["process_alive"] is False


def test_autopilot_retries_a_transient_stage_only_once(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A"], queue_path)
    calls = []

    def flaky(case_id, phase, attempt, run_dir):
        calls.append((phase, attempt))
        if phase == "solve" and attempt == 1:
            raise TransientPhaseError("temporary")
        return {"status": "pass"}

    result = run_autopilot(queue_path, runtime, flaky)
    assert result["status"] == "completed"
    assert calls[:2] == [("solve", 1), ("solve", 2)]
    assert load_training_queue(queue_path)["items"][0]["attempts"]["solve"] == 2


def test_case_failure_does_not_prevent_later_case_check(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A", "2004A"], queue_path)

    def executor(case_id, phase, attempt, run_dir):
        if case_id == "2003A":
            raise CasePhaseError("damaged current case")
        return None

    result = run_autopilot(queue_path, runtime, executor)
    queue = load_training_queue(queue_path)
    assert result["status"] == "completed_with_blocks"
    assert queue["items"][0]["status"] == "blocked"
    assert queue["items"][1]["status"] == "completed"


def test_system_failure_stops_entire_queue(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A", "2004A"], queue_path)

    def executor(case_id, phase, attempt, run_dir):
        raise SystemAutopilotError("leakage sentinel failed")

    with pytest.raises(SystemAutopilotError):
        run_autopilot(queue_path, runtime, executor)
    queue = load_training_queue(queue_path)
    assert queue["stop_requested"] is True
    assert queue["items"][1]["attempts"]["solve"] == 0
    assert autopilot_status(runtime, queue_path)["state"]["status"] == "failed"


def test_stop_and_resume_use_durable_checkpoint(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A"], queue_path)
    request_autopilot_stop(runtime, queue_path)
    executor = RecordingExecutor()
    stopped = run_autopilot(queue_path, runtime, executor)
    assert stopped["status"] == "stopped"
    assert executor.calls == []
    resumed = resume_autopilot(queue_path, runtime, executor)
    assert resumed["status"] == "completed"
    assert len(executor.calls) == len(TRAIN_PHASES)


def test_checkpoint_then_resume_next_case(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A", "2004A"], queue_path)
    executor = RecordingExecutor()
    first = run_autopilot(queue_path, runtime, executor, max_cases=1)
    assert first["status"] == "checkpointed"
    assert load_training_queue(queue_path)["items"][0]["status"] == "completed"
    second = resume_autopilot(queue_path, runtime, executor)
    assert second["status"] == "completed"
    assert load_training_queue(queue_path)["items"][1]["status"] == "completed"


def test_stop_after_phase_is_a_resumable_checkpoint(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A"], queue_path)
    executor = RecordingExecutor()
    first = run_autopilot(queue_path, runtime, executor, stop_after_phase="audit")
    assert first["status"] == "checkpointed"
    item = load_training_queue(queue_path)["items"][0]
    assert item["completed_phases"] == ["solve", "audit"]
    assert item["current_phase"] == "blind-revision"
    assert resume_autopilot(queue_path, runtime, executor)["status"] == "completed"


def test_lock_refuses_live_owner_and_resume_can_archive_stale_lock(tmp_path):
    runtime = tmp_path / "runtime"
    queue_path = tmp_path / "queue.json"
    create_training_queue(["2003A"], queue_path)
    lock = acquire_autopilot_lock(runtime, queue_path)
    with pytest.raises(AlreadyRunningError):
        acquire_autopilot_lock(runtime, queue_path)
    release_autopilot_lock(runtime, lock)

    runtime.mkdir(exist_ok=True)
    (runtime / "autopilot.lock").write_text(
        json.dumps({"pid": 99999999, "nonce": "dead", "queue_path": str(queue_path)}), encoding="utf-8"
    )
    with pytest.raises(StaleLockError):
        acquire_autopilot_lock(runtime, queue_path)
    recovered = acquire_autopilot_lock(runtime, queue_path, recover_stale=True)
    assert recovered["pid"] == os.getpid()
    assert list(runtime.glob("autopilot.lock.stale-*.json"))
    release_autopilot_lock(runtime, recovered)


def test_missing_dedicated_auth_records_blocker_without_consuming_attempt(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    create_training_queue(["2003A"], queue_path)
    result = preflight_autopilot(queue_path, runtime, codex_home)
    assert result["status"] == "blocked"
    assert result["blocker_kind"] == "codex_home_auth_missing"
    assert result["pid"] is None
    assert load_training_queue(queue_path)["items"][0]["attempts"]["solve"] == 0
    assert not (runtime / "autopilot.lock").exists()
