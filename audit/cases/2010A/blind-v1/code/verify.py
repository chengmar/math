"""Independent checks for numerical integration, tables, metrics, and paper."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from scipy.integrate import quad


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def circle_area(radius: float, level: float) -> float:
    if level <= -radius:
        return 0.0
    if level >= radius:
        return math.pi * radius * radius
    ratio = level / radius
    return radius * radius * (
        ratio * math.sqrt(max(0.0, 1.0 - ratio * ratio))
        + math.asin(ratio)
        + math.pi / 2.0
    )


def ellipse_area(a: float, b: float, level: float) -> float:
    if level <= -b:
        return 0.0
    if level >= b:
        return math.pi * a * b
    ratio = level / b
    return a * b * (
        ratio * math.sqrt(max(0.0, 1.0 - ratio * ratio))
        + math.asin(ratio)
        + math.pi / 2.0
    )


def integrate_piecewise(function: Callable[[float], float], intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    for lower, upper in intervals:
        value, _ = quad(
            function,
            lower,
            upper,
            epsabs=1e-11,
            epsrel=1e-11,
            limit=500,
        )
        total += value
    return total


def small_volume_quad_l(
    height_m: float,
    a: float,
    b: float,
    length: float,
    probe_x: float,
    alpha_deg: float,
    level_offset_m: float,
) -> float:
    slope = math.tan(math.radians(alpha_deg))
    value, _ = quad(
        lambda x: ellipse_area(
            a,
            b,
            height_m + level_offset_m - b - slope * (x - probe_x),
        ),
        0.0,
        length,
        epsabs=1e-11,
        epsrel=1e-11,
        limit=500,
    )
    return value * 1000.0


def actual_volume_quad_l(
    height_m: float,
    alpha_deg: float,
    beta_deg: float,
) -> float:
    radius = 1.5
    cylinder_length = 8.0
    cap_depth = 1.0
    sphere_radius = 1.625
    center_offset = sphere_radius - cap_depth
    slope = math.tan(math.radians(alpha_deg))
    beta_cos = math.cos(math.radians(beta_deg))

    def rho(x: float) -> float:
        if x < 0.0:
            return math.sqrt(max(0.0, sphere_radius**2 - (x - center_offset) ** 2))
        if x > cylinder_length:
            right_center = cylinder_length - center_offset
            return math.sqrt(max(0.0, sphere_radius**2 - (x - right_center) ** 2))
        return radius

    def integrand(x: float) -> float:
        level = beta_cos * (height_m - radius) - slope * (x - 2.0)
        return circle_area(rho(x), level)

    return (
        integrate_piecewise(integrand, [(-1.0, 0.0), (0.0, 8.0), (8.0, 9.0)])
        * 1000.0
    )


def metric(residual: np.ndarray, kind: str) -> float:
    if kind == "rmse":
        return float(np.sqrt(np.mean(residual**2)))
    if kind == "mae":
        return float(np.mean(np.abs(residual)))
    if kind == "max_abs":
        return float(np.max(np.abs(residual)))
    raise ValueError(kind)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results = workspace / "results"
    paper = workspace / "paper"

    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((results / "run_manifest.json").read_text(encoding="utf-8"))
    small_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    actual_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    actual_residuals = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    small_holdout = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    candidates = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")

    checks: dict[str, dict[str, object]] = {}

    input_paths = {
        "problem_doc": workspace / "input" / "problem" / "<SOURCE_FILE_REDACTED>",
        "attachment_1": workspace / "input" / "data" / "<SOURCE_FILE_REDACTED>",
        "attachment_2": workspace / "input" / "data" / "<SOURCE_FILE_REDACTED>",
    }
    input_hash_match = all(
        sha256(path) == manifest["input_sha256"][name]
        for name, path in input_paths.items()
    )
    checks["input_hashes"] = {"status": "pass" if input_hash_match else "fail"}

    small_step_status = (
        len(small_table) == 121
        and np.array_equal(small_table["height_cm"].to_numpy(), np.arange(121))
    )
    checks["small_table_grid"] = {"status": "pass" if small_step_status else "fail"}
    actual_step_status = (
        len(actual_table) == 31
        and np.array_equal(actual_table["height_cm"].to_numpy(), np.arange(0, 301, 10))
    )
    checks["actual_table_grid"] = {"status": "pass" if actual_step_status else "fail"}
    checks["table_monotonicity"] = {
        "status": "pass"
        if np.all(np.diff(small_table["volume_l"]) >= 0.0)
        and np.all(np.diff(actual_table["volume_l"]) >= 0.0)
        else "fail"
    }

    small_model = summary["small_tank"]["production_model"]
    small_quad_differences = []
    for height_cm in (0, 60, 120):
        expected = small_volume_quad_l(
            height_cm / 100.0,
            small_model["effective_horizontal_semiaxis_m"],
            small_model["vertical_semiaxis_m"],
            small_model["length_m"],
            small_model["probe_x_m"],
            small_model["alpha_deg"],
            small_model["level_offset_mm"] / 1000.0,
        )
        table_value = float(
            small_table.loc[small_table["height_cm"] == height_cm, "volume_l"].iloc[0]
        )
        small_quad_differences.append(abs(expected - table_value))
    checks["small_independent_quad"] = {
        "status": "pass" if max(small_quad_differences) < 0.005 else "fail",
        "max_abs_difference_l": max(small_quad_differences),
    }

    actual_model = summary["actual_tank"]["production_model"]
    actual_quad_differences = []
    for height_cm in (0, 150, 300):
        expected = actual_volume_quad_l(
            height_cm / 100.0,
            actual_model["alpha_deg"],
            actual_model["beta_abs_deg"],
        )
        table_value = float(
            actual_table.loc[actual_table["height_cm"] == height_cm, "volume_l"].iloc[0]
        )
        actual_quad_differences.append(abs(expected - table_value))
    checks["actual_independent_quad"] = {
        "status": "pass" if max(actual_quad_differences) < 0.005 else "fail",
        "max_abs_difference_l": max(actual_quad_differences),
    }

    analytic_capacity_l = (
        math.pi * 1.5**2 * 8.0
        + 2.0 * math.pi * 1.0**2 * (3.0 * 1.625 - 1.0) / 3.0
    ) * 1000.0
    recorded_capacity_l = summary["actual_tank"]["dimensions"]["capacity_l"]
    checks["analytic_capacity"] = {
        "status": "pass"
        if abs(analytic_capacity_l - recorded_capacity_l) < 1e-8
        else "fail",
        "difference_l": abs(analytic_capacity_l - recorded_capacity_l),
    }

    actual_residual_array = actual_residuals["model_residual_l"].to_numpy(float)
    recorded_actual_metrics = summary["actual_tank"]["production_model"]["all_data_metrics"]
    actual_metric_difference = max(
        abs(metric(actual_residual_array, "rmse") - recorded_actual_metrics["rmse_l"]),
        abs(metric(actual_residual_array, "mae") - recorded_actual_metrics["mae_l"]),
        abs(metric(actual_residual_array, "max_abs") - recorded_actual_metrics["max_abs_l"]),
    )
    checks["actual_metrics_recomputed"] = {
        "status": "pass" if actual_metric_difference < 1e-7 else "fail",
        "max_abs_difference_l": actual_metric_difference,
    }

    small_residual_array = small_holdout["calibrated_residual_l"].to_numpy(float)
    recorded_small_metrics = summary["small_tank"]["candidate_validation"][
        "calibrated_geometry"
    ]["holdout_metrics"]
    small_metric_difference = max(
        abs(metric(small_residual_array, "rmse") - recorded_small_metrics["rmse_l"]),
        abs(metric(small_residual_array, "mae") - recorded_small_metrics["mae_l"]),
        abs(metric(small_residual_array, "max_abs") - recorded_small_metrics["max_abs_l"]),
    )
    checks["small_holdout_metrics_recomputed"] = {
        "status": "pass" if small_metric_difference < 1e-7 else "fail",
        "max_abs_difference_l": small_metric_difference,
    }

    actual_candidates = candidates[candidates["task"] == "actual_tank"].set_index("candidate")
    nested_improvement = (
        actual_candidates.loc["two_angle", "validation_rmse_l"]
        < actual_candidates.loc["longitudinal_only", "validation_rmse_l"]
        < actual_candidates.loc["zero_tilt", "validation_rmse_l"]
    )
    checks["nested_candidate_improvement"] = {
        "status": "pass" if nested_improvement else "fail"
    }

    refill_row = actual_residuals.index[actual_residuals["serial"] == 503][0]
    refill_model_change = (
        actual_residuals.loc[refill_row, "model_volume_l"]
        - actual_residuals.loc[refill_row - 1, "model_volume_l"]
    )
    refill_meter_change = (
        actual_residuals.loc[refill_row, "cumulative_net_flow_l"]
        - actual_residuals.loc[refill_row - 1, "cumulative_net_flow_l"]
    )
    refill_difference = refill_model_change - refill_meter_change
    recorded_refill_difference = summary["actual_tank"]["increment_validation"][
        "refill_model_minus_meter_l"
    ]
    checks["refill_extreme_change"] = {
        "status": "pass"
        if abs(refill_difference - recorded_refill_difference) < 1e-7
        and abs(refill_difference) < 0.1
        else "fail",
        "model_minus_meter_l": refill_difference,
    }

    paper_md = paper / "paper.md"
    paper_tex = paper / "main.tex"
    if paper_md.exists() and paper_tex.exists():
        md_text = paper_md.read_text(encoding="utf-8")
        tex_text = paper_tex.read_text(encoding="utf-8")
        required_tokens = [
            f"{actual_model['alpha_deg']:.4f}",
            f"{actual_model['beta_abs_deg']:.4f}",
            f"{summary['actual_tank']['candidate_validation']['two_angle']['holdout_metrics']['rmse_l']:.3f}",
            f"{recorded_small_metrics['rmse_l']:.3f}",
            f"{summary['actual_tank']['dimensions']['capacity_l']:.2f}",
            f"{summary['small_tank']['tilt_impact_same_geometry']['max_abs_difference_l']:.2f}",
            "<SOURCE_FILE_REDACTED>",
            "<SOURCE_FILE_REDACTED>",
        ]
        missing_md = [token for token in required_tokens if token not in md_text]
        missing_tex = [token for token in required_tokens if token not in tex_text]
        checks["paper_result_consistency"] = {
            "status": "pass" if not missing_md and not missing_tex else "fail",
            "missing_from_markdown": missing_md,
            "missing_from_tex": missing_tex,
        }
    else:
        checks["paper_result_consistency"] = {
            "status": "fail",
            "missing_from_markdown": ["paper/paper.md"],
            "missing_from_tex": ["paper/main.tex"],
        }

    failed = [name for name, item in checks.items() if item["status"] == "fail"]
    verification = {
        "case_id": "2010A",
        "status": "fail" if failed else "pass",
        "checks": checks,
        "failed_checks": failed,
        "limitations_requiring_review": [
            {
                "item": "The sign of beta is not identifiable in an axisymmetric tank.",
                "status": "needs_review",
            },
            {
                "item": "Small-tank effective dimensions do not transfer exactly between runs.",
                "status": "needs_review",
            },
        ],
    }
    verification_path = results / "verification.json"
    verification_path.write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["verification_sha256"] = sha256(verification_path)
    manifest["paper_sha256"] = {
        str(path.relative_to(workspace)).replace("\\", "/"): sha256(path)
        for path in (paper / "paper.md", paper / "main.tex", paper / "<SOURCE_FILE_REDACTED>")
        if path.exists()
    }
    (results / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if failed:
        print("[fail] " + ", ".join(failed))
        sys.exit(1)
    print("[pass] independent integration, metrics, tables, and paper are consistent")


if __name__ == "__main__":
    main()
