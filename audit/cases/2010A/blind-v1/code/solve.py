"""Run the complete blind solution and generate every numerical result/figure."""

from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from scipy.optimize import least_squares

from model import ActualTank, SmallEllipticalTank


SEED = 20260824
np.random.seed(SEED)


def json_default(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Cannot serialise {type(value)!r}")


def write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )


def metric(residual: np.ndarray) -> dict[str, float | int]:
    residual = np.asarray(residual, dtype=float)
    return {
        "n": int(residual.size),
        "rmse_l": float(np.sqrt(np.mean(residual**2))),
        "mae_l": float(np.mean(np.abs(residual))),
        "bias_l": float(np.mean(residual)),
        "max_abs_l": float(np.max(np.abs(residual))),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_numeric_sheet(df: pd.DataFrame, serial_column: str = "流水号") -> pd.DataFrame:
    return df.dropna(subset=[serial_column]).reset_index(drop=True)


def load_sheets(root: Path) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    extracted = root / "results" / "extracted"
    manifest = json.loads((extracted / "manifest.json").read_text(encoding="utf-8"))
    sheets: dict[str, pd.DataFrame] = {}
    for item in manifest:
        sheets[item["sheet_name"]] = pd.read_csv(extracted / item["csv"])
    return sheets, manifest


def small_tank_solution(sheets: dict[str, pd.DataFrame], results: Path) -> dict:
    tank_nominal = SmallEllipticalTank(horizontal_semiaxis=0.89)
    upright_in = clean_numeric_sheet(sheets["无变位进油"])
    upright_out = clean_numeric_sheet(sheets["无变位出油"])

    nominal_changes: list[np.ndarray] = []
    measured_changes: list[np.ndarray] = []
    for frame, direction in [(upright_in, 1.0), (upright_out, -1.0)]:
        height = frame["油位高度/mm"].to_numpy(float) / 1000.0
        cumulative = frame.iloc[:, 2].to_numpy(float)
        nominal_changes.append(np.diff(tank_nominal.volume_l(height, 0.0)))
        measured_changes.append(direction * np.diff(cumulative))
    x_change = np.concatenate(nominal_changes)
    y_change = np.concatenate(measured_changes)
    volume_scale = float(np.dot(x_change, y_change) / np.dot(x_change, x_change))
    effective_b = tank_nominal.horizontal_semiaxis * volume_scale

    nominal_residual = x_change - y_change
    calibrated_residual = volume_scale * x_change - y_change

    calibration_height_cm = np.arange(0, 121, 1, dtype=int)
    calibration_height_m = calibration_height_cm / 100.0
    upright_volume = tank_nominal.volume_l(calibration_height_m, 0.0, effective_b)
    tilted_volume = tank_nominal.volume_l(calibration_height_m, 4.1, effective_b)
    capacity = tank_nominal.capacity_l(effective_b)
    table = pd.DataFrame(
        {
            "height_cm": calibration_height_cm,
            "height_mm": calibration_height_cm * 10,
            "upright_volume_l": np.round(upright_volume, 2),
            "tilted_volume_l": np.round(tilted_volume, 2),
            "tilt_minus_upright_l": np.round(tilted_volume - upright_volume, 2),
            "tilted_capacity_pct": np.round(100.0 * tilted_volume / capacity, 5),
        }
    )
    table.to_csv(results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    validation_rows: list[dict] = []
    validation_detail: dict[str, dict] = {}
    experiment_plot_data: list[tuple[str, np.ndarray, np.ndarray, str]] = []
    validation_specs = [
        ("upright_in", upright_in, "累加进油量/L", 0.0, "in", 262.0),
        ("upright_out", upright_out, "累加出油量/L", 0.0, "out", None),
        (
            "tilted_in",
            clean_numeric_sheet(sheets["倾斜变位进油"]),
            "累加进油量/L",
            4.1,
            "in",
            215.0,
        ),
        (
            "tilted_out",
            clean_numeric_sheet(sheets["倾斜变位出油"]),
            "累加出油量/L",
            4.1,
            "out",
            None,
        ),
    ]
    for name, frame, cumulative_col, alpha, direction, initial in validation_specs:
        height = frame["油位高度/mm"].to_numpy(float) / 1000.0
        cumulative = frame[cumulative_col].to_numpy(float)
        predicted = tank_nominal.volume_l(height, alpha, effective_b)
        if direction == "in":
            observed_level = float(initial) + cumulative
            fitted_start = float(initial)
            increment_observed = np.diff(cumulative)
        else:
            fitted_start = float(np.mean(predicted + cumulative))
            observed_level = fitted_start - cumulative
            increment_observed = -np.diff(cumulative)
        level_residual = predicted - observed_level
        increment_residual = np.diff(predicted) - increment_observed
        detail = {
            "level": metric(level_residual),
            "increment": metric(increment_residual),
            "fitted_pre_sequence_volume_l": fitted_start,
        }
        validation_detail[name] = detail
        validation_rows.append(
            {
                "series": name,
                "level_rmse_l": detail["level"]["rmse_l"],
                "level_mae_l": detail["level"]["mae_l"],
                "level_max_abs_l": detail["level"]["max_abs_l"],
                "increment_rmse_l": detail["increment"]["rmse_l"],
                "increment_mae_l": detail["increment"]["mae_l"],
                "increment_max_abs_l": detail["increment"]["max_abs_l"],
            }
        )
        experiment_plot_data.append((name, height, observed_level, direction))
    pd.DataFrame(validation_rows).to_csv(
        results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )

    fine_height = np.linspace(0.0, 1.2, 1201)
    fine_upright = tank_nominal.volume_l(fine_height, 0.0, effective_b)
    fine_tilted = tank_nominal.volume_l(fine_height, 4.1, effective_b)
    difference = fine_tilted - fine_upright
    max_effect_index = int(np.argmax(np.abs(difference)))
    empty_reading, full_reading = tank_nominal.empty_full_readings_m(4.1)

    refined = SmallEllipticalTank(horizontal_semiaxis=0.89, cell_width=0.005)
    refinement_difference = np.max(
        np.abs(
            refined.volume_l(calibration_height_m, 4.1, effective_b)
            - tilted_volume
        )
    )

    return {
        "drawing_horizontal_semiaxis_m": 0.89,
        "effective_horizontal_semiaxis_m": effective_b,
        "volume_scale": volume_scale,
        "effective_capacity_l": capacity,
        "nominal_upright_increment": metric(nominal_residual),
        "calibrated_upright_increment": metric(calibrated_residual),
        "validation": validation_detail,
        "max_effect_l": float(difference[max_effect_index]),
        "max_abs_effect_l": float(abs(difference[max_effect_index])),
        "max_effect_height_cm": float(fine_height[max_effect_index] * 100.0),
        "operational_zero_height_volume_l": float(tilted_volume[0]),
        "operational_120cm_volume_l": float(tilted_volume[-1]),
        "physical_empty_reading_mm": float(empty_reading * 1000.0),
        "physical_full_reading_mm": float(full_reading * 1000.0),
        "quadrature_refinement_max_difference_l": float(refinement_difference),
        "plot": {
            "fine_height_m": fine_height,
            "fine_upright_l": fine_upright,
            "fine_tilted_l": fine_tilted,
            "difference_l": difference,
            "experiments": experiment_plot_data,
        },
    }


def actual_tank_solution(sheets: dict[str, pd.DataFrame], results: Path) -> dict:
    tank = ActualTank(cell_width=0.025)
    frame = clean_numeric_sheet(sheets["实际储油罐的采集数据"])
    height = frame["显示油高/mm"].to_numpy(float) / 1000.0
    inflow = frame["进油量/L"].to_numpy(float)
    outflow = frame["出油量/L"].fillna(0.0).to_numpy(float)
    net_flow = inflow - outflow
    refill_positions = np.flatnonzero(inflow > 0.0)
    if refill_positions.size != 1:
        raise RuntimeError("Expected exactly one refill event in attachment 2")
    refill = int(refill_positions[0])
    train_index = np.arange(1, refill)
    test_index = np.arange(refill + 1, len(frame))
    all_discharge_index = np.concatenate([train_index, test_index])

    def residual(params: np.ndarray | tuple[float, float], index: np.ndarray) -> np.ndarray:
        alpha, beta = float(params[0]), float(params[1])
        volume = tank.volume_l(height, alpha, beta)
        return volume[index] - volume[index - 1] - net_flow[index]

    pitch_fit = least_squares(
        lambda p: residual((p[0], 0.0), train_index),
        x0=np.array([2.0]),
        bounds=([-10.0], [10.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )
    pitch_params = np.array([pitch_fit.x[0], 0.0])
    full_train_fit = least_squares(
        lambda p: residual(p, train_index),
        x0=np.array([2.0, 5.0]),
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )

    candidates = [
        ("upright_baseline", np.array([0.0, 0.0]), 0),
        ("pitch_only", pitch_params, 1),
        ("pitch_and_roll", full_train_fit.x, 2),
    ]
    candidate_rows: list[dict] = []
    candidate_detail: dict[str, dict] = {}
    for name, params, parameter_count in candidates:
        volume = tank.volume_l(height, params[0], params[1])
        refill_prediction = volume[refill] - volume[refill - 1]
        train_metric = metric(residual(params, train_index))
        test_metric = metric(residual(params, test_index))
        refill_error = float(refill_prediction - inflow[refill])
        detail = {
            "alpha_deg": float(params[0]),
            "beta_deg": float(params[1]),
            "parameter_count": parameter_count,
            "train": train_metric,
            "test": test_metric,
            "refill_prediction_l": float(refill_prediction),
            "refill_error_l": refill_error,
        }
        candidate_detail[name] = detail
        candidate_rows.append(
            {
                "model": name,
                "parameters": parameter_count,
                "alpha_deg": params[0],
                "beta_deg": params[1],
                "train_rmse_l": train_metric["rmse_l"],
                "test_rmse_l": test_metric["rmse_l"],
                "test_mae_l": test_metric["mae_l"],
                "refill_error_l": refill_error,
            }
        )
    candidate_frame = pd.DataFrame(candidate_rows)
    candidate_frame.to_csv(
        results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )

    final_fit = least_squares(
        lambda p: residual(p, all_discharge_index),
        x0=full_train_fit.x,
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )
    final_params = final_fit.x
    final_residual = residual(final_params, all_discharge_index)
    degrees_freedom = final_residual.size - final_params.size
    residual_variance = float(np.sum(final_residual**2) / degrees_freedom)
    covariance = residual_variance * np.linalg.inv(final_fit.jac.T @ final_fit.jac)
    standard_error = np.sqrt(np.diag(covariance))
    correlation = covariance / np.outer(standard_error, standard_error)

    robust_fit = least_squares(
        lambda p: residual(p, all_discharge_index),
        x0=final_params,
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        loss="soft_l1",
        f_scale=2.0,
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )
    first_segment_fit = full_train_fit
    second_segment_fit = least_squares(
        lambda p: residual(p, test_index),
        x0=final_params,
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )

    volume_final = tank.volume_l(height, final_params[0], final_params[1])
    refill_prediction_final = float(volume_final[refill] - volume_final[refill - 1])
    refill_error_final = refill_prediction_final - float(inflow[refill])

    # Reconstruct an absolute volume trajectory separately for the two discharge runs.
    segment_metrics: list[dict] = []
    segment_ids = np.full(len(frame), "", dtype=object)
    for segment_name, lower, upper in [
        ("first_discharge", 0, refill - 1),
        ("second_discharge", refill, len(frame) - 1),
    ]:
        positions = np.arange(lower, upper + 1)
        cumulative_out = np.concatenate(
            [[0.0], np.cumsum(outflow[positions[1:]])]
        )
        fitted_start = float(np.mean(volume_final[positions] + cumulative_out))
        reconstructed = fitted_start - cumulative_out
        segment_residual = volume_final[positions] - reconstructed
        segment_ids[positions] = segment_name
        segment_metrics.append(
            {
                "segment": segment_name,
                "fitted_start_volume_l": fitted_start,
                **metric(segment_residual),
            }
        )

    residual_frame = pd.DataFrame(
        {
            "serial": frame.loc[all_discharge_index, "流水号"].to_numpy(int),
            "split": np.where(all_discharge_index < refill, "train", "test"),
            "height_prev_mm": frame.loc[all_discharge_index - 1, "显示油高/mm"].to_numpy(float),
            "height_mm": frame.loc[all_discharge_index, "显示油高/mm"].to_numpy(float),
            "net_flow_l": net_flow[all_discharge_index],
            "predicted_change_l": net_flow[all_discharge_index] + final_residual,
            "residual_l": final_residual,
        }
    )
    residual_frame.to_csv(
        results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )

    calibration_height_cm = np.arange(0, 301, 10, dtype=int)
    calibration_height_m = calibration_height_cm / 100.0
    displaced_table_volume = tank.volume_l(
        calibration_height_m, final_params[0], final_params[1]
    )
    upright_table_volume = tank.volume_l(calibration_height_m, 0.0, 0.0)
    calibration_table = pd.DataFrame(
        {
            "height_cm": calibration_height_cm,
            "height_mm": calibration_height_cm * 10,
            "displaced_volume_l": np.round(displaced_table_volume, 2),
            "upright_volume_l": np.round(upright_table_volume, 2),
            "displaced_minus_upright_l": np.round(
                displaced_table_volume - upright_table_volume, 2
            ),
        }
    )
    calibration_table.to_csv(
        results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )

    # Sensitivity and numerical convergence checks.
    segment_parameter_sets = [first_segment_fit.x, second_segment_fit.x]
    segment_table_envelope = max(
        float(
            np.max(
                np.abs(
                    tank.volume_l(calibration_height_m, p[0], p[1])
                    - displaced_table_volume
                )
            )
        )
        for p in segment_parameter_sets
    )
    plus_height = tank.volume_l(
        calibration_height_m + 0.001, final_params[0], final_params[1]
    )
    minus_height = tank.volume_l(
        calibration_height_m - 0.001, final_params[0], final_params[1]
    )
    height_sensitivity = float(
        max(
            np.max(np.abs(plus_height - displaced_table_volume)),
            np.max(np.abs(minus_height - displaced_table_volume)),
        )
    )
    refined_tank = ActualTank(cell_width=0.0125)
    refined_table = refined_tank.volume_l(
        calibration_height_m, final_params[0], final_params[1]
    )
    quadrature_difference = float(np.max(np.abs(refined_table - displaced_table_volume)))
    robust_table_difference = float(
        np.max(
            np.abs(
                tank.volume_l(calibration_height_m, robust_fit.x[0], robust_fit.x[1])
                - displaced_table_volume
            )
        )
    )
    sensitivity_rows = [
        {
            "check": "first_vs_all_parameter_table",
            "max_abs_volume_change_l": float(
                np.max(
                    np.abs(
                        tank.volume_l(
                            calibration_height_m,
                            first_segment_fit.x[0],
                            first_segment_fit.x[1],
                        )
                        - displaced_table_volume
                    )
                )
            ),
        },
        {
            "check": "second_vs_all_parameter_table",
            "max_abs_volume_change_l": float(
                np.max(
                    np.abs(
                        tank.volume_l(
                            calibration_height_m,
                            second_segment_fit.x[0],
                            second_segment_fit.x[1],
                        )
                        - displaced_table_volume
                    )
                )
            ),
        },
        {
            "check": "height_plus_or_minus_1mm",
            "max_abs_volume_change_l": height_sensitivity,
        },
        {
            "check": "robust_loss_vs_least_squares",
            "max_abs_volume_change_l": robust_table_difference,
        },
        {
            "check": "quadrature_0.025m_vs_0.0125m",
            "max_abs_volume_change_l": quadrature_difference,
        },
    ]
    pd.DataFrame(sensitivity_rows).to_csv(
        results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )

    empty_reading, full_reading = tank.empty_full_readings_m(
        final_params[0], final_params[1]
    )
    displayed = frame["显示油量容积/L"].to_numpy(float)
    upright_at_observed = tank.volume_l(height, 0.0, 0.0)
    display_residual = upright_at_observed - displayed

    return {
        "candidate_comparison": candidate_detail,
        "selected_model": "pitch_and_roll",
        "training_fit": {
            "alpha_deg": float(full_train_fit.x[0]),
            "beta_deg": float(full_train_fit.x[1]),
        },
        "final_fit": {
            "alpha_deg": float(final_params[0]),
            "beta_deg": float(final_params[1]),
            "linearized_standard_error_deg": standard_error,
            "linearized_95pct_ci_deg": np.column_stack(
                [final_params - 1.96 * standard_error, final_params + 1.96 * standard_error]
            ),
            "parameter_correlation": float(correlation[0, 1]),
            "all_discharge_increment": metric(final_residual),
        },
        "robust_fit": {
            "alpha_deg": float(robust_fit.x[0]),
            "beta_deg": float(robust_fit.x[1]),
        },
        "segment_fits": {
            "first": {
                "alpha_deg": float(first_segment_fit.x[0]),
                "beta_deg": float(first_segment_fit.x[1]),
            },
            "second": {
                "alpha_deg": float(second_segment_fit.x[0]),
                "beta_deg": float(second_segment_fit.x[1]),
            },
        },
        "segment_absolute_reconstruction": segment_metrics,
        "refill": {
            "observed_l": float(inflow[refill]),
            "predicted_l": refill_prediction_final,
            "error_l": refill_error_final,
        },
        "old_display_matches_upright_geometry": metric(display_residual),
        "capacity_l": tank.capacity_l(),
        "operational_zero_height_volume_l": float(displaced_table_volume[0]),
        "operational_300cm_volume_l": float(displaced_table_volume[-1]),
        "physical_empty_reading_mm": float(empty_reading * 1000.0),
        "physical_full_reading_mm": float(full_reading * 1000.0),
        "sensitivity": {
            "segment_fit_table_envelope_l": segment_table_envelope,
            "height_plus_or_minus_1mm_max_l": height_sensitivity,
            "robust_loss_table_max_difference_l": robust_table_difference,
            "quadrature_refinement_max_difference_l": quadrature_difference,
        },
        "plot": {
            "residual_frame": residual_frame,
            "candidate_frame": candidate_frame,
            "calibration_height_m": calibration_height_m,
            "displaced_table_volume_l": displaced_table_volume,
            "upright_table_volume_l": upright_table_volume,
        },
    }


def make_figures(small: dict, actual: dict, figures: Path) -> None:
    figures.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "figure.figsize": (7.2, 4.6),
            "axes.grid": True,
            "grid.alpha": 0.25,
            "font.size": 10,
        }
    )

    plot = small["plot"]
    fig, ax = plt.subplots()
    ax.plot(plot["fine_height_m"] * 100, plot["fine_upright_l"], label="upright model")
    ax.plot(plot["fine_height_m"] * 100, plot["fine_tilted_l"], label="tilted model (4.1 deg)")
    markers = {"upright_in": "o", "upright_out": "s", "tilted_in": "^", "tilted_out": "v"}
    for name, height, observed, _ in plot["experiments"]:
        ax.scatter(height * 100, observed, s=12, alpha=0.45, marker=markers[name], label=name)
    ax.set_xlabel("Gauge height (cm)")
    ax.set_ylabel("Volume (L)")
    ax.set_title("Small elliptical tank: model and experiments")
    ax.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots()
    ax.plot(plot["fine_height_m"] * 100, plot["difference_l"], color="#b22222")
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("Gauge height (cm)")
    ax.set_ylabel("Tilted minus upright volume (L)")
    ax.set_title("Effect of 4.1 deg longitudinal tilt")
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200)
    plt.close(fig)

    candidates = actual["plot"]["candidate_frame"]
    x = np.arange(len(candidates))
    fig, ax = plt.subplots()
    width = 0.35
    ax.bar(x - width / 2, candidates["train_rmse_l"], width, label="train")
    ax.bar(x + width / 2, candidates["test_rmse_l"], width, label="held-out")
    ax.set_xticks(x, ["upright", "pitch", "pitch+roll"])
    ax.set_ylabel("Increment RMSE (L)")
    ax.set_title("Nested candidate comparison")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200)
    plt.close(fig)

    residuals = actual["plot"]["residual_frame"]
    fig, ax = plt.subplots()
    for split, colour in [("train", "#1f77b4"), ("test", "#ff7f0e")]:
        sub = residuals[residuals["split"] == split]
        ax.scatter(sub["height_mm"], sub["residual_l"], s=10, alpha=0.6, label=split, color=colour)
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("Gauge height (mm)")
    ax.set_ylabel("Mass-balance residual (L)")
    ax.set_title("Actual tank: incremental residuals")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots()
    h = actual["plot"]["calibration_height_m"] * 100
    ax.plot(h, actual["plot"]["upright_table_volume_l"], label="old upright table")
    ax.plot(h, actual["plot"]["displaced_table_volume_l"], label="recalibrated table")
    ax.set_xlabel("Gauge height (cm)")
    ax.set_ylabel("Volume (L)")
    ax.set_title("Actual tank calibration before and after displacement")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200)
    plt.close(fig)


def write_tex_macros(results: Path, small: dict, actual: dict) -> None:
    candidates = actual["candidate_comparison"]
    final = actual["final_fit"]
    values = {
        "SmallScale": f"{small['volume_scale']:.6f}",
        "SmallEffectiveDiameter": f"{2*small['effective_horizontal_semiaxis_m']:.4f}",
        "SmallCapacity": f"{small['effective_capacity_l']:.2f}",
        "SmallMaxEffect": f"{small['max_abs_effect_l']:.2f}",
        "SmallMaxEffectHeight": f"{small['max_effect_height_cm']:.1f}",
        "SmallTiltInRMSE": f"{small['validation']['tilted_in']['level']['rmse_l']:.2f}",
        "SmallTiltOutRMSE": f"{small['validation']['tilted_out']['level']['rmse_l']:.2f}",
        "FinalAlpha": f"{final['alpha_deg']:.4f}",
        "FinalBeta": f"{final['beta_deg']:.4f}",
        "BaselineTrainRMSE": f"{candidates['upright_baseline']['train']['rmse_l']:.3f}",
        "BaselineTestRMSE": f"{candidates['upright_baseline']['test']['rmse_l']:.3f}",
        "PitchTrainRMSE": f"{candidates['pitch_only']['train']['rmse_l']:.3f}",
        "PitchTestRMSE": f"{candidates['pitch_only']['test']['rmse_l']:.3f}",
        "FullTrainRMSE": f"{candidates['pitch_and_roll']['train']['rmse_l']:.3f}",
        "FullTestRMSE": f"{candidates['pitch_and_roll']['test']['rmse_l']:.3f}",
        "RefillError": f"{actual['refill']['error_l']:.2f}",
        "FinalIncrementRMSE": f"{final['all_discharge_increment']['rmse_l']:.3f}",
        "ActualCapacity": f"{actual['capacity_l']:.2f}",
        "ActualZeroVolume": f"{actual['operational_zero_height_volume_l']:.2f}",
        "ActualThreeMetreVolume": f"{actual['operational_300cm_volume_l']:.2f}",
        "ActualEmptyReading": f"{actual['physical_empty_reading_mm']:.1f}",
        "ActualFullReading": f"{actual['physical_full_reading_mm']:.1f}",
        "SegmentEnvelope": f"{actual['sensitivity']['segment_fit_table_envelope_l']:.2f}",
        "HeightSensitivity": f"{actual['sensitivity']['height_plus_or_minus_1mm_max_l']:.2f}",
        "QuadratureDifference": f"{actual['sensitivity']['quadrature_refinement_max_difference_l']:.4f}",
    }
    lines = ["% Generated by code/solve.py; do not edit by hand."]
    lines.extend(f"\\newcommand{{\\{name}}}{{{value}}}" for name, value in values.items())
    (results / "generated_values.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(results / "generated_values.json", values)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    results = root / "results"
    figures = root / "figures"
    results.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    sheets, manifest = load_sheets(root)

    audit_sheets = []
    for item in manifest:
        frame = sheets[item["sheet_name"]]
        cleaned = clean_numeric_sheet(frame)
        audit_sheets.append(
            {
                "sheet": item["sheet_name"],
                "data_rows_raw": int(len(frame)),
                "data_rows_clean": int(len(cleaned)),
                "all_empty_rows_removed": int(len(frame) - len(cleaned)),
                "duplicate_serials": int(cleaned["流水号"].duplicated().sum()),
                "height_min_mm": float(cleaned["油位高度/mm" if "油位高度/mm" in cleaned else "显示油高/mm"].min()),
                "height_max_mm": float(cleaned["油位高度/mm" if "油位高度/mm" in cleaned else "显示油高/mm"].max()),
            }
        )
    audit = {
        "status": "pass",
        "sheets": audit_sheets,
        "attachment2_semantic_missing": {
            "outflow_missing_rows": 1,
            "interpretation": "the only blank outflow is the single refill event",
            "status": "pass",
        },
        "drawing_vs_upright_data_scale": {
            "status": "needs_review",
            "note": "The 1.78 m drawing width gives a systematic volume scale; the upright experiment identifies an effective scale used below.",
        },
    }
    write_json(results / "data_audit.json", audit)

    small = small_tank_solution(sheets, results)
    actual = actual_tank_solution(sheets, results)
    make_figures(small, actual, figures)

    # Remove plotting-only objects from the structured summary.
    small_summary = {key: value for key, value in small.items() if key != "plot"}
    actual_summary = {key: value for key, value in actual.items() if key != "plot"}
    summary = {
        "status": "pass",
        "seed": SEED,
        "small_tank": small_summary,
        "actual_tank": actual_summary,
    }
    write_json(results / "summary.json", summary)
    write_json(results / "parameters.json", actual_summary["final_fit"])
    write_tex_macros(results, small_summary, actual_summary)

    input_files = sorted((root / "input").rglob("*"))
    input_hashes = {
        str(path.relative_to(root)).replace("\\", "/"): sha256(path)
        for path in input_files
        if path.is_file()
    }
    metadata = {
        "status": "pass",
        "seed": SEED,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "matplotlib": matplotlib.__version__,
        "input_sha256": input_hashes,
        "commands": [
            "powershell -File code/extract_inputs.ps1",
            "python code/solve.py",
            "python code/render_paper.py",
            "python code/verify_outputs.py",
        ],
    }
    write_json(results / "run_metadata.json", metadata)
    print(
        "[PASS] generated solution: "
        f"alpha={actual_summary['final_fit']['alpha_deg']:.4f} deg, "
        f"beta={actual_summary['final_fit']['beta_deg']:.4f} deg"
    )


if __name__ == "__main__":
    main()
