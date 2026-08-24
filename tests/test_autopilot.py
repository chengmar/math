from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cumcm_lab.autopilot import (
    AlreadyRunningError,
    CodexPhaseExecutor,
    CasePhaseError,
    PhaseResult,
    PlatformSafetyBlock,
    StaleLockError,
    SystemAutopilotError,
    TransientPhaseError,
    UsageLimitReached,
    acquire_autopilot_lock,
    autopilot_status,
    release_autopilot_lock,
    preflight_autopilot,
    request_autopilot_stop,
    resume_autopilot,
    run_autopilot,
    _candidate_knowledge_statuses,
    _validate_candidate_proposals,
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


def test_single_case_limit_checkpoints_after_case_failure(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A", "2004A"], queue_path)

    def executor(case_id, phase, attempt, run_dir):
        raise CasePhaseError("current case failed")

    result = run_autopilot(queue_path, runtime, executor, max_cases=1)
    queue = load_training_queue(queue_path)
    assert result["status"] == "checkpointed"
    assert queue["items"][0]["status"] == "blocked"
    assert queue["items"][1]["status"] == "pending"
    assert queue["items"][1]["attempts"]["solve"] == 0


def test_single_content_safety_block_defers_case_and_continues(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A", "2004A"], queue_path)

    def executor(case_id, phase, attempt, run_dir):
        if case_id == "2003A":
            raise PlatformSafetyBlock("biology classifier", run_id=run_dir.name, thread_id="thread-2003")
        return None

    result = run_autopilot(queue_path, runtime, executor)
    queue = load_training_queue(queue_path)
    assert result["status"] == "completed_with_blocks"
    assert queue["items"][0]["status"] == "deferred_platform_safety"
    assert queue["items"][0]["completed_phases"] == []
    assert queue["items"][1]["status"] == "completed"


def test_consecutive_content_safety_blocks_stop_the_queue(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A", "2004A", "2005A"], queue_path)

    def executor(case_id, phase, attempt, run_dir):
        raise PlatformSafetyBlock("biology classifier", run_id=run_dir.name, thread_id=f"thread-{case_id}")

    with pytest.raises(SystemAutopilotError, match="连续年份"):
        run_autopilot(queue_path, runtime, executor)
    queue = load_training_queue(queue_path)
    assert queue["stop_requested"] is True
    assert queue["items"][0]["status"] == "deferred_platform_safety"
    assert queue["items"][1]["status"] == "deferred_platform_safety"
    assert queue["items"][2]["attempts"]["solve"] == 0


def test_completed_year_breaks_content_safety_streak(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2003A", "2004A", "2005A", "2006A"], queue_path)

    def executor(case_id, phase, attempt, run_dir):
        if case_id in {"2003A", "2005A"}:
            raise PlatformSafetyBlock("biology classifier", run_id=run_dir.name, thread_id=f"thread-{case_id}")
        return None

    result = run_autopilot(queue_path, runtime, executor)
    queue = load_training_queue(queue_path)
    assert result["status"] == "completed_with_blocks"
    assert [item["status"] for item in queue["items"]] == [
        "deferred_platform_safety",
        "completed",
        "deferred_platform_safety",
        "completed",
    ]


def test_usage_limit_stops_with_resumable_atomic_checkpoint(tmp_path):
    queue_path = tmp_path / "queue.json"
    runtime = tmp_path / "runtime"
    create_training_queue(["2005A", "2006A"], queue_path)

    def executor(case_id, phase, attempt, run_dir):
        raise UsageLimitReached("usage limit resets tomorrow", run_id=run_dir.name)

    result = run_autopilot(queue_path, runtime, executor)
    queue = load_training_queue(queue_path)
    assert result["status"] == "resumable_after_quota_reset"
    assert queue["stop_requested"] is True
    assert queue["items"][0]["status"] == "pending"
    assert queue["items"][0]["current_phase"] == "solve"
    assert queue["items"][0]["attempts"]["solve"] == 0
    assert queue["items"][1]["attempts"]["solve"] == 0
    assert not (runtime / "autopilot.lock").exists()


def test_nested_candidate_statuses_ignore_evidence_pass_needs_review():
    payload = {
        "cards": [
            {"knowledge_status": "candidate", "checks": [{"status": "pass"}]},
            {"knowledge_status": "candidate", "checks": [{"status": "needs_review"}]},
        ]
    }
    assert _candidate_knowledge_statuses(payload) == ["candidate", "candidate"]


def test_candidate_status_parser_accepts_reflection_skill_collections():
    payload = {
        "case_id": "2010A",
        "lessons": [{"id": "EL-1", "status": "candidate"}],
        "failure_modes": [{"id": "FM-1", "status": "candidate"}],
        "patterns": [{"id": "VP-1", "status": "candidate"}],
        "checks": [{"status": "pass"}],
    }

    assert _candidate_knowledge_statuses(payload) == [
        "candidate",
        "candidate",
        "candidate",
    ]


def test_yaml_card_containers_are_counted_without_treating_evidence_as_lifecycle(tmp_path):
    lesson_root = tmp_path / "lessons-proposed"
    lesson_root.mkdir()
    (lesson_root / "method-cards.yaml").write_text(
        "case_id: 2008A\n"
        "package_status: candidate\n"
        "method_cards:\n"
        "  - id: METHOD-1\n"
        "    status: candidate\n"
        "    evidence_status: pass\n"
        "  - id: METHOD-2\n"
        "    status: candidate\n"
        "    evidence_status: needs_review\n",
        encoding="utf-8",
    )

    assert _validate_candidate_proposals(lesson_root, "2008A") == {
        "files": 1,
        "candidate_count": 2,
        "invalid": [],
    }


def test_yaml_card_container_rejects_wrong_case_or_non_candidate_card(tmp_path):
    lesson_root = tmp_path / "lessons-proposed"
    lesson_root.mkdir()
    (lesson_root / "cards.yaml").write_text(
        "case_id: 2007A\n"
        "package_status: candidate\n"
        "cards:\n"
        "  - id: CARD-1\n"
        "    status: verified\n",
        encoding="utf-8",
    )

    result = _validate_candidate_proposals(lesson_root, "2008A")

    assert result["candidate_count"] == 1
    assert result["invalid"] == ["cards.yaml"]


def test_yaml_card_container_accepts_state_alias(tmp_path):
    lesson_root = tmp_path / "lessons-proposed"
    lesson_root.mkdir()
    (lesson_root / "cards.yaml").write_text(
        "case_id: 2009A\n"
        "collection_state: candidate\n"
        "cards:\n"
        "  - id: CARD-1\n"
        "    state: candidate\n"
        "  - id: CARD-2\n"
        "    state: candidate\n",
        encoding="utf-8",
    )

    assert _validate_candidate_proposals(lesson_root, "2009A") == {
        "files": 1,
        "candidate_count": 2,
        "invalid": [],
    }


def test_indexed_markdown_candidate_cards_are_validated(tmp_path):
    lesson_root = tmp_path / "lessons-proposed"
    lesson_root.mkdir()
    (lesson_root / "index.yaml").write_text(
        "cards:\n  - id: 2005A-CARD-1\n    path: card-1.md\n    status: candidate\n",
        encoding="utf-8",
    )
    (lesson_root / "card-1.md").write_text(
        "---\nid: 2005A-CARD-1\nstatus: candidate\ncase_id: 2005A\n---\n\n# Card\n",
        encoding="utf-8",
    )
    assert _validate_candidate_proposals(lesson_root, "2005A") == {
        "files": 2,
        "candidate_count": 1,
        "invalid": [],
    }


def test_indexed_markdown_candidate_cards_reject_escape(tmp_path):
    lesson_root = tmp_path / "lessons-proposed"
    lesson_root.mkdir()
    (tmp_path / "outside.md").write_text("---\nstatus: candidate\n---\n", encoding="utf-8")
    (lesson_root / "index.yaml").write_text(
        "cards:\n  - id: escaped\n    path: ../outside.md\n    status: candidate\n",
        encoding="utf-8",
    )
    result = _validate_candidate_proposals(lesson_root, "2005A")
    assert result["candidate_count"] == 1
    assert result["invalid"] == ["index.yaml:cards[1]:path"]


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


def test_compile_paper_uses_temporary_copy_and_preserves_source_pdf(tmp_path, monkeypatch):
    trainer_root = tmp_path / "trainer"
    codex_home = tmp_path / "codex-home"
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    for directory in (trainer_root, codex_home, case_dir / "reports", workspace / "paper"):
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / "paper" / "main.tex").write_text("document", encoding="utf-8")
    source_pdf = workspace / "paper" / "main.pdf"
    source_pdf.write_bytes(b"original-pdf")

    def fake_run(command, *, cwd, **kwargs):
        del command, kwargs
        Path(cwd, "main.pdf").write_bytes(b"verified-pdf")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("cumcm_lab.autopilot.shutil.which", lambda _: "xelatex")
    monkeypatch.setattr("cumcm_lab.autopilot.subprocess.run", fake_run)
    executor = CodexPhaseExecutor(trainer_root, codex_home)

    report = executor._compile_paper(case_dir, workspace, "test")

    assert report["status"] == "pass"
    assert report["source_pdf_preserved"] is True
    assert source_pdf.read_bytes() == b"original-pdf"
