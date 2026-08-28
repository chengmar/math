"""Independent numerical and consistency checks for generated results.

This script deliberately uses scipy.solve_ivp instead of the exact frozen-air
recurrence used by solve.py.  It also performs step convergence and a seeded
neighbourhood search around Q3/Q4.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp

from core import (
    AMBIENT_C,
    FURNACE_LENGTH_CM,
    ModelParameters,
    curve_metrics,
    directional_air_field,
    grouped_setpoints,
    process_slacks,
    process_status,
    simulate,
)
from solve import DESIGN_BOUNDS, ROOT, adjusted_slacks, clean_json, evaluate_design, write_json


RESULTS = ROOT / "results"


def settings_vector(summary: dict) -> np.ndarray:
    s = summary["settings"]
    return np.array(
        [s["zones_1_5_C"], s["zone_6_C"], s["zone_7_C"], s["zones_8_9_C"], s["speed_cm_min"]],
        dtype=float,
    )


def independent_curve(params: ModelParameters, design: np.ndarray, eval_step_s: float = 0.02):
    setpoints = grouped_setpoints(design[:4])
    # Keep the calibrated spatial discretization but use independent adaptive
    # time integration with tight tolerances.
    x_air, _, air_grid = directional_air_field(
        setpoints, params.lambda_up_cm, params.lambda_down_cm, dx_cm=0.1
    )
    speed_s = float(design[4]) / 60.0
    duration = FURNACE_LENGTH_CM / speed_s
    regular = np.arange(0.0, duration, eval_step_s)
    t_eval = np.append(regular, duration)

    def rhs(time_s, state):
        air = float(np.interp(min(speed_s * time_s, FURNACE_LENGTH_CM), x_air, air_grid))
        temp = float(state[0])
        if air >= temp:
            rate = params.k_ref_s_inv * np.exp(params.beta * (air - 175.0) / 80.0)
        else:
            rate = params.k_cool_s_inv
        return [rate * (air - temp)]

    solution = solve_ivp(
        rhs,
        (0.0, duration),
        [AMBIENT_C],
        t_eval=t_eval,
        rtol=1e-9,
        atol=1e-10,
        max_step=0.05,
    )
    if not solution.success:
        raise RuntimeError(solution.message)
    t = solution.t
    temp = solution.y[0]
    x = np.minimum(speed_s * t, FURNACE_LENGTH_CM)
    air = np.interp(x, x_air, air_grid)
    rate = np.where(
        air >= temp,
        params.k_ref_s_inv * np.exp(params.beta * (air - 175.0) / 80.0),
        params.k_cool_s_inv,
    )
    return {
        "time_s": t,
        "position_cm": x,
        "programmed_air_C": np.full_like(t, np.nan),
        "effective_air_C": air,
        "temperature_C": temp,
        "slope_C_s": rate * (air - temp),
        "rate_s_inv": rate,
    }


def metric_differences(reference: dict, check: dict):
    tolerances = {
        "rise_slope_max_C_s": 0.01,
        "fall_slope_min_C_s": 0.01,
        "rise_150_190_s": 0.08,
        "above_217_s": 0.08,
        "peak_C": 0.03,
        "area_above_217_C_s": 1.0,
        "symmetry_area_abs_C_s": 2.0,
    }
    rows = []
    for key, tolerance in tolerances.items():
        difference = abs(float(reference[key]) - float(check[key]))
        rows.append(
            {
                "metric": key,
                "reference": float(reference[key]),
                "independent": float(check[key]),
                "absolute_difference": difference,
                "tolerance": tolerance,
                "status": "pass" if difference <= tolerance else "fail",
            }
        )
    return rows


def main():
    calibration = json.loads((RESULTS / "calibration.json").read_text(encoding="utf-8"))
    params = ModelParameters(**calibration["candidate_details"]["M2"]["parameters"])
    summaries = {
        name: json.loads((RESULTS / f"{name.lower()}_summary.json").read_text(encoding="utf-8"))
        for name in ["Q1", "Q2", "Q3", "Q4"]
    }
    checks = []
    independent_metrics = {}
    independent_curves = {}
    for name, summary in summaries.items():
        design = settings_vector(summary)
        curve = independent_curve(params, design)
        metrics = curve_metrics(curve)
        independent_curves[name] = curve
        independent_metrics[name] = metrics
        reference = summary["curve_metrics"] if name == "Q1" else summary["metrics"]
        for row in metric_differences(reference, metrics):
            checks.append({"case": name, **row})

    result_csv = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>", encoding="gb18030")
    result_times = result_csv.iloc[:, 0].to_numpy(float)
    result_temp = result_csv.iloc[:, 1].to_numpy(float)
    q1_independent_temp = np.interp(
        result_times,
        independent_curves["Q1"]["time_s"],
        independent_curves["Q1"]["temperature_C"],
    )
    csv_max_difference = float(np.max(np.abs(result_temp - q1_independent_temp)))
    csv_check = {
        "status": "pass" if len(result_csv) == 671 and csv_max_difference <= 0.03 else "fail",
        "rows": int(len(result_csv)),
        "expected_rows": 671,
        "time_start_s": float(result_times[0]),
        "time_end_s": float(result_times[-1]),
        "unique_time_steps_s": sorted(float(v) for v in np.unique(np.round(np.diff(result_times), 9))),
        "max_temperature_difference_vs_solve_ivp_C": csv_max_difference,
        "tolerance_C": 0.03,
    }

    convergence_rows = []
    for name, summary in summaries.items():
        design = settings_vector(summary)
        curves = {
            "dt_0.10": simulate(params, grouped_setpoints(design[:4]), design[4], 0.10, 0.10),
            "dt_0.05": simulate(params, grouped_setpoints(design[:4]), design[4], 0.05, 0.10),
            "dt_0.025": simulate(params, grouped_setpoints(design[:4]), design[4], 0.025, 0.05),
        }
        values = {grid: curve_metrics(curve) for grid, curve in curves.items()}
        for metric in ["peak_C", "rise_150_190_s", "above_217_s", "area_above_217_C_s"]:
            convergence_rows.append(
                {
                    "case": name,
                    "metric": metric,
                    "dt_0.10": values["dt_0.10"][metric],
                    "dt_0.05": values["dt_0.05"][metric],
                    "dt_0.025_dx_0.05": values["dt_0.025"][metric],
                    "fine_pair_difference": abs(values["dt_0.05"][metric] - values["dt_0.025"][metric]),
                }
            )
    convergence = pd.DataFrame(convergence_rows)
    convergence.to_csv(RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.10f")
    convergence_status = "pass" if (
        convergence.loc[convergence.metric == "peak_C", "fine_pair_difference"].max() <= 0.03
        and convergence.loc[convergence.metric == "rise_150_190_s", "fine_pair_difference"].max() <= 0.08
        and convergence.loc[convergence.metric == "above_217_s", "fine_pair_difference"].max() <= 0.08
        and convergence.loc[convergence.metric == "area_above_217_C_s", "fine_pair_difference"].max() <= 1.0
    ) else "fail"

    bounds = np.asarray(DESIGN_BOUNDS, dtype=float)
    rng = np.random.default_rng(2041)
    stability = {}
    q3_design = settings_vector(summaries["Q3"])
    q4_design = settings_vector(summaries["Q4"])
    area_cap = float(summaries["Q4"]["area_cap_C_s"])
    for name, center, scale, objective in [
        ("Q3", q3_design, np.array([3.0, 3.0, 3.0, 3.0, 2.0]), "area_above_217_C_s"),
        ("Q4", q4_design, np.array([3.0, 3.0, 3.0, 3.0, 2.0]), "symmetry_area_abs_C_s"),
    ]:
        incumbent = float(summaries[name]["metrics"][objective])
        feasible_count = 0
        better_count = 0
        best = incumbent
        best_x = center.copy()
        for _ in range(5000):
            trial = np.clip(center + rng.normal(size=5) * scale, bounds[:, 0], bounds[:, 1])
            _, metrics = evaluate_design(trial, params, 0.1, 0.1)
            feasible = min(adjusted_slacks(metrics).values()) >= 0.0
            if name == "Q4":
                feasible = feasible and metrics["area_above_217_C_s"] <= area_cap
            if feasible:
                feasible_count += 1
                value = float(metrics[objective])
                if value < best - 1e-6:
                    better_count += 1
                    best = value
                    best_x = trial.copy()
        stability[name] = {
            "status": "pass" if better_count == 0 else "needs_review",
            "seed": 2041,
            "samples": 5000,
            "feasible_samples": feasible_count,
            "strictly_better_samples": better_count,
            "incumbent_objective": incumbent,
            "best_sampled_objective": best,
            "best_sampled_settings": best_x.tolist(),
            "scope": "local stochastic stability only; not a proof of global optimality",
        }

    process_checks = {
        name: {
            "status": process_status(metrics, tolerance=0.02),
            "minimum_slack": float(min(process_slacks(metrics).values())),
            "metrics": metrics,
        }
        for name, metrics in independent_metrics.items()
    }
    numerical_rows_status = "pass" if all(row["status"] == "pass" for row in checks) else "fail"
    overall = "pass" if all(
        [numerical_rows_status == "pass", csv_check["status"] == "pass", convergence_status == "pass"]
    ) else "fail"
    report = {
        "status": overall,
        "independent_integrator": "scipy.solve_ivp RK45, rtol=1e-9, atol=1e-10, max_step=0.05 s",
        "metric_checks_status": numerical_rows_status,
        "metric_checks": checks,
        "result_csv_check": csv_check,
        "step_convergence_status": convergence_status,
        "independent_process_checks": process_checks,
        "optimization_neighbourhood_stability": stability,
        "global_optimality": "needs_review",
        "global_optimality_reason": "Differential evolution hit its fixed iteration budget; local random searches found no improvement but cannot prove a global optimum.",
        "external_validity": "needs_review",
        "external_validity_reason": "No independent experimental operating condition was provided.",
    }
    write_json(RESULTS / "verification.json", report)
    print(json.dumps(clean_json(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
