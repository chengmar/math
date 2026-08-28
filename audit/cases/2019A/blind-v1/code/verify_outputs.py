"""Verify artifacts, record/compare deterministic hashes, and update evidence status."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
from typing import Any

from PIL import Image
import yaml


ALLOWED_STATUSES = {"pass", "fail", "needs_review"}
MUTABLE_RESULT_FILES = {
    "all-results.json",
    "<SOURCE_FILE_REDACTED>",
    "rerun-baseline.json",
    "validation.json",
    "verification.json",
}


def hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def core_artifacts(workspace: Path) -> list[Path]:
    results = [
        path
        for path in sorted((workspace / "results").iterdir())
        if path.is_file() and path.name not in MUTABLE_RESULT_FILES
    ]
    figures = sorted((workspace / "figures").glob("*.png"))
    return results + figures


def artifact_manifest(workspace: Path) -> dict[str, Any]:
    files = {}
    for path in core_artifacts(workspace):
        relative = path.relative_to(workspace).as_posix()
        files[relative] = {"sha256": hash_file(path), "size_bytes": path.stat().st_size}
    return {
        "status": "needs_review",
        "scope": "run_all deterministic core outputs; mutable evidence status files excluded",
        "file_count": len(files),
        "files": files,
    }


def check_status_fields(value: Any, path: str = "root") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}.{key}"
            judgement_keys = {
                "status",
                "overall_status",
                "artifact_checks_status",
                "full_rerun_status",
                "external_validity_status",
                "completion_status",
            }
            if key in judgement_keys and isinstance(item, str):
                if item not in ALLOWED_STATUSES:
                    errors.append(f"{key_path}={item!r}")
            errors.extend(check_status_fields(item, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(check_status_fields(item, f"{path}[{index}]"))
    return errors


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def nearly_equal(left: float, right: float, tolerance: float = 1e-10) -> bool:
    return math.isfinite(left) and math.isfinite(right) and abs(left - right) <= tolerance


def perform_checks(workspace: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/model.py",
        "code/run_all.py",
        "code/verify_outputs.py",
        "results/problem1-summary.json",
        "results/problem2-summary.json",
        "results/problem3-summary.json",
        "results/validation.json",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/paper.md",
    ]
    missing = [relative for relative in required if not (workspace / relative).is_file()]
    checks.append(
        {
            "check": "required artifacts exist",
            "status": "pass" if not missing else "fail",
            "details": {"missing": missing},
        }
    )

    yaml_values: dict[str, Any] = {}
    yaml_errors = []
    for relative in ("assumptions.yaml", "variables.yaml", "solution-report.yaml", "reproducibility.yaml"):
        try:
            yaml_values[relative] = yaml.safe_load(
                (workspace / relative).read_text(encoding="utf-8")
            )
        except Exception as error:  # pragma: no cover - evidence path
            yaml_errors.append(f"{relative}: {error}")
    checks.append(
        {
            "check": "YAML parses",
            "status": "pass" if not yaml_errors else "fail",
            "details": {"errors": yaml_errors},
        }
    )

    status_errors = []
    for relative, value in yaml_values.items():
        status_errors.extend(check_status_fields(value, relative))
    for relative in ("results/data-audit.json", "results/validation.json"):
        if (workspace / relative).is_file():
            status_errors.extend(check_status_fields(load_json(workspace / relative), relative))
    checks.append(
        {
            "check": "automatic status vocabulary",
            "status": "pass" if not status_errors else "fail",
            "details": {"invalid_status_fields": status_errors},
        }
    )

    manifest_errors = []
    for item in load_json(workspace / "results" / "input-manifest.json"):
        path = workspace / item["relative_path"]
        if not path.is_file():
            manifest_errors.append(f"missing {item['relative_path']}")
        elif hash_file(path) != item["sha256"]:
            manifest_errors.append(f"hash mismatch {item['relative_path']}")
    checks.append(
        {
            "check": "input SHA-256 manifest",
            "status": "pass" if not manifest_errors else "fail",
            "details": {"errors": manifest_errors},
        }
    )

    p1 = load_json(workspace / "results" / "problem1-summary.json")
    p2 = load_json(workspace / "results" / "problem2-summary.json")
    p3 = load_json(workspace / "results" / "problem3-summary.json")
    validation = load_json(workspace / "results" / "validation.json")
    numerical_errors = []
    if abs(p1["holding"]["100_mpa"]["metrics_20s_to_30s"]["mean_mpa"] - 100.0) > 0.1:
        numerical_errors.append("Problem 1 100 MPa holding mean outside 0.1 MPa")
    if abs(p1["holding"]["150_mpa"]["metrics_20s_to_30s"]["mean_mpa"] - 150.0) > 0.1:
        numerical_errors.append("Problem 1 150 MPa holding mean outside 0.1 MPa")
    for item in p1["transitions"]:
        mean = item["hold_evaluation_500ms"]["mean_mpa"]
        if abs(mean - 150.0) > 0.1:
            numerical_errors.append(f"Transition {item['transition_ms']} ms post-hold mean")
    if abs(p2["independent_evaluation_window_metrics"]["mean_mpa"] - 100.0) > 0.2:
        numerical_errors.append("Problem 2 evaluation mean outside 0.2 MPa")
    best_phase = min(p3["phase_comparison"], key=lambda row: row["evaluation_rmse_mpa"])
    if best_phase["second_injector_offset_ms"] != 50.0:
        numerical_errors.append("50 ms is not the best tested injector phase")
    if p3["precision_scheme"]["metrics"]["peak_to_peak_mpa"] >= p3["economy_scheme"]["metrics"]["peak_to_peak_mpa"]:
        numerical_errors.append("Precision scheme does not improve peak-to-peak pressure")
    if validation["convergence"]["status"] != "pass":
        numerical_errors.append("Time-step convergence is not pass")
    checks.append(
        {
            "check": "numerical acceptance criteria",
            "status": "pass" if not numerical_errors else "fail",
            "details": {"errors": numerical_errors},
        }
    )

    figure_errors = []
    for path in sorted((workspace / "figures").glob("*.png")):
        try:
            if path.stat().st_size < 1000:
                raise ValueError("file too small")
            with Image.open(path) as image:
                image.verify()
        except Exception as error:  # pragma: no cover - evidence path
            figure_errors.append(f"{path.name}: {error}")
    checks.append(
        {
            "check": "PNG figures decode",
            "status": "pass" if not figure_errors else "fail",
            "details": {"errors": figure_errors},
        }
    )

    paper_values = load_json(workspace / "results" / "paper-values.json")
    markdown = (workspace / "paper" / "paper.md").read_text(encoding="utf-8")
    expected_strings = [
        f"{paper_values['p1_tau_100_ms']:.5f}",
        f"{paper_values['p1_tau_150_ms']:.5f}",
        f"{paper_values['p2_omega_rad_per_ms']:.6f}",
        f"{paper_values['p2_rpm']:.2f}",
        f"{paper_values['p3_economy_rpm']:.2f}",
        f"{paper_values['p3_precision_ptp_mpa']:.3f}",
        f"{paper_values['p3_relief_ratio']:.2f}",
    ]
    paper_errors = [text for text in expected_strings if text not in markdown]
    tex = (workspace / "results" / "paper-values.tex").read_text(encoding="utf-8")
    if f"{{{paper_values['p2_omega_rad_per_ms']:.5f}}}" not in tex:
        paper_errors.append("generated TeX p2 omega macro")
    checks.append(
        {
            "check": "paper key numbers trace to generated values",
            "status": "pass" if not paper_errors else "fail",
            "details": {"missing_expected_strings": paper_errors},
        }
    )

    report = yaml_values.get("solution-report.yaml", {})
    report_errors = []
    comparisons = [
        (report.get("key_results", {}).get("problem_1", {}).get("hold_100_open_ms"), paper_values["p1_tau_100_ms"]),
        (report.get("key_results", {}).get("problem_2", {}).get("omega_rad_per_ms"), paper_values["p2_omega_rad_per_ms"]),
        (report.get("key_results", {}).get("problem_3", {}).get("precision_peak_to_peak_mpa"), paper_values["p3_precision_ptp_mpa"]),
    ]
    for left, right in comparisons:
        if left is None or not nearly_equal(float(left), float(right)):
            report_errors.append(f"solution-report value {left!r} != {right!r}")
    checks.append(
        {
            "check": "solution-report key-number consistency",
            "status": "pass" if not report_errors else "fail",
            "details": {"errors": report_errors},
        }
    )
    return checks


def compare_manifest(workspace: Path, baseline: dict[str, Any]) -> dict[str, Any]:
    current = artifact_manifest(workspace)
    before = baseline["files"]
    after = current["files"]
    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = sorted(
        path for path in set(before) & set(after) if before[path]["sha256"] != after[path]["sha256"]
    )
    return {
        "status": "pass" if not (missing or added or changed) else "fail",
        "compared_file_count": len(before),
        "missing": missing,
        "added": added,
        "changed": changed,
    }


def update_rerun_evidence(workspace: Path, status: str, comparison: dict[str, Any]) -> None:
    validation_path = workspace / "results" / "validation.json"
    validation = load_json(validation_path)
    for item in validation["checks"]:
        if item["check"] == "deterministic full-script rerun":
            item["status"] = status
            item["evidence"] = (
                f"Compared {comparison.get('compared_file_count', 0)} core files; "
                f"changed={len(comparison.get('changed', []))}."
            )
    write_json(validation_path, validation)

    all_results_path = workspace / "results" / "all-results.json"
    if all_results_path.is_file():
        all_results = load_json(all_results_path)
        all_results["validation"] = validation
        write_json(all_results_path, all_results)

    evidence_path = workspace / "results" / "<SOURCE_FILE_REDACTED>"
    with evidence_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        if row["evidence_layer"] == "execution reproducibility":
            row["status"] = status
            row["artifact"] = "results/rerun-baseline.json; results/verification.json"
    with evidence_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    report_path = workspace / "solution-report.yaml"
    report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
    report["validation"]["code_full_rerun"]["status"] = status
    report["validation"]["code_full_rerun"]["evidence"] = "results/verification.json"
    report["evidence_boundary"]["execution_reproducibility"] = status
    report_path.write_text(
        yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    reproducibility_path = workspace / "reproducibility.yaml"
    reproducibility = yaml.safe_load(reproducibility_path.read_text(encoding="utf-8"))
    reproducibility["full_script_rerun"]["status"] = status
    reproducibility["full_script_rerun"]["compared_file_count"] = comparison.get(
        "compared_file_count", 0
    )
    reproducibility["full_script_rerun"]["changed_file_count"] = len(
        comparison.get("changed", [])
    )
    reproducibility_path.write_text(
        yaml.safe_dump(reproducibility, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--record-manifest", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results_dir = workspace / "results"
    baseline_path = results_dir / "rerun-baseline.json"

    checks = perform_checks(workspace)
    check_status = "fail" if any(item["status"] == "fail" for item in checks) else "pass"
    rerun_status = "needs_review"
    comparison: dict[str, Any] = {
        "status": "needs_review",
        "reason": "No second-run comparison performed in this invocation.",
    }

    if args.record_manifest:
        manifest = artifact_manifest(workspace)
        write_json(baseline_path, manifest)
        comparison = {
            "status": "needs_review",
            "recorded_file_count": manifest["file_count"],
            "reason": "Baseline recorded; run code/run_all.py again, then verify without flag.",
        }
    elif baseline_path.is_file():
        comparison = compare_manifest(workspace, load_json(baseline_path))
        rerun_status = comparison["status"]
        update_rerun_evidence(workspace, rerun_status, comparison)

    overall = "fail" if check_status == "fail" or rerun_status == "fail" else "needs_review"
    verification = {
        "overall_status": overall,
        "artifact_checks_status": check_status,
        "full_rerun_status": rerun_status,
        "checks": checks,
        "rerun_comparison": comparison,
        "external_validity_status": "needs_review",
        "note": (
            "Overall remains needs_review because deterministic reproduction and model-internal "
            "checks do not establish experimental external validity."
        ),
    }
    write_json(results_dir / "verification.json", verification)
    if check_status == "fail" or rerun_status == "fail":
        print("[FAIL] output verification failed")
        return 1
    if args.record_manifest:
        print("[NEEDS_REVIEW] baseline recorded; perform a full rerun")
    elif rerun_status == "pass":
        print("[PASS] artifacts and deterministic core-output rerun match")
    else:
        print("[NEEDS_REVIEW] artifact checks pass; rerun comparison not yet available")
    return 0


if __name__ == "__main__":
    sys.exit(main())
