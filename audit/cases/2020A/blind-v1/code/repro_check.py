"""Run a clean deterministic rerun and compare key output hashes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
KEY_FILES = [
    "<SOURCE_FILE_REDACTED>",
    "calibration.json",
    "<SOURCE_FILE_REDACTED>",
    "q1_summary.json",
    "q2_summary.json",
    "q3_summary.json",
    "q4_summary.json",
    "<SOURCE_FILE_REDACTED>",
    "all_results.json",
    "generated_macros.tex",
]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "code" / script)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(f"{script} failed: {completed.stderr}")


def main():
    before = {name: digest(RESULTS / name) for name in KEY_FILES}
    run("solve.py")
    after = {name: digest(RESULTS / name) for name in KEY_FILES}
    files = {
        name: {
            "before_sha256": before[name],
            "after_sha256": after[name],
            "status": "pass" if before[name] == after[name] else "fail",
        }
        for name in KEY_FILES
    }
    deterministic_status = "pass" if all(item["status"] == "pass" for item in files.values()) else "fail"
    run("verify_outputs.py")
    run("check_deliverables.py")
    verification = json.loads((RESULTS / "verification.json").read_text(encoding="utf-8"))
    deliverables = json.loads((RESULTS / "deliverable_check.json").read_text(encoding="utf-8"))
    status = "pass" if (
        deterministic_status == "pass"
        and verification["status"] == "pass"
        and deliverables["status"] == "pass"
    ) else "fail"
    report = {
        "status": status,
        "command": "python code/repro_check.py",
        "deterministic_rerun_status": deterministic_status,
        "independent_verification_status": verification["status"],
        "deliverable_check_status": deliverables["status"],
        "files": files,
    }
    (RESULTS / "rerun_hashes.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if status == "pass" else 1)


if __name__ == "__main__":
    main()
