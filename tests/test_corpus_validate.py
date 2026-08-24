from __future__ import annotations

import json
from pathlib import Path

from cumcm_lab import corpus_validate
from cumcm_lab.corpus_import import apply_import, plan_import, write_dry_run_lock
from cumcm_lab.corpus_inventory import inventory_corpus
from cumcm_lab.corpus_validate import validate_corpus


FULL_SPLIT = {
    "schema_version": 1,
    "train": {"years": list(range(2003, 2022)), "problem_letter": "A"},
    "dev": {"years": [], "problem_letter": "A"},
    "test": {"years": [2023], "problem_letter": "A"},
    "excluded": {"years": [2022]},
    "out_of_scope": {"before_year": 2003, "after_year": 2023},
    "expected_counts": {"train": 19, "dev": 0, "test": 1, "total": 20},
}


def _layout(tmp_path: Path) -> tuple[Path, Path, dict[str, str], Path]:
    problems = tmp_path / "intake" / "problems-raw"
    papers = tmp_path / "intake" / "papers-raw"
    problems.mkdir(parents=True)
    papers.mkdir(parents=True)
    trainer = tmp_path / "trainer"
    (trainer / "knowledge").mkdir(parents=True)
    paths = {
        "problems_intake": str(problems),
        "papers_intake": str(papers),
        "question_bank": str(tmp_path / "vaults" / "question-bank"),
        "reference_vault": str(tmp_path / "vaults" / "reference-vault"),
        "exam_vault": str(tmp_path / "vaults" / "exam-vault"),
    }
    return problems, papers, paths, trainer


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _check(report: dict, check_id: str) -> dict:
    return next(item for item in report["checks"] if item["id"] == check_id)


def _seal(paths: dict[str, str]) -> None:
    path = Path(paths["exam_vault"]) / "2023A" / "SEALED.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"status": "test_sealed"}), encoding="utf-8")


def test_validate_full_year_routing_and_sealed_metadata(tmp_path: Path, monkeypatch) -> None:
    problems, papers, paths, trainer = _layout(tmp_path)
    for year in range(2003, 2022):
        _write(problems / f"{year}年A题" / "题面.pdf", f"p-{year}".encode())
        _write(problems / f"{year}年A题" / "附件.csv", f"d-{year}".encode())
        _write(papers / str(year) / "论文.pdf", f"r-{year}".encode())
    _write(problems / "1999年A题" / "题面.pdf", b"scope")
    _write(problems / "2022年A题" / "题面.pdf", b"excluded")
    _write(problems / "2023年A题" / "题面.pdf", b"test-problem")
    _write(papers / "2023" / "保密论文标题.pdf", b"test-paper")
    _seal(paths)
    monkeypatch.setattr(corpus_validate, "_git_files", lambda _root: [])

    inventory = inventory_corpus(problems, papers, FULL_SPLIT)
    report = validate_corpus(inventory, paths, FULL_SPLIT, trainer_root=trainer)

    assert _check(report, "train_case_count")["status"] == "pass"
    assert _check(report, "train_problem_statements")["status"] == "pass"
    assert _check(report, "excluded_2022")["status"] == "pass"
    assert _check(report, "out_of_scope_not_cases")["status"] == "pass"
    assert _check(report, "test_2023_split")["status"] == "pass"
    assert _check(report, "test_2023_metadata_sealed")["status"] == "pass"
    assert report["summary"]["train_cases"] == 19
    assert report["summary"]["test_sealed"] is True


def test_low_confidence_statement_blocks_only_its_case(tmp_path: Path, monkeypatch) -> None:
    problems, papers, paths, trainer = _layout(tmp_path)
    config = {**FULL_SPLIT, "train": {"years": [2003], "problem_letter": "A"}, "expected_counts": {"train": 1}}
    _write(problems / "2003年A题" / "无法确认.bin", b"unknown")
    _write(problems / "2023年A题" / "题面.pdf", b"test")
    _seal(paths)
    monkeypatch.setattr(corpus_validate, "_git_files", lambda _root: [])
    report = validate_corpus(inventory_corpus(problems, papers, config), paths, config, trainer_root=trainer)
    assert report["summary"]["blocked_cases"] == ["2003A"]
    assert _check(report, "train_problem_statements")["status"] == "fail"


def test_low_confidence_reference_does_not_make_case_ready_for_reflection(tmp_path: Path, monkeypatch) -> None:
    problems, papers, paths, trainer = _layout(tmp_path)
    config = {**FULL_SPLIT, "train": {"years": [2003], "problem_letter": "A"}, "expected_counts": {"train": 1}}
    _write(problems / "2003年A题" / "题面.pdf", b"problem")
    _write(papers / "2003" / "unknown.bin", b"unknown-paper")
    _write(problems / "2023年A题" / "题面.pdf", b"test")
    _seal(paths)
    monkeypatch.setattr(corpus_validate, "_git_files", lambda _root: [])
    report = validate_corpus(inventory_corpus(problems, papers, config), paths, config, trainer_root=trainer)
    case = report["case_statuses"][0]
    assert case["status"] == "ready"
    assert case["reference_count"] == 0
    assert "reflection_material_needs_review" in case["notes"]


def test_validation_detects_mocked_git_real_hash(tmp_path: Path, monkeypatch) -> None:
    problems, papers, paths, trainer = _layout(tmp_path)
    config = {**FULL_SPLIT, "train": {"years": [2003], "problem_letter": "A"}, "expected_counts": {"train": 1}}
    _write(problems / "2003年A题" / "题面.pdf", b"real-hash")
    _write(problems / "2023年A题" / "题面.pdf", b"test")
    tracked = trainer / "leaked.bin"
    tracked.write_bytes(b"real-hash")
    _seal(paths)
    monkeypatch.setattr(corpus_validate, "_git_files", lambda _root: ["leaked.bin"])
    report = validate_corpus(inventory_corpus(problems, papers, config), paths, config, trainer_root=trainer)
    check = _check(report, "git_real_material_leak")
    assert check["status"] == "fail"
    assert check["evidence"]["findings"] == ["tracked_real_hash:leaked.bin"]


def test_validation_checks_destination_hashes_and_no_knowledge_write(tmp_path: Path, monkeypatch) -> None:
    problems, papers, paths, trainer = _layout(tmp_path)
    config = {**FULL_SPLIT, "train": {"years": [2003], "problem_letter": "A"}, "expected_counts": {"train": 1}}
    _write(problems / "2003年A题" / "题面.pdf", b"problem")
    _write(problems / "2023年A题" / "题面.pdf", b"test")
    inventory = inventory_corpus(problems, papers, config)
    plan = plan_import(inventory, paths)
    lock = tmp_path / "reports" / "import-lock.json"
    write_dry_run_lock(plan, lock)
    result = apply_import(inventory, plan, paths, lock_path=lock)
    _seal(paths)
    monkeypatch.setattr(corpus_validate, "_git_files", lambda _root: [])
    before = list((trainer / "knowledge").rglob("*"))
    report = validate_corpus(inventory, paths, config, trainer_root=trainer, import_result=result)
    after = list((trainer / "knowledge").rglob("*"))
    assert before == after == []
    assert _check(report, "destination_hashes")["status"] == "pass"
    assert _check(report, "importer_does_not_write_knowledge")["status"] == "pass"
