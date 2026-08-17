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
    mark_phase_success,
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
