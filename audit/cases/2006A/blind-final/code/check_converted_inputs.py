#!/usr/bin/env python3
"""Validate the retained legacy-file conversions without using Office COM."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_workspace_path(workspace: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts:
        raise RuntimeError(f"unsafe manifest path: {relative}")
    path = (workspace / Path(*pure.parts)).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as exc:
        raise RuntimeError(f"manifest path escapes workspace: {relative}") from exc
    return path


def tsv_shape(path: Path) -> tuple[int, int]:
    rows = 0
    columns = 0
    with path.open("r", encoding="utf-16", newline="") as stream:
        for row in csv.reader(stream, delimiter="\t"):
            rows += 1
            # Excel reports a completely blank sheet as UsedRange 1x1, while
            # its Unicode-text export is one empty record parsed as zero
            # fields.  Preserve the worksheet semantics by counting that
            # record as one blank cell.
            columns = max(columns, max(1, len(row)))
    return rows, columns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    manifest_path = workspace / "input" / "converted" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks: dict[str, dict[str, Any]] = {}

    def record(name: str, condition: bool, observed: Any, expected: Any) -> None:
        checks[name] = {
            "status": "pass" if condition else "fail",
            "observed": observed,
            "expected": expected,
        }

    word_items = manifest.get("word", [])
    excel_items = manifest.get("excel", [])
    formula_items = manifest.get("formula_audit", [])
    record("generator", manifest.get("generator") == "code/convert_inputs.ps1", manifest.get("generator"), "code/convert_inputs.ps1")
    record("word_entry_count", len(word_items) == 2, len(word_items), 2)
    record("excel_entry_count", len(excel_items) == 11, len(excel_items), 11)
    record("formula_audit_count", len(formula_items) == 9, len(formula_items), 9)

    paths = [item.get("output", "") for item in [*word_items, *excel_items]]
    record("unique_output_paths", len(paths) == len(set(paths)), len(paths) - len(set(paths)), 0)

    missing_sources: list[str] = []
    missing_outputs: list[str] = []
    word_mismatches: list[str] = []
    shape_mismatches: list[dict[str, Any]] = []
    hashes: list[dict[str, Any]] = []

    for role, items in (("word", word_items), ("excel", excel_items)):
        for item in items:
            source_relative = item["source"]
            output_relative = item["output"]
            source = safe_workspace_path(workspace, source_relative)
            output = safe_workspace_path(workspace, output_relative)
            if not source.is_file():
                missing_sources.append(source_relative)
            if not output.is_file():
                missing_outputs.append(output_relative)
                continue
            hashes.append(
                {
                    "role": f"{role}_output",
                    "path": output_relative,
                    "bytes": output.stat().st_size,
                    "sha256": sha256_file(output),
                }
            )
            if role == "word":
                observed_characters = len(output.read_text(encoding="utf-8"))
                if observed_characters != int(item["characters"]):
                    word_mismatches.append(output_relative)
            else:
                observed_shape = tsv_shape(output)
                expected_shape = (int(item["rows"]), int(item["columns"]))
                if observed_shape != expected_shape:
                    shape_mismatches.append(
                        {
                            "path": output_relative,
                            "observed": list(observed_shape),
                            "expected": list(expected_shape),
                        }
                    )

    source_paths = sorted(
        {
            item["source"]
            for item in [*word_items, *excel_items]
            if safe_workspace_path(workspace, item["source"]).is_file()
        }
    )
    for source_relative in source_paths:
        source = safe_workspace_path(workspace, source_relative)
        hashes.append(
            {
                "role": "legacy_source",
                "path": source_relative,
                "bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        )

    formula_kinds = [item.get("request_cell_kind") for item in formula_items]
    record("source_files_exist", not missing_sources, missing_sources, [])
    record("converted_files_exist", not missing_outputs, missing_outputs, [])
    record("word_character_counts", not word_mismatches, word_mismatches, [])
    record("excel_shapes", not shape_mismatches, shape_mismatches, [])
    record(
        "formula_audit_kinds",
        set(formula_kinds) == {"formula", "hardcoded"},
        sorted(set(formula_kinds)),
        ["formula", "hardcoded"],
    )

    status = "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    report = {
        "status": status,
        "scope": "structural and byte-hash check of retained Word/Excel conversions",
        "office_reconversion_performed": False,
        "authoritative_freeze_claim": False,
        "freeze_status": "needs_review",
        "checks": checks,
        "hash_entry_count": len(hashes),
        "hashes": sorted(hashes, key=lambda item: (item["path"], item["role"])),
    }
    output = workspace / "results" / "conversion_check.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
