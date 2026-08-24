from __future__ import annotations

import pytest

from cumcm_lab.training_queue import (
    FinalTestExecutionDenied,
    QueueError,
    TRAIN_PHASES,
    begin_phase,
    create_training_queue,
    load_training_queue,
    mark_phase_failure,
    mark_phase_interrupted_quota,
    mark_phase_interrupted_user,
    mark_phase_success,
    recover_phase_success,
    recover_unstarted_phase,
    mark_case_deferred_platform_safety,
    next_runnable_item,
    queue_summary,
    set_stop_requested,
)


def test_queue_is_sorted_and_separate_from_case_state(tmp_path):
    path = tmp_path / "runtime" / "training-queue-state.json"
    queue = create_training_queue(["2005A", "2003A", "2004A"], path)
    assert [item["case_id"] for item in queue["items"]] == ["2003A", "2004A", "2005A"]
    assert all(item["split"] == "train" for item in queue["items"])
    assert not (tmp_path / "case-state.yaml").exists()
    assert queue_summary(queue)["counts"]["pending"] == 3


def test_queue_creation_hard_rejects_2023_and_non_training_years(tmp_path):
    with pytest.raises(FinalTestExecutionDenied):
        create_training_queue(["2003A", "2023A"], tmp_path / "q.json")
    with pytest.raises(QueueError):
        create_training_queue(["2022A"], tmp_path / "q.json")
    with pytest.raises(QueueError):
        create_training_queue(["2002A"], tmp_path / "q.json")


def test_queue_rejects_duplicates_and_nondefault_retry_policy(tmp_path):
    with pytest.raises(QueueError, match="重复"):
        create_training_queue(["2003A", "2003A"], tmp_path / "q.json")
    with pytest.raises(QueueError, match="max_retries=1"):
        create_training_queue(["2003A"], tmp_path / "q.json", max_retries=2)


def test_phase_progress_is_an_ordered_queue_layer(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2003A"], path)
    for expected_phase in TRAIN_PHASES:
        current = next_runnable_item(load_training_queue(path))
        assert current is not None and current["current_phase"] == expected_phase
        _, attempt = begin_phase(path, "2003A")
        assert attempt == 1
        mark_phase_success(path, "2003A", expected_phase)
    item = load_training_queue(path)["items"][0]
    assert item["status"] == "completed"
    assert item["current_phase"] is None
    assert item["completed_phases"] == list(TRAIN_PHASES)


def test_transient_error_retries_once_then_blocks(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2003A"], path)
    _, first = begin_phase(path, "2003A")
    assert first == 1
    item = mark_phase_failure(path, "2003A", "solve", "temporary", transient=True)
    assert item["status"] == "pending"
    _, second = begin_phase(path, "2003A")
    assert second == 2
    item = mark_phase_failure(path, "2003A", "solve", "temporary again", transient=True)
    assert item["status"] == "blocked"
    assert item["blocked_reason"] == "retry_exhausted"
    assert next_runnable_item(load_training_queue(path)) is None


def test_retry_exhausted_phase_can_be_advanced_by_evidence_backed_recovery(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2005A"], path)
    begin_phase(path, "2005A")
    mark_phase_failure(path, "2005A", "solve", "finish hang", transient=True)
    begin_phase(path, "2005A")
    blocked = mark_phase_failure(path, "2005A", "solve", "finish hang", transient=True)
    assert blocked["status"] == "blocked"

    recovered = recover_phase_success(
        path,
        "2005A",
        "solve",
        evidence_id="reports/solve-recovery.json",
    )
    assert recovered["status"] == "pending"
    assert recovered["current_phase"] == "audit"
    assert recovered["completed_phases"] == ["solve"]
    assert recovered["attempts"]["solve"] == 2
    assert recovered["recovered_phase"]["attempt_count_preserved"] == 2


def test_interrupted_running_phase_can_be_advanced_without_new_attempt(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2005A"], path)
    begin_phase(path, "2005A")
    recovered = recover_phase_success(
        path,
        "2005A",
        "solve",
        evidence_id="run/model-completed-before-user-stop.json",
    )
    assert recovered["status"] == "pending"
    assert recovered["current_phase"] == "audit"
    assert recovered["attempts"]["solve"] == 1


def test_pending_retry_phase_can_be_advanced_without_new_attempt(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2005A"], path)
    begin_phase(path, "2005A")
    pending = mark_phase_failure(path, "2005A", "solve", "local gate false negative", transient=True)
    assert pending["status"] == "pending"

    recovered = recover_phase_success(
        path,
        "2005A",
        "solve",
        evidence_id="reports/solve-recovery.json",
    )

    assert recovered["status"] == "pending"
    assert recovered["current_phase"] == "audit"
    assert recovered["completed_phases"] == ["solve"]
    assert recovered["attempts"]["solve"] == 1


def test_quota_interruption_is_resumable_without_spending_retry(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2005A"], path)
    begin_phase(path, "2005A")
    item = mark_phase_interrupted_quota(
        path,
        "2005A",
        "solve",
        message="usage limit reached",
        run_id="run-quota",
    )
    assert item["status"] == "pending"
    assert item["blocked_reason"] == "resumable_after_quota_reset"
    assert item["attempts"]["solve"] == 0
    resumed, next_attempt = begin_phase(path, "2005A")
    assert next_attempt == 1
    assert resumed["blocked_reason"] is None


def test_user_interruption_is_resumable_without_spending_retry(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2005A"], path)
    begin_phase(path, "2005A")
    item = mark_phase_interrupted_user(
        path,
        "2005A",
        "solve",
        message="user clicked stop",
        run_id="run-user-stop",
        evidence_id="reports/interrupted-user.json",
    )
    assert item["status"] == "pending"
    assert item["blocked_reason"] == "interrupted_user"
    assert item["attempts"]["solve"] == 0
    assert item["user_interruptions"][-1]["evidence_id"] == "reports/interrupted-user.json"
    set_stop_requested(path, False)
    resumed, next_attempt = begin_phase(path, "2005A")
    assert next_attempt == 1
    assert resumed["blocked_reason"] is None


def test_prestart_infrastructure_recovery_rolls_back_attempt_accounting(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2005A"], path)
    begin_phase(path, "2005A")
    item = recover_unstarted_phase(
        path,
        "2005A",
        "solve",
        evidence_id="runs/prestart-error.json",
    )
    assert item["status"] == "pending"
    assert item["attempts"]["solve"] == 0
    assert item["prestart_recovery"]["attempt_count_preserved"] == 0


def test_case_error_blocks_only_that_item_and_stop_is_durable(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2003A", "2004A"], path)
    begin_phase(path, "2003A")
    mark_phase_failure(path, "2003A", "solve", "broken source", transient=False)
    assert next_runnable_item(load_training_queue(path))["case_id"] == "2004A"
    set_stop_requested(path, True)
    stopped = load_training_queue(path)
    assert stopped["stop_requested"] is True
    assert next_runnable_item(stopped) is None
    set_stop_requested(path, False)
    assert next_runnable_item(load_training_queue(path))["case_id"] == "2004A"


def test_platform_safety_defer_is_not_completed_and_next_year_is_runnable(tmp_path):
    path = tmp_path / "q.json"
    create_training_queue(["2003A", "2004A"], path)
    begin_phase(path, "2003A")
    item = mark_case_deferred_platform_safety(
        path,
        "2003A",
        error="classifier blocked",
        run_id="run-1",
        thread_id="thread-1",
    )
    assert item["status"] == "deferred_platform_safety"
    assert item["lifecycle_status"] == "deferred_platform_safety"
    assert item["completed_phases"] == []
    assert item["deferred_details"]["consumed_as_training_result"] is False
    assert next_runnable_item(load_training_queue(path))["case_id"] == "2004A"
