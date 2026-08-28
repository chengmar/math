"""Rerun the canonical Python pipeline and compare every generated artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


PRIMARY_FILES = [
    *[f"working/current-run/extracted/{name}.json" for name in ["problem", "attachment1", "attachment2", "attachment3", "attachment4"]],
    *[f"results/data/{name}" for name in ["<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>", "input_audit.json"]],
    *[f"results/{name}" for name in [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "key_results.json",
        "verification.json",
    ]],
    *[f"figures/{name}" for name in [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]],
    "paper/generated-values.tex",
    "paper/generated-paper-values.json",
    *[f"paper/generated/{name}" for name in [
        "q1_station_table.tex",
        "q2_segment_table.tex",
        "q3_forecast_table.tex",
        "q4_treatment_table.tex",
    ]],
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(workspace: Path) -> dict[str, str]:
    missing = [relative for relative in PRIMARY_FILES if not (workspace / relative).is_file()]
    if missing:
        raise FileNotFoundError("Missing canonical outputs: " + ", ".join(missing))
    return {relative: sha256(workspace / relative) for relative in PRIMARY_FILES}


def run_stage(command: list[str], workspace: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--docx-dir", type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    docx_dir = (<SOURCE_FILE_REDACTED>_dir or workspace / "working" / "docx").resolve()
    extracted_dir = workspace / "working" / "current-run" / "extracted"
    data_dir = workspace / "results" / "data"
    results_dir = workspace / "results"
    figures_dir = workspace / "figures"
    paper_dir = workspace / "paper"

    before = snapshot(workspace)
    commands = [
        [sys.executable, str(workspace / "code" / "extract_docx.py"), "--input-dir", str(docx_dir), "--output-dir", str(extracted_dir)],
        [sys.executable, str(workspace / "code" / "build_data.py"), "--extracted-dir", str(extracted_dir), "--output-dir", str(data_dir)],
        [sys.executable, str(workspace / "code" / "solve_case.py"), "--data-dir", str(data_dir), "--results-dir", str(results_dir), "--figures-dir", str(figures_dir), "--paper-dir", str(paper_dir)],
    ]
    runs = []
    return_code = 0
    for command in commands:
        completed = run_stage(command, workspace)
        runs.append(
            {
                "command": command,
                "exit_code": completed.returncode,
                "stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
                "stdout_length": len(completed.stdout),
                "stderr": completed.stderr,
            }
        )
        if completed.returncode != 0:
            return_code = completed.returncode
            break

    try:
        after = snapshot(workspace)
    except FileNotFoundError:
        after = {}
        return_code = return_code or 1

    comparisons = {
        relative: {
            "before": before[relative],
            "after": after.get(relative),
            "status": "pass" if before[relative] == after.get(relative) else "fail",
        }
        for relative in before
    }
    status = "pass" if return_code == 0 and all(item["status"] == "pass" for item in comparisons.values()) else "fail"
    payload = {
        "schema_version": 2,
        "status": status,
        "pipeline": "extract_docx -> build_data -> solve_case",
        "tracked_file_count": len(PRIMARY_FILES),
        "rerun_return_code": return_code,
        "runs": runs,
        "comparisons": comparisons,
    }
    (results_dir / "reproducibility_check.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": status, "tracked_file_count": len(PRIMARY_FILES), "rerun_return_code": return_code}, ensure_ascii=False))
    if status == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
