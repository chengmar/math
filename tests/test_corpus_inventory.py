from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import yaml

from cumcm_lab.corpus_inventory import inventory_corpus, sanitize_manifest, write_inventory_reports
from cumcm_lab.corpus_match import classify_path, load_split_config, split_for_year
from cumcm_lab.safe_extract import safe_extract


SPLIT = {
    "schema_version": 1,
    "train": {"years": list(range(2003, 2022)), "problem_letter": "A"},
    "dev": {"years": [], "problem_letter": "A"},
    "test": {"years": [2023], "problem_letter": "A"},
    "excluded": {"years": [2022]},
    "out_of_scope": {"before_year": 2003, "after_year": 2023},
}


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    problems = tmp_path / "problems-raw"
    papers = tmp_path / "papers-raw"
    problems.mkdir()
    papers.mkdir()
    return problems, papers


def test_load_config_and_fixed_split(tmp_path: Path) -> None:
    config_path = tmp_path / "split.yaml"
    config_path.write_text(yaml.safe_dump(SPLIT), encoding="utf-8")
    loaded = load_split_config(config_path)
    assert split_for_year(2003, loaded) == "train"
    assert split_for_year(2021, loaded) == "train"
    assert split_for_year(2022, loaded) == "excluded"
    assert split_for_year(2023, loaded) == "test"
    assert split_for_year(2002, loaded) == "out_of_scope"
    assert split_for_year(2024, loaded) == "out_of_scope"
    assert split_for_year(2010, loaded, "B") == "quarantine"


def test_inventory_is_read_only_and_uses_path_metadata(tmp_path: Path) -> None:
    problems, papers = _roots(tmp_path)
    statement = problems / "2003年A题" / "2003A题面.pdf"
    data = problems / "2003年A题" / "附件数据.xls"
    paper = papers / "2003" / "优秀论文.pdf"
    for path, payload in ((statement, b"statement"), (data, b"data"), (paper, b"paper")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (statement, data, paper)}

    report = inventory_corpus(problems, papers, SPLIT)

    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (statement, data, paper)}
    assert before == after
    assert report["summary"]["total_files"] == 3
    assert report["summary"]["by_split"] == {"train": 3}
    types = {entry["document_type"] for entry in report["files"]}
    assert types == {"problem_statement", "official_data", "reference_paper"}


def test_duplicate_and_cross_split_duplicate_are_flagged(tmp_path: Path) -> None:
    problems, papers = _roots(tmp_path)
    first = problems / "2003年A题" / "题面.pdf"
    second = problems / "2004年A题" / "题面副本.pdf"
    test_copy = problems / "2023年A题" / "sealed.pdf"
    for path in (first, second, test_copy):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"identical")

    report = inventory_corpus(problems, papers, SPLIT)
    duplicates = [entry for entry in report["files"] if entry["duplicate_of"]]
    file_ids = [entry["file_id"] for entry in report["files"]]
    assert len(file_ids) == len(set(file_ids))
    assert all(file_id.startswith("file-") and len(file_id) == 25 for file_id in file_ids)
    assert len(duplicates) == 2
    assert report["summary"]["duplicates"] == 2
    assert report["summary"]["cross_split_duplicates"] == 1
    sealed_duplicate = next(entry for entry in duplicates if entry["detected_year"] == 2023)
    assert "duplicate_across_splits" in sealed_duplicate["review_reason"]


def test_2022_scope_non_a_and_ambiguous_paths_are_not_forced(tmp_path: Path) -> None:
    assert classify_path(Path("2022年A题/题面.pdf"), "problems", SPLIT)["split"] == "excluded"
    assert classify_path(Path("1999年A题/题面.pdf"), "problems", SPLIT)["split"] == "out_of_scope"

    non_a = classify_path(Path("2010年B题/题面.pdf"), "problems", SPLIT)
    assert non_a["split"] == "quarantine"
    assert non_a["quarantine_reason"] == "non_a_problem"
    assert non_a["matched_case_id"] is None

    ambiguous = classify_path(Path("2009年A题/2010A题面.pdf"), "problems", SPLIT)
    assert ambiguous["split"] == "quarantine"
    assert ambiguous["classification_confidence"] == "low"
    assert "ambiguous_year" in ambiguous["review_reason"]


def test_gif_and_tif_are_recognized_as_problem_attachments() -> None:
    for suffix in ("gif", "tif"):
        result = classify_path(Path(f"2014年A题/附件.{suffix}"), "problems", SPLIT)
        assert result["document_type"] == "official_attachment"
        assert result["requires_review"] is False


def test_2023_title_is_absent_from_sanitized_and_written_reports(tmp_path: Path) -> None:
    problems, papers = _roots(tmp_path)
    # The filename intentionally contains a conflicting year.  The containing
    # 2023 directory must still force sealed redaction.
    secret_title = "绝不能泄漏的2022对比论文标题"
    problem = problems / "2023年A题" / "2023A题面.pdf"
    paper = papers / "2023" / f"{secret_title}.pdf"
    for path, payload in ((problem, b"sealed-problem"), (paper, b"sealed-paper")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    report = inventory_corpus(problems, papers, SPLIT)
    assert any("_source_path" in entry for entry in report["files"])
    public = sanitize_manifest(report)
    serialized = json.dumps(public, ensure_ascii=False)
    assert secret_title not in serialized
    sealed = [entry for entry in public["files"] if entry.get("split") == "test"]
    assert len(sealed) == 2
    assert all(set(entry) == {"file_id", "sha256", "size", "count", "split"} for entry in sealed)

    outputs = write_inventory_reports(report, tmp_path / "reports")
    for output in outputs.values():
        assert secret_title not in Path(output).read_text(encoding="utf-8-sig")


def test_safe_zip_extract_and_no_overwrite(tmp_path: Path) -> None:
    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("folder/data.txt", "ok")
    destination = tmp_path / "out"
    result = safe_extract(archive, destination)
    assert result["status"] == "pass"
    assert (destination / "folder" / "data.txt").read_text(encoding="utf-8") == "ok"

    again = safe_extract(archive, destination)
    assert again["status"] == "needs_review"
    assert again["review_reason"] == "destination_member_exists"
    assert (destination / "folder" / "data.txt").read_text(encoding="utf-8") == "ok"


def test_safe_extract_blocks_zip_and_tar_traversal(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as handle:
        handle.writestr("../escape.txt", "bad")
    zip_result = safe_extract(zip_path, tmp_path / "zip-out")
    assert zip_result["status"] == "needs_review"
    assert zip_result["review_reason"] == "archive_path_traversal"
    assert not (tmp_path / "escape.txt").exists()

    tar_path = tmp_path / "bad.tar"
    payload = b"bad"
    with tarfile.open(tar_path, "w") as handle:
        info = tarfile.TarInfo("../tar-escape.txt")
        info.size = len(payload)
        handle.addfile(info, io.BytesIO(payload))
    tar_result = safe_extract(tar_path, tmp_path / "tar-out")
    assert tar_result["status"] == "needs_review"
    assert tar_result["review_reason"] == "archive_path_traversal"
    assert not (tmp_path / "tar-escape.txt").exists()


def test_unsupported_archive_requires_review(tmp_path: Path) -> None:
    archive = tmp_path / "legacy.rar"
    archive.write_bytes(b"not-a-rar")
    result = safe_extract(archive, tmp_path / "out")
    assert result == {
        "status": "unsupported",
        "requires_review": True,
        "review_reason": "unsupported_archive_format",
        "format": "rar",
        "extracted_files": 0,
    }
