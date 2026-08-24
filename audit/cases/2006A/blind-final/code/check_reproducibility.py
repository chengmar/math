#!/usr/bin/env python3
"""Rerun the deterministic numerical pipeline and compare core hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


TARGETS = [
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/summary.json",
    "results/data_audit.json",
    "results/forecast_validation.json",
    "results/elasticity.json",
    "results/constraint_checks.json",
    "results/boundary_checks.json",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "paper/generated/result_macros.tex",
    "paper/generated/branch_allocation.tex",
    "paper/generated/candidate_comparison.tex",
    "paper/generated/forecast_validation.tex",
    "paper/generated/course_allocation.tex",
]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    missing_before = [relative for relative in TARGETS if not (workspace / relative).is_file()]
    if missing_before:
        raise RuntimeError(f"Missing pre-run targets: {missing_before}")
    before = {relative: digest(workspace / relative) for relative in TARGETS}
    command = [sys.executable, str(workspace / "code" / "solve.py"), "--workspace", str(workspace)]
    # Capture bytes because the child can inherit a legacy Windows code page
    # even though all files themselves are UTF-8/UTF-16.
    completed = subprocess.run(command, cwd=workspace, capture_output=True)
    after = {relative: digest(workspace / relative) for relative in TARGETS}
    mismatches = [relative for relative in TARGETS if before[relative] != after[relative]]
    status = "pass" if completed.returncode == 0 and not mismatches else "fail"
    report = {
        "status": status,
        "scope": "second execution of code/solve.py with fixed seed and hash comparison of deterministic core outputs",
        "command": "python code/solve.py --workspace .",
        "target_count": len(TARGETS),
        "matched_count": len(TARGETS) - len(mismatches),
        "mismatches": mismatches,
        "return_code": completed.returncode,
        "stderr": completed.stderr.decode("utf-8", errors="replace")[-4000:],
    }
    path = workspace / "results" / "reproducibility_check.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
