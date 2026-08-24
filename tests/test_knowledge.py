from pathlib import Path

import pytest
import yaml

from cumcm_lab.knowledge import promote_lesson, retrieve_knowledge


def write_card(path: Path, card_id: str, status: str = "verified"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"id": card_id, "title": f"ODE validation {card_id}", "status": status, "tags": ["ode", "validation"]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_verified_knowledge_retrieval(tmp_path):
    root = tmp_path / "knowledge"
    write_card(root / "method-cards" / "v.yaml", "verified-card")
    write_card(root / "method-cards" / "c.yaml", "candidate-card", "candidate")
    result = retrieve_knowledge(root, {"problem_family": "ode"}, phase="solve")
    assert [item["id"] for item in result] == ["verified-card"]


def test_retrieval_returns_at_most_five(tmp_path):
    root = tmp_path / "knowledge"
    for index in range(8):
        write_card(root / "method-cards" / f"{index}.yaml", f"v{index}")
    assert len(retrieve_knowledge(root, {"problem_family": "ode"}, limit=99)) == 5


def full_candidate(root: Path, independent: bool = True) -> Path:
    candidate = {
        "id": "candidate-1",
        "title": "Candidate",
        "status": "candidate",
        "applicable_conditions": ["condition"],
        "inapplicable_conditions": ["not condition"],
        "minimum_baseline": "baseline",
        "required_validation": ["validation"],
        "counterexamples": ["risk"],
        "evidence": [{"kind": "score", "value": 1}],
        "regression_status": "pass",
        "source_cases": [
            {"case_id": "c1", "variant_group": "g1", "outcome": "positive"},
            {"case_id": "c2", "variant_group": "g2" if independent else "g1", "outcome": "positive"},
        ],
    }
    path = root / "candidates" / "candidate-1.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
    return path


def test_promotion_rejects_insufficient_independent_cases(tmp_path):
    root = tmp_path / "knowledge"
    (root / "promotion-proposals").mkdir(parents=True)
    path = full_candidate(root, independent=False)
    with pytest.raises(ValueError, match="升级条件不足"):
        promote_lesson(root, path, human_approved=True, approved_by="judge")


def test_promotion_requires_human_approval(tmp_path):
    root = tmp_path / "knowledge"
    (root / "promotion-proposals").mkdir(parents=True)
    path = full_candidate(root)
    proposal = promote_lesson(root, path)
    assert proposal["status"] == "needs_review"
    assert not (root / "method-cards" / "candidate-1.yaml").exists()


def test_promotion_succeeds_with_two_cases_and_approval(tmp_path):
    root = tmp_path / "knowledge"
    (root / "promotion-proposals").mkdir(parents=True)
    path = full_candidate(root)
    proposal = promote_lesson(root, path, human_approved=True, approved_by="human-reviewer")
    assert proposal["status"] == "approved"
    promoted = yaml.safe_load((root / "method-cards" / "candidate-1.yaml").read_text(encoding="utf-8"))
    assert promoted["status"] == "verified"
    assert promoted["approved_by"] == "human-reviewer"
