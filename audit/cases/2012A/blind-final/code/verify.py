"""Internal consistency verification with honest tri-state propagation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


EXPECTED_INPUTS = {
    "<SOURCE_FILE_REDACTED>": "6ac7331378ab4b4275b65459d6e094663fee5ca41d1518d010bf5051bb4e0c63",
    "<SOURCE_FILE_REDACTED>": "0b6b845715fbe852ddde8b709e592455eba2c845ef0d3f2be144758f674d2fab",
    "<SOURCE_FILE_REDACTED>": "fdb95e99c881afaff62375182e4dd5d6ca2858297d2ee6450d747ce3b078d547",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def close(a: float, b: float, tolerance: float = 1e-9) -> bool:
    return bool(np.isfinite(a) and np.isfinite(b) and abs(a - b) <= tolerance)


def rollup(statuses: list[str]) -> str:
    if any(status == "fail" for status in statuses):
        return "fail"
    if any(status == "needs_review" for status in statuses):
        return "needs_review"
    return "pass"


def exact_sign_flip(values: np.ndarray) -> float:
    scaled = np.rint(np.asarray(values, dtype=float) * 10).astype(int)
    distribution: Counter[int] = Counter({0: 1})
    for value in scaled:
        updated: Counter[int] = Counter()
        for total, count in distribution.items():
            updated[total + int(value)] += count
            updated[total - int(value)] += count
        distribution = updated
    observed = abs(int(np.sum(scaled)))
    return sum(count for total, count in distribution.items() if abs(total) >= observed) / (2 ** len(scaled))


def check(status: str, evidence: Any) -> dict[str, Any]:
    if status not in {"pass", "fail", "needs_review"}:
        raise ValueError(status)
    return {"status": status, "evidence": evidence}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results = workspace / "results"
    checks: dict[str, dict[str, Any]] = {}

    required = [
        "problem-analysis.md", "data-audit.md", "assumptions.yaml", "variables.yaml",
        "model-selection.md", "solution-report.yaml", "reproducibility.yaml",
        "code/run_all.ps1", "code/check_workspace.py", "code/extract_xls.ps1",
        "code/prepare_data.py", "code/analyze.py", "code/fold_models.py",
        "code/permutation_links.py", "paper/main.tex", "paper/paper.md", "paper/<SOURCE_FILE_REDACTED>",
        "results/summary.json", "results/q1_summary.json", "results/q2_summary.json",
        "results/q3_summary.json", "results/q4_summary.json", "results/fold_audit.json",
        "results/unit_invariance.json", "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>", "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
    ]
    missing = [relative for relative in required if not (workspace / relative).is_file()]
    checks["required_artifacts"] = check("pass" if not missing else "fail", {"missing": missing})

    current_hashes = {
        path.name: sha256(path) for path in sorted((workspace / "input" / "data").glob("*.xls"))
    }
    extraction_manifest = json.loads((results / "extracted" / "manifest.json").read_text(encoding="utf-8"))
    manifest_hashes = {item["workbook"]: item["workbook_sha256"] for item in extraction_manifest}
    provenance_ok = current_hashes == EXPECTED_INPUTS == manifest_hashes
    extracted_bad = []
    for item in extraction_manifest:
        path = results / "extracted" / item["extracted_file"]
        if not path.is_file() or sha256(path) != item["extracted_sha256"]:
            extracted_bad.append(item["extracted_file"])
    checks["input_and_extraction_provenance"] = check(
        "pass" if provenance_ok and not extracted_bad else "fail",
        {"current_hashes": current_hashes, "manifest_hashes": manifest_hashes, "bad_extracted": extracted_bad},
    )

    tasting = pd.read_csv(results / "clean" / "<SOURCE_FILE_REDACTED>")
    score_ok = (
        len(tasting) == 1100
        and tasting.total.between(0, 100).all()
        and not tasting.isna().any().any()
    )
    clean_audit = json.loads((results / "clean" / "data_audit.json").read_text(encoding="utf-8"))
    align_ok = all(item["status"] == "pass" for item in clean_audit["sample_alignment"])
    checks["clean_data_bounds_and_alignment"] = check(
        "pass" if score_ok and align_ok else "fail",
        {"records": len(tasting), "score_ok": bool(score_ok), "alignment_ok": align_ok},
    )

    q1 = json.loads((results / "q1_summary.json").read_text(encoding="utf-8"))
    q1_evidence: dict[str, Any] = {}
    q1_ok = True
    for color in ["red", "white"]:
        pivot = (
            tasting[tasting.color == color]
            .groupby(["panel", "sample_id"]).total.mean()
            .unstack("panel").sort_index()
        )
        p_value = exact_sign_flip((pivot[1] - pivot[2]).to_numpy(float))
        reported = q1["colors"][color]["exact_sign_flip"]["p"]
        q1_evidence[color] = {"recomputed": p_value, "reported": reported}
        q1_ok &= close(p_value, reported, 1e-14)
    checks["q1_exact_recomputation"] = check("pass" if q1_ok else "fail", q1_evidence)

    grades = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    q2_ok = True
    q2_evidence: dict[str, Any] = {}
    for color, expected in [("red", set(range(1, 28))), ("white", set(range(1, 29)))]:
        subset = grades[grades.color == color]
        observed = set(subset.sample_id.astype(int))
        valid_grades = set(subset.grade) == {"A", "B", "C"}
        q2_ok &= observed == expected and valid_grades
        q2_evidence[color] = {"samples": len(observed), "grades": sorted(set(subset.grade))}
    checks["q2_complete_membership"] = check("pass" if q2_ok else "fail", q2_evidence)

    q3 = json.loads((results / "q3_summary.json").read_text(encoding="utf-8"))
    links = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    q3_ok = True
    q3_evidence: dict[str, Any] = {}
    for color in ["red", "white"]:
        subset = links[links.color == color]
        count = int(subset.significant_200k.sum())
        reported = int(q3["colors"][color]["significant_links"])
        repetitions = q3["colors"][color]["multiple_testing"]["permutations_final"]
        q3_ok &= count == reported and repetitions == 200_000
        q3_evidence[color] = {"csv_count": count, "reported": reported, "permutations": repetitions}
    checks["q3_permutation_bh_consistency"] = check("pass" if q3_ok else "fail", q3_evidence)

    q4 = json.loads((results / "q4_summary.json").read_text(encoding="utf-8"))
    predictions = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    q4_ok = True
    q4_evidence: dict[str, Any] = {}
    for color in ["red", "white"]:
        subset = predictions[predictions.color == color]
        residual = subset.observed_quality - subset.predicted_quality
        baseline_residual = subset.observed_quality - subset.training_mean_baseline
        rmse = float(np.sqrt(np.mean(residual**2)))
        q2_value = 1 - float(np.sum(residual**2) / np.sum(baseline_residual**2))
        reported = q4["colors"][color]["metrics"]
        expected_rows = (27 if color == "red" else 28) * 4
        current_ok = len(subset) == expected_rows and close(rmse, reported["rmse"]) and close(q2_value, reported["q2_vs_training_mean"])
        q4_ok &= current_ok
        q4_evidence[color] = {"rows": len(subset), "rmse": rmse, "q2": q2_value, "reported": reported}
    checks["q4_metric_recomputation"] = check("pass" if q4_ok else "fail", q4_evidence)

    fold_audit = json.loads((results / "fold_audit.json").read_text(encoding="utf-8"))
    overlap_records = [row for row in fold_audit["records"] if row["overlap"]]
    checks["fold_isolation"] = check(
        "pass" if fold_audit["status"] == "pass" and not overlap_records else "fail",
        {"records": len(fold_audit["records"]), "overlap_records": len(overlap_records)},
    )

    unit = json.loads((results / "unit_invariance.json").read_text(encoding="utf-8"))
    checks["aroma_unit_invariance"] = check(
        "pass" if unit["overall_status"] == "pass" else "fail",
        {"overall_status": unit["overall_status"], "tolerance": 1e-9},
    )

    constraints_ok = all(
        q3["colors"][color]["support_violations_after_constraint"] == 0
        and q4["colors"][color]["support_violations_after_clip"] == 0
        for color in ["red", "white"]
    )
    checks["physical_support_after_constraint"] = check(
        "pass" if constraints_ok else "fail",
        {
            color: {
                "q3": q3["colors"][color]["support_violations_after_constraint"],
                "q4": q4["colors"][color]["support_violations_after_clip"],
            }
            for color in ["red", "white"]
        },
    )

    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    macros = (results / "paper_values.tex").read_text(encoding="utf-8")
    expected_fragments = [
        f"\\newcommand{{\\RedPanelP}}{{{q1['colors']['red']['exact_sign_flip']['p']:.5f}}}",
        f"\\newcommand{{\\WhiteLinkCount}}{{{q3['colors']['white']['significant_links']}}}",
        f"\\newcommand{{\\RedQualityQ}}{{{q4['colors']['red']['metrics']['q2_vs_training_mean']:.3f}}}",
    ]
    checks["paper_result_macro_consistency"] = check(
        "pass" if all(fragment in macros for fragment in expected_fragments) else "fail",
        {"fragments": expected_fragments},
    )

    prohibited_patterns = [
        r"trainer\\\.agents\\skills",
        r"workspaces\\solve",
        r"blind-v1",
        r"cumcm-a-audit",
    ]
    inspected = [
        workspace / "code" / "run_all.ps1",
        workspace / "code" / "README.md",
        workspace / "reproducibility.yaml",
        workspace / "paper" / "paper.md",
    ]
    hits: list[dict[str, str]] = []
    for path in inspected:
        text = path.read_text(encoding="utf-8")
        for pattern in prohibited_patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                hits.append({"file": path.relative_to(workspace).as_posix(), "pattern": pattern})
    checks["portable_no_cross_stage_paths"] = check("pass" if not hits else "fail", {"hits": hits})

    solution_report = yaml.safe_load((workspace / "solution-report.yaml").read_text(encoding="utf-8"))
    external_status = summary["checks"]["independent_external_validation"]["status"]
    report_external = solution_report["validation"]["external_validation"]
    honest = external_status == "needs_review" and report_external == "needs_review"
    checks["external_validation_semantics"] = check(
        "needs_review" if honest else "fail",
        {"summary": external_status, "solution_report": report_external, "reason": "no independent external data"},
    )
    checks["mathematical_truth_semantics"] = check(
        "needs_review",
        {"reason": "consistency tests do not prove unique mathematical correctness"},
    )
    checks["artifact_manifest_ordering"] = check(
        "pass",
        {"note": "the deterministic artifact manifest is generated immediately after this verification file to avoid a circular verification hash"},
    )

    overall = rollup([item["status"] for item in checks.values()])
    payload = {
        "schema_version": 2,
        "case_id": "2012A",
        "phase": "blind-revision",
        "overall_status": overall,
        "checks": checks,
        "status_rule": "fail > needs_review > pass; needs_review is never collapsed to pass",
    }
    (results / "verification.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    no_fail = sum(item["status"] != "fail" for item in checks.values())
    print(f"[{overall}] {no_fail}/{len(checks)} verification checks without fail")
    for name, item in checks.items():
        if item["status"] == "fail":
            print(f"[fail] {name}: {item['evidence']}")
    return 1 if overall == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
