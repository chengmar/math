from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "build_review_export.py"
SPEC = importlib.util.spec_from_file_location("build_review_export", MODULE_PATH)
assert SPEC and SPEC.loader
review_export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_export)


def test_final_export_case_range_covers_all_completed_training_years() -> None:
    assert review_export.COMPLETED_CASES == tuple(f"{year}A" for year in range(2004, 2022))
    assert review_export.INCOMPLETE_CASES == ()


def test_final_status_document_is_dynamic_and_has_no_stale_breakpoint(tmp_path: Path) -> None:
    metrics = [
        {"case_id": f"{year}A", "status": "completed"}
        for year in range(2004, 2022)
    ]
    knowledge_rows = [{"status": "candidate"}]
    source = {
        "branch": "training/full-corpus-v1",
        "commit": "abc123",
        "dirty": False,
        "test_result": "189 passed",
    }

    review_export.generate_root_docs(tmp_path, {}, metrics, source, knowledge_rows)

    status = (tmp_path / "CURRENT_STATUS.md").read_text(encoding="utf-8")
    assert "training_complete_ready_for_final_test" in status
    assert "2021A" in status
    assert "189 passed" in status
    assert "2012A / Solve" not in status


def test_candidate_entries_accept_indexed_yaml_proposals(tmp_path: Path) -> None:
    lesson_root = tmp_path / "lessons-proposed"
    lesson_root.mkdir()
    (lesson_root / "index.yaml").write_text(
        "default_proposal_state: candidate\n"
        "proposals:\n"
        "  - id: CP-2015A-001\n"
        "    file: card.yaml\n",
        encoding="utf-8",
    )
    (lesson_root / "card.yaml").write_text(
        "proposal_id: CP-2015A-001\n"
        "proposal_state: candidate\n"
        "source_case:\n"
        "  case_id: 2015A\n",
        encoding="utf-8",
    )

    rows = review_export.candidate_entries(lesson_root, "2015A")

    assert len(rows) == 1
    assert rows[0]["card_id"] == "CP-2015A-001"
    assert rows[0]["status"] == "candidate"


def test_metrics_do_not_count_demo_as_candidate(tmp_path: Path) -> None:
    case_dir = tmp_path / "2018A"
    lessons = case_dir / "workspaces" / "reflection" / "lessons-proposed"
    lessons.mkdir(parents=True)
    (lessons / "candidate.yaml").write_text("status: candidate\n", encoding="utf-8")
    (lessons / "demo.yaml").write_text("status: demo\n", encoding="utf-8")

    metric = review_export.metrics_for_case("2018A", {"status": "completed"}, case_dir)

    assert metric["candidate_count"] == 1
