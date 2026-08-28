"""Independent adaptive-quadrature checks for the production outputs.

This module intentionally does not import ``model.py``.  It re-expresses the
geometry with scalar ``math`` functions and SciPy adaptive quadrature.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from scipy.integrate import quad


def circle_area(radius: float, cut: float) -> float:
    if radius <= 0.0:
        return 0.0
    if cut <= -radius:
        return 0.0
    if cut >= radius:
        return math.pi * radius * radius
    ratio = cut / radius
    return radius * radius * (
        ratio * math.sqrt(max(0.0, 1.0 - ratio * ratio))
        + math.asin(ratio)
        + math.pi / 2.0
    )


def ellipse_area(vertical: float, horizontal: float, cut: float) -> float:
    if cut <= -vertical:
        return 0.0
    if cut >= vertical:
        return math.pi * vertical * horizontal
    ratio = cut / vertical
    return vertical * horizontal * (
        ratio * math.sqrt(max(0.0, 1.0 - ratio * ratio))
        + math.asin(ratio)
        + math.pi / 2.0
    )


def integrate_piecewise(
    function: Callable[[float], float], intervals: list[tuple[float, float]]
) -> float:
    total = 0.0
    for lower, upper in intervals:
        value, _ = quad(
            function,
            lower,
            upper,
            epsabs=1e-10,
            epsrel=1e-10,
            limit=500,
        )
        total += value
    return total


def small_volume_l(
    height_m: float,
    horizontal_semiaxis_m: float,
    level_offset_m: float,
) -> float:
    vertical = 0.60
    slope = math.tan(math.radians(4.1))
    value, _ = quad(
        lambda x: ellipse_area(
            vertical,
            horizontal_semiaxis_m,
            height_m
            + level_offset_m
            - vertical
            - slope * (x - 0.40),
        ),
        0.0,
        2.45,
        epsabs=1e-10,
        epsrel=1e-10,
        limit=500,
    )
    return value * 1000.0


def actual_volume_l(height_m: float, alpha_deg: float, beta_deg: float) -> float:
    radius = 1.5
    cylinder_length = 8.0
    cap_depth = 1.0
    sphere_radius = (radius**2 + cap_depth**2) / (2.0 * cap_depth)
    left_centre = -cap_depth + sphere_radius
    right_centre = cylinder_length + cap_depth - sphere_radius
    slope = math.tan(math.radians(alpha_deg))
    beta_cosine = math.cos(math.radians(beta_deg))

    def section_radius(x: float) -> float:
        if x < 0.0:
            return math.sqrt(
                max(0.0, sphere_radius**2 - (x - left_centre) ** 2)
            )
        if x > cylinder_length:
            return math.sqrt(
                max(0.0, sphere_radius**2 - (x - right_centre) ** 2)
            )
        return radius

    def integrand(x: float) -> float:
        cut = beta_cosine * (height_m - radius) - slope * (x - 2.0)
        return circle_area(section_radius(x), cut)

    return (
        integrate_piecewise(
            integrand,
            [(-1.0, 0.0), (0.0, 8.0), (8.0, 9.0)],
        )
        * 1000.0
    )


def add_check(checks: dict, name: str, passed: bool, **evidence) -> None:
    checks[name] = {"status": "pass" if passed else "fail", **evidence}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    results = root / "results"
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    small_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    actual_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    checks: dict[str, dict] = {}

    small_model = summary["small_tank"]["production_model"]
    small_recomputed = np.array(
        [
            small_volume_l(
                height_mm / 1000.0,
                small_model["horizontal_semiaxis_m"],
                small_model["level_offset_mm"] / 1000.0,
            )
            for height_mm in small_table["height_mm"].to_numpy(float)
        ]
    )
    small_difference = float(
        np.max(
            np.abs(small_recomputed - small_table["volume_l_numeric"].to_numpy(float))
        )
    )
    add_check(
        checks,
        "small_table_adaptive_quadrature",
        small_difference <= 0.002,
        max_abs_difference_l=small_difference,
        tolerance_l=0.002,
        nodes=int(len(small_table)),
    )

    actual_model = summary["actual_tank"]["conditional_production_model"]
    alpha = float(actual_model["alpha_deg"])
    beta = float(actual_model["beta_abs_deg"])
    actual_recomputed = np.array(
        [
            actual_volume_l(height_mm / 1000.0, alpha, beta)
            for height_mm in actual_table["height_mm"].to_numpy(float)
        ]
    )
    actual_difference = float(
        np.max(
            np.abs(actual_recomputed - actual_table["volume_l_numeric"].to_numpy(float))
        )
    )
    add_check(
        checks,
        "actual_table_adaptive_quadrature",
        actual_difference <= 0.005,
        max_abs_difference_l=actual_difference,
        tolerance_l=0.005,
        nodes=int(len(actual_table)),
    )

    analytic_capacity = (
        math.pi * 1.5**2 * 8.0
        + 2.0 * math.pi * 1.0**2 * (3.0 * 1.625 - 1.0) / 3.0
    ) * 1000.0
    reported_capacity = float(summary["actual_tank"]["capacity_l"])
    capacity_difference = abs(analytic_capacity - reported_capacity)
    add_check(
        checks,
        "analytic_capacity",
        capacity_difference <= 1e-7,
        analytic_capacity_l=analytic_capacity,
        reported_capacity_l=reported_capacity,
        difference_l=capacity_difference,
    )

    small_empty = summary["small_tank"]["physical_empty_reading_mm"] / 1000.0
    small_full = summary["small_tank"]["physical_full_reading_mm"] / 1000.0
    small_capacity = float(small_model["capacity_l"])
    small_empty_volume = small_volume_l(
        small_empty,
        small_model["horizontal_semiaxis_m"],
        small_model["level_offset_mm"] / 1000.0,
    )
    small_full_volume = small_volume_l(
        small_full,
        small_model["horizontal_semiaxis_m"],
        small_model["level_offset_mm"] / 1000.0,
    )
    actual_empty = summary["actual_tank"]["physical_empty_reading_mm"] / 1000.0
    actual_full = summary["actual_tank"]["physical_full_reading_mm"] / 1000.0
    actual_empty_volume = actual_volume_l(actual_empty, alpha, beta)
    actual_full_volume = actual_volume_l(actual_full, alpha, beta)
    boundary_error = max(
        abs(small_empty_volume),
        abs(small_full_volume - small_capacity),
        abs(actual_empty_volume),
        abs(actual_full_volume - analytic_capacity),
    )
    add_check(
        checks,
        "empty_full_boundaries",
        boundary_error <= 0.01,
        max_abs_boundary_error_l=boundary_error,
        tolerance_l=0.01,
    )

    small_monotone = bool(np.all(np.diff(small_recomputed) >= -1e-8))
    actual_monotone = bool(np.all(np.diff(actual_recomputed) >= -1e-8))
    bounded = bool(
        np.min(small_recomputed) >= -1e-6
        and np.max(small_recomputed) <= small_capacity + 1e-6
        and np.min(actual_recomputed) >= -1e-6
        and np.max(actual_recomputed) <= analytic_capacity + 1e-6
    )
    add_check(
        checks,
        "monotonicity_and_bounds",
        small_monotone and actual_monotone and bounded,
        small_monotone=small_monotone,
        actual_monotone=actual_monotone,
        bounded=bounded,
    )

    beta_symmetry = max(
        abs(actual_volume_l(height_m, alpha, beta) - actual_volume_l(height_m, alpha, -beta))
        for height_m in (0.0, 0.75, 1.5, 2.25, 3.0)
    )
    add_check(
        checks,
        "beta_sign_symmetry",
        beta_symmetry <= 1e-9,
        max_abs_difference_l=beta_symmetry,
    )

    actual_data = pd.read_csv(results / "extracted" / "<SOURCE_FILE_REDACTED>")
    observed_height = actual_data["显示油高/mm"].to_numpy(float) / 1000.0
    inflow = actual_data["进油量/L"].to_numpy(float)
    outflow = actual_data["出油量/L"].fillna(0.0).to_numpy(float)
    refill = int(np.flatnonzero(inflow > 0.0)[0])
    discharge_index = np.concatenate(
        [np.arange(1, refill), np.arange(refill + 1, len(actual_data))]
    )
    adaptive_observed_volume = np.array(
        [actual_volume_l(value, alpha, beta) for value in observed_height]
    )
    adaptive_residual = (
        adaptive_observed_volume[discharge_index]
        - adaptive_observed_volume[discharge_index - 1]
        - (inflow - outflow)[discharge_index]
    )
    adaptive_rmse = float(np.sqrt(np.mean(adaptive_residual**2)))
    reported_rmse = float(actual_model["all_discharge_increment"]["rmse_l"])
    refill_error = float(
        adaptive_observed_volume[refill]
        - adaptive_observed_volume[refill - 1]
        - inflow[refill]
    )
    reported_refill_error = float(
        summary["actual_tank"]["time_ordered_checks"]["final_refit_refill_error_l"]
    )
    mass_balance_difference = max(
        abs(adaptive_rmse - reported_rmse),
        abs(refill_error - reported_refill_error),
    )
    add_check(
        checks,
        "mass_balance_from_independent_volume",
        mass_balance_difference <= 0.01,
        adaptive_rmse_l=adaptive_rmse,
        reported_rmse_l=reported_rmse,
        adaptive_refill_error_l=refill_error,
        reported_refill_error_l=reported_refill_error,
        max_abs_difference_l=mass_balance_difference,
    )

    failed = [name for name, item in checks.items() if item["status"] == "fail"]
    report = {
        "schema_version": 1,
        "case_id": "2010A",
        "phase": "blind-revision",
        "status": "fail" if failed else "pass",
        "implementation_independence": {
            "status": "pass",
            "production_model_imported_status": "pass",
            "method": "scalar math formulas plus scipy.integrate.quad",
        },
        "failed_checks": failed,
        "checks": checks,
        "mathematical_correctness": {
            "status": "needs_review",
            "note": "Passing numerical invariants and an independent implementation is not a proof of mathematical correctness.",
        },
    }
    (results / "independent-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if failed:
        print("[FAIL] independent checks: " + ", ".join(failed))
        sys.exit(1)
    print("[PASS] independent adaptive integration, boundaries, and mass balance")


if __name__ == "__main__":
    main()
