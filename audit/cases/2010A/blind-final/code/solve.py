"""Generate the complete blind-revision numerical solution and figures."""

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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metric(residual: np.ndarray) -> dict[str, float | int]:
    residual = np.asarray(residual, dtype=float)
    return {
        "n": int(residual.size),
        "rmse_l": float(np.sqrt(np.mean(residual**2))),
        "mae_l": float(np.mean(np.abs(residual))),
        "bias_l": float(np.mean(residual)),
        "max_abs_l": float(np.max(np.abs(residual))),
    }


def residual_diagnostics(residual: np.ndarray) -> dict[str, float | str]:
    residual = np.asarray(residual, dtype=float)
    lag1 = float(np.corrcoef(residual[:-1], residual[1:])[0, 1])
    denominator = float(np.sum(residual**2))
    durbin_watson = float(np.sum(np.diff(residual) ** 2) / denominator)
    return {
        "status": "needs_review" if abs(lag1) >= 0.2 else "pass",
        "lag1_correlation": lag1,
        "durbin_watson": durbin_watson,
    }


def clean_numeric_sheet(frame: pd.DataFrame, serial_column: str = "流水号") -> pd.DataFrame:
    return frame.dropna(subset=[serial_column]).reset_index(drop=True)


def load_sheets(root: Path) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    extracted = root / "results" / "extracted"
    manifest = json.loads((extracted / "manifest.json").read_text(encoding="utf-8"))
    sheets: dict[str, pd.DataFrame] = {}
    for item in manifest:
        sheets[item["sheet_name"]] = pd.read_csv(extracted / item["csv"])
    return sheets, manifest


def correlation_with_height(residual: np.ndarray, height: np.ndarray) -> float:
    return float(np.corrcoef(np.asarray(residual), np.asarray(height))[0, 1])


def small_tank_solution(sheets: dict[str, pd.DataFrame], results: Path) -> dict:
    tank = SmallEllipticalTank(horizontal_semiaxis=0.89, cell_width=0.01)
    upright_in = clean_numeric_sheet(sheets["无变位进油"])
    upright_out = clean_numeric_sheet(sheets["无变位出油"])

    nominal_changes: list[np.ndarray] = []
    measured_changes: list[np.ndarray] = []
    for frame, direction in ((upright_in, 1.0), (upright_out, -1.0)):
        height = frame["油位高度/mm"].to_numpy(float) / 1000.0
        cumulative = frame.iloc[:, 2].to_numpy(float)
        nominal_changes.append(np.diff(tank.volume_l(height, 0.0)))
        measured_changes.append(direction * np.diff(cumulative))
    nominal_change = np.concatenate(nominal_changes)
    measured_change = np.concatenate(measured_changes)
    volume_scale = float(
        np.dot(nominal_change, measured_change) / np.dot(nominal_change, nominal_change)
    )
    transferable_b = tank.horizontal_semiaxis * volume_scale

    tilted_in = clean_numeric_sheet(sheets["倾斜变位进油"])
    tilted_out = clean_numeric_sheet(sheets["倾斜变位出油"])
    height_in = tilted_in["油位高度/mm"].to_numpy(float) / 1000.0
    height_out = tilted_out["油位高度/mm"].to_numpy(float) / 1000.0
    observed_in = 215.0 + tilted_in["累加进油量/L"].to_numpy(float)
    cumulative_out = tilted_out["累加出油量/L"].to_numpy(float)

    def predict(height: np.ndarray, b_value: float, offset_m: float) -> np.ndarray:
        return tank.volume_l(height, 4.1, b_value, offset_m)

    zero_fit = least_squares(
        lambda value: predict(height_in, transferable_b, float(value[0])) - observed_in,
        x0=np.array([0.0]),
        bounds=([-0.05], [0.05]),
        xtol=1e-13,
        ftol=1e-13,
        gtol=1e-13,
    )
    pose_fit = least_squares(
        lambda value: predict(height_in, float(value[0]), float(value[1])) - observed_in,
        x0=np.array([0.89, -0.015]),
        bounds=([0.80, -0.05], [0.95, 0.05]),
        xtol=1e-13,
        ftol=1e-13,
        gtol=1e-13,
        max_nfev=2000,
    )
    candidates = [
        ("transfer_scale", transferable_b, 0.0, 0),
        ("transfer_scale_plus_zero", transferable_b, float(zero_fit.x[0]), 1),
        ("pose_scale_plus_zero", float(pose_fit.x[0]), float(pose_fit.x[1]), 2),
    ]

    comparison_rows: list[dict] = []
    comparison_detail: dict[str, dict] = {}
    residual_rows: list[pd.DataFrame] = []
    for name, b_value, offset_m, parameter_count in candidates:
        predicted_in = predict(height_in, b_value, offset_m)
        train_residual = predicted_in - observed_in
        predicted_out = predict(height_out, b_value, offset_m)
        fitted_start = float(np.mean(predicted_out + cumulative_out))
        observed_out = fitted_start - cumulative_out
        holdout_residual = predicted_out - observed_out
        train_metrics = metric(train_residual)
        holdout_metrics = metric(holdout_residual)
        train_corr = correlation_with_height(train_residual, height_in)
        holdout_corr = correlation_with_height(holdout_residual, height_out)
        comparison_detail[name] = {
            "parameters_fitted_on_tilted_in": parameter_count,
            "horizontal_semiaxis_m": b_value,
            "level_offset_mm": offset_m * 1000.0,
            "training_tilted_in": train_metrics,
            "time_ordered_check_tilted_out": holdout_metrics,
            "training_residual_height_correlation": train_corr,
            "time_check_residual_height_correlation": holdout_corr,
            "time_check_fitted_start_volume_l": fitted_start,
        }
        comparison_rows.append(
            {
                "candidate": name,
                "fitted_parameters": parameter_count,
                "horizontal_semiaxis_m": b_value,
                "level_offset_mm": offset_m * 1000.0,
                "training_rmse_l": train_metrics["rmse_l"],
                "time_check_rmse_l": holdout_metrics["rmse_l"],
                "training_residual_height_correlation": train_corr,
                "time_check_residual_height_correlation": holdout_corr,
            }
        )
        residual_rows.extend(
            [
                pd.DataFrame(
                    {
                        "candidate": name,
                        "series": "tilted_in_training",
                        "height_mm": height_in * 1000.0,
                        "observed_volume_l": observed_in,
                        "predicted_volume_l": predicted_in,
                        "residual_l": train_residual,
                    }
                ),
                pd.DataFrame(
                    {
                        "candidate": name,
                        "series": "tilted_out_time_check",
                        "height_mm": height_out * 1000.0,
                        "observed_volume_l": observed_out,
                        "predicted_volume_l": predicted_out,
                        "residual_l": holdout_residual,
                    }
                ),
            ]
        )
    pd.DataFrame(comparison_rows).to_csv(
        results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8", float_format="%.10f"
    )
    pd.concat(residual_rows, ignore_index=True).to_csv(
        results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8", float_format="%.10f"
    )

    selected_name = "pose_scale_plus_zero"
    selected_b = float(pose_fit.x[0])
    selected_offset = float(pose_fit.x[1])
    calibration_height_cm = np.arange(0, 121, dtype=int)
    calibration_height_m = calibration_height_cm / 100.0
    upright_volume = tank.volume_l(calibration_height_m, 0.0, transferable_b, 0.0)
    candidate_tables = {
        name: predict(calibration_height_m, b_value, offset_m)
        for name, b_value, offset_m, _ in candidates
    }
    production_volume = candidate_tables[selected_name]
    envelope_stack = np.vstack(list(candidate_tables.values()))
    envelope_lower = np.min(envelope_stack, axis=0)
    envelope_upper = np.max(envelope_stack, axis=0)
    table = pd.DataFrame(
        {
            "height_cm": calibration_height_cm,
            "height_mm": calibration_height_cm * 10,
            "volume_l_numeric": production_volume,
            "volume_l_reported": np.rint(production_volume).astype(int),
            "model_envelope_lower_l_numeric": envelope_lower,
            "model_envelope_upper_l_numeric": envelope_upper,
            "model_envelope_lower_l_reported": np.floor(envelope_lower).astype(int),
            "model_envelope_upper_l_reported": np.ceil(envelope_upper).astype(int),
            "upright_volume_l_numeric": upright_volume,
            "production_minus_upright_l_numeric": production_volume - upright_volume,
            "production_minus_upright_l_reported": np.rint(
                production_volume - upright_volume
            ).astype(int),
        }
    )
    table.to_csv(
        results / "<SOURCE_FILE_REDACTED>",
        index=False,
        encoding="utf-8",
        float_format="%.6f",
    )

    def combined_residual(value: np.ndarray) -> np.ndarray:
        b_value, offset_m = float(value[0]), float(value[1])
        residual_in = predict(height_in, b_value, offset_m) - observed_in
        predicted_out = predict(height_out, b_value, offset_m)
        residual_out = predicted_out + cumulative_out
        residual_out = residual_out - np.mean(residual_out)
        return np.concatenate([residual_in, residual_out])

    combined_fit = least_squares(
        combined_residual,
        x0=pose_fit.x,
        bounds=([0.80, -0.05], [0.95, 0.05]),
        xtol=1e-13,
        ftol=1e-13,
        gtol=1e-13,
        max_nfev=2000,
    )
    combined_table = predict(
        calibration_height_m, float(combined_fit.x[0]), float(combined_fit.x[1])
    )

    fine_height = np.linspace(0.0, 1.2, 1201)
    fine_upright = tank.volume_l(fine_height, 0.0, transferable_b, 0.0)
    fine_production = predict(fine_height, selected_b, selected_offset)
    fine_difference = fine_production - fine_upright
    maximum_index = int(np.argmax(np.abs(fine_difference)))
    empty_reading, full_reading = tank.empty_full_readings_m(4.1, selected_offset)
    refined = SmallEllipticalTank(horizontal_semiaxis=0.89, cell_width=0.005)
    refined_table = refined.volume_l(
        calibration_height_m, 4.1, selected_b, selected_offset
    )

    return {
        "status": "needs_review",
        "upright_scale_calibration": {
            "status": "pass",
            "drawing_horizontal_semiaxis_m": 0.89,
            "transferable_horizontal_semiaxis_m": transferable_b,
            "volume_scale": volume_scale,
            "nominal_increment": metric(nominal_change - measured_change),
            "calibrated_increment": metric(volume_scale * nominal_change - measured_change),
        },
        "candidate_comparison": comparison_detail,
        "selection": {
            "status": "needs_review",
            "selected_candidate": selected_name,
            "selection_data": "tilted_in_training_only",
            "selection_rule": "Choose the physically interpretable nested candidate with the lowest tilted-in RMSE; tilted-out is not used in this rule.",
            "physical_interpretation": "needs_review",
            "note": "The data support pose-specific scale and zero, but the allowed materials do not identify the installation mechanism.",
        },
        "production_model": {
            "alpha_deg": 4.1,
            "horizontal_semiaxis_m": selected_b,
            "level_offset_mm": selected_offset * 1000.0,
            "capacity_l": tank.capacity_l(selected_b),
            "training_metrics": comparison_detail[selected_name]["training_tilted_in"],
            "time_ordered_check_metrics": comparison_detail[selected_name][
                "time_ordered_check_tilted_out"
            ],
            "time_check_residual_height_correlation": comparison_detail[selected_name][
                "time_check_residual_height_correlation"
            ],
        },
        "transfer_model_time_check_rmse_l": comparison_detail["transfer_scale"][
            "time_ordered_check_tilted_out"
        ]["rmse_l"],
        "transfer_model_time_check_residual_height_correlation": comparison_detail[
            "transfer_scale"
        ]["time_check_residual_height_correlation"],
        "model_envelope": {
            "status": "needs_review",
            "basis": [name for name, _, _, _ in candidates],
            "max_width_l": float(np.max(envelope_upper - envelope_lower)),
            "max_width_height_cm": int(
                calibration_height_cm[np.argmax(envelope_upper - envelope_lower)]
            ),
            "not_a_confidence_interval": "needs_review",
        },
        "combined_fit_cross_check": {
            "horizontal_semiaxis_m": float(combined_fit.x[0]),
            "level_offset_mm": float(combined_fit.x[1] * 1000.0),
            "combined_rmse_l": float(np.sqrt(np.mean(combined_residual(combined_fit.x) ** 2))),
            "max_table_change_from_production_l": float(
                np.max(np.abs(combined_table - production_volume))
            ),
        },
        "max_abs_change_from_upright_l": float(abs(fine_difference[maximum_index])),
        "max_change_height_cm": float(fine_height[maximum_index] * 100.0),
        "operational_zero_height_volume_l": float(production_volume[0]),
        "operational_120cm_volume_l": float(production_volume[-1]),
        "physical_empty_reading_mm": float(empty_reading * 1000.0),
        "physical_full_reading_mm": float(full_reading * 1000.0),
        "quadrature_refinement_max_difference_l": float(
            np.max(np.abs(refined_table - production_volume))
        ),
        "table_rows": int(len(table)),
        "_plot": {
            "fine_height_cm": fine_height * 100.0,
            "fine_upright_l": fine_upright,
            "fine_production_l": fine_production,
            "table_height_cm": calibration_height_cm,
            "envelope_lower_l": envelope_lower,
            "envelope_upper_l": envelope_upper,
            "height_in_cm": height_in * 100.0,
            "observed_in_l": observed_in,
            "height_out_cm": height_out * 100.0,
            "baseline_out_residual_l": (
                predict(height_out, transferable_b, 0.0)
                + cumulative_out
                - np.mean(predict(height_out, transferable_b, 0.0) + cumulative_out)
            ),
            "production_out_residual_l": (
                predict(height_out, selected_b, selected_offset)
                + cumulative_out
                - np.mean(
                    predict(height_out, selected_b, selected_offset) + cumulative_out
                )
            ),
            "fine_difference_l": fine_difference,
        },
    }


def conditional_hac_covariance(
    jacobian: np.ndarray,
    residual: np.ndarray,
    segment_lengths: list[int],
    lag: int = 1,
) -> np.ndarray:
    """Newey--West sandwich covariance, with lags kept inside each segment."""

    bread = np.linalg.inv(jacobian.T @ jacobian)
    meat = np.zeros((jacobian.shape[1], jacobian.shape[1]))
    offset = 0
    for length in segment_lengths:
        segment_jacobian = jacobian[offset : offset + length]
        segment_residual = residual[offset : offset + length]
        score = segment_jacobian * segment_residual[:, None]
        meat += score.T @ score
        for current_lag in range(1, lag + 1):
            weight = 1.0 - current_lag / (lag + 1.0)
            cross = score[current_lag:].T @ score[:-current_lag]
            meat += weight * (cross + cross.T)
        offset += length
    finite_sample = residual.size / (residual.size - jacobian.shape[1])
    return finite_sample * bread @ meat @ bread


def actual_tank_solution(sheets: dict[str, pd.DataFrame], results: Path) -> dict:
    frame = clean_numeric_sheet(sheets["实际储油罐的采集数据"])
    height = frame["显示油高/mm"].to_numpy(float) / 1000.0
    inflow = frame["进油量/L"].to_numpy(float)
    outflow = frame["出油量/L"].fillna(0.0).to_numpy(float)
    refill_positions = np.flatnonzero(inflow > 0.0)
    if refill_positions.size != 1:
        raise RuntimeError("Attachment 2 must contain exactly one refill event")
    refill = int(refill_positions[0])
    first_index = np.arange(1, refill)
    second_index = np.arange(refill + 1, len(frame))
    all_discharge_index = np.concatenate([first_index, second_index])
    tank = ActualTank(cell_width=0.025)

    def residual(
        params: np.ndarray | tuple[float, float],
        index: np.ndarray,
        scenario_tank: ActualTank = tank,
        flow_scale: float = 1.0,
        height_offset_m: float = 0.0,
    ) -> np.ndarray:
        volume = scenario_tank.volume_l(
            height, float(params[0]), float(params[1]), height_offset_m
        )
        measured_net = inflow - flow_scale * outflow
        return volume[index] - volume[index - 1] - measured_net[index]

    pitch_fit = least_squares(
        lambda value: residual((float(value[0]), 0.0), first_index),
        x0=np.array([2.0]),
        bounds=([-10.0], [10.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )
    two_angle_first_fit = least_squares(
        lambda value: residual(value, first_index),
        x0=np.array([2.0, 5.0]),
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )
    candidate_specs = [
        ("upright_baseline", np.array([0.0, 0.0]), 0),
        ("longitudinal_only", np.array([pitch_fit.x[0], 0.0]), 1),
        ("two_angle", two_angle_first_fit.x, 2),
    ]
    candidate_rows: list[dict] = []
    candidate_detail: dict[str, dict] = {}
    for name, params, parameter_count in candidate_specs:
        volume = tank.volume_l(height, float(params[0]), float(params[1]))
        train_metrics = metric(residual(params, first_index))
        time_metrics = metric(residual(params, second_index))
        refill_error = float(
            volume[refill] - volume[refill - 1] - inflow[refill]
        )
        candidate_detail[name] = {
            "alpha_deg": float(params[0]),
            "beta_abs_deg": float(params[1]),
            "parameter_count": parameter_count,
            "selection_fit_first_segment": train_metrics,
            "post_selection_time_ordered_check": time_metrics,
            "post_selection_refill_check_error_l": refill_error,
        }
        candidate_rows.append(
            {
                "candidate": name,
                "parameters": parameter_count,
                "alpha_deg": params[0],
                "beta_abs_deg": params[1],
                "selection_first_segment_rmse_l": train_metrics["rmse_l"],
                "post_selection_time_check_rmse_l": time_metrics["rmse_l"],
                "post_selection_refill_error_l": refill_error,
            }
        )
    candidate_frame = pd.DataFrame(candidate_rows)
    candidate_frame.to_csv(
        results / "<SOURCE_FILE_REDACTED>",
        index=False,
        encoding="utf-8",
        float_format="%.10f",
    )

    final_fit = least_squares(
        lambda value: residual(value, all_discharge_index),
        x0=two_angle_first_fit.x,
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )
    final_params = final_fit.x
    final_residual = residual(final_params, all_discharge_index)
    degrees_freedom = final_residual.size - final_params.size
    iid_variance = float(np.sum(final_residual**2) / degrees_freedom)
    iid_covariance = iid_variance * np.linalg.inv(final_fit.jac.T @ final_fit.jac)
    iid_standard_error = np.sqrt(np.diag(iid_covariance))
    hac_covariance = conditional_hac_covariance(
        final_fit.jac,
        final_residual,
        [len(first_index), len(second_index)],
        lag=1,
    )
    hac_standard_error = np.sqrt(np.diag(hac_covariance))

    first_residual = final_residual[: len(first_index)]
    second_residual = final_residual[len(first_index) :]
    residual_frame = pd.DataFrame(
        {
            "serial": frame.loc[all_discharge_index, "流水号"].to_numpy(int),
            "segment": np.where(
                all_discharge_index < refill, "first_discharge", "second_discharge"
            ),
            "height_prev_mm": frame.loc[
                all_discharge_index - 1, "显示油高/mm"
            ].to_numpy(float),
            "height_mm": frame.loc[
                all_discharge_index, "显示油高/mm"
            ].to_numpy(float),
            "measured_change_l": (inflow - outflow)[all_discharge_index],
            "model_change_l": (inflow - outflow)[all_discharge_index]
            + final_residual,
            "model_residual_l": final_residual,
        }
    )
    residual_frame.to_csv(
        results / "<SOURCE_FILE_REDACTED>",
        index=False,
        encoding="utf-8",
        float_format="%.10f",
    )

    joint_scale_fit = least_squares(
        lambda value: residual(
            (float(value[0]), float(value[1])),
            all_discharge_index,
            flow_scale=float(value[2]),
        ),
        x0=np.array([final_params[0], final_params[1], 1.0]),
        bounds=([-10.0, 0.0, 0.99], [10.0, 20.0, 1.01]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=1000,
    )
    joint_residual = residual(
        joint_scale_fit.x[:2], all_discharge_index, flow_scale=joint_scale_fit.x[2]
    )
    joint_variance = float(
        np.sum(joint_residual**2) / (joint_residual.size - joint_scale_fit.x.size)
    )
    joint_covariance = joint_variance * np.linalg.inv(
        joint_scale_fit.jac.T @ joint_scale_fit.jac
    )
    joint_standard_error = np.sqrt(np.diag(joint_covariance))
    joint_correlation = joint_covariance / np.outer(
        joint_standard_error, joint_standard_error
    )

    segment_positions = [np.arange(0, refill), np.arange(refill, len(frame))]

    def cumulative_residual(params: np.ndarray) -> np.ndarray:
        volume = tank.volume_l(height, float(params[0]), float(params[1]))
        all_residuals: list[np.ndarray] = []
        for positions in segment_positions:
            cumulative = np.concatenate(
                [[0.0], np.cumsum(outflow[positions[1:]])]
            )
            centered = volume[positions] + cumulative
            all_residuals.append(centered - np.mean(centered))
        return np.concatenate(all_residuals)

    cumulative_fit = least_squares(
        cumulative_residual,
        x0=final_params,
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=1000,
    )
    robust_fit = least_squares(
        lambda value: residual(value, all_discharge_index),
        x0=final_params,
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        loss="soft_l1",
        f_scale=2.0,
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=1000,
    )
    second_segment_fit = least_squares(
        lambda value: residual(value, second_index),
        x0=final_params,
        bounds=([-10.0, 0.0], [10.0, 20.0]),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=500,
    )

    calibration_height_cm = np.arange(0, 301, 10, dtype=int)
    calibration_height_m = calibration_height_cm / 100.0
    point_table = tank.volume_l(calibration_height_m, *final_params)
    upright_table = tank.volume_l(calibration_height_m, 0.0, 0.0)

    scenario_specs = [
        ("flow_scale_minus_0.1pct", ActualTank(), 0.999, 0.0, "diagnostic_not_tolerance"),
        ("flow_scale_plus_0.1pct", ActualTank(), 1.001, 0.0, "diagnostic_not_tolerance"),
        ("radius_minus_5mm", ActualTank(cylinder_radius=1.495), 1.0, 0.0, "diagnostic_not_tolerance"),
        ("radius_plus_5mm", ActualTank(cylinder_radius=1.505), 1.0, 0.0, "diagnostic_not_tolerance"),
        ("cylinder_length_minus_10mm", ActualTank(cylinder_length=7.99), 1.0, 0.0, "diagnostic_not_tolerance"),
        ("cylinder_length_plus_10mm", ActualTank(cylinder_length=8.01), 1.0, 0.0, "diagnostic_not_tolerance"),
        ("probe_x_minus_10mm", ActualTank(probe_x=1.99), 1.0, 0.0, "diagnostic_not_tolerance"),
        ("probe_x_plus_10mm", ActualTank(probe_x=2.01), 1.0, 0.0, "diagnostic_not_tolerance"),
        ("calibration_zero_minus_1mm", ActualTank(), 1.0, -0.001, "diagnostic_not_tolerance"),
        ("calibration_zero_plus_1mm", ActualTank(), 1.0, 0.001, "diagnostic_not_tolerance"),
    ]
    scenario_rows: list[dict] = []
    stress_tables: list[np.ndarray] = [point_table]
    scenario_tables: dict[str, np.ndarray] = {"baseline": point_table}
    for name, scenario_tank, flow_scale, height_offset_m, basis_status in scenario_specs:
        scenario_fit = least_squares(
            lambda value: residual(
                value,
                all_discharge_index,
                scenario_tank=scenario_tank,
                flow_scale=flow_scale,
                height_offset_m=height_offset_m,
            ),
            x0=final_params,
            bounds=([-10.0, 0.0], [10.0, 20.0]),
            xtol=1e-12,
            ftol=1e-12,
            gtol=1e-12,
            max_nfev=1000,
        )
        scenario_residual = residual(
            scenario_fit.x,
            all_discharge_index,
            scenario_tank=scenario_tank,
            flow_scale=flow_scale,
            height_offset_m=height_offset_m,
        )
        scenario_table = scenario_tank.volume_l(
            calibration_height_m,
            float(scenario_fit.x[0]),
            float(scenario_fit.x[1]),
            height_offset_m,
        )
        stress_tables.append(scenario_table)
        scenario_tables[name] = scenario_table
        scenario_rows.append(
            {
                "scenario": name,
                "range_basis_status": basis_status,
                "alpha_deg": scenario_fit.x[0],
                "beta_abs_deg": scenario_fit.x[1],
                "increment_rmse_l": np.sqrt(np.mean(scenario_residual**2)),
                "max_table_change_l": np.max(np.abs(scenario_table - point_table)),
            }
        )

    for direction, offset_m in (("minus", -0.001), ("plus", 0.001)):
        name = f"post_calibration_reading_drift_{direction}_1mm"
        drift_table = tank.volume_l(
            calibration_height_m + offset_m, *final_params
        )
        stress_tables.append(drift_table)
        scenario_tables[name] = drift_table
        scenario_rows.append(
            {
                "scenario": name,
                "range_basis_status": "diagnostic_not_tolerance",
                "alpha_deg": final_params[0],
                "beta_abs_deg": final_params[1],
                "increment_rmse_l": np.nan,
                "max_table_change_l": np.max(np.abs(drift_table - point_table)),
            }
        )

    cumulative_table = tank.volume_l(calibration_height_m, *cumulative_fit.x)
    robust_table = tank.volume_l(calibration_height_m, *robust_fit.x)
    first_table = tank.volume_l(calibration_height_m, *two_angle_first_fit.x)
    second_table = tank.volume_l(calibration_height_m, *second_segment_fit.x)
    refined_tank = ActualTank(cell_width=0.0125)
    refined_table = refined_tank.volume_l(calibration_height_m, *final_params)
    analysis_rows = [
        {
            "scenario": "cumulative_state_objective",
            "range_basis_status": "cross_check",
            "alpha_deg": cumulative_fit.x[0],
            "beta_abs_deg": cumulative_fit.x[1],
            "increment_rmse_l": np.sqrt(
                np.mean(residual(cumulative_fit.x, all_discharge_index) ** 2)
            ),
            "max_table_change_l": np.max(np.abs(cumulative_table - point_table)),
        },
        {
            "scenario": "soft_l1_loss",
            "range_basis_status": "cross_check",
            "alpha_deg": robust_fit.x[0],
            "beta_abs_deg": robust_fit.x[1],
            "increment_rmse_l": np.sqrt(
                np.mean(residual(robust_fit.x, all_discharge_index) ** 2)
            ),
            "max_table_change_l": np.max(np.abs(robust_table - point_table)),
        },
        {
            "scenario": "first_segment_fit",
            "range_basis_status": "cross_check",
            "alpha_deg": two_angle_first_fit.x[0],
            "beta_abs_deg": two_angle_first_fit.x[1],
            "increment_rmse_l": np.sqrt(
                np.mean(residual(two_angle_first_fit.x, all_discharge_index) ** 2)
            ),
            "max_table_change_l": np.max(np.abs(first_table - point_table)),
        },
        {
            "scenario": "second_segment_fit",
            "range_basis_status": "cross_check",
            "alpha_deg": second_segment_fit.x[0],
            "beta_abs_deg": second_segment_fit.x[1],
            "increment_rmse_l": np.sqrt(
                np.mean(residual(second_segment_fit.x, all_discharge_index) ** 2)
            ),
            "max_table_change_l": np.max(np.abs(second_table - point_table)),
        },
        {
            "scenario": "quadrature_0.025m_vs_0.0125m",
            "range_basis_status": "numerical_check",
            "alpha_deg": final_params[0],
            "beta_abs_deg": final_params[1],
            "increment_rmse_l": np.nan,
            "max_table_change_l": np.max(np.abs(refined_table - point_table)),
        },
    ]
    sensitivity_frame = pd.DataFrame(scenario_rows + analysis_rows)
    sensitivity_frame.to_csv(
        results / "<SOURCE_FILE_REDACTED>",
        index=False,
        encoding="utf-8",
        float_format="%.10f",
    )

    stress_stack = np.vstack(stress_tables)
    stress_lower = np.min(stress_stack, axis=0)
    stress_upper = np.max(stress_stack, axis=0)
    calibration_table = pd.DataFrame(
        {
            "height_cm": calibration_height_cm,
            "height_mm": calibration_height_cm * 10,
            "volume_l_numeric": point_table,
            "volume_l_reported": np.rint(point_table).astype(int),
            "diagnostic_stress_lower_l_numeric": stress_lower,
            "diagnostic_stress_upper_l_numeric": stress_upper,
            "diagnostic_stress_lower_l_reported": np.floor(stress_lower).astype(int),
            "diagnostic_stress_upper_l_reported": np.ceil(stress_upper).astype(int),
            "upright_volume_l_numeric": upright_table,
            "point_minus_upright_l_numeric": point_table - upright_table,
            "point_minus_upright_l_reported": np.rint(point_table - upright_table).astype(int),
        }
    )
    calibration_table.to_csv(
        results / "<SOURCE_FILE_REDACTED>",
        index=False,
        encoding="utf-8",
        float_format="%.6f",
    )

    volume_final = tank.volume_l(height, *final_params)
    refill_prediction_final = float(volume_final[refill] - volume_final[refill - 1])
    refill_error_final = refill_prediction_final - float(inflow[refill])
    empty_reading, full_reading = tank.empty_full_readings_m(*final_params)
    displayed = frame["显示油量容积/L"].to_numpy(float)
    old_display_residual = tank.volume_l(height, 0.0, 0.0) - displayed

    multistart_rows: list[dict] = []
    for alpha_start, beta_start in [
        (-8.0, 1.0),
        (-4.0, 12.0),
        (0.0, 1.0),
        (2.0, 5.0),
        (6.0, 12.0),
        (9.0, 19.0),
    ]:
        fit = least_squares(
            lambda value: residual(value, all_discharge_index),
            x0=np.array([alpha_start, beta_start]),
            bounds=([-10.0, 0.0], [10.0, 20.0]),
            xtol=1e-11,
            ftol=1e-11,
            gtol=1e-11,
            max_nfev=1000,
        )
        fit_residual = residual(fit.x, all_discharge_index)
        multistart_rows.append(
            {
                "start_alpha_deg": alpha_start,
                "start_beta_deg": beta_start,
                "alpha_deg": fit.x[0],
                "beta_abs_deg": fit.x[1],
                "rmse_l": np.sqrt(np.mean(fit_residual**2)),
                "optimizer_success_status": "pass" if fit.success else "fail",
            }
        )
    multistart_frame = pd.DataFrame(multistart_rows)
    multistart_frame.to_csv(
        results / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8", float_format="%.10f"
    )

    flow_names = ["flow_scale_minus_0.1pct", "flow_scale_plus_0.1pct"]
    radius_names = ["radius_minus_5mm", "radius_plus_5mm"]
    length_names = ["cylinder_length_minus_10mm", "cylinder_length_plus_10mm"]
    scenario_indexed = sensitivity_frame.set_index("scenario")
    return {
        "status": "needs_review",
        "data_roles": {
            "selection_fit": "first_discharge_only",
            "time_ordered_check": "second_discharge",
            "large_change_check": "single_refill",
            "production_refit": "both_discharge_segments",
            "prospectively_untouched_test_status": "needs_review",
            "note": "The revision has seen the audit summary; the two checks are not described as pristine prospective tests.",
        },
        "candidate_comparison": candidate_detail,
        "selection": {
            "status": "pass",
            "selected_candidate": "two_angle",
            "basis": "The task asks for both displacement angles; among geometry candidates, only first-segment fit and applicability are used for selection.",
            "validation_data_used_for_selection_status": "pass",
        },
        "conditional_production_model": {
            "status": "needs_review",
            "alpha_deg": float(final_params[0]),
            "beta_abs_deg": float(final_params[1]),
            "all_discharge_increment": metric(final_residual),
            "conditional_iid_standard_error_deg": iid_standard_error,
            "conditional_iid_interval_status": "needs_review",
            "conditional_hac_lag1_standard_error_deg": hac_standard_error,
            "conditional_hac_lag1_95pct_interval_deg": np.column_stack(
                [final_params - 1.96 * hac_standard_error, final_params + 1.96 * hac_standard_error]
            ),
            "conditional_hac_interval_status": "needs_review",
        },
        "time_ordered_checks": {
            "status": "needs_review",
            "first_fit_second_segment_rmse_l": candidate_detail["two_angle"][
                "post_selection_time_ordered_check"
            ]["rmse_l"],
            "first_fit_refill_error_l": candidate_detail["two_angle"][
                "post_selection_refill_check_error_l"
            ],
            "final_refit_refill_prediction_l": refill_prediction_final,
            "final_refit_refill_error_l": refill_error_final,
        },
        "difference_error_diagnostics": {
            "status": "needs_review",
            "first_segment": residual_diagnostics(first_residual),
            "second_segment": residual_diagnostics(second_residual),
        },
        "cumulative_state_cross_check": {
            "status": "pass",
            "alpha_deg": float(cumulative_fit.x[0]),
            "beta_abs_deg": float(cumulative_fit.x[1]),
            "absolute_state_rmse_l": float(
                np.sqrt(np.mean(cumulative_residual(cumulative_fit.x) ** 2))
            ),
            "increment_rmse_l": float(
                np.sqrt(np.mean(residual(cumulative_fit.x, all_discharge_index) ** 2))
            ),
            "max_table_change_l": float(np.max(np.abs(cumulative_table - point_table))),
        },
        "flow_scale_joint_fit": {
            "status": "needs_review",
            "alpha_deg": float(joint_scale_fit.x[0]),
            "beta_abs_deg": float(joint_scale_fit.x[1]),
            "flow_scale": float(joint_scale_fit.x[2]),
            "linearized_standard_error": joint_standard_error,
            "beta_flow_scale_correlation": float(joint_correlation[1, 2]),
            "note": "No meter accuracy prior is available, so this local fit is a confounding diagnostic, not an engineering confidence interval.",
        },
        "sensitivity": {
            "status": "needs_review",
            "flow_scale_plus_minus_0.1pct_max_table_change_l": float(
                scenario_indexed.loc[flow_names, "max_table_change_l"].max()
            ),
            "radius_plus_minus_5mm_max_table_change_l": float(
                scenario_indexed.loc[radius_names, "max_table_change_l"].max()
            ),
            "cylinder_length_plus_minus_10mm_max_table_change_l": float(
                scenario_indexed.loc[length_names, "max_table_change_l"].max()
            ),
            "post_calibration_height_drift_plus_minus_1mm_max_table_change_l": float(
                scenario_indexed.loc[
                    [
                        "post_calibration_reading_drift_minus_1mm",
                        "post_calibration_reading_drift_plus_1mm",
                    ],
                    "max_table_change_l",
                ].max()
            ),
            "diagnostic_stress_envelope_max_width_l": float(
                np.max(stress_upper - stress_lower)
            ),
            "diagnostic_ranges_are_tolerances_status": "needs_review",
        },
        "flow_scale_profiles": {
            name: {
                "alpha_deg": float(scenario_indexed.loc[name, "alpha_deg"]),
                "beta_abs_deg": float(scenario_indexed.loc[name, "beta_abs_deg"]),
                "increment_rmse_l": float(
                    scenario_indexed.loc[name, "increment_rmse_l"]
                ),
                "max_table_change_l": float(
                    scenario_indexed.loc[name, "max_table_change_l"]
                ),
            }
            for name in flow_names
        },
        "multistart": {
            "status": "pass"
            if (multistart_frame["optimizer_success_status"] == "pass").all()
            else "fail",
            "runs": int(len(multistart_frame)),
            "alpha_spread_deg": float(multistart_frame["alpha_deg"].max() - multistart_frame["alpha_deg"].min()),
            "beta_spread_deg": float(multistart_frame["beta_abs_deg"].max() - multistart_frame["beta_abs_deg"].min()),
            "rmse_spread_l": float(multistart_frame["rmse_l"].max() - multistart_frame["rmse_l"].min()),
        },
        "quadrature_refinement_max_difference_l": float(
            np.max(np.abs(refined_table - point_table))
        ),
        "old_display_matches_upright_geometry": metric(old_display_residual),
        "capacity_l": tank.capacity_l(),
        "operational_zero_height_volume_l": float(point_table[0]),
        "operational_300cm_volume_l": float(point_table[-1]),
        "physical_empty_reading_mm": float(empty_reading * 1000.0),
        "physical_full_reading_mm": float(full_reading * 1000.0),
        "table_rows": int(len(calibration_table)),
        "_plot": {
            "candidate_frame": candidate_frame,
            "residual_frame": residual_frame,
            "calibration_height_cm": calibration_height_cm,
            "point_table_l": point_table,
            "upright_table_l": upright_table,
            "stress_lower_l": stress_lower,
            "stress_upper_l": stress_upper,
            "sensitivity_frame": sensitivity_frame,
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
            "savefig.facecolor": "white",
        }
    )
    metadata = {"Software": "cumcm-a-solve blind-revision"}

    small_plot = small["_plot"]
    fig, ax = plt.subplots()
    ax.fill_between(
        small_plot["table_height_cm"],
        small_plot["envelope_lower_l"],
        small_plot["envelope_upper_l"],
        alpha=0.22,
        color="#e69f00",
        label="diagnostic model envelope",
    )
    ax.plot(
        small_plot["fine_height_cm"],
        small_plot["fine_production_l"],
        color="#0072b2",
        label="pose scale + zero",
    )
    ax.plot(
        small_plot["fine_height_cm"],
        small_plot["fine_upright_l"],
        color="#666666",
        linestyle="--",
        label="upright calibrated geometry",
    )
    ax.scatter(
        small_plot["height_in_cm"],
        small_plot["observed_in_l"],
        s=13,
        color="#009e73",
        alpha=0.6,
        label="tilted-in fit data",
    )
    ax.set_xlabel("Gauge height (cm)")
    ax.set_ylabel("Volume (L)")
    ax.set_title("Small tank calibration with model-form envelope")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200, metadata=metadata)
    plt.close(fig)

    fig, ax = plt.subplots()
    ax.scatter(
        small_plot["height_out_cm"],
        small_plot["baseline_out_residual_l"],
        s=18,
        alpha=0.65,
        label="transferred scale",
    )
    ax.scatter(
        small_plot["height_out_cm"],
        small_plot["production_out_residual_l"],
        s=18,
        alpha=0.65,
        label="pose scale + zero",
    )
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("Gauge height (cm)")
    ax.set_ylabel("Time-check residual (L)")
    ax.set_title("Small tank: held-out tilted-out residual structure")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200, metadata=metadata)
    plt.close(fig)

    fig, ax = plt.subplots()
    ax.plot(
        small_plot["fine_height_cm"],
        small_plot["fine_difference_l"],
        color="#b22222",
    )
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("Gauge height (cm)")
    ax.set_ylabel("Production minus upright volume (L)")
    ax.set_title("Small tank: combined pose/calibration effect")
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200, metadata=metadata)
    plt.close(fig)

    actual_plot = actual["_plot"]
    candidates = actual_plot["candidate_frame"]
    x = np.arange(len(candidates))
    fig, ax = plt.subplots()
    ax.bar(
        x,
        candidates["selection_first_segment_rmse_l"],
        color=["#999999", "#56b4e9", "#0072b2"],
    )
    ax.set_xticks(x, ["upright", "pitch", "pitch+roll"])
    ax.set_ylabel("First-segment RMSE (L)")
    ax.set_title("Candidate comparison on selection data only")
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200, metadata=metadata)
    plt.close(fig)

    residuals = actual_plot["residual_frame"]
    fig, ax = plt.subplots()
    for segment, colour in [
        ("first_discharge", "#0072b2"),
        ("second_discharge", "#e69f00"),
    ]:
        subset = residuals[residuals["segment"] == segment]
        ax.scatter(
            subset["height_mm"],
            subset["model_residual_l"],
            s=11,
            alpha=0.62,
            color=colour,
            label=segment,
        )
    ax.axhline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("Gauge height (mm)")
    ax.set_ylabel("Increment residual (L)")
    ax.set_title("Actual tank: differenced mass-balance residuals")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200, metadata=metadata)
    plt.close(fig)

    sensitivity = actual_plot["sensitivity_frame"]
    sensitivity = sensitivity[
        sensitivity["range_basis_status"] == "diagnostic_not_tolerance"
    ].copy()
    sensitivity = sensitivity.sort_values("max_table_change_l", ascending=True)
    fig, ax = plt.subplots(figsize=(7.4, 5.8))
    ax.barh(
        sensitivity["scenario"],
        sensitivity["max_table_change_l"],
        color="#cc79a7",
    )
    ax.set_xlabel("Maximum table change (L)")
    ax.set_title("Diagnostic stresses (ranges are not certified tolerances)")
    ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200, metadata=metadata)
    plt.close(fig)

    fig, ax = plt.subplots()
    height_cm = actual_plot["calibration_height_cm"]
    ax.fill_between(
        height_cm,
        actual_plot["stress_lower_l"],
        actual_plot["stress_upper_l"],
        alpha=0.20,
        color="#cc79a7",
        label="diagnostic stress envelope",
    )
    ax.plot(height_cm, actual_plot["point_table_l"], label="conditional point table")
    ax.plot(
        height_cm,
        actual_plot["upright_table_l"],
        linestyle="--",
        color="#666666",
        label="upright table",
    )
    ax.set_xlabel("Gauge height (cm)")
    ax.set_ylabel("Volume (L)")
    ax.set_title("Actual tank calibration and diagnostic envelope")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(figures / "<SOURCE_FILE_REDACTED>", dpi=200, metadata=metadata)
    plt.close(fig)


def write_generated_values(results: Path, small: dict, actual: dict) -> None:
    production = actual["conditional_production_model"]
    checks = actual["time_ordered_checks"]
    hac_interval = production["conditional_hac_lag1_95pct_interval_deg"]
    flow_minus = actual["flow_scale_profiles"]["flow_scale_minus_0.1pct"]
    flow_plus = actual["flow_scale_profiles"]["flow_scale_plus_0.1pct"]
    values = {
        "SmallTransferDiameter": f"{2*small['upright_scale_calibration']['transferable_horizontal_semiaxis_m']:.4f}",
        "SmallPoseDiameter": f"{2*small['production_model']['horizontal_semiaxis_m']:.4f}",
        "SmallOffsetMM": f"{small['production_model']['level_offset_mm']:.2f}",
        "SmallTrainRMSE": f"{small['production_model']['training_metrics']['rmse_l']:.2f}",
        "SmallCheckRMSE": f"{small['production_model']['time_ordered_check_metrics']['rmse_l']:.2f}",
        "SmallBaselineCheckRMSE": f"{small['transfer_model_time_check_rmse_l']:.2f}",
        "SmallBaselineCorrelation": f"{small['transfer_model_time_check_residual_height_correlation']:.3f}",
        "SmallProductionCorrelation": f"{small['production_model']['time_check_residual_height_correlation']:.3f}",
        "SmallEnvelope": f"{small['model_envelope']['max_width_l']:.2f}",
        "SmallMaxEffect": f"{small['max_abs_change_from_upright_l']:.2f}",
        "BaselineTrainRMSE": f"{actual['candidate_comparison']['upright_baseline']['selection_fit_first_segment']['rmse_l']:.3f}",
        "PitchTrainRMSE": f"{actual['candidate_comparison']['longitudinal_only']['selection_fit_first_segment']['rmse_l']:.3f}",
        "FullTrainRMSE": f"{actual['candidate_comparison']['two_angle']['selection_fit_first_segment']['rmse_l']:.3f}",
        "ActualAlpha": f"{production['alpha_deg']:.4f}",
        "ActualBeta": f"{production['beta_abs_deg']:.4f}",
        "ActualIncrementRMSE": f"{production['all_discharge_increment']['rmse_l']:.3f}",
        "ActualTimeCheckRMSE": f"{checks['first_fit_second_segment_rmse_l']:.3f}",
        "ActualTimeRefillError": f"{checks['first_fit_refill_error_l']:.2f}",
        "ActualFinalRefillError": f"{checks['final_refit_refill_error_l']:.2f}",
        "ActualHACAlphaLower": f"{hac_interval[0][0]:.4f}",
        "ActualHACAlphaUpper": f"{hac_interval[0][1]:.4f}",
        "ActualHACBetaLower": f"{hac_interval[1][0]:.4f}",
        "ActualHACBetaUpper": f"{hac_interval[1][1]:.4f}",
        "FirstLagOne": f"{actual['difference_error_diagnostics']['first_segment']['lag1_correlation']:.3f}",
        "SecondLagOne": f"{actual['difference_error_diagnostics']['second_segment']['lag1_correlation']:.3f}",
        "CumulativeAlpha": f"{actual['cumulative_state_cross_check']['alpha_deg']:.4f}",
        "CumulativeBeta": f"{actual['cumulative_state_cross_check']['beta_abs_deg']:.4f}",
        "CumulativeTableChange": f"{actual['cumulative_state_cross_check']['max_table_change_l']:.2f}",
        "FlowMinusBeta": f"{flow_minus['beta_abs_deg']:.4f}",
        "FlowPlusBeta": f"{flow_plus['beta_abs_deg']:.4f}",
        "FlowMaxChange": f"{actual['sensitivity']['flow_scale_plus_minus_0.1pct_max_table_change_l']:.2f}",
        "RadiusMaxChange": f"{actual['sensitivity']['radius_plus_minus_5mm_max_table_change_l']:.2f}",
        "LengthMaxChange": f"{actual['sensitivity']['cylinder_length_plus_minus_10mm_max_table_change_l']:.2f}",
        "HeightDriftMaxChange": f"{actual['sensitivity']['post_calibration_height_drift_plus_minus_1mm_max_table_change_l']:.2f}",
        "BetaFlowCorrelation": f"{actual['flow_scale_joint_fit']['beta_flow_scale_correlation']:.3f}",
        "ActualCapacity": f"{actual['capacity_l']:.2f}",
        "ActualEmptyReading": f"{actual['physical_empty_reading_mm']:.1f}",
        "ActualFullReading": f"{actual['physical_full_reading_mm']:.1f}",
        "QuadratureDifference": f"{actual['quadrature_refinement_max_difference_l']:.4f}",
    }
    lines = ["% Generated by code/solve.py; do not edit by hand."]
    lines.extend(f"\\newcommand{{\\{name}}}{{{value}}}" for name, value in values.items())
    (results / "generated_values.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(results / "generated_values.json", values)


def build_data_audit(
    sheets: dict[str, pd.DataFrame], manifest: list[dict]
) -> dict:
    expected_sheets = {
        "实际储油罐的采集数据",
        "无变位进油",
        "无变位出油",
        "倾斜变位进油",
        "倾斜变位出油",
    }
    rows: list[dict] = []
    for item in manifest:
        frame = sheets[item["sheet_name"]]
        cleaned = clean_numeric_sheet(frame)
        height_column = "油位高度/mm" if "油位高度/mm" in cleaned else "显示油高/mm"
        rows.append(
            {
                "sheet": item["sheet_name"],
                "data_rows_raw": int(len(frame)),
                "data_rows_clean": int(len(cleaned)),
                "empty_tail_rows_removed": int(len(frame) - len(cleaned)),
                "duplicate_serials": int(cleaned["流水号"].duplicated().sum()),
                "height_min_mm": float(cleaned[height_column].min()),
                "height_max_mm": float(cleaned[height_column].max()),
            }
        )
    actual = clean_numeric_sheet(sheets["实际储油罐的采集数据"])
    missing_outflow = int(actual["出油量/L"].isna().sum())
    refill_rows = int((actual["进油量/L"].fillna(0.0) > 0.0).sum())
    structural_pass = (
        set(sheets) == expected_sheets
        and all(row["duplicate_serials"] == 0 for row in rows)
        and missing_outflow == 1
        and refill_rows == 1
    )
    return {
        "status": "needs_review" if structural_pass else "fail",
        "structural_checks": {
            "status": "pass" if structural_pass else "fail",
            "sheets": rows,
            "actual_tank_missing_outflow_rows": missing_outflow,
            "actual_tank_refill_rows": refill_rows,
        },
        "known_modeling_issues": [
            {
                "status": "needs_review",
                "item": "The drawing width and upright small-tank flow imply different volume scales.",
            },
            {
                "status": "needs_review",
                "item": "Tilted small-tank data require a pose-dependent scale/zero candidate to remove structured residuals.",
            },
            {
                "status": "needs_review",
                "item": "No manufacturer tolerances are supplied for meter scale, probe zero, or actual-tank dimensions.",
            },
        ],
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    results = root / "results"
    figures = root / "figures"
    results.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    sheets, manifest = load_sheets(root)
    data_audit = build_data_audit(sheets, manifest)
    write_json(results / "data_audit.json", data_audit)

    small = small_tank_solution(sheets, results)
    actual = actual_tank_solution(sheets, results)
    make_figures(small, actual, figures)
    small_summary = {key: value for key, value in small.items() if key != "_plot"}
    actual_summary = {key: value for key, value in actual.items() if key != "_plot"}
    summary = {
        "schema_version": 2,
        "case_id": "2010A",
        "phase": "blind-revision",
        "status": "needs_review",
        "seed": SEED,
        "small_tank": small_summary,
        "actual_tank": actual_summary,
    }
    write_json(results / "summary.json", summary)
    write_json(
        results / "parameters.json",
        {
            "status": "needs_review",
            "small_tank": small_summary["production_model"],
            "actual_tank": actual_summary["conditional_production_model"],
            "actual_flow_scale_joint_fit": actual_summary["flow_scale_joint_fit"],
        },
    )
    write_generated_values(results, small_summary, actual_summary)

    input_hashes = {
        str(path.relative_to(root)).replace("\\", "/"): sha256(path)
        for path in sorted((root / "input").rglob("*"))
        if path.is_file()
    }
    metadata = {
        "status": "pass",
        "seed": SEED,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "matplotlib": matplotlib.__version__,
        "input_sha256": input_hashes,
        "commands": {
            "full_pipeline": "pwsh -NoProfile -File code/run_all.ps1",
            "without_pdf": "pwsh -NoProfile -File code/run_all.ps1 -SkipPdf",
        },
    }
    write_json(results / "run_metadata.json", metadata)
    print(
        "[PASS] generated blind revision: "
        f"small b={small_summary['production_model']['horizontal_semiaxis_m']:.6f} m, "
        f"alpha={actual_summary['conditional_production_model']['alpha_deg']:.4f} deg, "
        f"beta={actual_summary['conditional_production_model']['beta_abs_deg']:.4f} deg"
    )


if __name__ == "__main__":
    main()
