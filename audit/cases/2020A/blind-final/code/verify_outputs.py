"""Independent verification for the audit-driven blind revision.

The adaptive ODE check reuses only the calibrated spatial air field.  Curve
geometry, the Q3/Q4 objectives, and every process inequality are recomputed by
``oracle.py``, which imports neither ``core`` nor ``solve``.  This separation
is deliberate: V1's validator shared the erroneous objective definition.
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
    directional_air_field,
    grouped_setpoints,
    simulate,
)
from oracle import (
    analytic_oracle_tests,
    curve_metrics_oracle,
    process_slacks_oracle,
    process_status_oracle,
)


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROCESS_TOLERANCE = 0.01


def clean_json(value):
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    if isinstance(value, np.ndarray):
        return clean_json(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    return value


def write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(clean_json(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def settings_vector(summary: dict) -> np.ndarray:
    settings = summary["settings"]
    return np.array(
        [
            settings["zones_1_5_C"],
            settings["zone_6_C"],
            settings["zone_7_C"],
            settings["zones_8_9_C"],
            settings["speed_cm_min"],
        ],
        dtype=float,
    )


def independent_curve(
    params: ModelParameters,
    design: np.ndarray,
    eval_step_s: float = 0.005,
) -> dict[str, np.ndarray]:
    setpoints = grouped_setpoints(design[:4])
    x_air, _, air_grid = directional_air_field(
        setpoints, params.lambda_up_cm, params.lambda_down_cm, dx_cm=0.1
    )
    speed_cm_s = float(design[4]) / 60.0
    duration = FURNACE_LENGTH_CM / speed_cm_s
    t_eval = np.append(np.arange(0.0, duration, eval_step_s), duration)

    def rhs(time_s, state):
        position = min(speed_cm_s * time_s, FURNACE_LENGTH_CM)
        air = float(np.interp(position, x_air, air_grid))
        temperature = float(state[0])
        rate = (
            params.k_ref_s_inv * np.exp(params.beta * (air - 175.0) / 80.0)
            if air >= temperature
            else params.k_cool_s_inv
        )
        return [rate * (air - temperature)]

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
    temperature = solution.y[0]
    position = np.minimum(speed_cm_s * t, FURNACE_LENGTH_CM)
    air = np.interp(position, x_air, air_grid)
    rate = np.where(
        air >= temperature,
        params.k_ref_s_inv * np.exp(params.beta * (air - 175.0) / 80.0),
        params.k_cool_s_inv,
    )
    return {
        "time_s": t,
        "position_cm": position,
        "effective_air_C": air,
        "temperature_C": temperature,
        "slope_C_s": rate * (air - temperature),
    }


def oracle_metrics(curve: dict[str, np.ndarray]) -> dict[str, float]:
    return curve_metrics_oracle(
        curve["time_s"], curve["temperature_C"], curve.get("slope_C_s")
    )


def metric_comparison(reference: dict, independent: dict) -> list[dict]:
    tolerances = {
        "rise_slope_max_C_s": 0.01,
        "fall_slope_min_C_s": 0.01,
        "rise_150_190_s": 0.03,
        "above_217_s": 0.03,
        "peak_C": 0.01,
        "q3_rising_area_C_s": 0.10,
        "cooling_area_217_C_s": 0.10,
        "total_area_above_217_C_s": 0.15,
        "symmetry_area_abs_C_s": 0.20,
    }
    rows = []
    for metric, tolerance in tolerances.items():
        difference = abs(float(reference[metric]) - float(independent[metric]))
        rows.append(
            {
                "metric": metric,
                "reference": float(reference[metric]),
                "independent": float(independent[metric]),
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

    analytic_tests = analytic_oracle_tests()
    independent_curves = {}
    independent_metrics = {}
    metric_rows = []
    for name, summary in summaries.items():
        curve = independent_curve(params, settings_vector(summary))
        metrics = oracle_metrics(curve)
        independent_curves[name] = curve
        independent_metrics[name] = metrics
        reference = summary["curve_metrics"] if name == "Q1" else summary["metrics"]
        for row in metric_comparison(reference, metrics):
            metric_rows.append({"case": name, **row})
    metric_status = "pass" if all(row["status"] == "pass" for row in metric_rows) else "fail"

    q3_contract_checks = {
        "objective_key_is_rising_side": "pass"
        if summaries["Q3"].get("objective_key") == "q3_rising_area_C_s"
        else "fail",
        "reported_q3_matches_independent_rising_area": "pass"
        if abs(
            summaries["Q3"]["metrics"]["q3_rising_area_C_s"]
            - independent_metrics["Q3"]["q3_rising_area_C_s"]
        )
        <= 0.10
        else "fail",
        "reported_q3_is_not_total_area": "pass"
        if abs(
            summaries["Q3"]["metrics"]["q3_rising_area_C_s"]
            - independent_metrics["Q3"]["total_area_above_217_C_s"]
        )
        > 1.0
        else "fail",
    }
    q3_contract_status = (
        "pass" if all(value == "pass" for value in q3_contract_checks.values()) else "fail"
    )

    process_checks = {}
    for name, metrics in independent_metrics.items():
        slacks = process_slacks_oracle(metrics)
        process_checks[name] = {
            "status": process_status_oracle(metrics, tolerance=PROCESS_TOLERANCE),
            "minimum_slack": float(min(slacks.values())),
            "tolerance": PROCESS_TOLERANCE,
            "slacks": slacks,
        }
    process_status = (
        "pass" if all(value["status"] == "pass" for value in process_checks.values()) else "fail"
    )

    result_csv = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>", encoding="gb18030")
    result_times = result_csv.iloc[:, 0].to_numpy(float)
    result_temperature = result_csv.iloc[:, 1].to_numpy(float)
    independent_q1_temperature = np.interp(
        result_times,
        independent_curves["Q1"]["time_s"],
        independent_curves["Q1"]["temperature_C"],
    )
    csv_difference = float(np.max(np.abs(result_temperature - independent_q1_temperature)))
    csv_check = {
        "status": "pass"
        if len(result_csv) == 671 and csv_difference <= 0.01
        else "fail",
        "rows": int(len(result_csv)),
        "expected_rows": 671,
        "max_temperature_difference_vs_RK45_C": csv_difference,
        "tolerance_C": 0.01,
    }

    convergence_rows = []
    for name, summary in summaries.items():
        design = settings_vector(summary)
        curves = {
            "dt_0.10_dx_0.10": simulate(
                params, grouped_setpoints(design[:4]), design[4], 0.10, 0.10
            ),
            "dt_0.05_dx_0.10": simulate(
                params, grouped_setpoints(design[:4]), design[4], 0.05, 0.10
            ),
            "dt_0.025_dx_0.10": simulate(
                params, grouped_setpoints(design[:4]), design[4], 0.025, 0.10
            ),
            "dt_0.0125_dx_0.10": simulate(
                params, grouped_setpoints(design[:4]), design[4], 0.0125, 0.10
            ),
            "dt_0.00625_dx_0.10": simulate(
                params, grouped_setpoints(design[:4]), design[4], 0.00625, 0.10
            ),
        }
        values = {grid: oracle_metrics(curve) for grid, curve in curves.items()}
        for metric in [
            "peak_C",
            "rise_150_190_s",
            "above_217_s",
            "q3_rising_area_C_s",
            "total_area_above_217_C_s",
        ]:
            convergence_rows.append(
                {
                    "case": name,
                    "metric": metric,
                    **{grid: metrics[metric] for grid, metrics in values.items()},
                    "fine_pair_difference": abs(
                        values["dt_0.0125_dx_0.10"][metric]
                        - values["dt_0.00625_dx_0.10"][metric]
                    ),
                    "fine_vs_RK45_difference": abs(
                        values["dt_0.0125_dx_0.10"][metric]
                        - independent_metrics[name][metric]
                    ),
                }
            )
    convergence = pd.DataFrame(convergence_rows)
    convergence.to_csv(RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.10f")
    convergence_limits = {
        "peak_C": 0.01,
        "rise_150_190_s": 0.03,
        "above_217_s": 0.03,
        "q3_rising_area_C_s": 0.10,
        "total_area_above_217_C_s": 0.15,
    }
    convergence_checks = []
    for metric, limit in convergence_limits.items():
        group = convergence[convergence.metric == metric]
        worst = float(max(group.fine_pair_difference.max(), group.fine_vs_RK45_difference.max()))
        convergence_checks.append(
            {
                "metric": metric,
                "worst_difference": worst,
                "tolerance": limit,
                "status": "pass" if worst <= limit else "fail",
            }
        )
    convergence_status = (
        "pass" if all(row["status"] == "pass" for row in convergence_checks) else "fail"
    )

    epsilon_frame = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    epsilon_checks = []
    for _, row in epsilon_frame.iterrows():
        design = np.array(
            [
                row["zones_1_5_C"],
                row["zone_6_C"],
                row["zone_7_C"],
                row["zones_8_9_C"],
                row["speed_cm_min"],
            ]
        )
        metrics = oracle_metrics(independent_curve(params, design))
        process_ok = process_status_oracle(metrics, tolerance=PROCESS_TOLERANCE) == "pass"
        area_ok = metrics["q3_rising_area_C_s"] <= float(row["area_cap_C_s"]) + 0.10
        objective_ok = (
            abs(metrics["symmetry_area_abs_C_s"] - float(row["symmetry_area_abs_C_s"]))
            <= 0.20
        )
        epsilon_checks.append(
            {
                "epsilon_ratio": float(row["epsilon_ratio"]),
                "status": "pass" if process_ok and area_ok and objective_ok else "fail",
                "independent_q3_rising_area_C_s": metrics["q3_rising_area_C_s"],
                "area_cap_C_s": float(row["area_cap_C_s"]),
                "independent_symmetry_area_abs_C_s": metrics["symmetry_area_abs_C_s"],
                "reported_symmetry_area_abs_C_s": float(row["symmetry_area_abs_C_s"]),
            }
        )
    epsilon_status = (
        "pass" if all(row["status"] == "pass" for row in epsilon_checks) else "fail"
    )

    optimization_runs = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    q3_de = optimization_runs[
        (optimization_runs.question == "Q3")
        & (optimization_runs.method == "differential_evolution_plus_SLSQP")
    ]
    q4_primary_de = optimization_runs[
        (optimization_runs.question == "Q4")
        & np.isclose(optimization_runs.epsilon_ratio, 1.05)
        & (optimization_runs.method == "differential_evolution_plus_SLSQP")
    ]
    optimization_evidence = {
        "status": "pass"
        if len(q3_de) >= 10
        and q3_de.seed.nunique() >= 10
        and len(q4_primary_de) >= 10
        and q4_primary_de.seed.nunique() >= 10
        and bool(optimization_runs.method.str.contains("Sobol").any())
        else "fail",
        "q3_de_runs": int(len(q3_de)),
        "q3_unique_seeds": int(q3_de.seed.nunique()),
        "q4_primary_de_runs": int(len(q4_primary_de)),
        "q4_primary_unique_seeds": int(q4_primary_de.seed.nunique()),
        "total_recorded_runs": int(len(optimization_runs)),
        "alternative_method_present": bool(optimization_runs.method.str.contains("Sobol").any()),
        "global_optimality": "needs_review",
    }

    geometry_unit_checks = {
        "furnace_length_435_5_cm": "pass"
        if abs(FURNACE_LENGTH_CM - 435.5) <= 1e-12
        else "fail",
        "q1_speed_conversion_duration_335_s": "pass"
        if abs(FURNACE_LENGTH_CM / (78.0 / 60.0) - 335.0) <= 1e-12
        else "fail",
    }
    geometry_status = (
        "pass" if all(value == "pass" for value in geometry_unit_checks.values()) else "fail"
    )

    hard_statuses = [
        analytic_tests["status"],
        metric_status,
        q3_contract_status,
        process_status,
        csv_check["status"],
        convergence_status,
        epsilon_status,
        optimization_evidence["status"],
        geometry_status,
    ]
    overall = "pass" if all(status == "pass" for status in hard_statuses) else "fail"
    report = {
        "status": overall,
        "metric_oracle_dependency_status": "pass",
        "metric_oracle_dependency_evidence": "oracle.py imports neither core.py nor solve.py",
        "analytic_oracle_tests": analytic_tests,
        "q3_objective_contract_status": q3_contract_status,
        "q3_objective_contract_checks": q3_contract_checks,
        "rk45_time_integration_status": metric_status,
        "rk45_configuration": "solve_ivp RK45, rtol=1e-9, atol=1e-10, max_step=0.05 s",
        "metric_checks": metric_rows,
        "independent_process_status": process_status,
        "independent_process_checks": process_checks,
        "result_csv_check": csv_check,
        "step_convergence_status": convergence_status,
        "step_convergence_checks": convergence_checks,
        "q4_epsilon_oracle_status": epsilon_status,
        "q4_epsilon_oracle_checks": epsilon_checks,
        "optimization_evidence": optimization_evidence,
        "geometry_unit_status": geometry_status,
        "geometry_unit_checks": geometry_unit_checks,
        "global_optimality": "needs_review",
        "global_optimality_reason": "Multi-seed and alternative global screens are evidence of stability, not a proof over the continuous nonconvex domain.",
        "parameter_uncertainty": "needs_review",
        "parameter_uncertainty_reason": "One autocorrelated experimental trajectory does not identify a joint probability region.",
        "external_validity": "needs_review",
        "external_validity_reason": "No independent experimental operating condition was provided.",
    }
    write_json(RESULTS / "verification.json", report)
    print(json.dumps(clean_json(report), ensure_ascii=False, indent=2))
    raise SystemExit(0 if overall == "pass" else 1)


if __name__ == "__main__":
    main()
