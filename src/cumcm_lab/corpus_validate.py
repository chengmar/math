from __future__ import annotations

import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .corpus_inventory import sanitize_manifest
from .util import now_iso, read_json, read_yaml, sha256_file, write_json


SENSITIVE_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
}
TRACKED_BINARY_ALLOWLIST = ("cases/dummy/", "tests/fixtures/", "templates/")


def _record_source_kind(record: dict[str, Any]) -> str | None:
    private = str(record.get("_source_kind") or "").casefold()
    if private in {"problem", "problems"}:
        return "problems"
    if private in {"paper", "papers", "reference", "references"}:
        return "papers"
    public = str(record.get("source_root") or "").replace("\\", "/").rstrip("/").casefold()
    if public.endswith("/problems-raw") or public == "problems-raw":
        return "problems"
    if public.endswith("/papers-raw") or public == "papers-raw":
        return "papers"
    return None


def _source_path(record: dict[str, Any], paths: dict[str, str]) -> Path:
    explicit = record.get("_source_path")
    if explicit:
        return Path(str(explicit))
    source_kind = _record_source_kind(record)
    if source_kind not in {"problems", "papers"}:
        raise ValueError(f"无法确定源类型：{record.get('file_id')}")
    root_key = "problems_intake" if source_kind == "problems" else "papers_intake"
    relative = record.get("_source_relative") or record.get("relative_source_path")
    return Path(paths[root_key]) / str(relative)


def _git_files(trainer_root: Path) -> list[str]:
    output = subprocess.run(
        ["git", "-C", str(trainer_root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    return [item.decode("utf-8", errors="replace") for item in output.split(b"\0") if item]


def _git_leak_findings(trainer_root: Path, real_hashes: set[str]) -> list[str]:
    findings: list[str] = []
    for relative in _git_files(trainer_root):
        normalized = relative.replace("\\", "/")
        path = trainer_root / relative
        suffix = path.suffix.casefold()
        if suffix in SENSITIVE_SUFFIXES and not normalized.startswith(TRACKED_BINARY_ALLOWLIST):
            findings.append(f"tracked_sensitive_extension:{normalized}")
        if path.is_file() and sha256_file(path) in real_hashes:
            findings.append(f"tracked_real_hash:{normalized}")
    return findings


def validate_corpus(
    inventory: dict[str, Any],
    paths: dict[str, str],
    split_config: Path | dict[str, Any],
    *,
    trainer_root: Path,
    import_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = read_yaml(split_config) if isinstance(split_config, Path) else split_config
    records = inventory.get("files", [])
    checks: list[dict[str, Any]] = []

    def add(check_id: str, status: str, evidence: Any) -> None:
        checks.append({"id": check_id, "status": status, "evidence": evidence})

    train_years = [int(year) for year in config["train"]["years"]]
    test_years = [int(year) for year in config["test"]["years"]]
    excluded_years = {int(year) for year in config["excluded"]["years"]}
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("matched_case_id"):
            by_case[str(record["matched_case_id"])].append(record)

    case_statuses: list[dict[str, Any]] = []
    for year in train_years:
        case_id = f"{year}A"
        items = by_case.get(case_id, [])
        statements = [
            item
            for item in items
            if _record_source_kind(item) == "problems"
            and item.get("document_type") == "problem_statement"
            and not item.get("requires_review")
        ]
        supporting = [
            item
            for item in items
            if _record_source_kind(item) == "problems"
            and item.get("document_type") in {"official_attachment", "official_data", "archive_container"}
            and not item.get("requires_review")
        ]
        references = [
            item
            for item in items
            if _record_source_kind(item) == "papers"
            and item.get("document_type") in {"reference_paper", "reference_code", "reference_attachment", "archive_container"}
            and not item.get("requires_review")
        ]
        status = "ready" if statements else "import_blocked"
        notes: list[str] = []
        if not supporting:
            notes.append("attachments_or_data_needs_review")
        if not references:
            notes.append("reflection_material_needs_review")
        case_statuses.append(
            {
                "case_id": case_id,
                "split": "train",
                "status": status,
                "problem_statement_count": len(statements),
                "supporting_file_count": len(supporting),
                "reference_count": len(references),
                "notes": notes,
            }
        )
    add(
        "train_case_count",
        "pass" if len(case_statuses) == int(config["expected_counts"]["train"]) else "fail",
        {"actual": len(case_statuses), "expected": int(config["expected_counts"]["train"])},
    )
    blocked = [item["case_id"] for item in case_statuses if item["status"] == "import_blocked"]
    add("train_problem_statements", "pass" if not blocked else "fail", {"blocked_cases": blocked})
    support_review = [item["case_id"] for item in case_statuses if "attachments_or_data_needs_review" in item["notes"]]
    add("train_supporting_files", "pass" if not support_review else "needs_review", {"cases": support_review})

    observed_train = {record.get("detected_year") for record in records if record.get("split") == "train"}
    add("train_year_split", "pass" if observed_train <= set(train_years) else "fail", {"years": sorted(year for year in observed_train if year)})
    observed_excluded = [record for record in records if record.get("detected_year") in excluded_years and record.get("split") == "train"]
    add("excluded_2022", "pass" if not observed_excluded else "fail", {"violations": len(observed_excluded)})
    out_of_scope_in_queue = [record for record in records if record.get("split") == "out_of_scope" and record.get("matched_case_id")]
    add("out_of_scope_not_cases", "pass" if not out_of_scope_in_queue else "fail", {"violations": len(out_of_scope_in_queue)})
    non_a = [record for record in records if record.get("problem_letter") not in {None, "A"} and record.get("split") in {"train", "test"}]
    add("non_a_quarantined", "pass" if not non_a else "fail", {"violations": len(non_a)})

    hashes_by_split: dict[str, set[str]] = defaultdict(set)
    for record in records:
        digest = record.get("sha256")
        if digest and record.get("split") in {"train", "dev", "test"}:
            hashes_by_split[str(digest)].add(str(record.get("split")))
    cross_split = [digest for digest, splits in hashes_by_split.items() if len(splits) > 1]
    add("hash_split_exclusive", "pass" if not cross_split else "fail", {"conflict_count": len(cross_split)})

    source_changes: list[str] = []
    unreadable: list[str] = []
    for record in records:
        path = _source_path(record, paths)
        try:
            if not path.is_file() or sha256_file(path) != record.get("sha256"):
                source_changes.append(str(record.get("file_id")))
        except OSError:
            unreadable.append(str(record.get("file_id")))
    add("intake_source_unchanged", "pass" if not source_changes and not unreadable else "fail", {"changed": source_changes, "unreadable": unreadable})

    if import_result is not None:
        hashes_by_id = {str(record.get("file_id")): str(record.get("sha256")) for record in records}

        def destination_matches(item: dict[str, Any]) -> bool:
            if item.get("result") not in {"copied", "reused"} or not item.get("destination"):
                return False
            target = Path(str(item["destination"]))
            try:
                return target.is_file() and sha256_file(target) == hashes_by_id.get(str(item.get("file_id")))
            except OSError:
                return False

        bad_targets = [
            item.get("file_id")
            for item in import_result.get("results", [])
            if item.get("action") == "copy"
            and not destination_matches(item)
        ]
        add("destination_hashes", "pass" if not bad_targets else "fail", {"violations": bad_targets})
        knowledge_root = (trainer_root / "knowledge").resolve(strict=False)
        knowledge_destinations = [
            str(item.get("file_id"))
            for item in import_result.get("results", [])
            if item.get("destination")
            and Path(str(item["destination"])).resolve(strict=False).is_relative_to(knowledge_root)
        ]
        add(
            "importer_does_not_write_knowledge",
            "pass" if not knowledge_destinations else "fail",
            {"violations": knowledge_destinations},
        )

    test_records = [
        record
        for record in records
        if record.get("detected_year") in test_years or (record.get("_sealed_test") and 2023 in test_years)
    ]
    test_leaks = [record for record in test_records if record.get("split") != "test" or record.get("matched_case_id") != "2023A"]
    add("test_2023_split", "pass" if test_records and not test_leaks else "fail", {"sealed_file_count": len(test_records), "violations": len(test_leaks)})
    public = sanitize_manifest(inventory)
    allowed_test_keys = {"file_id", "sha256", "size", "count", "split"}
    public_test_records = [item for item in public.get("files", []) if item.get("split") == "test"]
    title_leaks = sum(1 for item in public_test_records if set(item) != allowed_test_keys)
    if len(public_test_records) != len(test_records):
        title_leaks += abs(len(public_test_records) - len(test_records))
    add("test_2023_metadata_sealed", "pass" if title_leaks == 0 else "fail", {"violations": title_leaks})

    seal_path = Path(paths["exam_vault"]) / "2023A" / "SEALED.json"
    seal = read_json(seal_path, {}) if seal_path.exists() else {}
    add("test_2023_seal", "pass" if seal.get("status") == "test_sealed" else "fail", {"status": seal.get("status", "missing")})

    real_hashes = {str(record.get("sha256")) for record in records if record.get("sha256")}
    git_findings = _git_leak_findings(trainer_root, real_hashes)
    add("git_real_material_leak", "pass" if not git_findings else "fail", {"findings": git_findings})

    counts = Counter(str(record.get("document_type")) for record in records)
    statuses = {item["status"] for item in checks}
    overall = "fail" if "fail" in statuses else ("needs_review" if "needs_review" in statuses else "pass")
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "status": overall,
        "checks": checks,
        "case_statuses": case_statuses,
        "summary": {
            "file_count": len(records),
            "document_type_counts": dict(sorted(counts.items())),
            "duplicate_count": sum(1 for record in records if record.get("duplicate_of")),
            "review_count": sum(1 for record in records if record.get("requires_review")),
            "train_cases": len(case_statuses),
            "blocked_cases": blocked,
            "test_sealed": seal.get("status") == "test_sealed",
        },
    }


def write_validation_report(report: dict[str, Any], path: Path) -> Path:
    write_json(path, report)
    return path
