"""Build or verify the canonical authority manifest.

Canonical tree records are UTF-8 bytes of
``relative_posix_path + NUL + lowercase_sha256 + LF`` sorted by path.  The
manifest excludes itself to avoid a circular hash definition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


TOP_LEVEL = {
    "problem-analysis.md",
    "data-audit.md",
    "assumptions.yaml",
    "variables.yaml",
    "model-selection.md",
    "solution-report.yaml",
    "reproducibility.yaml",
    "requirements-lock.txt",
}

CODE_FILES = {
    "code/model.py",
    "code/solve.py",
    "code/extract_inputs.ps1",
    "code/render_paper.py",
    "code/verify_independent.py",
    "code/verify_outputs.py",
    "code/build_manifest.py",
    "code/run_all.ps1",
}

RESULT_FILES = {
    "results/extracted/manifest.json",
    "results/extracted/<SOURCE_FILE_REDACTED>",
    "results/extracted/<SOURCE_FILE_REDACTED>",
    "results/extracted/<SOURCE_FILE_REDACTED>",
    "results/extracted/<SOURCE_FILE_REDACTED>",
    "results/extracted/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/actual_tank_calibration_table.tex",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/data_audit.json",
    "results/generated_values.json",
    "results/generated_values.tex",
    "results/independent-verification.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/parameters.json",
    "results/run_metadata.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/small_tank_calibration_table.tex",
    "results/<SOURCE_FILE_REDACTED>",
    "results/summary.json",
    "results/verification.json",
}

FIGURE_FILES = {
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
}

PAPER_FILES = {
    "paper/main.tex",
    "paper/preamble.tex",
    "paper/paper.template.md",
    "paper/paper.md",
    "paper/sections/01-problem.tex",
    "paper/sections/02-analysis.tex",
    "paper/sections/03-assumptions-symbols.tex",
    "paper/sections/04-data-model.tex",
    "paper/sections/05-solution-validation.tex",
    "paper/sections/06-evaluation-conclusion.tex",
    "paper/sections/appendix.tex",
}

SELF_PATH = "results/run_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(records: list[dict]) -> str:
    payload = b"".join(
        f"{record['path']}\0{record['sha256']}\n".encode("utf-8")
        for record in sorted(records, key=lambda item: item["path"])
    )
    return hashlib.sha256(payload).hexdigest()


def expected_paths(root: Path) -> set[str]:
    expected = TOP_LEVEL | CODE_FILES | RESULT_FILES | FIGURE_FILES | PAPER_FILES
    if (root / "paper" / "<SOURCE_FILE_REDACTED>").is_file():
        expected = expected | {"paper/<SOURCE_FILE_REDACTED>"}
    return expected


def physical_authority_paths(root: Path) -> set[str]:
    physical: set[str] = set()
    for relative in TOP_LEVEL:
        if (root / relative).is_file():
            physical.add(relative)
    for directory in ("code", "results", "figures", "paper"):
        base = root / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if relative != SELF_PATH:
                    physical.add(relative)
    return physical


def records_for(root: Path, paths: set[str]) -> list[dict]:
    return [
        {
            "path": relative,
            "bytes": (root / relative).stat().st_size,
            "sha256": sha256(root / relative),
        }
        for relative in sorted(paths)
    ]


def core_paths(expected: set[str]) -> set[str]:
    return {
        relative
        for relative in expected
        if (
            relative.startswith("results/extracted/")
            or relative.startswith("figures/")
            or relative
            in {
                "results/<SOURCE_FILE_REDACTED>",
                "results/actual_tank_calibration_table.tex",
                "results/<SOURCE_FILE_REDACTED>",
                "results/<SOURCE_FILE_REDACTED>",
                "results/data_audit.json",
                "results/generated_values.json",
                "results/generated_values.tex",
                "results/independent-verification.json",
                "results/<SOURCE_FILE_REDACTED>",
                "results/parameters.json",
                "results/<SOURCE_FILE_REDACTED>",
                "results/<SOURCE_FILE_REDACTED>",
                "results/<SOURCE_FILE_REDACTED>",
                "results/small_tank_calibration_table.tex",
                "results/<SOURCE_FILE_REDACTED>",
                "results/summary.json",
                "paper/paper.md",
            }
        )
    }


def generate(root: Path) -> None:
    expected = expected_paths(root)
    physical = physical_authority_paths(root)
    missing = sorted(expected - physical)
    unexpected = sorted(physical - expected)
    if missing or unexpected:
        print(
            "[FAIL] authority directory closure: "
            + json.dumps({"missing": missing, "unexpected": unexpected}, ensure_ascii=False)
        )
        sys.exit(1)
    records = records_for(root, expected)
    generated_core = core_paths(expected)
    core_records = [record for record in records if record["path"] in generated_core]
    metadata = json.loads((root / "results" / "run_metadata.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 2,
        "case_id": "2010A",
        "phase": "blind-revision",
        "status": "pass",
        "seed": 20260824,
        "canonical_tree_algorithm": "sort POSIX relative paths; hash UTF-8(path + NUL + lowercase file_sha256 + LF)",
        "manifest_self": {
            "status": "pass",
            "path": SELF_PATH,
            "rule": "excluded from file list and tree hashes to avoid circularity",
        },
        "directory_closure": {
            "status": "pass",
            "authority_roots": ["code", "results", "figures", "paper"],
            "top_level_deliverables": sorted(TOP_LEVEL),
            "missing": [],
            "unexpected": [],
        },
        "pdf": {
            "status": "pass" if "paper/<SOURCE_FILE_REDACTED>" in expected else "needs_review",
            "path": "paper/<SOURCE_FILE_REDACTED>" if "paper/<SOURCE_FILE_REDACTED>" in expected else None,
        },
        "input_sha256": metadata["input_sha256"],
        "authority_file_count_excluding_manifest": len(records),
        "authority_tree_sha256": tree_digest(records),
        "generated_core_file_count": len(core_records),
        "generated_core_tree_sha256": tree_digest(core_records),
        "files": records,
    }
    (root / SELF_PATH).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "[PASS] generated canonical manifest: "
        f"{len(records)} authority files, core={manifest['generated_core_tree_sha256']}"
    )


def verify(root: Path) -> None:
    manifest_path = root / SELF_PATH
    if not manifest_path.is_file():
        print("[FAIL] run_manifest.json is missing")
        sys.exit(1)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = expected_paths(root)
    physical = physical_authority_paths(root)
    listed = {record["path"] for record in manifest["files"]}
    mismatched: list[str] = []
    for record in manifest["files"]:
        path = root / record["path"]
        if (
            not path.is_file()
            or path.stat().st_size != record["bytes"]
            or sha256(path) != record["sha256"]
        ):
            mismatched.append(record["path"])
    recomputed_records = records_for(root, expected) if expected == physical else []
    recomputed_core = [
        record for record in recomputed_records if record["path"] in core_paths(expected)
    ]
    conditions = {
        "closure": expected == physical,
        "listed_paths": listed == expected,
        "file_hashes": not mismatched,
        "authority_tree": bool(recomputed_records)
        and tree_digest(recomputed_records) == manifest["authority_tree_sha256"],
        "generated_core_tree": bool(recomputed_core)
        and tree_digest(recomputed_core) == manifest["generated_core_tree_sha256"],
    }
    failed = [name for name, passed in conditions.items() if not passed]
    if failed:
        print(
            "[FAIL] manifest verification: "
            + json.dumps(
                {
                    "failed": failed,
                    "missing": sorted(expected - physical),
                    "unexpected": sorted(physical - expected),
                    "mismatched": mismatched,
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    print("[PASS] manifest hashes and authority directory closure")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if arguments.verify:
        verify(root)
    else:
        generate(root)


if __name__ == "__main__":
    main()
