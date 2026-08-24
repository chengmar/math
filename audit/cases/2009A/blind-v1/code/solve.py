from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import platform
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SEED = 2009
G = 9.8
WHEEL_RADIUS_M = 0.286
WHEEL_LOAD_N = 6230.0
STEEL_DENSITY_KG_M3 = 7810.0
OUTER_DIAMETER_M = 1.0
INNER_DIAMETER_M = 0.2
THICKNESSES_M = np.array([0.0392, 0.0784, 0.1568], dtype=float)
BASE_INERTIA_KGM2 = 10.0
CURRENT_PER_TORQUE_A_PER_NM = 1.5
COMPENSATION_RANGE_KGM2 = (-30.0, 30.0)

Q4_EQUIVALENT_INERTIA = 48.0
Q4_MECHANICAL_INERTIA = 35.0
Q4_INITIAL_RPM = 514.0
Q4_FINAL_RPM = 257.0


def rpm_to_rad_s(value: np.ndarray | float) -> np.ndarray | float:
    return np.asarray(value) * (2.0 * math.pi / 60.0)


def to_builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return [to_builtin(v) for v in value.tolist()]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, Path):
        return value.as_posix()
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(to_builtin(data), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{k: to_builtin(row[k]) for k in fieldnames} for row in rows])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_observations(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows: list[tuple[float, float, float]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"time_s", "brake_torque_Nm", "speed_rpm"}
        if set(reader.fieldnames or []) != required:
            raise ValueError(f"Unexpected observation columns: {reader.fieldnames}")
        for row in reader:
            rows.append(
                (
                    float(row["time_s"]),
                    float(row["brake_torque_Nm"]),
                    float(row["speed_rpm"]),
                )
            )
    data = np.asarray(rows, dtype=float)
    if data.shape[0] < 2 or data.shape[1] != 3:
        raise ValueError(f"Unexpected observation shape: {data.shape}")
    return data[:, 0], data[:, 1], data[:, 2]


def annular_flywheel_inertia(thickness_m: np.ndarray) -> np.ndarray:
    outer_radius = OUTER_DIAMETER_M / 2.0
    inner_radius = INNER_DIAMETER_M / 2.0
    volume = math.pi * (outer_radius**2 - inner_radius**2) * thickness_m
    mass = STEEL_DENSITY_KG_M3 * volume
    return 0.5 * mass * (outer_radius**2 + inner_radius**2)


def interval_energy(t: np.ndarray, torque: np.ndarray, omega: np.ndarray) -> dict[str, float]:
    dt = np.diff(t)
    power = torque * omega
    trapezoid_power = float(np.sum(0.5 * (power[:-1] + power[1:]) * dt))
    left = float(np.sum(power[:-1] * dt))
    right = float(np.sum(power[1:] * dt))
    midpoint_product = float(
        np.sum(0.5 * (torque[:-1] + torque[1:]) * 0.5 * (omega[:-1] + omega[1:]) * dt)
    )
    return {
        "trapezoid_power_J": trapezoid_power,
        "left_rectangle_J": left,
        "right_rectangle_J": right,
        "midpoint_product_J": midpoint_product,
        "quadrature_span_J": max(left, right, midpoint_product, trapezoid_power)
        - min(left, right, midpoint_product, trapezoid_power),
    }


def simulate_controller(
    t: np.ndarray,
    true_torque_samples: np.ndarray,
    controller: str,
    *,
    sensed_torque_samples: np.ndarray | None = None,
    equivalent_inertia: float = Q4_EQUIVALENT_INERTIA,
    design_equivalent_inertia: float = Q4_EQUIVALENT_INERTIA,
    mechanical_inertia: float = Q4_MECHANICAL_INERTIA,
    design_mechanical_inertia: float = Q4_MECHANICAL_INERTIA,
    initial_rpm: float = Q4_INITIAL_RPM,
    feedback_fraction: float = 1.0,
    compensation_limit: float = 30.0,
) -> dict[str, Any]:
    if sensed_torque_samples is None:
        sensed_torque_samples = true_torque_samples
    if len(t) != len(true_torque_samples) or len(t) != len(sensed_torque_samples):
        raise ValueError("Time and torque arrays must have equal length.")
    if np.any(np.diff(t) <= 0):
        raise ValueError("Time must be strictly increasing.")

    n = len(t)
    omega_ref = np.empty(n, dtype=float)
    omega_internal_ref = np.empty(n, dtype=float)
    omega_bench = np.empty(n, dtype=float)
    motor_torque = np.empty(n - 1, dtype=float)
    predicted_torque = np.empty(n - 1, dtype=float)
    clipped = np.zeros(n - 1, dtype=bool)
    omega_ref[0] = float(rpm_to_rad_s(initial_rpm))
    omega_internal_ref[0] = omega_ref[0]
    omega_bench[0] = omega_ref[0]
    delta_j_design = design_equivalent_inertia - design_mechanical_inertia
    gain = delta_j_design / design_equivalent_inertia

    for k in range(n - 1):
        h = t[k + 1] - t[k]
        actual_mid_torque = 0.5 * (true_torque_samples[k] + true_torque_samples[k + 1])
        sensed_mid_torque = 0.5 * (sensed_torque_samples[k] + sensed_torque_samples[k + 1])
        omega_ref[k + 1] = omega_ref[k] - h * actual_mid_torque / equivalent_inertia

        if controller == "motor_off":
            estimate = 0.0
            raw_motor_torque = 0.0
        elif controller == "speed_difference":
            estimate = float(sensed_torque_samples[k])
            if k == 0:
                raw_motor_torque = gain * estimate
            else:
                previous_h = t[k] - t[k - 1]
                observed_deceleration = (omega_bench[k] - omega_bench[k - 1]) / previous_h
                raw_motor_torque = -delta_j_design * observed_deceleration
        elif controller == "torque_zoh":
            estimate = float(sensed_torque_samples[k])
            raw_motor_torque = gain * estimate
        elif controller == "predictive_feedback":
            if k == 0:
                estimate = float(sensed_torque_samples[k])
            else:
                previous_h = t[k] - t[k - 1]
                estimate = float(
                    sensed_torque_samples[k]
                    + 0.5
                    * h
                    / previous_h
                    * (sensed_torque_samples[k] - sensed_torque_samples[k - 1])
                )
            speed_error = omega_bench[k] - omega_internal_ref[k]
            raw_motor_torque = (
                gain * estimate
                - feedback_fraction * design_mechanical_inertia * speed_error / h
            )
        else:
            raise ValueError(f"Unknown controller: {controller}")

        if controller == "motor_off":
            bounded_motor_torque = raw_motor_torque
        else:
            torque_scale = max(abs(estimate), 1.0)
            torque_limit = compensation_limit * torque_scale / design_equivalent_inertia
            bounded_motor_torque = float(np.clip(raw_motor_torque, -torque_limit, torque_limit))
            clipped[k] = not math.isclose(
                bounded_motor_torque, raw_motor_torque, rel_tol=0.0, abs_tol=1e-12
            )
        predicted_torque[k] = estimate
        motor_torque[k] = bounded_motor_torque
        omega_bench[k + 1] = (
            omega_bench[k]
            + h * (bounded_motor_torque - actual_mid_torque) / mechanical_inertia
        )
        omega_internal_ref[k + 1] = (
            omega_internal_ref[k] - h * sensed_mid_torque / design_equivalent_inertia
        )

    dt = np.diff(t)
    actual_mid_torque = 0.5 * (true_torque_samples[:-1] + true_torque_samples[1:])
    ref_mid_speed = 0.5 * (omega_ref[:-1] + omega_ref[1:])
    bench_mid_speed = 0.5 * (omega_bench[:-1] + omega_bench[1:])
    road_brake_energy = float(np.sum(actual_mid_torque * ref_mid_speed * dt))
    bench_brake_energy = float(np.sum(actual_mid_torque * bench_mid_speed * dt))
    motor_energy = float(np.sum(motor_torque * bench_mid_speed * dt))
    speed_error_rpm = (omega_bench - omega_ref) * 60.0 / (2.0 * math.pi)
    current_a = CURRENT_PER_TORQUE_A_PER_NM * motor_torque
    relative_energy_error = (bench_brake_energy - road_brake_energy) / road_brake_energy
    mask = np.abs(predicted_torque) > 1e-9
    equivalent_command = np.full_like(motor_torque, np.nan)
    equivalent_command[mask] = (
        design_equivalent_inertia * motor_torque[mask] / predicted_torque[mask]
    )

    return {
        "controller": controller,
        "omega_reference_rad_s": omega_ref,
        "omega_internal_reference_rad_s": omega_internal_ref,
        "omega_bench_rad_s": omega_bench,
        "speed_error_rpm": speed_error_rpm,
        "motor_torque_Nm": motor_torque,
        "current_A": current_a,
        "predicted_torque_Nm": predicted_torque,
        "equivalent_command_kgm2": equivalent_command,
        "metrics": {
            "reference_terminal_rpm": omega_ref[-1] * 60.0 / (2.0 * math.pi),
            "bench_terminal_rpm": omega_bench[-1] * 60.0 / (2.0 * math.pi),
            "terminal_speed_error_rpm": speed_error_rpm[-1],
            "rmse_speed_error_rpm": float(np.sqrt(np.mean(speed_error_rpm**2))),
            "max_abs_speed_error_rpm": float(np.max(np.abs(speed_error_rpm))),
            "road_brake_energy_J": road_brake_energy,
            "bench_brake_energy_J": bench_brake_energy,
            "signed_energy_error_J": bench_brake_energy - road_brake_energy,
            "absolute_relative_energy_error_pct": 100.0 * abs(relative_energy_error),
            "motor_energy_J": motor_energy,
            "max_abs_current_A": float(np.max(np.abs(current_a))),
            "rms_current_A": float(np.sqrt(np.mean(current_a**2))),
            "clipped_intervals": int(np.count_nonzero(clipped)),
            "equivalent_command_min_kgm2": float(np.nanmin(equivalent_command))
            if np.any(mask)
            else 0.0,
            "equivalent_command_max_kgm2": float(np.nanmax(equivalent_command))
            if np.any(mask)
            else 0.0,
        },
    }


def resample_trace(
    t: np.ndarray, torque: np.ndarray, nominal_step: float
) -> tuple[np.ndarray, np.ndarray]:
    points = list(np.arange(t[0], t[-1], nominal_step, dtype=float))
    if not points or not math.isclose(points[-1], t[-1], abs_tol=1e-12):
        points.append(float(t[-1]))
    new_t = np.asarray(points, dtype=float)
    return new_t, np.interp(new_t, t, torque)


def fmt(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}"


def save_figures(
    figures_dir: Path,
    flywheel_j: np.ndarray,
    combo_rows: list[dict[str, Any]],
    q1_equivalent_j: float,
    t: np.ndarray,
    torque: np.ndarray,
    rpm: np.ndarray,
    q4: dict[str, Any],
    simulations: dict[str, dict[str, Any]],
    sensitivity_rows: list[dict[str, Any]],
) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.3), constrained_layout=True)
    axes[0].bar(["Flywheel 1", "Flywheel 2", "Flywheel 3"], flywheel_j, color="#4472C4")
    axes[0].set_ylabel(r"Inertia (kg m$^2$)")
    axes[0].set_title("Individual flywheel inertias")
    mechanical = np.asarray([row["mechanical_inertia_kgm2"] for row in combo_rows])
    axes[1].stem(mechanical, np.ones_like(mechanical), basefmt=" ", linefmt="#70AD47", markerfmt="o")
    axes[1].axvline(q1_equivalent_j, color="#C00000", linestyle="--", label="Equivalent inertia")
    axes[1].axvspan(
        q1_equivalent_j + COMPENSATION_RANGE_KGM2[0],
        q1_equivalent_j + COMPENSATION_RANGE_KGM2[1],
        color="#FFC000",
        alpha=0.18,
        label="Feasible with motor",
    )
    axes[1].set_yticks([])
    axes[1].set_xlabel(r"Mechanical inertia (kg m$^2$)")
    axes[1].set_title("Eight mechanical combinations")
    axes[1].legend(fontsize=8)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    omega = rpm_to_rad_s(rpm)
    dt = np.diff(t)
    cumulative_test = np.concatenate(
        ([0.0], np.cumsum(0.5 * (torque[:-1] * omega[:-1] + torque[1:] * omega[1:]) * dt))
    )
    cumulative_equivalent = 0.5 * Q4_EQUIVALENT_INERTIA * (omega[0] ** 2 - omega**2)
    fig, axes = plt.subplots(2, 1, figsize=(10.5, 7.0), sharex=True, constrained_layout=True)
    ax2 = axes[0].twinx()
    axes[0].plot(t, rpm, color="#4472C4", label="Measured speed")
    ax2.plot(t, torque, color="#ED7D31", alpha=0.85, label="Brake torque")
    axes[0].set_ylabel("Speed (rpm)")
    ax2.set_ylabel("Torque (N m)")
    axes[0].set_title("Observed test-bench trace")
    lines = axes[0].lines + ax2.lines
    axes[0].legend(lines, [line.get_label() for line in lines], loc="upper right")
    axes[1].plot(t, cumulative_test / 1000.0, label="Bench brake work", color="#70AD47")
    axes[1].plot(
        t,
        cumulative_equivalent / 1000.0,
        label="48 kg m$^2$ equivalent energy",
        color="#C00000",
        linestyle="--",
    )
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Cumulative energy (kJ)")
    axes[1].legend()
    axes[1].set_title(
        "Terminal relative energy error = "
        + fmt(q4["absolute_relative_energy_error_pct"], 4)
        + "%"
    )
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    names = ["motor_off", "speed_difference", "torque_zoh", "predictive_feedback"]
    labels = {
        "motor_off": "Motor off (baseline)",
        "speed_difference": "Speed-difference",
        "torque_zoh": "Torque ZOH (Q5)",
        "predictive_feedback": "Predictive feedback (Q6)",
    }
    colors = ["#7F7F7F", "#A5A5A5", "#4472C4", "#C00000"]
    fig, axes = plt.subplots(3, 1, figsize=(10.5, 9.0), sharex=True, constrained_layout=True)
    for name, color in zip(names, colors):
        axes[0].plot(t, simulations[name]["speed_error_rpm"], label=labels[name], color=color)
    for name, color in zip(names[1:], colors[1:]):
        axes[1].plot(t, simulations[name]["speed_error_rpm"], label=labels[name], color=color)
        axes[2].step(
            t[:-1], simulations[name]["current_A"], where="post", label=labels[name], color=color
        )
    axes[0].axhline(0.0, color="black", linewidth=0.7)
    axes[0].set_ylabel("Speed error (rpm)")
    axes[0].set_title("Offline replay: baseline and controlled cases")
    axes[0].legend(fontsize=8)
    axes[1].legend(fontsize=8)
    axes[1].axhline(0.0, color="black", linewidth=0.7)
    axes[1].set_ylabel("Zoomed error (rpm)")
    axes[1].set_title("Controlled cases only")
    axes[2].set_xlabel("Time (s)")
    axes[2].set_ylabel("Motor current (A)")
    axes[2].legend(fontsize=8)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    step_rows = [row for row in sensitivity_rows if row["scenario"] == "step_size"]
    fig, ax = plt.subplots(figsize=(8.5, 4.8), constrained_layout=True)
    for name, marker, color in [
        ("torque_zoh", "o", "#4472C4"),
        ("predictive_feedback", "s", "#C00000"),
    ]:
        subset = sorted(
            [row for row in step_rows if row["controller"] == name],
            key=lambda row: row["value"],
        )
        ax.plot(
            [1000.0 * row["value"] for row in subset],
            [row["absolute_relative_energy_error_pct"] for row in subset],
            marker=marker,
            color=color,
            label=labels[name],
        )
    ax.set_xlabel("Control interval (ms)")
    ax.set_ylabel("Absolute relative energy error (%)")
    ax.set_yscale("log")
    ax.set_title("Step-size sensitivity")
    ax.legend()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Blind solution for CUMCM 2009 A.")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results_dir = workspace / "results"
    figures_dir = workspace / "figures"
    paper_dir = workspace / "paper"
    results_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    paper_dir.mkdir(parents=True, exist_ok=True)
    np.random.seed(SEED)

    observation_path = results_dir / "<SOURCE_FILE_REDACTED>"
    t, torque, rpm = load_observations(observation_path)
    dt = np.diff(t)
    if not np.all(np.isfinite(np.column_stack((t, torque, rpm)))):
        raise ValueError("Non-finite values found in observations.")
    if np.any(dt <= 0):
        raise ValueError("Observation times are not strictly increasing.")

    q1_equivalent_j = WHEEL_LOAD_N * WHEEL_RADIUS_M**2 / G
    flywheel_j = annular_flywheel_inertia(THICKNESSES_M)
    combo_rows: list[dict[str, Any]] = []
    for bits in itertools.product((0, 1), repeat=len(flywheel_j)):
        flywheel_sum = float(np.dot(np.asarray(bits), flywheel_j))
        mechanical = BASE_INERTIA_KGM2 + flywheel_sum
        required = q1_equivalent_j - mechanical
        combo_rows.append(
            {
                "flywheel_1": bits[0],
                "flywheel_2": bits[1],
                "flywheel_3": bits[2],
                "flywheel_inertia_sum_kgm2": flywheel_sum,
                "mechanical_inertia_kgm2": mechanical,
                "required_compensation_kgm2": required,
                "feasible_with_motor": COMPENSATION_RANGE_KGM2[0] - 1e-12
                <= required
                <= COMPENSATION_RANGE_KGM2[1] + 1e-12,
            }
        )
    combo_rows.sort(key=lambda row: row["mechanical_inertia_kgm2"])
    feasible = [row for row in combo_rows if row["feasible_with_motor"]]
    selected_combo = min(
        feasible,
        key=lambda row: (
            abs(row["required_compensation_kgm2"]),
            row["mechanical_inertia_kgm2"],
        ),
    )

    initial_linear_speed = 50.0 / 3.6
    initial_omega = initial_linear_speed / WHEEL_RADIUS_M
    angular_acceleration = -initial_omega / 5.0
    motor_torque_q3 = -selected_combo["required_compensation_kgm2"] * angular_acceleration
    current_q3 = CURRENT_PER_TORQUE_A_PER_NM * motor_torque_q3
    brake_torque_q3 = -q1_equivalent_j * angular_acceleration

    omega = rpm_to_rad_s(rpm)
    energy = interval_energy(t, torque, omega)
    nominal_initial_omega = float(rpm_to_rad_s(Q4_INITIAL_RPM))
    nominal_final_omega = float(rpm_to_rad_s(Q4_FINAL_RPM))
    nominal_road_energy = 0.5 * Q4_EQUIVALENT_INERTIA * (
        nominal_initial_omega**2 - nominal_final_omega**2
    )
    observed_endpoint_road_energy = 0.5 * Q4_EQUIVALENT_INERTIA * (
        omega[0] ** 2 - omega[-1] ** 2
    )
    test_energy = energy["trapezoid_power_J"]
    signed_error = test_energy - nominal_road_energy
    integration_values = [
        energy["trapezoid_power_J"],
        energy["left_rectangle_J"],
        energy["right_rectangle_J"],
        energy["midpoint_product_J"],
    ]
    integration_relative_errors = [
        100.0 * abs(value - nominal_road_energy) / nominal_road_energy
        for value in integration_values
    ]
    effective_inertia_observed = 2.0 * test_energy / (omega[0] ** 2 - omega[-1] ** 2)

    m_mid = 0.5 * (torque[:-1] + torque[1:])
    omega_mid = 0.5 * (omega[:-1] + omega[1:])
    inferred_motor_torque = m_mid + Q4_MECHANICAL_INERTIA * np.diff(omega) / dt
    inferred_motor_energy = float(np.sum(inferred_motor_torque * omega_mid * dt))
    mechanical_energy_drop = 0.5 * Q4_MECHANICAL_INERTIA * (omega[0] ** 2 - omega[-1] ** 2)
    balance_residual = energy["midpoint_product_J"] - inferred_motor_energy - mechanical_energy_drop
    inferred_compensation_inertia = 2.0 * inferred_motor_energy / (omega[0] ** 2 - omega[-1] ** 2)

    q4 = {
        "sample_count": int(len(t)),
        "interval_count": int(len(dt)),
        "duration_s": float(t[-1] - t[0]),
        "median_step_s": float(np.median(dt)),
        "min_step_s": float(np.min(dt)),
        "max_step_s": float(np.max(dt)),
        "missing_numeric_count": 0,
        "duplicate_time_count": int(len(t) - len(np.unique(t))),
        "positive_speed_increment_count": int(np.count_nonzero(np.diff(rpm) > 1e-12)),
        "flat_speed_increment_count": int(np.count_nonzero(np.abs(np.diff(rpm)) <= 1e-12)),
        "torque_min_Nm": float(np.min(torque)),
        "torque_max_Nm": float(np.max(torque)),
        "observed_initial_rpm": float(rpm[0]),
        "observed_final_rpm": float(rpm[-1]),
        "nominal_initial_rpm": Q4_INITIAL_RPM,
        "nominal_final_rpm": Q4_FINAL_RPM,
        "initial_endpoint_error_rpm": float(rpm[0] - Q4_INITIAL_RPM),
        "final_endpoint_error_rpm": float(rpm[-1] - Q4_FINAL_RPM),
        "road_brake_energy_nominal_J": nominal_road_energy,
        "road_brake_energy_observed_endpoints_J": observed_endpoint_road_energy,
        "test_bench_brake_energy_J": test_energy,
        "signed_energy_error_test_minus_road_J": signed_error,
        "absolute_energy_error_J": abs(signed_error),
        "absolute_relative_energy_error_pct": 100.0 * abs(signed_error) / nominal_road_energy,
        "signed_relative_energy_error_pct": 100.0 * signed_error / nominal_road_energy,
        "effective_inertia_from_observed_endpoints_kgm2": effective_inertia_observed,
        "effective_inertia_error_kgm2": effective_inertia_observed - Q4_EQUIVALENT_INERTIA,
        "inferred_motor_energy_J": inferred_motor_energy,
        "inferred_compensation_inertia_kgm2": inferred_compensation_inertia,
        "inferred_current_min_A": float(CURRENT_PER_TORQUE_A_PER_NM * np.min(inferred_motor_torque)),
        "inferred_current_max_A": float(CURRENT_PER_TORQUE_A_PER_NM * np.max(inferred_motor_torque)),
        "dynamic_energy_balance_residual_J": balance_residual,
        "quadrature_span_J": energy["quadrature_span_J"],
        "quadrature_relative_span_pct": 100.0 * energy["quadrature_span_J"] / nominal_road_energy,
        "integration_estimates_J": energy,
        "integration_absolute_relative_error_min_pct": min(integration_relative_errors),
        "integration_absolute_relative_error_max_pct": max(integration_relative_errors),
        "assessment": "needs_review",
        "assessment_rule": "No acceptance tolerance is supplied; report the numerical deficit and leave acceptance to engineering review.",
    }

    simulations = {
        name: simulate_controller(t, torque, name)
        for name in ["motor_off", "speed_difference", "torque_zoh", "predictive_feedback"]
    }
    comparison_rows = []
    for name, result in simulations.items():
        comparison_rows.append({"controller": name, **result["metrics"]})

    sensitivity_rows: list[dict[str, Any]] = []
    for step in [0.005, 0.01, 0.02, 0.05]:
        rt, rm = resample_trace(t, torque, step)
        for controller in ["torque_zoh", "predictive_feedback"]:
            metrics = simulate_controller(rt, rm, controller)["metrics"]
            sensitivity_rows.append(
                {
                    "scenario": "step_size",
                    "value": step,
                    "controller": controller,
                    "terminal_speed_error_rpm": metrics["terminal_speed_error_rpm"],
                    "max_abs_speed_error_rpm": metrics["max_abs_speed_error_rpm"],
                    "absolute_relative_energy_error_pct": metrics[
                        "absolute_relative_energy_error_pct"
                    ],
                    "max_abs_current_A": metrics["max_abs_current_A"],
                    "clipped_intervals": metrics["clipped_intervals"],
                }
            )
    for scale in [0.95, 1.0, 1.05]:
        for controller in ["torque_zoh", "predictive_feedback"]:
            metrics = simulate_controller(
                t,
                torque,
                controller,
                mechanical_inertia=Q4_MECHANICAL_INERTIA * scale,
            )["metrics"]
            sensitivity_rows.append(
                {
                    "scenario": "mechanical_inertia_scale",
                    "value": scale,
                    "controller": controller,
                    "terminal_speed_error_rpm": metrics["terminal_speed_error_rpm"],
                    "max_abs_speed_error_rpm": metrics["max_abs_speed_error_rpm"],
                    "absolute_relative_energy_error_pct": metrics[
                        "absolute_relative_energy_error_pct"
                    ],
                    "max_abs_current_A": metrics["max_abs_current_A"],
                    "clipped_intervals": metrics["clipped_intervals"],
                }
            )
    for scale in [0.95, 1.0, 1.05]:
        for controller in ["torque_zoh", "predictive_feedback"]:
            metrics = simulate_controller(
                t,
                torque,
                controller,
                sensed_torque_samples=torque * scale,
            )["metrics"]
            sensitivity_rows.append(
                {
                    "scenario": "torque_sensor_scale",
                    "value": scale,
                    "controller": controller,
                    "terminal_speed_error_rpm": metrics["terminal_speed_error_rpm"],
                    "max_abs_speed_error_rpm": metrics["max_abs_speed_error_rpm"],
                    "absolute_relative_energy_error_pct": metrics[
                        "absolute_relative_energy_error_pct"
                    ],
                    "max_abs_current_A": metrics["max_abs_current_A"],
                    "clipped_intervals": metrics["clipped_intervals"],
                }
            )

    audit = {
        "status": "pass"
        if q4["missing_numeric_count"] == 0
        and q4["duplicate_time_count"] == 0
        and math.isclose(q4["median_step_s"], 0.01, abs_tol=1e-12)
        else "fail",
        "observations": q4,
        "facts": [
            "The first worksheet contains one contiguous numeric block followed by formatted blank rows.",
            "All retained time, torque, and speed entries are finite.",
            "Time is strictly increasing with a nominal 10 ms interval.",
        ],
        "engineering_judgments": [
            "Small upward or flat speed steps are retained because they are plausible quantization/control effects, not proven data errors.",
            "Trapezoidal integration of measured power is the primary energy estimate; alternative quadratures are reported as sensitivity.",
        ],
    }

    metrics = {
        "status": "pass",
        "seed": SEED,
        "question_1": {
            "equivalent_inertia_kgm2": q1_equivalent_j,
            "gravity_m_s2": G,
        },
        "question_2": {
            "flywheel_inertias_kgm2": flywheel_j,
            "mechanical_inertias_kgm2": [
                row["mechanical_inertia_kgm2"] for row in combo_rows
            ],
            "feasible_compensations": feasible,
            "selected": selected_combo,
        },
        "question_3": {
            "initial_speed_kmh": 50.0,
            "stop_time_s": 5.0,
            "initial_angular_speed_rad_s": initial_omega,
            "angular_acceleration_rad_s2": angular_acceleration,
            "brake_torque_Nm": brake_torque_q3,
            "motor_torque_Nm": motor_torque_q3,
            "drive_current_A": current_q3,
        },
        "question_4": q4,
        "question_5": simulations["torque_zoh"]["metrics"],
        "question_6": simulations["predictive_feedback"]["metrics"],
        "controller_comparison": {row["controller"]: row for row in comparison_rows},
        "data_audit": audit,
    }
    write_json(results_dir / "metrics.json", metrics)
    write_json(results_dir / "data-audit.json", audit)
    write_csv(
        results_dir / "<SOURCE_FILE_REDACTED>",
        list(combo_rows[0].keys()),
        combo_rows,
    )
    write_csv(
        results_dir / "<SOURCE_FILE_REDACTED>",
        list(comparison_rows[0].keys()),
        comparison_rows,
    )
    write_csv(
        results_dir / "<SOURCE_FILE_REDACTED>",
        list(sensitivity_rows[0].keys()),
        sensitivity_rows,
    )

    timeseries_rows: list[dict[str, Any]] = []
    for k in range(len(t)):
        row: dict[str, Any] = {
            "time_s": t[k],
            "observed_torque_Nm": torque[k],
            "observed_speed_rpm": rpm[k],
        }
        for name, result in simulations.items():
            row[f"{name}_reference_rpm"] = (
                result["omega_reference_rad_s"][k] * 60.0 / (2.0 * math.pi)
            )
            row[f"{name}_bench_rpm"] = (
                result["omega_bench_rad_s"][k] * 60.0 / (2.0 * math.pi)
            )
            row[f"{name}_error_rpm"] = result["speed_error_rpm"][k]
            row[f"{name}_current_A"] = result["current_A"][k] if k < len(t) - 1 else ""
        timeseries_rows.append(row)
    write_csv(results_dir / "<SOURCE_FILE_REDACTED>", list(timeseries_rows[0].keys()), timeseries_rows)

    sensitivity_lookup = {
        (row["scenario"], float(row["value"]), row["controller"]): row
        for row in sensitivity_rows
    }
    q5_step_50 = sensitivity_lookup[("step_size", 0.05, "torque_zoh")]
    q6_step_50 = sensitivity_lookup[("step_size", 0.05, "predictive_feedback")]
    q5_jm_worst = max(
        sensitivity_lookup[("mechanical_inertia_scale", scale, "torque_zoh")][
            "absolute_relative_energy_error_pct"
        ]
        for scale in (0.95, 1.05)
    )
    q6_jm_worst = max(
        sensitivity_lookup[("mechanical_inertia_scale", scale, "predictive_feedback")][
            "absolute_relative_energy_error_pct"
        ]
        for scale in (0.95, 1.05)
    )
    q6_sensor_worst = max(
        sensitivity_lookup[("torque_sensor_scale", scale, "predictive_feedback")][
            "absolute_relative_energy_error_pct"
        ]
        for scale in (0.95, 1.05)
    )

    tex_macros = "\n".join(
        [
            "% Generated by code/solve.py; do not edit numeric values by hand.",
            rf"\newcommand{{\QOneJ}}{{{q1_equivalent_j:.3f}}}",
            rf"\newcommand{{\FlywheelOneJ}}{{{flywheel_j[0]:.3f}}}",
            rf"\newcommand{{\FlywheelTwoJ}}{{{flywheel_j[1]:.3f}}}",
            rf"\newcommand{{\FlywheelThreeJ}}{{{flywheel_j[2]:.3f}}}",
            rf"\newcommand{{\QTwoMechanicalList}}{{{', '.join(f'{row['mechanical_inertia_kgm2']:.3f}' for row in combo_rows)}}}",
            rf"\newcommand{{\QTwoCompensation}}{{{selected_combo['required_compensation_kgm2']:.3f}}}",
            rf"\newcommand{{\QTwoAlternativeCompensation}}{{{feasible[1]['required_compensation_kgm2']:.3f}}}",
            rf"\newcommand{{\QThreeCurrent}}{{{current_q3:.3f}}}",
            rf"\newcommand{{\QThreeMotorTorque}}{{{motor_torque_q3:.3f}}}",
            rf"\newcommand{{\QThreeBrakeTorque}}{{{brake_torque_q3:.3f}}}",
            rf"\newcommand{{\QThreeInitialOmega}}{{{initial_omega:.4f}}}",
            rf"\newcommand{{\QThreeAlpha}}{{{angular_acceleration:.4f}}}",
            rf"\newcommand{{\QFourSamples}}{{{len(t)}}}",
            rf"\newcommand{{\QFourDuration}}{{{q4['duration_s']:.2f}}}",
            rf"\newcommand{{\QFourObservedInitial}}{{{q4['observed_initial_rpm']:.2f}}}",
            rf"\newcommand{{\QFourObservedFinal}}{{{q4['observed_final_rpm']:.2f}}}",
            rf"\newcommand{{\QFourRoadEnergy}}{{{nominal_road_energy / 1000.0:.3f}}}",
            rf"\newcommand{{\QFourBenchEnergy}}{{{test_energy / 1000.0:.3f}}}",
            rf"\newcommand{{\QFourEnergyError}}{{{signed_error:.3f}}}",
            rf"\newcommand{{\QFourAbsEnergyErrorKJ}}{{{abs(signed_error) / 1000.0:.3f}}}",
            rf"\newcommand{{\QFourRelativeError}}{{{q4['absolute_relative_energy_error_pct']:.4f}}}",
            rf"\newcommand{{\QFourEffectiveJ}}{{{effective_inertia_observed:.4f}}}",
            rf"\newcommand{{\QFourInferredDeltaJ}}{{{inferred_compensation_inertia:.4f}}}",
            rf"\newcommand{{\QFourQuadratureMin}}{{{q4['integration_absolute_relative_error_min_pct']:.4f}}}",
            rf"\newcommand{{\QFourQuadratureMax}}{{{q4['integration_absolute_relative_error_max_pct']:.4f}}}",
            rf"\newcommand{{\BaselineEnergyError}}{{{simulations['motor_off']['metrics']['absolute_relative_energy_error_pct']:.4f}}}",
            rf"\newcommand{{\BaselineTerminalError}}{{{simulations['motor_off']['metrics']['terminal_speed_error_rpm']:.3f}}}",
            rf"\newcommand{{\QFiveEnergyError}}{{{simulations['torque_zoh']['metrics']['absolute_relative_energy_error_pct']:.5f}}}",
            rf"\newcommand{{\QFiveMaxSpeedError}}{{{simulations['torque_zoh']['metrics']['max_abs_speed_error_rpm']:.4f}}}",
            rf"\newcommand{{\QFiveTerminalError}}{{{simulations['torque_zoh']['metrics']['terminal_speed_error_rpm']:.4f}}}",
            rf"\newcommand{{\QFiveReferenceTerminal}}{{{simulations['torque_zoh']['metrics']['reference_terminal_rpm']:.3f}}}",
            rf"\newcommand{{\QFiveSignedEnergyError}}{{{simulations['torque_zoh']['metrics']['signed_energy_error_J']:.3f}}}",
            rf"\newcommand{{\QFiveMaxCurrent}}{{{simulations['torque_zoh']['metrics']['max_abs_current_A']:.3f}}}",
            rf"\newcommand{{\QSixEnergyError}}{{{simulations['predictive_feedback']['metrics']['absolute_relative_energy_error_pct']:.8f}}}",
            rf"\newcommand{{\QSixMaxSpeedError}}{{{simulations['predictive_feedback']['metrics']['max_abs_speed_error_rpm']:.4f}}}",
            rf"\newcommand{{\QSixTerminalError}}{{{simulations['predictive_feedback']['metrics']['terminal_speed_error_rpm']:.4f}}}",
            rf"\newcommand{{\QSixMaxCurrent}}{{{simulations['predictive_feedback']['metrics']['max_abs_current_A']:.3f}}}",
            rf"\newcommand{{\QuadratureSpan}}{{{q4['quadrature_relative_span_pct']:.4f}}}",
            rf"\newcommand{{\QFiveStepFiftyError}}{{{q5_step_50['absolute_relative_energy_error_pct']:.4f}}}",
            rf"\newcommand{{\QSixStepFiftyError}}{{{q6_step_50['absolute_relative_energy_error_pct']:.8f}}}",
            rf"\newcommand{{\QFiveJMWorst}}{{{q5_jm_worst:.4f}}}",
            rf"\newcommand{{\QSixJMWorst}}{{{q6_jm_worst:.5f}}}",
            rf"\newcommand{{\QSixSensorWorst}}{{{q6_sensor_worst:.4f}}}",
            "",
        ]
    )
    (paper_dir / "generated-results.tex").write_text(tex_macros, encoding="utf-8")

    summary_lines = [
        "# Code-generated result summary",
        "",
        f"- Q1 equivalent inertia: {q1_equivalent_j:.6f} kg·m².",
        "- Q2 individual flywheel inertias: "
        + ", ".join(f"{value:.6f}" for value in flywheel_j)
        + " kg·m².",
        "- Q2 mechanical combinations: "
        + ", ".join(f"{row['mechanical_inertia_kgm2']:.6f}" for row in combo_rows)
        + " kg·m².",
        f"- Q2 selected compensation: {selected_combo['required_compensation_kgm2']:.6f} kg·m².",
        f"- Q3 drive current: {current_q3:.6f} A.",
        f<LONG_QUOTE_REDACTED>,
        f"- Q5 replay absolute relative energy error: {simulations['torque_zoh']['metrics']['absolute_relative_energy_error_pct']:.8f}%.",
        f"- Q6 replay absolute relative energy error: {simulations['predictive_feedback']['metrics']['absolute_relative_energy_error_pct']:.8f}%.",
        "",
        "All signed energy errors use bench brake work minus road brake work.",
    ]
    (results_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    save_figures(
        figures_dir,
        flywheel_j,
        combo_rows,
        q1_equivalent_j,
        t,
        torque,
        rpm,
        q4,
        simulations,
        sensitivity_rows,
    )

    tracked_files = [
        workspace / "input" / "data" / "<SOURCE_FILE_REDACTED>",
        workspace / "input" / "problem" / "<SOURCE_FILE_REDACTED>",
        workspace / "code" / "extract_data.ps1",
        workspace / "code" / "solve.py",
        observation_path,
        results_dir / "metrics.json",
    ]
    manifest = {
        "status": "pass",
        "seed": SEED,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "matplotlib": matplotlib.__version__,
        "files": {
            path.relative_to(workspace).as_posix(): sha256(path)
            for path in tracked_files
            if path.exists()
        },
    }
    write_json(results_dir / "run-manifest.json", manifest)
    print("[pass] numerical results and figures generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
