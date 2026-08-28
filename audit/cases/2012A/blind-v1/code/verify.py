"""Internal consistency checks for generated solution artifacts.

These checks establish reproducibility and cross-file consistency; they do not
claim to prove the external mathematical truth of the chosen models.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze  # noqa: E402


VALID_STATUS = {"pass", "fail", "needs_review"}
SCORE_COLUMNS = [
    "appearance_clarity",
    "appearance_tone",
    "aroma_purity",
    "aroma_intensity",
    "aroma_quality",
    "taste_purity",
    "taste_intensity",
    "taste_persistence",
    "taste_quality",
    "balance_overall",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def close(a: float, b: float, tolerance: float = 1e-9) -> bool:
    return bool(math.isclose(float(a), float(b), rel_tol=tolerance, abs_tol=tolerance))


def collect_statuses(value: Any, path: str = "root") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            current = f"{path}.{key}"
            if key == "status" or key.endswith("_status"):
                if isinstance(item, str):
                    found.append((current, item))
            found.extend(collect_statuses(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(collect_statuses(item, f"{path}[{index}]"))
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    results = root / "results"
    clean = results / "clean"
    figures = root / "figures"
    paper = root / "paper"
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: Any, review: bool = False) -> None:
        status = "pass" if passed else ("needs_review" if review else "fail")
        checks.append({"name": name, "status": status, "detail": detail})

    required = [
        root / "problem-analysis.md",
        root / "data-audit.md",
        root / "assumptions.yaml",
        root / "variables.yaml",
        root / "model-selection.md",
        root / "solution-report.yaml",
        root / "reproducibility.yaml",
        root / "code" / "extract_xls.ps1",
        root / "code" / "prepare_data.py",
        root / "code" / "analyze.py",
        root / "code" / "run_all.ps1",
        paper / "main.tex",
        paper / "paper.md",
        paper / "<SOURCE_FILE_REDACTED>",
        results / "summary.json",
        results / "q1_summary.json",
        results / "<SOURCE_FILE_REDACTED>",
        results / "<SOURCE_FILE_REDACTED>",
        results / "<SOURCE_FILE_REDACTED>",
    ]
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    record("required_artifacts", not missing, {"missing": missing})

    manifest = json.loads((results / "extracted" / "manifest.json").read_text(encoding="utf-8"))
    hash_mismatches = []
    for item in manifest:
        extracted = results / "extracted" / item["extracted_file"]
        source = root / "input" / "data" / item["workbook"]
        if sha256(extracted) != item["extracted_sha256"]:
            hash_mismatches.append(item["extracted_file"])
        if sha256(source) != item["workbook_sha256"]:
            hash_mismatches.append(item["workbook"])
    record("current_input_and_extraction_hashes", not hash_mismatches, hash_mismatches)

    tasting = pd.read_csv(clean / "<SOURCE_FILE_REDACTED>")
    score_sum_error = float(np.max(np.abs(tasting[SCORE_COLUMNS].sum(axis=1) - tasting.total)))
    score_bounds = bool((tasting.total >= 0).all() and (tasting.total <= 100).all())
    repaired_cells = int(tasting.repaired_item_count.sum())
    record(
        "score_identity_bounds_and_repairs",
        score_sum_error < 1e-12 and score_bounds and repaired_cells == 3 and len(tasting) == 1100,
        {
            "records": len(tasting),
            "maximum_sum_error": score_sum_error,
            "repaired_cells": repaired_cells,
        },
    )

    expected = {"red": set(range(1, 28)), "white": set(range(1, 29))}
    alignment_errors: list[str] = []
    for color in ["red", "white"]:
        for panel in [1, 2]:
            observed = set(
                tasting.loc[(tasting.color == color) & (tasting.panel == panel), "sample_id"].astype(int)
            )
            if observed != expected[color]:
                alignment_errors.append(f"tasting-{color}-{panel}")
        for kind in ["grape_conventional", "wine_conventional", "grape_aroma", "wine_aroma"]:
            observed = set(pd.read_csv(clean / f"{kind}_{color}.csv").sample_id.astype(int))
            if observed != expected[color]:
                alignment_errors.append(f"{kind}-{color}")
    record("sample_alignment", not alignment_errors, alignment_errors)

    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    q1_errors: list[str] = []
    for color in ["red", "white"]:
        means = (
            tasting[tasting.color == color]
            .groupby(["panel", "sample_id"])
            .total.mean()
            .unstack("panel")
            .sort_index()
        )
        differences = means[1].to_numpy() - means[2].to_numpy()
        t_stat = float(differences.mean() / (differences.std(ddof=1) / math.sqrt(len(differences))))
        manual_p = float(2 * stats.t.sf(abs(t_stat), len(differences) - 1))
        stored = summary["q1"]["colors"][color]
        if not close(differences.mean(), stored["mean_difference_panel1_minus_panel2"]):
            q1_errors.append(f"{color}-difference")
        if not close(manual_p, stored["paired_t_p"]):
            q1_errors.append(f"{color}-paired-t")
    perfect = np.tile(np.arange(1, 8, dtype=float)[:, None], (1, 4))
    if not close(analyze.icc_two_way(perfect)["icc_2_k"], 1.0):
        q1_errors.append("icc-perfect-boundary")
    if not close(analyze.kendall_w(perfect)[0], 1.0):
        q1_errors.append("kendall-perfect-boundary")
    record("q1_formula_cross_checks", not q1_errors, q1_errors)

    grades = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    q2_errors: list[str] = []
    for color in ["red", "white"]:
        subset = grades[grades.color == color]
        if set(subset.sample_id.astype(int)) != expected[color] or subset.sample_id.duplicated().any():
            q2_errors.append(f"{color}-coverage")
        if set(subset.grade) != {"A", "B", "C"}:
            q2_errors.append(f"{color}-labels")
        grade_means = subset.groupby("grade").quality_mean.mean()
        if not (grade_means["A"] > grade_means["B"] > grade_means["C"]):
            q2_errors.append(f"{color}-ordering")
        stored_members = summary["q2"]["colors"][color]["members"]
        for grade in ["A", "B", "C"]:
            observed_members = set(subset.loc[subset.grade == grade, "sample_id"].astype(int))
            if observed_members != set(stored_members[grade]):
                q2_errors.append(f"{color}-{grade}-members")
    record("q2_partition_coverage_and_order", not q2_errors, q2_errors)

    predictions = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    q4_errors: list[str] = []
    for color in ["red", "white"]:
        subset = predictions[predictions.color == color].sort_values("sample_id")
        metrics = analyze.regression_metrics(
            subset.observed_quality.to_numpy(dtype=float),
            subset.oof_predicted_quality.to_numpy(dtype=float),
        )
        stored = summary["q4"]["colors"][color]["main_nested_loo"]
        for name in ["rmse", "mae", "q2", "spearman_rho"]:
            if not close(metrics[name], stored[name], tolerance=1e-8):
                q4_errors.append(f"{color}-{name}")
    record("q4_prediction_metric_recalculation", not q4_errors, q4_errors)

    status_errors = [
        {"path": path, "value": value}
        for path, value in collect_statuses(summary)
        if value not in VALID_STATUS
    ]
    record("status_vocabulary", not status_errors, status_errors)

    macro_text = (results / "paper_values.tex").read_text(encoding="utf-8")
    macros = dict(re.findall(r"\\newcommand\{\\([^}]+)\}\{([^}]+)\}", macro_text))
    expected_macros = {
        "RedPanelDiff": summary["q1"]["colors"]["red"]["mean_difference_panel1_minus_panel2"],
        "WhitePanelDiff": summary["q1"]["colors"]["white"]["mean_difference_panel1_minus_panel2"],
        "RedCrossQ": summary["q3"]["colors"]["red"]["nested_loo_q2"],
        "WhiteCrossQ": summary["q3"]["colors"]["white"]["nested_loo_q2"],
        "RedQualityRMSE": summary["q4"]["colors"]["red"]["main_nested_loo"]["rmse"],
        "WhiteQualityRMSE": summary["q4"]["colors"]["white"]["main_nested_loo"]["rmse"],
    }
    macro_errors = []
    for name, expected_value in expected_macros.items():
        if name not in macros or not close(float(macros[name]), round(float(expected_value), 2 if "RMSE" in name or "PanelDiff" in name else 3), tolerance=5e-4):
            macro_errors.append(name)
    record("paper_macro_consistency", not macro_errors, macro_errors)

    figure_names = [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]
    small_figures = [name for name in figure_names if not (figures / name).is_file() or (figures / name).stat().st_size < 10_000]
    record("figures_exist_and_nontrivial", not small_figures, small_figures)

    pdf_ok = (paper / "<SOURCE_FILE_REDACTED>").is_file() and (paper / "<SOURCE_FILE_REDACTED>").stat().st_size > 50_000
    log_text = (paper / "main.log").read_text(encoding="utf-8", errors="ignore") if (paper / "main.log").is_file() else ""
    fatal = "Fatal error" in log_text or "Emergency stop" in log_text
    record(
        "paper_compilation",
        pdf_ok and not fatal,
        {"pdf_bytes": (paper / "<SOURCE_FILE_REDACTED>").stat().st_size if (paper / "<SOURCE_FILE_REDACTED>").is_file() else 0, "fatal_log_marker": fatal},
    )

    review_items = [
        "pre-session XLS byte identity is not proven",
        "Q1 trusted-panel metrics conflict",
        "Q2 grading is unstable to perturbation and target panel",
        "external mathematical correctness and generalization are not proven by internal checks",
    ]
    record("known_limits_disclosed", False, review_items, review=True)

    failures = [item for item in checks if item["status"] == "fail"]
    overall = "fail" if failures else "pass"
    verification = {
        "overall_status": overall,
        "scope_note": "Internal reproducibility and cross-file consistency only; not a proof of external mathematical correctness.",
        "checks": checks,
    }
    (results / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    manifest_paths: list[Path] = []
    for relative in [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "paper/main.tex",
        "paper/paper.md",
        "paper/<SOURCE_FILE_REDACTED>",
    ]:
        path = root / relative
        if path.is_file():
            manifest_paths.append(path)
    manifest_paths.extend(sorted((root / "code").glob("*")))
    manifest_paths.extend(sorted(figures.glob("*.png")))
    manifest_paths.extend(
        sorted(
            path
            for path in results.glob("*")
            if path.is_file() and path.name not in {"artifact-manifest.json", "verification.json"}
        )
    )
    artifact_manifest = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in manifest_paths
        if path.is_file()
    ]
    (results / "artifact-manifest.json").write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[{overall}] {len(checks) - len(failures)}/{len(checks)} verification checks without fail")
    if failures:
        for item in failures:
            print(f"[fail] {item['name']}: {item['detail']}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
