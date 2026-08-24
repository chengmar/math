import json
import shutil

import pytest
import yaml

from cumcm_lab.cases import init_case
from cumcm_lab.freeze import freeze_solution, verify_frozen
from cumcm_lab.leakage import check_leakage
from cumcm_lab.phases import prepare_phase
from cumcm_lab.util import sha256_file


def _write_valid_phase_lock(workspace, case_id: str, phase: str = "solve") -> None:
    allowed = workspace / "allowed-paths.json"
    forbidden = workspace / "forbidden-paths.json"
    allowed.write_text(json.dumps({"phase": phase, "paths": []}), encoding="utf-8")
    forbidden.write_text(json.dumps({"phase": phase, "paths": []}), encoding="utf-8")
    (workspace / "phase-lock.json").write_text(
        json.dumps(
            {
                "phase": phase,
                "case_id": case_id,
                "allowed_paths_sha256": sha256_file(allowed),
                "forbidden_paths_sha256": sha256_file(forbidden),
            }
        ),
        encoding="utf-8",
    )


def test_intentional_reference_leak_is_detected(tmp_path):
    workspace = tmp_path / "solve"
    workspace.mkdir()
    (workspace / "phase-lock.json").write_text(json.dumps({"phase": "solve"}), encoding="utf-8")
    fixture = __file__.replace("test_leakage_freeze.py", "fixtures/leakage/reference-paper-answer-key.md")
    shutil.copy2(fixture, workspace / "reference-paper-answer-key.md")
    report = check_leakage(workspace, "solve")
    assert report["status"] == "fail"
    assert any(item["category"] == "forbidden_path_token" for item in report["findings"])


def test_candidate_knowledge_in_solve_is_detected(tmp_path):
    workspace = tmp_path / "solve"
    workspace.mkdir()
    (workspace / "phase-lock.json").write_text(json.dumps({"phase": "solve"}), encoding="utf-8")
    (workspace / "candidate.yaml").write_text("id: c\nstatus: candidate\n", encoding="utf-8")
    assert check_leakage(workspace, "solve")["status"] == "fail"


def test_future_provisional_training_memory_is_detected(tmp_path):
    workspace = tmp_path / "solve"
    workspace.mkdir()
    _write_valid_phase_lock(workspace, "2011A")
    (workspace / "future.yaml").write_text(
        "id: future\nstatus: provisional_training\nsource_cases: [2011A]\n",
        encoding="utf-8",
    )
    report = check_leakage(workspace, "solve")
    assert report["status"] == "fail"
    assert any(item["category"] == "non_earlier_training_memory" for item in report["findings"])


def test_earlier_provisional_training_memory_is_allowed_in_training_solve(tmp_path):
    workspace = tmp_path / "solve"
    workspace.mkdir()
    _write_valid_phase_lock(workspace, "2011A")
    (workspace / "earlier.yaml").write_text(
        "id: earlier\nstatus: provisional_training\nsource_cases: [2009A]\n",
        encoding="utf-8",
    )
    assert check_leakage(workspace, "solve")["status"] == "pass"


def test_solve_workspace_excludes_candidate(lab_factory):
    trainer, _, _ = lab_factory()
    verified = trainer / "knowledge" / "method-cards" / "v.yaml"
    verified.write_text("id: v\ntitle: ode card\nstatus: verified\ntags: [ode]\n", encoding="utf-8")
    candidate = trainer / "knowledge" / "method-cards" / "c.yaml"
    candidate.write_text("id: c\ntitle: ode card\nstatus: candidate\ntags: [ode]\n", encoding="utf-8")
    case_dir = init_case(trainer, "dummy-a", "dummy", problem_family="ode")
    (case_dir / "input" / "problem" / "problem.md").write_text("dummy", encoding="utf-8")
    workspace = prepare_phase(trainer, "dummy-a", "solve")
    names = {path.name for path in (workspace / "knowledge").glob("*.yaml")}
    assert names == {"v.yaml"}


def test_blind_v1_freeze_and_verify(lab_factory, solution_writer):
    trainer, _, _ = lab_factory()
    case_dir = init_case(trainer, "dummy-a", "dummy")
    (case_dir / "input" / "problem" / "problem.md").write_text("dummy", encoding="utf-8")
    workspace = prepare_phase(trainer, "dummy-a", "solve")
    solution_writer(workspace)
    manifest = freeze_solution(case_dir, "blind-v1")
    assert manifest.exists()
    assert verify_frozen(case_dir, "blind-v1")["status"] == "pass"


def test_frozen_tampering_is_detected(lab_factory, solution_writer):
    trainer, _, _ = lab_factory()
    case_dir = init_case(trainer, "dummy-a", "dummy")
    (case_dir / "input" / "problem" / "problem.md").write_text("dummy", encoding="utf-8")
    workspace = prepare_phase(trainer, "dummy-a", "solve")
    solution_writer(workspace)
    freeze_solution(case_dir, "blind-v1")
    (case_dir / "frozen" / "blind-v1" / "results" / "summary.json").write_text("tampered", encoding="utf-8")
    assert verify_frozen(case_dir, "blind-v1")["status"] == "fail"


def test_freeze_excludes_non_deterministic_build_artifacts(lab_factory, solution_writer):
    trainer, _, _ = lab_factory()
    case_dir = init_case(trainer, "dummy-a", "dummy")
    (case_dir / "input" / "problem" / "problem.md").write_text("dummy", encoding="utf-8")
    workspace = prepare_phase(trainer, "dummy-a", "solve")
    solution_writer(workspace)
    (workspace / "paper" / "main.log").write_text("timestamped", encoding="utf-8")
    (workspace / "paper" / "main.aux").write_text("timestamped", encoding="utf-8")
    (workspace / "code" / "__pycache__").mkdir()
    (workspace / "code" / "__pycache__" / "run.pyc").write_bytes(b"cache")

    manifest_path = freeze_solution(case_dir, "blind-v1")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert not (case_dir / "frozen" / "blind-v1" / "paper" / "main.log").exists()
    assert not (case_dir / "frozen" / "blind-v1" / "code" / "__pycache__").exists()
    assert sorted(manifest["excluded_build_artifacts"]) == [
        "code/__pycache__/run.pyc",
        "paper/main.aux",
        "paper/main.log",
    ]


def test_freeze_refuses_overwrite(lab_factory, solution_writer):
    trainer, _, _ = lab_factory()
    case_dir = init_case(trainer, "dummy-a", "dummy")
    (case_dir / "input" / "problem" / "problem.md").write_text("dummy", encoding="utf-8")
    workspace = prepare_phase(trainer, "dummy-a", "solve")
    solution_writer(workspace)
    freeze_solution(case_dir, "blind-v1")
    with pytest.raises((FileExistsError, ValueError)):
        freeze_solution(case_dir, "blind-v1")
