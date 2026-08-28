#!/usr/bin/env python3
"""Create the run-level code/input/output lineage manifest after a successful run."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def entry(relative: str) -> dict[str, object]:
    path = ROOT / relative
    if not path.is_file():
        raise FileNotFoundError(f"Required manifest file is missing: {relative}")
    return {
        "path": relative.replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "status": "pass",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monte-carlo", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    code_files = [
        "code/run_all.ps1",
        "code/extract_problem.ps1",
        "code/solve.py",
        "code/check_extremes.py",
        "code/check_consistency.py",
        "code/build_run_manifest.py",
        "code/verify_run_manifest.py",
        "code/compare_run_manifests.py",
        "code/requirements.txt",
    ]
    input_files = [
        "input/problem/<SOURCE_FILE_REDACTED>",
        "working/source-extract/docx-unpacked/word/media/<SOURCE_FILE_REDACTED>",
    ]
    output_files = [
        "results/source_metadata.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/model_metrics.json",
        "results/validation.json",
        "results/sensitivity.json",
        "results/main_results.json",
        "results/pose.json",
        "results/environment.json",
        "results/generated_numbers.tex",
        "results/center_table.tex",
        "results/generated_summary.md",
        "results/extreme_checks.json",
        "results/paper_consistency.json",
        "results/run_all.stdout.log",
        "results/run_all.stderr.log",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "paper/<SOURCE_FILE_REDACTED>",
    ]
    command = (
        "powershell -NoProfile -ExecutionPolicy Bypass -File "
        f"code/run_all.ps1 -MonteCarlo {args.monte_carlo} -Seed {args.seed}"
    )
    manifest = {
        "schema_version": 1,
        "status": "pass",
        "created_at": datetime.now().astimezone().isoformat(),
        "scope": "run-level lineage for the single authoritative blind-revision pipeline",
        "scope_status": "pass",
        "command": command,
        "randomness": {
            "seed": args.seed,
            "monte_carlo_repetitions": args.monte_carlo,
            "deterministic_core_solver": "pass",
        },
        "code": [entry(relative) for relative in code_files],
        "inputs": [entry(relative) for relative in input_files],
        "outputs": [entry(relative) for relative in output_files],
        "manifest_self_exclusion": {
            "status": "pass",
            "excluded_paths": [
                "results/run_manifest.json",
                "results/run_manifest_verification.json",
            ],
            "reason": "The manifest cannot hash itself; its independent verification report is created afterward.",
        },
        "whole_tree_freeze_manifest_status": "needs_review",
        "whole_tree_freeze_reason": (
            "The external blind-final freeze script must inventory the final payload after this revision; "
            "this run manifest is not a substitute for that freeze manifest."
        ),
    }
    output = ROOT / "results/run_manifest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"RUN_MANIFEST_STATUS={manifest['status']}")
    print(f"SOLVE_PY_SHA256={next(item['sha256'] for item in manifest['code'] if item['path'] == 'code/solve.py')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
