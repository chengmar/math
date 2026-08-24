from pathlib import Path

import pytest

from cumcm_lab.knowledge import machine_verify_lesson, retrieve_knowledge
from cumcm_lab.util import read_yaml, write_yaml


def _candidate(path: Path, *, complete: bool) -> None:
    validations = [
        {
            "role": "shadow_validation",
            "outcome": "positive",
            "case_id": "2004A",
            "year": 2004,
            "problem_family": "prediction",
            "core_metric_improved": True,
            "accuracy_not_degraded": True,
            "paper_not_degraded": True,
            "complexity_justified": True,
            "leakage_status": "pass",
        },
        {
            "role": "shadow_validation",
            "outcome": "positive",
            "case_id": "2005A",
            "year": 2005,
            "problem_family": "optimization",
            "core_metric_improved": True,
            "accuracy_not_degraded": True,
            "paper_not_degraded": True,
            "complexity_justified": True,
            "leakage_status": "pass",
        },
    ]
    write_yaml(
        path,
        {
            "id": "cross-year-demo",
            "title": "跨年验证卡",
            "type": "method_card",
            "status": "candidate",
            "provenance": "real_training",
            "origin_year": 2003,
            "tags": ["robust"],
            "applicable_conditions": ["条件明确"],
            "inapplicable_conditions": ["边界外"],
            "failure_cases": ["失败案例"],
            "counterexamples": ["反例"],
            "source_cases": validations if complete else validations[:1],
            "evidence": [{"metric": "mae", "delta": -0.1}],
            "independent_review_status": "pass",
            "regression_status": "pass",
        },
    )


def test_machine_verified_requires_two_later_independent_cases(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    candidate = knowledge / "candidates" / "card.yaml"
    _candidate(candidate, complete=False)
    with pytest.raises(ValueError, match="条件不足"):
        machine_verify_lesson(knowledge, candidate)
    assert not (knowledge / "method-cards" / "cross-year-demo.yaml").exists()


def test_machine_verified_is_available_to_solve(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    candidate = knowledge / "candidates" / "card.yaml"
    _candidate(candidate, complete=True)
    result = machine_verify_lesson(knowledge, candidate)
    assert result["status"] == "approved"
    promoted = knowledge / "method-cards" / "cross-year-demo.yaml"
    card = read_yaml(promoted)
    assert card["status"] == "machine_verified"
    assert card["verification_kind"] == "machine"
    assert "approved_by" not in card
    found = retrieve_knowledge(knowledge, {"task_type": "robust"}, phase="solve")
    assert [item["id"] for item in found] == ["cross-year-demo"]
