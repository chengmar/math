"""Verify required deliverables and paper/result consistency without overclaiming."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml


EXPECTED_INPUT_HASHES = {
    "input/problem/<SOURCE_FILE_REDACTED>": "CEA35513E302801D4504F3FEBDF444AF783D928C4748BDF91A21724A910E271A",
    "input/attachments/<SOURCE_FILE_REDACTED>": "DDD7B8E70AA727A2858E2476DDFEDA7E3042BE09D55B304297F990A203071E4F",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results_dir = workspace / "results"

    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/extract_docx.py",
        "code/build_data.py",
        "code/solve_case.py",
        "code/verify_results.py",
        "code/run_all.ps1",
        "results/key_results.json",
        "results/verification.json",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/paper.md",
        "paper/generated-values.tex",
    ]
    tests: dict[str, str] = {}
    missing = [relative for relative in required if not (workspace / relative).is_file()]
    tests["required_outputs_exist"] = "pass" if not missing else "fail"

    input_hashes = {}
    for relative, expected in EXPECTED_INPUT_HASHES.items():
        actual = sha256(workspace / relative).upper()
        input_hashes[relative] = actual
        tests[f"input_hash_{Path(relative).name}"] = "pass" if actual == expected else "fail"

    internal = load_json(results_dir / "verification.json")
    tests["no_internal_test_failed"] = (
        "pass" if "fail" not in internal["tests"].values() else "fail"
    )
    tests["external_mathematical_and_policy_guarantee"] = "needs_review"

    generated_values = load_json(workspace / "paper/generated-paper-values.json")
    main_tex = (workspace / "paper/main.tex").read_text(encoding="utf-8")
    paper_md = (workspace / "paper/paper.md").read_text(encoding="utf-8")
    tests["latex_loads_generated_values"] = (
        "pass" if "\\input{generated-values}" in main_tex else "fail"
    )
    key_markdown_values = [
        generated_values["OverallDrinkablePct"],
        generated_values["OverallIVVPct"],
        generated_values["OverallInferiorPct"],
        generated_values["TreatmentFirst"],
        generated_values["TreatmentLast"],
    ]
    tests["markdown_contains_key_generated_values"] = (
        "pass" if all(value in paper_md for value in key_markdown_values) else "fail"
    )

    with (workspace / "solution-report.yaml").open(encoding="utf-8") as handle:
        solution_report = yaml.safe_load(handle)
    with (workspace / "reproducibility.yaml").open(encoding="utf-8") as handle:
        reproducibility = yaml.safe_load(handle)
    tests["solution_report_phase_is_solve"] = (
        "pass" if solution_report.get("phase") == "solve" else "fail"
    )
    tests["no_freeze_or_cross_phase_claim"] = (
        "pass"
        if solution_report.get("freeze") is False
        and solution_report.get("cross_phase_resume") is False
        else "fail"
    )
    tests["reproducibility_command_recorded"] = (
        "pass" if reproducibility.get("commands") else "fail"
    )

    tracked_roots = [workspace / "code", workspace / "results", workspace / "figures", workspace / "paper"]
    tracked_files = [workspace / relative for relative in required[:7]]
    excluded_manifest_paths = {
        "results/reproduction-manifest.json",
        "results/final-verification.json",
    }
    for root in tracked_roots:
        tracked_files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.relative_to(workspace).as_posix() not in excluded_manifest_paths
        )
    manifest_entries = []
    for path in sorted(set(tracked_files)):
        # Compilers may add auxiliary files; all are hashed, but they do not
        # alter the required-output checks above.
        manifest_entries.append(
            {
                "path": path.relative_to(workspace).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    manifest = {
        "status": "pass",
        "algorithm": "sha256",
        "files": manifest_entries,
        "input_hashes": input_hashes,
    }
    (results_dir / "reproduction-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if "fail" in tests.values():
        overall = "fail"
    elif "needs_review" in tests.values():
        overall = "needs_review"
    else:
        overall = "pass"
    final = {
        "overall": overall,
        "tests": tests,
        "missing": missing,
        "internal_verification": internal,
    }
    (results_dir / "final-verification.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(final, ensure_ascii=False, indent=2))
    if overall == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
