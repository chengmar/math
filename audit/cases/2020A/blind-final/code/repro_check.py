"""Deterministically rerun solve, oracle verification, and deliverable gates."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"

SOLVE_FILES = [
    "results/data_audit.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/calibration.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/q1_summary.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/q2_summary.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/q3_summary.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/q4_summary.json",
    "results/q4_epsilon_1.<SOURCE_FILE_REDACTED>",
    "results/q4_epsilon_1.<SOURCE_FILE_REDACTED>",
    "results/q4_epsilon_1.<SOURCE_FILE_REDACTED>",
    "results/q4_epsilon_1.<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/sensitivity_summary.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/joint_perturbation_summary.json",
    "results/boundary_checks.json",
    "results/all_results.json",
    "results/generated_macros.tex",
    "results/generated_summary.md",
    "results/reproducibility_runtime.json",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
    "figures/<SOURCE_FILE_REDACTED>",
]
VERIFY_FILES = ["results/verification.json", "results/<SOURCE_FILE_REDACTED>"]
DELIVERABLE_FILES = ["results/deliverable_check.json"]
def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def hashes(paths: list[str]) -> dict[str, str]:
    return {rel: digest(ROOT / rel) for rel in paths}


def compare(before: dict[str, str], after: dict[str, str]) -> dict[str, dict[str, str]]:
    return {
        rel: {
            "before_sha256": before[rel],
            "after_sha256": after[rel],
            "status": "pass" if before[rel] == after[rel] else "fail",
        }
        for rel in before
    }


def run(script: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-E", "-s", str(ROOT / "code" / script)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    result = {
        "script": script,
        "command": f"python -E -s code/{script}",
        "exit_code": completed.returncode,
        "stdout_bytes": len(completed.stdout.encode("utf-8")),
        "stderr_bytes": len(completed.stderr.encode("utf-8")),
        "status": "pass" if completed.returncode == 0 and not completed.stderr else "fail",
    }
    if result["status"] == "fail":
        result["stderr"] = completed.stderr
    return result


def main():
    before_solve = hashes(SOLVE_FILES)
    solve_run = run("solve.py")
    after_solve = hashes(SOLVE_FILES)
    solve_comparison = compare(before_solve, after_solve)
    solve_hash_status = (
        "pass" if all(row["status"] == "pass" for row in solve_comparison.values()) else "fail"
    )

    before_verify = hashes(VERIFY_FILES)
    verify_run = run("verify_outputs.py")
    after_verify = hashes(VERIFY_FILES)
    verify_comparison = compare(before_verify, after_verify)
    verify_hash_status = (
        "pass" if all(row["status"] == "pass" for row in verify_comparison.values()) else "fail"
    )

    before_deliverables = hashes(DELIVERABLE_FILES)
    deliverable_run = run("check_deliverables.py")
    after_deliverables = hashes(DELIVERABLE_FILES)
    deliverable_comparison = compare(before_deliverables, after_deliverables)
    deliverable_hash_status = (
        "pass"
        if all(row["status"] == "pass" for row in deliverable_comparison.values())
        else "fail"
    )

    verification = json.loads((RESULTS / "verification.json").read_text(encoding="utf-8"))
    deliverables = json.loads((RESULTS / "deliverable_check.json").read_text(encoding="utf-8"))
    run_status = (
        "pass"
        if all(item["status"] == "pass" for item in [solve_run, verify_run, deliverable_run])
        else "fail"
    )
    overall = (
        "pass"
        if all(
            status == "pass"
            for status in [
                run_status,
                solve_hash_status,
                verify_hash_status,
                deliverable_hash_status,
                verification["status"],
                deliverables["status"],
            ]
        )
        else "fail"
    )
    report = {
        "status": overall,
        "command": "python -E -s code/repro_check.py",
        "runs": [solve_run, verify_run, deliverable_run],
        "deterministic_solve_payload_status": solve_hash_status,
        "solve_files_compared": len(SOLVE_FILES),
        "solve_files": solve_comparison,
        "deterministic_verification_status": verify_hash_status,
        "verification_files": verify_comparison,
        "deterministic_deliverable_status": deliverable_hash_status,
        "deliverable_files": deliverable_comparison,
        "independent_verification_status": verification["status"],
        "deliverable_check_status": deliverables["status"],
        "source_hash_manifest": "needs_review",
        "source_hash_note": "Current source hashes are intentionally deferred to the external blind-final freeze manifest.",
        "freeze_status": "needs_review",
        "freeze_note": "External freezing is intentionally not performed by this script.",
    }
    (RESULTS / "rerun_hashes.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if overall == "pass" else 1)


if __name__ == "__main__":
    main()
