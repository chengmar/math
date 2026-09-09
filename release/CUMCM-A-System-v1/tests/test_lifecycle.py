from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cumcm_system.core import SystemError, _fake_stage, add_references, case_status, export_submission, freeze_tree, new_case, pause_case, read_json, resume_case, run_case, set_mode, verify_freeze, write_json


def make_system(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / "system"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns("user-cases", "*.pyc", "__pycache__", ".pytest_cache"))
    return root


def test_dummy_full_lifecycle_pause_resume_gate_and_export(tmp_path: Path) -> None:
    root = make_system(tmp_path)
    demo = root / "demo" / "Dummy-A" / "input"
    state = new_case(root, "dummy-lifecycle", [demo / "problem.txt"], [demo / "data.csv"])
    assert state["state"] == "initialized"

    with pytest.raises(SystemError, match="Blind Final"):
        add_references(root, "dummy-lifecycle", [demo / "reference-1.txt", demo / "reference-2.txt"], "fake")

    state = run_case(root, "dummy-lifecycle", "fake", max_stages=1)
    assert state["state"] == "blind_v1_frozen"
    assert verify_freeze(root / "user-cases" / "dummy-lifecycle" / "frozen" / "blind-v1", root / "user-cases" / "dummy-lifecycle" / "frozen" / "FROZEN_BLIND_V1.json")["status"] == "pass"

    state = pause_case(root, "dummy-lifecycle")
    assert state["paused"] is True
    state = resume_case(root, "dummy-lifecycle", "fake")
    assert state["state"] == "blind_final_frozen"
    assert state["paused"] is False
    deterministic = read_json(root / "user-cases" / "dummy-lifecycle" / "reports" / "deterministic-revision-verification.json")
    assert deterministic["status"] == "pass"
    assert deterministic["additional_model_calls"] == 0

    state = add_references(root, "dummy-lifecycle", [demo / "reference-1.txt", demo / "reference-2.txt"], "fake")
    assert state["state"] == "reflected"
    assert state["reference_accessed"] is True
    reflection = read_json(root / "user-cases" / "dummy-lifecycle" / "reports" / "reference-reflection.json")
    assert reflection["thread_id"] == "fake-reflection-thread"
    assert reflection["blind_final_immutable"] is True

    exported = export_submission(root, "dummy-lifecycle", tmp_path / "exported")
    assert exported["status"] == "pass"
    assert exported["freeze"]["status"] == "pass"
    assert not (root / "user-cases" / "dummy-lifecycle" / "run.lock").exists()
    assert not (root / "user-cases" / "dummy-lifecycle" / "pause.requested").exists()


def test_modes_are_user_selectable(tmp_path: Path) -> None:
    root = make_system(tmp_path)
    assert set_mode(root, "full-trained")["mode"] == "full-trained"
    assert set_mode(root, "workflow-only")["mode"] == "workflow-only"
    assert read_json(root / "config" / "system.json")["mode"] == "workflow-only"


def test_full_trained_case_gets_exact_frozen_memory_snapshot(tmp_path: Path) -> None:
    root = make_system(tmp_path)
    set_mode(root, "full-trained")
    demo = root / "demo" / "Dummy-A" / "input"
    state = new_case(root, "dummy-full-trained", [demo / "problem.txt"], [demo / "data.csv"])
    cards = sorted((root / "user-cases" / "dummy-full-trained" / "resources" / "training-memory" / "cards").glob("*.yaml"))
    assert state["mode"] == "full-trained"
    assert len(cards) == 22
    completed = run_case(root, "dummy-full-trained", "fake")
    assert completed["state"] == "blind_final_frozen"
    selection = read_json(root / "user-cases" / "dummy-full-trained" / "frozen" / "blind-v1" / "training-memory-selection.json")
    assert selection["retrieved"] == []


def test_case_status_reports_no_active_lock_after_completion(tmp_path: Path) -> None:
    root = make_system(tmp_path)
    demo = root / "demo" / "Dummy-A" / "input"
    new_case(root, "dummy-status", [demo / "problem.txt"], [])
    run_case(root, "dummy-status", "fake")
    status = case_status(root, "dummy-status")
    assert status["state"] == "blind_final_frozen"
    assert status["runtime"]["lock"] is None
    assert status["runtime"]["pause_requested"] is False


def test_freeze_detects_tampering(tmp_path: Path) -> None:
    root = make_system(tmp_path)
    demo = root / "demo" / "Dummy-A" / "input"
    new_case(root, "dummy-tamper", [demo / "problem.txt"], [])
    run_case(root, "dummy-tamper", "fake")
    case = root / "user-cases" / "dummy-tamper"
    (case / "frozen" / "blind-final" / "solution-report.json").write_text("tampered", encoding="utf-8")
    assert verify_freeze(case / "frozen" / "blind-final", case / "frozen" / "FROZEN_BLIND_FINAL.json")["status"] == "fail"


def test_invalid_new_case_leaves_no_partial_directory(tmp_path: Path) -> None:
    root = make_system(tmp_path)
    with pytest.raises(SystemError, match="至少需要"):
        new_case(root, "missing-problem", [], [])
    assert not (root / "user-cases" / "missing-problem").exists()
    with pytest.raises(FileNotFoundError):
        new_case(root, "bad-source", [tmp_path / "absent.txt"], [])
    assert not (root / "user-cases" / "bad-source").exists()


def test_case_phase_gate_is_present_and_passes_without_outside_search(tmp_path: Path) -> None:
    root = make_system(tmp_path)
    demo = root / "demo" / "Dummy-A" / "input"
    new_case(root, "guarded-case", [demo / "problem.txt"], [])
    state = run_case(root, "guarded-case", "fake", max_stages=1)
    case = root / "user-cases" / "guarded-case"
    assert state["state"] == "blind_v1_frozen"
    completed = subprocess.run(
        [sys.executable, str(case / "scripts" / "check_phase.py"), "--workspace", str(case), "--phase", "solve"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert read_json(case / "allowed-paths.json")["phase"] == "solve"


def test_completed_codex_stage_is_recovered_without_second_model_call(tmp_path: Path) -> None:
    root = make_system(tmp_path)
    demo = root / "demo" / "Dummy-A" / "input"
    new_case(root, "completed-on-disk", [demo / "problem.txt"], [])
    case = root / "user-cases" / "completed-on-disk"
    _fake_stage(case, "solve", "workflow-only")
    run_dir = case / "runs" / "solve-existing"
    run_dir.mkdir()
    write_json(run_dir / "run-metadata.json", {
        "stage": "solve", "status": "pass", "requested_model": "gpt-5.6-sol",
        "actual_model": "gpt-5.6-sol", "reasoning": "max", "fallback": False,
        "ephemeral": True, "turn_completed": 1, "thread_id": "existing-thread",
    })
    freeze_tree(case / "submission" / "blind-v1", case / "frozen" / "blind-v1", case / "frozen" / "FROZEN_BLIND_V1.json", "FROZEN_BLIND_V1")
    state = run_case(root, "completed-on-disk", "codex", max_stages=1)
    assert state["state"] == "blind_v1_frozen"
    recovered = [item for item in state["history"] if item["event"] == "completed_stage_recovered_without_model_call"]
    assert recovered and recovered[-1]["thread_id"] == "existing-thread"
    assert len(list((case / "runs").glob("solve-*/run-metadata.json"))) == 1


def test_open_latest_report_has_case_id_and_containment_guards() -> None:
    script = (Path(__file__).resolve().parents[1] / "scripts" / "Open-Latest-Report.ps1").read_text(encoding="utf-8-sig")
    assert "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$" in script
    assert "StartsWith($casesRoot+'\\'" in script
