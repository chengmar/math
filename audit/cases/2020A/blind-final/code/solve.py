"""Reproducible blind solution for CUMCM 2020 A (furnace curve).

Run from the solve workspace with:
    python code/solve.py

The script calibrates three predeclared candidates, performs rolling-origin
comparison, solves Questions 1--4, and generates all numerical tables/figures.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import platform
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from scipy.optimize import differential_evolution, least_squares, minimize
from scipy.stats import qmc

from core import (
    AMBIENT_C,
    EXPERIMENT_SETPOINTS_C,
    EXPERIMENT_SPEED_CM_MIN,
    FURNACE_LENGTH_CM,
    ModelParameters,
    ZONE_END_CM,
    ZONE_MID_CM,
    curve_metrics,
    grouped_setpoints,
    load_experiment,
    process_slacks,
    process_status,
    programmed_field,
    simulate,
    spatial_grid,
)


SEED = 2020
ROOT = Path(__file__).resolve().parents[1]
INPUT_XLSX = ROOT / "input" / "data" / "<SOURCE_FILE_REDACTED>"
INPUT_CSV = ROOT / "input" / "data" / "<SOURCE_FILE_REDACTED>"
INPUT_DOCX = ROOT / "input" / "problem" / "<SOURCE_FILE_REDACTED>"
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"

CAL_DT_S = 0.1
CAL_DX_CM = 0.1
OPT_DT_S = 0.2
OPT_DX_CM = 0.25
FINE_DT_S = 0.0125
FINE_DX_CM = 0.1
Q1_OUTPUT_DT_S = 0.005
Q3_SEEDS = tuple(range(2020, 2030))
Q4_SEEDS = tuple(range(2030, 2040))
Q4_EPSILON_RATIOS = (1.0, 1.025, 1.05, 1.10)
PRIMARY_Q4_EPSILON = 1.05
NUMERICAL_FEASIBILITY_TOL = 5e-4

DESIGN_BOUNDS = [(165.0, 185.0), (185.0, 205.0), (225.0, 245.0), (245.0, 265.0), (65.0, 100.0)]
SLACK_SCALE = {
    "rise_slope_upper": 0.10,
    "fall_slope_lower": 0.10,
    "rise_150_190_lower": 1.0,
    "rise_150_190_upper": 1.0,
    "above_217_lower": 1.0,
    "above_217_upper": 1.0,
    "peak_lower": 0.20,
    "peak_upper": 0.20,
}


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, np.ndarray):
        return [clean_json(v) for v in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        v = float(value)
        return v if math.isfinite(v) else None
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(clean_json(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def candidate_config(model: str):
    if model == "M0":
        return np.log([0.02]), np.log([0.003]), np.log([0.08])
    if model == "M1":
        return np.log([10.0, 0.02, 0.01]), np.log([0.5, 0.003, 0.003]), np.log([80.0, 0.08, 0.08])
    if model == "M2":
        return (
            np.array([np.log(10.0), np.log(20.0), np.log(0.02), 0.3, np.log(0.01)]),
            np.array([np.log(0.5), np.log(0.5), np.log(0.003), -1.0, np.log(0.003)]),
            np.array([np.log(80.0), np.log(80.0), np.log(0.08), 2.0, np.log(0.08)]),
        )
    raise ValueError(model)


def decode_candidate(q: np.ndarray, model: str) -> dict[str, float]:
    if model == "M0":
        return {"k_s_inv": float(np.exp(q[0]))}
    if model == "M1":
        return {
            "lambda_cm": float(np.exp(q[0])),
            "k_heat_s_inv": float(np.exp(q[1])),
            "k_cool_s_inv": float(np.exp(q[2])),
        }
    return {
        "lambda_up_cm": float(np.exp(q[0])),
        "lambda_down_cm": float(np.exp(q[1])),
        "k_ref_s_inv": float(np.exp(q[2])),
        "beta": float(q[3]),
        "k_cool_s_inv": float(np.exp(q[4])),
    }


def candidate_prediction(q: np.ndarray, model: str, times_s: np.ndarray) -> np.ndarray:
    x_grid = spatial_grid(CAL_DX_CM)
    target = programmed_field(EXPERIMENT_SETPOINTS_C, x_grid)
    if model == "M0":
        air_grid = target
        k_all = float(np.exp(q[0]))
    else:
        if model == "M1":
            lambda_up = lambda_down = float(np.exp(q[0]))
            k_heat, k_cool = np.exp(q[1:3])
        else:
            lambda_up, lambda_down = np.exp(q[:2])
            k_ref = float(np.exp(q[2]))
            beta = float(q[3])
            k_cool = float(np.exp(q[4]))
        air_grid = np.empty_like(target)
        air_grid[0] = AMBIENT_C
        dx = x_grid[1] - x_grid[0]
        for i in range(len(target) - 1):
            lam = lambda_up if target[i] >= air_grid[i] else lambda_down
            a = np.exp(-dx / lam)
            air_grid[i + 1] = target[i] + (air_grid[i] - target[i]) * a
    duration = FURNACE_LENGTH_CM / (EXPERIMENT_SPEED_CM_MIN / 60.0)
    t = np.arange(0.0, duration + CAL_DT_S, CAL_DT_S)
    t[-1] = duration
    x = np.minimum(t * EXPERIMENT_SPEED_CM_MIN / 60.0, FURNACE_LENGTH_CM)
    air = np.interp(x, x_grid, air_grid)
    temp = np.empty_like(t)
    temp[0] = AMBIENT_C
    for i in range(len(t) - 1):
        air_mid = 0.5 * (air[i] + air[i + 1])
        if model == "M0":
            rate = k_all
        elif model == "M1":
            rate = k_heat if air_mid >= temp[i] else k_cool
        else:
            rate = (
                k_ref * np.exp(beta * (air_mid - 175.0) / 80.0)
                if air_mid >= temp[i]
                else k_cool
            )
        temp[i + 1] = air_mid + (temp[i] - air_mid) * np.exp(-rate * (t[i + 1] - t[i]))
    return np.interp(times_s, t, temp)


def residual_diagnostics(residual: np.ndarray) -> dict:
    residual = np.asarray(residual, dtype=float)
    lag1 = float(np.corrcoef(residual[:-1], residual[1:])[0, 1])
    denominator = float(np.sum(residual**2))
    durbin_watson = float(np.sum(np.diff(residual) ** 2) / denominator) if denominator > 0.0 else float("nan")
    signs = np.sign(residual)
    nonzero = signs[signs != 0.0]
    runs = int(1 + np.sum(nonzero[1:] != nonzero[:-1])) if len(nonzero) else 0
    status = "needs_review" if abs(lag1) >= 0.8 or durbin_watson <= 0.5 else "pass"
    return {
        "status": status,
        "lag1_correlation": lag1,
        "durbin_watson": durbin_watson,
        "sign_runs": runs,
        "n_residuals": int(len(residual)),
        "interpretation": "Dense residuals are not treated as independent observations.",
    }


def jacobian_diagnostics(jacobian: np.ndarray, names: list[str]) -> dict:
    jacobian = np.asarray(jacobian, dtype=float)
    singular = np.linalg.svd(jacobian, compute_uv=False)
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0.0 else float("inf")
    covariance_shape = np.linalg.pinv(jacobian.T @ jacobian)
    scale = np.sqrt(np.maximum(np.diag(covariance_shape), 0.0))
    denominator = np.outer(scale, scale)
    correlation = np.divide(
        covariance_shape,
        denominator,
        out=np.full_like(covariance_shape, np.nan),
        where=denominator > 0.0,
    )
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pairs.append(
                {
                    "parameter_1": names[i],
                    "parameter_2": names[j],
                    "local_correlation": float(correlation[i, j]),
                }
            )
    pairs.sort(key=lambda row: abs(row["local_correlation"]), reverse=True)
    max_abs = abs(pairs[0]["local_correlation"]) if pairs else 0.0
    return {
        "status": "needs_review" if condition > 1e3 or max_abs >= 0.9 else "pass",
        "jacobian_condition_number": condition,
        "singular_values": singular.tolist(),
        "parameterization": names,
        "largest_absolute_correlations": pairs[:5],
        "scope": "local least-squares geometry only; not a confidence region under autocorrelated residuals",
    }


def moving_block_bootstrap(
    observations: np.ndarray,
    predictions: dict[str, np.ndarray],
    *,
    block_length: int = 40,
    repetitions: int = 1000,
    seed: int = SEED + 77,
) -> dict:
    """Compare fixed candidate predictions using contiguous residual blocks."""
    observations = np.asarray(observations, dtype=float)
    n = len(observations)
    models = list(predictions)
    squared = {model: (np.asarray(predictions[model]) - observations) ** 2 for model in models}
    rng = np.random.default_rng(seed)
    samples = {model: np.empty(repetitions, dtype=float) for model in models}
    best_counts = {model: 0 for model in models}
    blocks_needed = int(np.ceil(n / block_length))
    for b in range(repetitions):
        starts = rng.integers(0, n, size=blocks_needed)
        indices = np.concatenate(
            [(start + np.arange(block_length)) % n for start in starts]
        )[:n]
        values = {}
        for model in models:
            value = float(np.sqrt(np.mean(squared[model][indices])))
            samples[model][b] = value
            values[model] = value
        best_counts[min(values, key=values.get)] += 1
    return {
        "status": "pass" if best_counts.get("M2", 0) == repetitions else "needs_review",
        "method": "circular moving-block bootstrap of fixed-candidate squared residuals",
        "block_length_points": block_length,
        "block_length_s": block_length * 0.5,
        "repetitions": repetitions,
        "seed": seed,
        "models": {
            model: {
                "rmse_quantiles_C": {
                    "q025": float(np.quantile(samples[model], 0.025)),
                    "median": float(np.quantile(samples[model], 0.5)),
                    "q975": float(np.quantile(samples[model], 0.975)),
                },
                "best_fraction": best_counts[model] / repetitions,
            }
            for model in models
        },
        "scope": "accounts for local time dependence in performance comparison but does not refit parameters",
    }


def calibrate_candidates(exp: pd.DataFrame):
    obs_t = exp["time_s"].to_numpy(float)
    obs_y = exp["temperature_C"].to_numpy(float)
    folds = [
        {"train_end_s": 305.0, "valid_start_s": 305.0, "valid_end_s": 325.0},
        {"train_end_s": 325.0, "valid_start_s": 325.0, "valid_end_s": 345.0},
        {"train_end_s": 345.0, "valid_start_s": 345.0, "valid_end_s": 365.0},
    ]
    comparison_rows = []
    details = {}
    predictions = {}
    for model in ["M0", "M1", "M2"]:
        q0, lower, upper = candidate_config(model)
        fit = least_squares(
            lambda q: candidate_prediction(q, model, obs_t) - obs_y,
            q0,
            bounds=(lower, upper),
            max_nfev=500,
        )
        pred = candidate_prediction(fit.x, model, obs_t)
        residual = pred - obs_y
        rss = float(np.sum(residual**2))
        n = len(obs_y)
        p = len(fit.x)
        full_metrics = {
            "rmse_C": float(np.sqrt(np.mean(residual**2))),
            "mae_C": float(np.mean(np.abs(residual))),
            "max_abs_error_C": float(np.max(np.abs(residual))),
            "bic": float(n * np.log(rss / n) + p * np.log(n)),
            "bic_status": "needs_review",
            "bic_reason": "The standard independent-residual BIC is retained only as a descriptive value.",
        }
        fold_results = []
        for fold in folds:
            train = obs_t <= fold["train_end_s"]
            valid = (obs_t > fold["valid_start_s"]) & (obs_t <= fold["valid_end_s"])
            fold_fit = least_squares(
                lambda q: (candidate_prediction(q, model, obs_t) - obs_y)[train],
                q0,
                bounds=(lower, upper),
                max_nfev=500,
            )
            fold_error = (candidate_prediction(fold_fit.x, model, obs_t) - obs_y)[valid]
            fold_results.append(
                {
                    **fold,
                    "rmse_C": float(np.sqrt(np.mean(fold_error**2))),
                    "parameters": decode_candidate(fold_fit.x, model),
                    "fit_status": "pass" if fold_fit.success else "needs_review",
                }
            )
        rolling = [row["rmse_C"] for row in fold_results]
        details[model] = {
            "parameters": decode_candidate(fit.x, model),
            "full_fit": full_metrics,
            "rolling_folds": fold_results,
            "rolling_mean_rmse_C": float(np.mean(rolling)),
            "rolling_worst_rmse_C": float(np.max(rolling)),
            "optimizer_status": "pass" if fit.success else "needs_review",
            "nfev": int(fit.nfev),
            "residual_diagnostics": residual_diagnostics(residual),
            "local_identifiability": jacobian_diagnostics(
                fit.jac,
                {
                    "M0": ["log_k"],
                    "M1": ["log_lambda", "log_k_heat", "log_k_cool"],
                    "M2": ["log_lambda_up", "log_lambda_down", "log_k_ref", "beta", "log_k_cool"],
                }[model],
            ),
        }
        comparison_rows.append(
            {
                "model": model,
                "n_parameters": p,
                **full_metrics,
                "rolling_mean_rmse_C": float(np.mean(rolling)),
                "rolling_worst_rmse_C": float(np.max(rolling)),
            }
        )
        predictions[model] = pred
    comparison = pd.DataFrame(comparison_rows)
    comparison["full_fit_rank"] = comparison["rmse_C"].rank(method="min").astype(int)
    comparison["rolling_mean_rank"] = comparison["rolling_mean_rmse_C"].rank(method="min").astype(int)
    bootstrap = moving_block_bootstrap(obs_y, predictions)
    for model in ["M0", "M1", "M2"]:
        details[model]["moving_block_bootstrap"] = bootstrap["models"][model]
    selected = "M2"
    selection_status = "pass" if (
        comparison.set_index("model").loc[selected, "full_fit_rank"] == 1
        and comparison.set_index("model").loc[selected, "rolling_mean_rank"] == 1
    ) else "needs_review"
    return comparison, details, predictions, selected, selection_status, bootstrap


def evaluate_design(x: np.ndarray, params: ModelParameters, dt_s=OPT_DT_S, dx_cm=OPT_DX_CM):
    setpoints = grouped_setpoints(x[:4])
    curve = simulate(params, setpoints, float(x[4]), dt_s=dt_s, dx_cm=dx_cm)
    return curve, curve_metrics(curve)


def adjusted_slacks(metrics: dict[str, float]) -> dict[str, float]:
    """Original problem slacks, with no uncalibrated engineering margin.

    The function name is retained for a small amount of internal call-site
    compatibility.  V1 subtracted fixed 0.02 C/s, 0.20 s, and 0.10 C margins
    here, thereby optimizing a different feasible set.  The revision solves
    the stated inequalities and reports engineering robustness separately.
    """
    return process_slacks(metrics)


def squared_violation(metrics: dict[str, float]) -> float:
    slacks = adjusted_slacks(metrics)
    if not all(np.isfinite(v) for v in slacks.values()):
        return 1e6
    return float(sum((max(-value, 0.0) / SLACK_SCALE[name]) ** 2 for name, value in slacks.items()))


class CachedEvaluator:
    def __init__(self, params: ModelParameters, dt_s: float, dx_cm: float):
        self.params = params
        self.dt_s = dt_s
        self.dx_cm = dx_cm
        self.last_x = None
        self.last_value = None

    def __call__(self, x):
        x = np.asarray(x, dtype=float)
        if self.last_x is None or not np.array_equal(x, self.last_x):
            self.last_x = x.copy()
            self.last_value = evaluate_design(x, self.params, self.dt_s, self.dx_cm)
        return self.last_value


def fine_refine(
    start: np.ndarray,
    params: ModelParameters,
    objective_key: str,
    extra_constraint: Callable[[dict[str, float]], float] | None = None,
):
    evaluator = CachedEvaluator(params, FINE_DT_S, FINE_DX_CM)

    def objective(x):
        return evaluator(x)[1][objective_key]

    def constraints(x):
        metrics = evaluator(x)[1]
        values = list(adjusted_slacks(metrics).values())
        if extra_constraint is not None:
            values.append(extra_constraint(metrics))
        return np.asarray(values, dtype=float)

    result = minimize(
        objective,
        np.asarray(start, dtype=float),
        method="SLSQP",
        bounds=DESIGN_BOUNDS,
        constraints=[{"type": "ineq", "fun": constraints}],
        options={"ftol": 1e-10, "maxiter": 500, "disp": False},
    )
    curve, metrics = evaluator(result.x)
    feasible = min(adjusted_slacks(metrics).values()) >= -NUMERICAL_FEASIBILITY_TOL
    if extra_constraint is not None:
        feasible = feasible and extra_constraint(metrics) >= -NUMERICAL_FEASIBILITY_TOL
    return result, curve, metrics, feasible


def _run_de_candidate(
    params: ModelParameters,
    objective_key: str,
    seed: int,
    *,
    question: str,
    area_cap: float | None = None,
    epsilon_ratio: float | None = None,
):
    evaluator = CachedEvaluator(params, OPT_DT_S, OPT_DX_CM)

    def penalized(x):
        metrics = evaluator(x)[1]
        violation = squared_violation(metrics)
        if area_cap is not None:
            violation += (max(metrics["q3_rising_area_C_s"] - area_cap, 0.0) / 5.0) ** 2
        return metrics[objective_key] + 2.0e5 * violation

    de = differential_evolution(
        penalized,
        DESIGN_BOUNDS,
        seed=seed,
        popsize=8,
        maxiter=55 if question == "Q3" else 70,
        tol=1e-6,
        polish=False,
        workers=1,
        updating="immediate",
    )
    extra = None if area_cap is None else lambda metrics: area_cap - metrics["q3_rising_area_C_s"]
    local, curve, metrics, feasible = fine_refine(
        de.x, params, objective_key, extra_constraint=extra
    )
    record = {
        "question": question,
        "epsilon_ratio": epsilon_ratio,
        "method": "differential_evolution_plus_SLSQP",
        "seed": seed,
        "global_status": "pass" if de.success else "needs_review",
        "global_message": str(de.message),
        "global_iterations": int(de.nit),
        "global_evaluations": int(de.nfev),
        "local_status": "pass" if local.success else ("needs_review" if feasible else "fail"),
        "local_message": str(local.message),
        "local_iterations": int(local.nit),
        "feasibility_status": "pass" if feasible else "fail",
        "minimum_process_slack": float(min(process_slacks(metrics).values())),
        "area_cap_C_s": area_cap,
        "area_cap_slack_C_s": None if area_cap is None else float(area_cap - metrics["q3_rising_area_C_s"]),
        "objective_value": float(metrics[objective_key]),
        "q3_rising_area_C_s": float(metrics["q3_rising_area_C_s"]),
        "settings": np.asarray(local.x, dtype=float).tolist(),
    }
    return {"record": record, "local": local, "curve": curve, "metrics": metrics, "feasible": feasible}


def _run_sobol_candidate(
    params: ModelParameters,
    objective_key: str,
    seed: int,
    *,
    question: str,
    area_cap: float | None = None,
    epsilon_ratio: float | None = None,
):
    """Independent space-filling global screen followed by constrained SLSQP."""
    evaluator = CachedEvaluator(params, OPT_DT_S, OPT_DX_CM)
    sampler = qmc.Sobol(d=5, scramble=True, seed=seed)
    unit = sampler.random_base2(m=10)
    bounds = np.asarray(DESIGN_BOUNDS, dtype=float)
    samples = qmc.scale(unit, bounds[:, 0], bounds[:, 1])
    best_x = None
    best_penalized = float("inf")
    feasible_screen = 0
    for trial in samples:
        metrics = evaluator(trial)[1]
        violation = squared_violation(metrics)
        if area_cap is not None:
            violation += (max(metrics["q3_rising_area_C_s"] - area_cap, 0.0) / 5.0) ** 2
        if violation <= 1e-18:
            feasible_screen += 1
        value = float(metrics[objective_key] + 2.0e5 * violation)
        if value < best_penalized:
            best_penalized = value
            best_x = np.asarray(trial, dtype=float)
    extra = None if area_cap is None else lambda metrics: area_cap - metrics["q3_rising_area_C_s"]
    local, curve, metrics, feasible = fine_refine(
        best_x, params, objective_key, extra_constraint=extra
    )
    record = {
        "question": question,
        "epsilon_ratio": epsilon_ratio,
        "method": "scrambled_Sobol_screen_plus_SLSQP",
        "seed": seed,
        "global_status": "pass" if feasible_screen > 0 else "needs_review",
        "global_message": "1024-point Sobol screen completed",
        "global_iterations": 1,
        "global_evaluations": int(len(samples)),
        "screen_feasible_points": feasible_screen,
        "local_status": "pass" if local.success else ("needs_review" if feasible else "fail"),
        "local_message": str(local.message),
        "local_iterations": int(local.nit),
        "feasibility_status": "pass" if feasible else "fail",
        "minimum_process_slack": float(min(process_slacks(metrics).values())),
        "area_cap_C_s": area_cap,
        "area_cap_slack_C_s": None if area_cap is None else float(area_cap - metrics["q3_rising_area_C_s"]),
        "objective_value": float(metrics[objective_key]),
        "q3_rising_area_C_s": float(metrics["q3_rising_area_C_s"]),
        "settings": np.asarray(local.x, dtype=float).tolist(),
    }
    return {"record": record, "local": local, "curve": curve, "metrics": metrics, "feasible": feasible}


def _run_start_candidate(
    start: np.ndarray,
    params: ModelParameters,
    objective_key: str,
    *,
    question: str,
    area_cap: float | None,
    epsilon_ratio: float | None,
):
    extra = None if area_cap is None else lambda metrics: area_cap - metrics["q3_rising_area_C_s"]
    local, curve, metrics, feasible = fine_refine(
        start, params, objective_key, extra_constraint=extra
    )
    record = {
        "question": question,
        "epsilon_ratio": epsilon_ratio,
        "method": "Q3_incumbent_plus_SLSQP",
        "seed": None,
        "global_status": "needs_review",
        "global_message": "deterministic incumbent start; no global search",
        "global_iterations": 0,
        "global_evaluations": 1,
        "local_status": "pass" if local.success else ("needs_review" if feasible else "fail"),
        "local_message": str(local.message),
        "local_iterations": int(local.nit),
        "feasibility_status": "pass" if feasible else "fail",
        "minimum_process_slack": float(min(process_slacks(metrics).values())),
        "area_cap_C_s": area_cap,
        "area_cap_slack_C_s": None if area_cap is None else float(area_cap - metrics["q3_rising_area_C_s"]),
        "objective_value": float(metrics[objective_key]),
        "q3_rising_area_C_s": float(metrics["q3_rising_area_C_s"]),
        "settings": np.asarray(local.x, dtype=float).tolist(),
    }
    return {"record": record, "local": local, "curve": curve, "metrics": metrics, "feasible": feasible}


def _best_candidate(candidates: list[dict], objective_key: str) -> dict:
    feasible = [candidate for candidate in candidates if candidate["feasible"]]
    if not feasible:
        raise RuntimeError("No feasible optimization candidate survived fine evaluation")
    return min(feasible, key=lambda candidate: float(candidate["metrics"][objective_key]))


def optimize_q3(params: ModelParameters):
    candidates = [
        _run_de_candidate(
            params,
            "q3_rising_area_C_s",
            seed,
            question="Q3",
        )
        for seed in Q3_SEEDS
    ]
    candidates.append(
        _run_sobol_candidate(
            params,
            "q3_rising_area_C_s",
            SEED + 500,
            question="Q3",
        )
    )
    return _best_candidate(candidates, "q3_rising_area_C_s"), [item["record"] for item in candidates]


def optimize_q4_frontier(params: ModelParameters, q3_candidate: dict):
    q3_area = float(q3_candidate["metrics"]["q3_rising_area_C_s"])
    q3_start = np.asarray(q3_candidate["local"].x, dtype=float)
    solutions = {}
    all_records = []
    for ratio in Q4_EPSILON_RATIOS:
        cap = ratio * q3_area
        seeds = Q4_SEEDS if ratio == PRIMARY_Q4_EPSILON else Q4_SEEDS[:3]
        candidates = [
            _run_de_candidate(
                params,
                "symmetry_area_abs_C_s",
                seed + int(round(1000 * ratio)),
                question="Q4",
                area_cap=cap,
                epsilon_ratio=ratio,
            )
            for seed in seeds
        ]
        candidates.append(
            _run_start_candidate(
                q3_start,
                params,
                "symmetry_area_abs_C_s",
                question="Q4",
                area_cap=cap,
                epsilon_ratio=ratio,
            )
        )
        if ratio == PRIMARY_Q4_EPSILON:
            candidates.append(
                _run_sobol_candidate(
                    params,
                    "symmetry_area_abs_C_s",
                    SEED + 600,
                    question="Q4",
                    area_cap=cap,
                    epsilon_ratio=ratio,
                )
            )
        solutions[ratio] = _best_candidate(candidates, "symmetry_area_abs_C_s")
        all_records.extend(item["record"] for item in candidates)
    return solutions, all_records


def q2_maximum_speed(params: ModelParameters):
    speeds = np.arange(65.0, 100.0 + 0.025, 0.05)
    records = []
    for speed in speeds:
        curve = simulate(params, grouped_setpoints([182.0, 203.0, 237.0, 254.0]), speed, OPT_DT_S, OPT_DX_CM)
        metrics = curve_metrics(curve)
        slacks = process_slacks(metrics)
        records.append({"speed_cm_min": float(speed), **metrics, "min_slack": float(min(slacks.values()))})
    scan = pd.DataFrame(records)
    feasible = scan[scan["min_slack"] >= 0.0]
    if feasible.empty:
        raise RuntimeError("No feasible speed found for Question 2")
    low = float(feasible["speed_cm_min"].max())
    high = min(100.0, low + 0.05)

    def min_slack(speed):
        curve = simulate(params, grouped_setpoints([182.0, 203.0, 237.0, 254.0]), speed, FINE_DT_S, FINE_DX_CM)
        return min(process_slacks(curve_metrics(curve)).values())

    if high > low and min_slack(high) < 0.0:
        for _ in range(45):
            mid = (low + high) / 2.0
            if min_slack(mid) >= 0.0:
                low = mid
            else:
                high = mid
    max_speed = low
    curve = simulate(params, grouped_setpoints([182.0, 203.0, 237.0, 254.0]), max_speed, FINE_DT_S, FINE_DX_CM)
    metrics = curve_metrics(curve)
    slacks = process_slacks(metrics)
    active = min(slacks, key=slacks.get)
    return max_speed, curve, metrics, slacks, active, scan


def sampled_curve(curve: dict[str, np.ndarray], step_s: float = 0.5) -> pd.DataFrame:
    end = float(curve["time_s"][-1])
    n = int(np.floor(end / step_s + 1e-10))
    times = np.arange(n + 1, dtype=float) * step_s
    if end - times[-1] > 1e-8:
        times = np.append(times, end)
    return pd.DataFrame(
        {
            "time_s": times,
            "position_cm": np.interp(times, curve["time_s"], curve["position_cm"]),
            "programmed_air_C": np.interp(times, curve["time_s"], curve["programmed_air_C"]),
            "effective_air_C": np.interp(times, curve["time_s"], curve["effective_air_C"]),
            "temperature_C": np.interp(times, curve["time_s"], curve["temperature_C"]),
        }
    )


def settings_dict(x: np.ndarray) -> dict[str, float]:
    return {
        "zones_1_5_C": float(x[0]),
        "zone_6_C": float(x[1]),
        "zone_7_C": float(x[2]),
        "zones_8_9_C": float(x[3]),
        "speed_cm_min": float(x[4]),
        "zones_10_11_C": 25.0,
    }


def save_curve(path: Path, curve: dict[str, np.ndarray]):
    sampled_curve(curve).to_csv(path, index=False, float_format="%.6f", encoding="utf-8")


def sensitivity_analysis(params: ModelParameters, designs: dict[str, np.ndarray]):
    param_names = list(params.as_dict())
    scenarios = [("nominal", params, 0.0, 0.0)]
    base = params.as_dict()
    for name in param_names:
        for sign in [-1.0, 1.0]:
            changed = base.copy()
            changed[name] *= 1.0 + sign * 0.05
            scenarios.append((f"{name}_{sign:+.0%}", ModelParameters(**changed), 0.0, 0.0))
    scenarios.extend(
        [
            ("all_heated_setpoints_-1C", params, -1.0, 0.0),
            ("all_heated_setpoints_+1C", params, 1.0, 0.0),
            ("speed_-0.5", params, 0.0, -0.5),
            ("speed_+0.5", params, 0.0, 0.5),
        ]
    )
    rows = []
    for design_name, design in designs.items():
        for scenario_name, scenario_params, setpoint_delta, speed_delta in scenarios:
            trial = np.asarray(design, dtype=float).copy()
            trial[:4] += setpoint_delta
            trial[4] = np.clip(trial[4] + speed_delta, 65.0, 100.0)
            curve, metrics = evaluate_design(trial, scenario_params, FINE_DT_S, FINE_DX_CM)
            rows.append(
                {
                    "design": design_name,
                    "scenario": scenario_name,
                    "status": process_status(metrics),
                    **metrics,
                }
            )
    frame = pd.DataFrame(rows)
    summary = {}
    metric_names = [
        "rise_slope_max_C_s",
        "fall_slope_min_C_s",
        "rise_150_190_s",
        "above_217_s",
        "peak_C",
        "q3_rising_area_C_s",
        "total_area_above_217_C_s",
        "symmetry_ratio",
    ]
    for name, group in frame.groupby("design"):
        summary[name] = {
            "scenario_count": int(len(group)),
            "process_pass_count": int((group["status"] == "pass").sum()),
            "process_pass_fraction": float((group["status"] == "pass").mean()),
            "ranges": {
                metric: {"min": float(group[metric].min()), "max": float(group[metric].max())}
                for metric in metric_names
            },
        }
    return frame, summary


def joint_corner_analysis(params: ModelParameters, designs: dict[str, np.ndarray]):
    """Pressure-test five parameters, all heated settings, and speed jointly.

    The 128 corners are not assigned probabilities.  They are a deterministic
    counterexample search and therefore support only a robustness stress-test
    statement, not a failure probability.
    """
    base = params.as_dict()
    param_names = list(base)
    rows = []
    for design_name, design in designs.items():
        for signs in itertools.product((-1.0, 1.0), repeat=7):
            changed = {
                name: base[name] * (1.0 + 0.05 * signs[i])
                for i, name in enumerate(param_names)
            }
            trial = np.asarray(design, dtype=float).copy()
            trial[:4] += signs[5]
            trial[4] = np.clip(trial[4] + 0.5 * signs[6], 65.0, 100.0)
            _, metrics = evaluate_design(
                trial, ModelParameters(**changed), FINE_DT_S, FINE_DX_CM
            )
            slacks = process_slacks(metrics)
            rows.append(
                {
                    "design": design_name,
                    "corner_id": "".join("+" if value > 0 else "-" for value in signs),
                    "status": process_status(metrics),
                    "minimum_slack": float(min(slacks.values())),
                    **metrics,
                }
            )
    frame = pd.DataFrame(rows)
    summary = {}
    for name, group in frame.groupby("design"):
        worst = group.loc[group["minimum_slack"].idxmin()]
        summary[name] = {
            "status": "pass" if bool((group["status"] == "pass").all()) else "fail",
            "corner_count": int(len(group)),
            "process_pass_count": int((group["status"] == "pass").sum()),
            "process_pass_fraction": float((group["status"] == "pass").mean()),
            "peak_range_C": [float(group["peak_C"].min()), float(group["peak_C"].max())],
            "rise_150_190_range_s": [
                float(group["rise_150_190_s"].min()),
                float(group["rise_150_190_s"].max()),
            ],
            "worst_corner": str(worst["corner_id"]),
            "worst_minimum_slack": float(worst["minimum_slack"]),
            "scope": "deterministic stress test; not a probability distribution",
        }
    return frame, summary


def make_figures(
    exp,
    predictions,
    comparison,
    q1_curve,
    q1_markers,
    q2_scan,
    q2_speed,
    q3_curve,
    q4_curve,
    sensitivity,
    q4_frontier,
):
    plt.rcParams.update({"figure.dpi": 140, "savefig.dpi": 180, "font.size": 9})
    colors = {"M0": "#888888", "M1": "#1f77b4", "M2": "#d62728"}
    fig, axes = plt.subplots(2, 1, figsize=(8.0, 6.2), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    axes[0].plot(exp.time_s, exp.temperature_C, color="black", lw=1.5, label="observed")
    for model in ["M0", "M1", "M2"]:
        axes[0].plot(exp.time_s, predictions[model], lw=1.1, color=colors[model], label=model)
        axes[1].plot(exp.time_s, predictions[model] - exp.temperature_C, lw=1.0, color=colors[model], label=model)
    axes[0].set_ylabel("Temperature (degC)")
    axes[0].legend(ncol=4)
    axes[0].grid(alpha=0.25)
    axes[1].axhline(0.0, color="black", lw=0.7)
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Residual (degC)")
    axes[1].grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)

    residual = predictions["M2"] - exp.temperature_C.to_numpy(float)
    lags = np.arange(0, 81)
    acf = np.array(
        [1.0]
        + [float(np.corrcoef(residual[:-lag], residual[lag:])[0, 1]) for lag in lags[1:]]
    )
    positions = exp.time_s.to_numpy(float) * EXPERIMENT_SPEED_CM_MIN / 60.0
    fig, axes = plt.subplots(2, 2, figsize=(8.4, 6.0))
    axes[0, 0].plot(exp.time_s, residual, lw=1.0)
    axes[0, 0].axhline(0.0, color="black", lw=0.7)
    axes[0, 0].set(xlabel="Time (s)", ylabel="Residual (degC)")
    axes[0, 1].scatter(exp.temperature_C, residual, s=7, alpha=0.65)
    axes[0, 1].axhline(0.0, color="black", lw=0.7)
    axes[0, 1].set(xlabel="Observed temperature (degC)", ylabel="Residual (degC)")
    axes[1, 0].plot(positions, residual, lw=1.0)
    axes[1, 0].axhline(0.0, color="black", lw=0.7)
    axes[1, 0].set(xlabel="Position (cm)", ylabel="Residual (degC)")
    axes[1, 1].stem(lags * 0.5, acf, basefmt=" ")
    axes[1, 1].set(xlabel="Lag (s)", ylabel="Residual ACF", ylim=(-0.2, 1.05))
    for ax in axes.ravel():
        ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    xpos = np.arange(len(comparison))
    width = 0.25
    ax.bar(xpos - width, comparison.rmse_C, width, label="full fit RMSE")
    ax.bar(xpos, comparison.rolling_mean_rmse_C, width, label="rolling mean RMSE")
    ax.bar(xpos + width, comparison.rolling_worst_rmse_C, width, label="rolling worst RMSE")
    ax.set_xticks(xpos, comparison.model)
    ax.set_ylabel("RMSE (degC)")
    ax.set_yscale("log")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.0, 4.5))
    ax.plot(q1_curve["time_s"], q1_curve["temperature_C"], color="#d62728", lw=1.6, label="board center")
    ax.plot(q1_curve["time_s"], q1_curve["effective_air_C"], color="#1f77b4", lw=1.0, alpha=0.75, label="effective air")
    for marker in q1_markers.values():
        ax.scatter(marker["time_s"], marker["temperature_C"], s=28, zorder=3)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Temperature (degC)")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(2, 2, figsize=(8.2, 6.0), sharex=True)
    panels = [
        ("peak_C", 240, 250, "Peak (degC)"),
        ("above_217_s", 40, 90, "Time above 217 (s)"),
        ("rise_150_190_s", 60, 120, "150--190 rise time (s)"),
        ("rise_slope_max_C_s", 0, 3, "Max rising slope (degC/s)"),
    ]
    for ax, (key, lo, hi, label) in zip(axes.ravel(), panels):
        ax.plot(q2_scan.speed_cm_min, q2_scan[key], lw=1.2)
        ax.axhspan(lo, hi, color="#2ca02c", alpha=0.12)
        ax.axvline(q2_speed, color="#d62728", ls="--", lw=1.0)
        ax.set_ylabel(label)
        ax.grid(alpha=0.25)
    for ax in axes[-1, :]:
        ax.set_xlabel("Speed (cm/min)")
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)

    for name, curve, color in [("q3", q3_curve, "#d62728"), ("q4", q4_curve, "#9467bd")]:
        fig, ax = plt.subplots(figsize=(8.0, 4.4))
        t = curve["time_s"]
        temp = curve["temperature_C"]
        metrics = curve_metrics(curve)
        ax.plot(t, temp, color=color, lw=1.6, label=f"{name.upper()} curve")
        ax.axhline(217.0, color="black", ls="--", lw=0.9)
        if name == "q3":
            shaded = (t >= metrics["t217_up_s"]) & (t <= metrics["peak_time_s"])
        else:
            shaded = temp >= 217.0
        ax.fill_between(t, 217.0, temp, where=shaded, color=color, alpha=0.22, interpolate=True)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Temperature (degC)")
        ax.grid(alpha=0.25)
        ax.legend()
        fig.tight_layout()
        fig.savefig(FIGURES / f"{name}<SOURCE_FILE_REDACTED>", bbox_inches="tight")
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    ax.plot(
        100.0 * (q4_frontier["epsilon_ratio"] - 1.0),
        q4_frontier["symmetry_area_abs_C_s"],
        marker="o",
    )
    for _, row in q4_frontier.iterrows():
        ax.annotate(
            f"{row['q3_rising_area_C_s']:.1f}",
            (100.0 * (row["epsilon_ratio"] - 1.0), row["symmetry_area_abs_C_s"]),
            xytext=(4, 5),
            textcoords="offset points",
            fontsize=8,
        )
    ax.set_xlabel("Allowed Q3 rising-area increase (%)")
    ax.set_ylabel("Symmetry absolute area (degC s)")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    metrics = sensitivity.groupby("design").agg(peak_min=("peak_C", "min"), peak_max=("peak_C", "max"))
    x = np.arange(len(metrics))
    center = (metrics.peak_min + metrics.peak_max) / 2.0
    error = (metrics.peak_max - metrics.peak_min) / 2.0
    ax.errorbar(x, center, yerr=error, fmt="o", capsize=5)
    ax.axhspan(240, 250, color="#2ca02c", alpha=0.12)
    ax.set_xticks(x, metrics.index)
    ax.set_ylabel("Peak range under perturbations (degC)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", bbox_inches="tight")
    plt.close(fig)


def main():
    np.random.seed(SEED)
    RESULTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    exp = load_experiment(INPUT_XLSX)

    time_diff = exp.time_s.diff().dropna()
    audit = {
        "status": "pass",
        "rows": int(len(exp)),
        "columns": ["time_s", "temperature_C"],
        "missing_cells": int(exp.isna().sum().sum()),
        "duplicate_rows": int(exp.duplicated().sum()),
        "duplicate_times": int(exp.time_s.duplicated().sum()),
        "time_monotone": bool(exp.time_s.is_monotonic_increasing),
        "time_start_s": float(exp.time_s.min()),
        "time_end_s": float(exp.time_s.max()),
        "time_step_values_s": sorted(float(v) for v in time_diff.unique()),
        "temperature_min_C": float(exp.temperature_C.min()),
        "temperature_max_C": float(exp.temperature_C.max()),
        "sensor_activation_note": "No observations before 19 s because the sensor starts at 30 degC; model initial condition at furnace entry is 25 degC.",
        "input_sha256": {
            INPUT_DOCX.name: sha256(INPUT_DOCX),
            INPUT_XLSX.name: sha256(INPUT_XLSX),
            INPUT_CSV.name: sha256(INPUT_CSV),
        },
    }
    write_json(RESULTS / "data_audit.json", audit)

    comparison, calibration, predictions, selected, selection_status, block_bootstrap = calibrate_candidates(exp)
    comparison.to_csv(RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.8f")
    selected_params = ModelParameters(**calibration[selected]["parameters"])
    calibration_out = {
        "status": selection_status,
        "selected_model": selected,
        "selection_rule": "lowest full-fit RMSE and lowest mean rolling-origin RMSE among the three predeclared candidates",
        "candidate_details": calibration,
        "moving_block_bootstrap": block_bootstrap,
        "standard_bic_status": "needs_review",
        "standard_bic_reason": "Residual autocorrelation violates the independent-observation evidence interpretation.",
        "parameter_identifiability": calibration[selected]["local_identifiability"],
        "external_validity": "needs_review",
        "external_validity_reason": "Only one experimental trajectory is available; rolling windows are source-separated in time but not a new operating condition.",
    }
    write_json(RESULTS / "calibration.json", calibration_out)

    q1_x = np.array([173.0, 198.0, 230.0, 257.0, 78.0])
    q1 = simulate(selected_params, grouped_setpoints(q1_x[:4]), q1_x[4], Q1_OUTPUT_DT_S, FINE_DX_CM)
    q1_metrics = curve_metrics(q1)
    marker_positions = {
        "zone_3_mid": float(ZONE_MID_CM[2]),
        "zone_6_mid": float(ZONE_MID_CM[5]),
        "zone_7_mid": float(ZONE_MID_CM[6]),
        "zone_8_end": float(ZONE_END_CM[7]),
    }
    q1_markers = {}
    for name, position in marker_positions.items():
        time_s = position / (q1_x[4] / 60.0)
        q1_markers[name] = {
            "position_cm": position,
            "time_s": float(time_s),
            "temperature_C": float(np.interp(time_s, q1["time_s"], q1["temperature_C"])),
        }
    q1_sample = sampled_curve(q1, 0.5)
    result_csv = q1_sample[["time_s", "temperature_C"]].copy()
    result_csv.columns = ["时间(s)", "温度(摄氏度)"]
    result_csv.to_csv(RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.6f", encoding="gb18030", lineterminator="\r\n")
    q1_sample.to_csv(RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.6f")
    q1_summary = {
        "status": "pass",
        "settings": settings_dict(q1_x),
        "transit_time_s": float(q1["time_s"][-1]),
        "markers": q1_markers,
        "curve_metrics": q1_metrics,
        "result_csv_rows": int(len(result_csv)),
        "result_csv_step_s": 0.5,
    }
    write_json(RESULTS / "q1_summary.json", q1_summary)

    q2_speed, q2_curve, q2_metrics, q2_slacks, q2_active, q2_scan = q2_maximum_speed(selected_params)
    q2_scan.to_csv(RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.8f")
    save_curve(RESULTS / "<SOURCE_FILE_REDACTED>", q2_curve)
    q2_summary = {
        "status": process_status(q2_metrics, tolerance=1e-6),
        "settings": settings_dict(np.array([182.0, 203.0, 237.0, 254.0, q2_speed])),
        "maximum_speed_cm_min": q2_speed,
        "recommended_floor_0.1_cm_min": math.floor(q2_speed * 10.0) / 10.0,
        "metrics": q2_metrics,
        "slacks": q2_slacks,
        "active_constraint": q2_active,
        "search": {"coarse_step_cm_min": 0.05, "bisection_iterations": 45},
    }
    write_json(RESULTS / "q2_summary.json", q2_summary)

    q3_candidate, q3_runs = optimize_q3(selected_params)
    q3_local = q3_candidate["local"]
    q3_curve = q3_candidate["curve"]
    q3_metrics = q3_candidate["metrics"]
    q3_x = np.asarray(q3_local.x, dtype=float)
    save_curve(RESULTS / "<SOURCE_FILE_REDACTED>", q3_curve)
    q3_summary = {
        "status": "pass"
        if q3_candidate["feasible"]
        and process_status(q3_metrics, tolerance=NUMERICAL_FEASIBILITY_TOL) == "pass"
        else "fail",
        "settings": settings_dict(q3_x),
        "metrics": q3_metrics,
        "slacks": process_slacks(q3_metrics),
        "objective": "minimize integral from the 217 C up-crossing to the interpolated peak of (T-217) dt",
        "objective_key": "q3_rising_area_C_s",
        "cooling_area_role": "diagnostic_only",
        "constraint_set": "original_problem_constraints",
        "engineering_margin": "none; robustness is reported separately",
        "numerical_feasibility_tolerance": NUMERICAL_FEASIBILITY_TOL,
        "optimizer": {
            "selected_run": q3_candidate["record"],
            "differential_evolution_seed_count": len(Q3_SEEDS),
            "alternative_global_screen": "1024-point scrambled Sobol plus SLSQP",
            "global_optimality": "needs_review",
        },
    }
    write_json(RESULTS / "q3_summary.json", q3_summary)

    q4_solutions, q4_runs = optimize_q4_frontier(selected_params, q3_candidate)
    q4_candidate = q4_solutions[PRIMARY_Q4_EPSILON]
    q4_local = q4_candidate["local"]
    q4_curve = q4_candidate["curve"]
    q4_metrics = q4_candidate["metrics"]
    q4_x = np.asarray(q4_local.x, dtype=float)
    q4_area_cap = PRIMARY_Q4_EPSILON * q3_metrics["q3_rising_area_C_s"]
    save_curve(RESULTS / "<SOURCE_FILE_REDACTED>", q4_curve)
    frontier_rows = []
    for ratio, candidate in q4_solutions.items():
        metrics = candidate["metrics"]
        x = np.asarray(candidate["local"].x, dtype=float)
        frontier_rows.append(
            {
                "epsilon_ratio": ratio,
                "area_cap_C_s": ratio * q3_metrics["q3_rising_area_C_s"],
                "feasibility_status": "pass" if candidate["feasible"] else "fail",
                "q3_rising_area_C_s": metrics["q3_rising_area_C_s"],
                "symmetry_area_abs_C_s": metrics["symmetry_area_abs_C_s"],
                "symmetry_ratio": metrics["symmetry_ratio"],
                "zones_1_5_C": x[0],
                "zone_6_C": x[1],
                "zone_7_C": x[2],
                "zones_8_9_C": x[3],
                "speed_cm_min": x[4],
            }
        )
        save_curve(RESULTS / f"q4_epsilon_{ratio:.3f}<SOURCE_FILE_REDACTED>", candidate["curve"])
    q4_frontier = pd.DataFrame(frontier_rows).sort_values("epsilon_ratio")
    q4_frontier.to_csv(
        RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.10f"
    )
    pd.DataFrame(q3_runs + q4_runs).to_csv(
        RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.10f"
    )
    q4_summary = {
        "status": "pass"
        if q4_candidate["feasible"]
        and process_status(q4_metrics, tolerance=NUMERICAL_FEASIBILITY_TOL) == "pass"
        else "fail",
        "settings": settings_dict(q4_x),
        "metrics": q4_metrics,
        "slacks": process_slacks(q4_metrics),
        "objective": "minimize mirrored absolute excess-temperature difference about the peak",
        "primary_objective_key": "symmetry_area_abs_C_s",
        "symmetry_ratio_role": "diagnostic_only",
        "area_cap_metric": "q3_rising_area_C_s",
        "area_cap_rule": "Primary report uses 105% of corrected Q3 rising-side minimum; 100%, 102.5%, 105%, and 110% are all reported.",
        "area_cap_C_s": q4_area_cap,
        "epsilon_sensitivity": frontier_rows,
        "optimizer": {
            "selected_run": q4_candidate["record"],
            "primary_differential_evolution_seed_count": len(Q4_SEEDS),
            "alternative_global_screen": "1024-point scrambled Sobol plus SLSQP",
            "global_optimality": "needs_review",
        },
        "constraint_set": "original_problem_constraints",
        "engineering_judgement": "The 105% epsilon is explicit and replaceable; it is not a theorem-derived optimum weight.",
    }
    write_json(RESULTS / "q4_summary.json", q4_summary)

    nominal_designs = {
        "Q2": np.array([182.0, 203.0, 237.0, 254.0, q2_speed]),
        "Q3": q3_x,
        "Q4": q4_x,
    }
    sensitivity, sensitivity_summary = sensitivity_analysis(selected_params, nominal_designs)
    sensitivity.to_csv(RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.8f")
    write_json(RESULTS / "sensitivity_summary.json", sensitivity_summary)
    joint_corners, joint_corner_summary = joint_corner_analysis(selected_params, nominal_designs)
    joint_corners.to_csv(
        RESULTS / "<SOURCE_FILE_REDACTED>", index=False, float_format="%.8f"
    )
    write_json(RESULTS / "joint_perturbation_summary.json", joint_corner_summary)

    boundary_checks = []
    for name, design in [
        ("lower_temperature_slow_speed", np.array([165.0, 185.0, 225.0, 245.0, 65.0])),
        ("upper_temperature_fast_speed", np.array([185.0, 205.0, 245.0, 265.0, 100.0])),
    ]:
        curve, metrics = evaluate_design(design, selected_params, FINE_DT_S, FINE_DX_CM)
        finite = all(np.all(np.isfinite(arr)) for arr in curve.values())
        bounded = float(curve["temperature_C"].min()) >= AMBIENT_C - 1e-8 and float(curve["temperature_C"].max()) <= 265.0 + 1e-8
        boundary_checks.append(
            {
                "case": name,
                "status": "pass" if finite and bounded else "fail",
                "finite": finite,
                "temperature_bounded": bounded,
                "settings": settings_dict(design),
                "metrics": metrics,
            }
        )
    write_json(RESULTS / "boundary_checks.json", boundary_checks)

    make_figures(
        exp,
        predictions,
        comparison,
        q1,
        q1_markers,
        q2_scan,
        q2_speed,
        q3_curve,
        q4_curve,
        sensitivity,
        q4_frontier,
    )

    numerical_completion = all(
        [selection_status == "pass", q1_summary["status"] == "pass", q2_summary["status"] == "pass", q3_summary["status"] == "pass", q4_summary["status"] == "pass"]
    )
    all_results = {
        # The overall claim cannot exceed the external-validity evidence layer.
        "status": "needs_review" if numerical_completion else "fail",
        "selected_model": selected,
        "model_parameters": selected_params.as_dict(),
        "q1": q1_summary,
        "q2": q2_summary,
        "q3": q3_summary,
        "q4": q4_summary,
        "sensitivity": sensitivity_summary,
        "joint_perturbation_corners": joint_corner_summary,
        "boundary_checks": boundary_checks,
        "evidence": {
            "identity": "pass",
            "reproduction": "needs_review",
            "independent_metric_oracle": "needs_review",
            "internal_numerical_validation": "needs_review",
            "rolling_holdout": "pass",
            "residual_independence": calibration[selected]["residual_diagnostics"]["status"],
            "parameter_identifiability": calibration[selected]["local_identifiability"]["status"],
            "engineering_robustness": "fail"
            if any(value["status"] == "fail" for value in joint_corner_summary.values())
            else "pass",
            "global_optimality": "needs_review",
            "external_validity": "needs_review",
        },
    }
    write_json(RESULTS / "all_results.json", all_results)

    macros = f"""% Generated by code/solve.py; do not edit numbers manually.
\\newcommand{{\\QOneZThree}}{{{q1_markers['zone_3_mid']['temperature_C']:.2f}}}
\\newcommand{{\\QOneZSix}}{{{q1_markers['zone_6_mid']['temperature_C']:.2f}}}
\\newcommand{{\\QOneZSeven}}{{{q1_markers['zone_7_mid']['temperature_C']:.2f}}}
\\newcommand{{\\QOneZEightEnd}}{{{q1_markers['zone_8_end']['temperature_C']:.2f}}}
\\newcommand{{\\QTwoSpeed}}{{{q2_speed:.3f}}}
\\newcommand{{\\QThreeTOne}}{{{q3_x[0]:.2f}}}
\\newcommand{{\\QThreeTSix}}{{{q3_x[1]:.2f}}}
\\newcommand{{\\QThreeTSeven}}{{{q3_x[2]:.2f}}}
\\newcommand{{\\QThreeTEight}}{{{q3_x[3]:.2f}}}
\\newcommand{{\\QThreeSpeed}}{{{q3_x[4]:.3f}}}
\\newcommand{{\\QThreeArea}}{{{q3_metrics['q3_rising_area_C_s']:.2f}}}
\\newcommand{{\\QFourTOne}}{{{q4_x[0]:.2f}}}
\\newcommand{{\\QFourTSix}}{{{q4_x[1]:.2f}}}
\\newcommand{{\\QFourTSeven}}{{{q4_x[2]:.2f}}}
\\newcommand{{\\QFourTEight}}{{{q4_x[3]:.2f}}}
\\newcommand{{\\QFourSpeed}}{{{q4_x[4]:.3f}}}
\\newcommand{{\\QFourArea}}{{{q4_metrics['q3_rising_area_C_s']:.2f}}}
\\newcommand{{\\QFourSymmetryArea}}{{{q4_metrics['symmetry_area_abs_C_s']:.2f}}}
\\newcommand{{\\QFourSymmetryRatio}}{{{q4_metrics['symmetry_ratio']:.4f}}}
"""
    (RESULTS / "generated_macros.tex").write_text(macros, encoding="utf-8")

    summary_md = f"""# Code-generated result summary

- Selected model: {selected}; full-fit RMSE {calibration[selected]['full_fit']['rmse_C']:.3f} degC; rolling mean/worst RMSE {calibration[selected]['rolling_mean_rmse_C']:.3f}/{calibration[selected]['rolling_worst_rmse_C']:.3f} degC.
- Q1 marker temperatures (degC): zone 3 midpoint {q1_markers['zone_3_mid']['temperature_C']:.3f}, zone 6 midpoint {q1_markers['zone_6_mid']['temperature_C']:.3f}, zone 7 midpoint {q1_markers['zone_7_mid']['temperature_C']:.3f}, zone 8 end {q1_markers['zone_8_end']['temperature_C']:.3f}.
- Q2 maximum speed: {q2_speed:.6f} cm/min; active constraint: {q2_active}; peak {q2_metrics['peak_C']:.3f} degC.
- Q3 settings: {q3_x.tolist()}; rising-side shaded area {q3_metrics['q3_rising_area_C_s']:.3f} degC*s; cooling-side diagnostic area {q3_metrics['cooling_area_217_C_s']:.3f} degC*s; peak {q3_metrics['peak_C']:.3f} degC; above-217 time {q3_metrics['above_217_s']:.3f} s.
- Q4 settings (105% epsilon): {q4_x.tolist()}; rising-side area {q4_metrics['q3_rising_area_C_s']:.3f} degC*s; symmetry absolute area {q4_metrics['symmetry_area_abs_C_s']:.3f} degC*s; symmetry ratio {q4_metrics['symmetry_ratio']:.6f}; duration imbalance {q4_metrics['duration_imbalance_s']:.3f} s.
"""
    (RESULTS / "generated_summary.md").write_text(summary_md, encoding="utf-8")

    reproducibility = {
        "status": "needs_review",
        "reason": "A second clean run and independent adaptive-integration check have not yet been executed.",
        "seeds": {
            "base": SEED,
            "q3_differential_evolution": list(Q3_SEEDS),
            "q4_primary_differential_evolution": list(Q4_SEEDS),
            "q3_sobol": SEED + 500,
            "q4_sobol": SEED + 600,
            "block_bootstrap": SEED + 77,
        },
        "command": "python code/solve.py",
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "numerical_grids": {
            "calibration_dt_s": CAL_DT_S,
            "calibration_dx_cm": CAL_DX_CM,
            "optimization_dt_s": OPT_DT_S,
            "optimization_dx_cm": OPT_DX_CM,
            "final_dt_s": FINE_DT_S,
            "final_dx_cm": FINE_DX_CM,
            "q1_output_dt_s": Q1_OUTPUT_DT_S,
        },
        "time_step_method": "midpoint frozen-air exponential update",
        "q3_endpoint_method": "linear 217 C crossing plus local quadratic peak interpolation",
        "q4_epsilon_ratios": list(Q4_EPSILON_RATIOS),
    }
    write_json(RESULTS / "reproducibility_runtime.json", reproducibility)
    print(json.dumps(clean_json({"status": all_results["status"], "q2_speed": q2_speed, "q3": q3_summary, "q4": q4_summary}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
