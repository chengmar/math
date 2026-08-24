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
COMPENSATION_LIMIT_KGM2 = 30.0

Q4_EQUIVALENT_INERTIA = 48.0
Q4_MECHANICAL_INERTIA = 35.0
Q4_INITIAL_RPM = 514.0
Q4_FINAL_RPM = 257.0

# Q6 design settings.  The motor/current/rate values are software-study settings,
# not claims about unspecified hardware.
ROBUST_SPEED_GAIN_NMS_PER_RAD = 500.0
OBSERVER_CORRECTION_GAIN = 0.25
LOW_TORQUE_THRESHOLD_NM = 1.0
DESIGN_BRAKE_TORQUE_ENVELOPE_NM = 300.0
ABS_MOTOR_TORQUE_LIMIT_NM = (
    COMPENSATION_LIMIT_KGM2
    * DESIGN_BRAKE_TORQUE_ENVELOPE_NM
    / Q4_EQUIVALENT_INERTIA
)
MOTOR_TORQUE_RATE_LIMIT_NM_S = ABS_MOTOR_TORQUE_LIMIT_NM / 0.02
DECLARED_STEP_DOMAIN_S = (0.005, 0.05)
DECLARED_ACTUAL_INERTIA_DOMAIN_KGM2 = (18.0, 78.0)


def rpm_to_rad_s(value: np.ndarray | float) -> np.ndarray | float:
    return np.asarray(value) * (2.0 * math.pi / 60.0)


def rad_s_to_rpm(value: np.ndarray | float) -> np.ndarray | float:
    return np.asarray(value) * (60.0 / (2.0 * math.pi))


def to_builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): to_builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return [to_builtin(item) for item in value.tolist()]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, Path):
        return value.as_posix()
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(to_builtin(data), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows([{key: to_builtin(row.get(key, "")) for key in fieldnames} for row in rows])


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
    if data.ndim != 2 or data.shape[0] < 2 or data.shape[1] != 3:
        raise ValueError(f"Unexpected observation shape: {data.shape}")
    return data[:, 0], data[:, 1], data[:, 2]


def annular_flywheel_inertia(thickness_m: np.ndarray) -> np.ndarray:
    outer_radius = OUTER_DIAMETER_M / 2.0
    inner_radius = INNER_DIAMETER_M / 2.0
    volume = math.pi * (outer_radius**2 - inner_radius**2) * thickness_m
    mass = STEEL_DENSITY_KG_M3 * volume
    return 0.5 * mass * (outer_radius**2 + inner_radius**2)


def exact_linear_product_integral(
    t: np.ndarray, first: np.ndarray, second: np.ndarray
) -> float:
    """Integrate the product when both endpoint series are piecewise linear."""
    dt = np.diff(t)
    terms = (
        2.0 * first[:-1] * second[:-1]
        + first[:-1] * second[1:]
        + first[1:] * second[:-1]
        + 2.0 * first[1:] * second[1:]
    )
    return float(np.sum(dt * terms / 6.0))


def interval_energy(t: np.ndarray, torque: np.ndarray, omega: np.ndarray) -> dict[str, float]:
    dt = np.diff(t)
    power = torque * omega
    exact = exact_linear_product_integral(t, torque, omega)
    trapezoid_power = float(np.sum(0.5 * (power[:-1] + power[1:]) * dt))
    left = float(np.sum(power[:-1] * dt))
    right = float(np.sum(power[1:] * dt))
    midpoint_product = float(
        np.sum(0.25 * (torque[:-1] + torque[1:]) * (omega[:-1] + omega[1:]) * dt)
    )
    values = [exact, trapezoid_power, left, right, midpoint_product]
    return {
        "piecewise_linear_exact_J": exact,
        "trapezoid_power_J": trapezoid_power,
        "left_rectangle_J": left,
        "right_rectangle_J": right,
        "midpoint_product_J": midpoint_product,
        "quadrature_span_J": max(values) - min(values),
        "exact_minus_trapezoid_J": exact - trapezoid_power,
    }


def minmod(first: float, second: float) -> float:
    if first * second <= 0.0:
        return 0.0
    return math.copysign(min(abs(first), abs(second)), first)


def bounded_torque_prediction(t: np.ndarray, samples: np.ndarray, index: int) -> float:
    """Nonnegative causal midpoint prediction with a two-slope minmod limiter."""
    if index < 2:
        return max(0.0, float(samples[index]))
    previous_h = t[index] - t[index - 1]
    older_h = t[index - 1] - t[index - 2]
    next_h = t[index + 1] - t[index]
    recent_slope = (samples[index] - samples[index - 1]) / previous_h
    older_slope = (samples[index - 1] - samples[index - 2]) / older_h
    limited_slope = minmod(float(recent_slope), float(older_slope))
    return max(0.0, float(samples[index] + 0.5 * next_h * limited_slope))


def project_motor_command(
    raw_motor_torque: float,
    predicted_brake_torque: float,
    previous_motor_torque: float,
    step_s: float,
) -> dict[str, Any]:
    """Project onto absolute, rate and predicted-equivalent-inertia constraints."""
    if abs(predicted_brake_torque) <= LOW_TORQUE_THRESHOLD_NM:
        amplitude_limit = 0.0
        mode = "low_torque_zero"
    else:
        amplitude_limit = min(
            ABS_MOTOR_TORQUE_LIMIT_NM,
            COMPENSATION_LIMIT_KGM2
            * abs(predicted_brake_torque)
            / Q4_EQUIVALENT_INERTIA,
        )
        mode = "equivalent_inertia"

    desired = float(np.clip(raw_motor_torque, -amplitude_limit, amplitude_limit))
    rate_delta = MOTOR_TORQUE_RATE_LIMIT_NM_S * step_s
    lower = max(-amplitude_limit, previous_motor_torque - rate_delta)
    upper = min(amplitude_limit, previous_motor_torque + rate_delta)
    if lower <= upper:
        command = float(np.clip(desired, lower, upper))
        rate_status = "pass"
        rate_emergency_override = 0
    else:
        # A sudden torque collapse can make amplitude and slew sets disjoint.
        # The instantaneous torque/equivalent-inertia safety bound has priority.
        command = desired
        rate_status = "needs_review"
        rate_emergency_override = 1

    if abs(predicted_brake_torque) > LOW_TORQUE_THRESHOLD_NM:
        equivalent_command = (
            Q4_EQUIVALENT_INERTIA * command / predicted_brake_torque
        )
    else:
        equivalent_command = None
    constraint_ok = (
        abs(command) <= ABS_MOTOR_TORQUE_LIMIT_NM + 1e-12
        and abs(command) <= amplitude_limit + 1e-12
        and (
            equivalent_command is None
            or abs(equivalent_command) <= COMPENSATION_LIMIT_KGM2 + 1e-12
        )
        and (
            abs(predicted_brake_torque) > LOW_TORQUE_THRESHOLD_NM
            or abs(command) <= 1e-12
        )
    )
    return {
        "command_Nm": command,
        "amplitude_limit_Nm": amplitude_limit,
        "equivalent_command_kgm2": equivalent_command,
        "mode": mode,
        "constraint_status": "pass" if constraint_ok else "fail",
        "rate_status": rate_status,
        "rate_emergency_override": rate_emergency_override,
        "amplitude_clipped": int(abs(command - raw_motor_torque) > 1e-12),
    }


def advance_nonnegative(
    omega: float, step_s: float, acceleration: float
) -> tuple[float, float, float, int]:
    """Advance constant acceleration, returning speed integral and a zero event."""
    if omega <= 0.0:
        return 0.0, 0.0, 0.0, 0
    candidate = omega + step_s * acceleration
    if acceleration < 0.0 and candidate <= 0.0:
        active_time = min(step_s, omega / (-acceleration))
        speed_time_integral = 0.5 * omega * active_time
        return 0.0, speed_time_integral, active_time, 1
    next_omega = max(0.0, candidate)
    return next_omega, 0.5 * (omega + next_omega) * step_s, step_s, 0


def simulate_controller(
    t: np.ndarray,
    true_torque_samples: np.ndarray,
    controller: str,
    *,
    sensed_torque_samples: np.ndarray | None = None,
    speed_noise_rpm: np.ndarray | None = None,
    equivalent_inertia: float = Q4_EQUIVALENT_INERTIA,
    design_equivalent_inertia: float = Q4_EQUIVALENT_INERTIA,
    mechanical_inertia: float = Q4_MECHANICAL_INERTIA,
    design_mechanical_inertia: float = Q4_MECHANICAL_INERTIA,
    initial_rpm: float = Q4_INITIAL_RPM,
) -> dict[str, Any]:
    if sensed_torque_samples is None:
        sensed_torque_samples = true_torque_samples
    if speed_noise_rpm is None:
        speed_noise_rpm = np.zeros_like(t)
    if not (
        len(t)
        == len(true_torque_samples)
        == len(sensed_torque_samples)
        == len(speed_noise_rpm)
    ):
        raise ValueError("Time, torque and noise arrays must have equal length.")
    if np.any(np.diff(t) <= 0.0):
        raise ValueError("Time must be strictly increasing.")
    if mechanical_inertia <= 0.0 or equivalent_inertia <= 0.0:
        raise ValueError("Inertias must be positive.")

    n = len(t)
    omega_ref = np.zeros(n, dtype=float)
    omega_internal_ref = np.zeros(n, dtype=float)
    omega_bench = np.zeros(n, dtype=float)
    omega_estimate = np.zeros(n, dtype=float)
    motor_torque = np.zeros(n - 1, dtype=float)
    raw_motor_torque = np.zeros(n - 1, dtype=float)
    predicted_torque = np.zeros(n - 1, dtype=float)
    equivalent_command = np.full(n - 1, np.nan, dtype=float)
    amplitude_limits = np.zeros(n - 1, dtype=float)
    clipped = np.zeros(n - 1, dtype=int)
    constraint_failures = np.zeros(n - 1, dtype=int)
    rate_emergency_overrides = np.zeros(n - 1, dtype=int)

    initial_omega = float(rpm_to_rad_s(initial_rpm))
    omega_ref[0] = initial_omega
    omega_internal_ref[0] = initial_omega
    omega_bench[0] = initial_omega
    omega_estimate[0] = initial_omega
    delta_j_design = design_equivalent_inertia - design_mechanical_inertia
    feedforward_gain = delta_j_design / design_equivalent_inertia
    previous_command = 0.0
    road_energy = 0.0
    bench_energy = 0.0
    motor_energy = 0.0
    reference_stopped = False
    bench_stopped = False
    reference_stop_time: float | None = None
    bench_stop_time: float | None = None

    for k in range(n - 1):
        step_s = float(t[k + 1] - t[k])
        actual_mid_torque = 0.5 * (
            true_torque_samples[k] + true_torque_samples[k + 1]
        )
        sensed_mid_torque = 0.5 * (
            sensed_torque_samples[k] + sensed_torque_samples[k + 1]
        )
        measured_omega = omega_bench[k] + float(rpm_to_rad_s(speed_noise_rpm[k]))

        if controller == "robust_predictive":
            if k == 0:
                omega_estimate[k] = max(0.0, measured_omega)
            else:
                omega_estimate[k] = max(
                    0.0,
                    omega_estimate[k]
                    + OBSERVER_CORRECTION_GAIN
                    * (measured_omega - omega_estimate[k]),
                )

        if controller == "motor_off":
            estimate = 0.0
            raw_command = 0.0
        elif controller == "speed_difference":
            estimate = max(0.0, float(sensed_torque_samples[k]))
            if k == 0:
                raw_command = feedforward_gain * estimate
            else:
                previous_step = float(t[k] - t[k - 1])
                current_measurement = omega_bench[k] + float(rpm_to_rad_s(speed_noise_rpm[k]))
                previous_measurement = omega_bench[k - 1] + float(
                    rpm_to_rad_s(speed_noise_rpm[k - 1])
                )
                raw_command = -delta_j_design * (
                    current_measurement - previous_measurement
                ) / previous_step
        elif controller == "torque_zoh":
            estimate = max(0.0, float(sensed_torque_samples[k]))
            raw_command = feedforward_gain * estimate
        elif controller == "robust_predictive":
            estimate = bounded_torque_prediction(t, sensed_torque_samples, k)
            speed_error_estimate = omega_estimate[k] - omega_internal_ref[k]
            raw_command = (
                feedforward_gain * estimate
                - ROBUST_SPEED_GAIN_NMS_PER_RAD * speed_error_estimate
            )
        else:
            raise ValueError(f"Unknown controller: {controller}")

        if controller == "motor_off" or bench_stopped:
            projection = {
                "command_Nm": 0.0,
                "amplitude_limit_Nm": 0.0,
                "equivalent_command_kgm2": None,
                "constraint_status": "pass",
                "rate_status": "pass",
                "rate_emergency_override": 0,
                "amplitude_clipped": 0,
            }
        else:
            projection = project_motor_command(
                float(raw_command), float(estimate), previous_command, step_s
            )

        command = float(projection["command_Nm"])
        predicted_torque[k] = estimate
        raw_motor_torque[k] = raw_command
        motor_torque[k] = command
        amplitude_limits[k] = float(projection["amplitude_limit_Nm"])
        clipped[k] = int(projection["amplitude_clipped"])
        constraint_failures[k] = int(projection["constraint_status"] == "fail")
        rate_emergency_overrides[k] = int(projection["rate_emergency_override"])
        if projection["equivalent_command_kgm2"] is not None:
            equivalent_command[k] = float(projection["equivalent_command_kgm2"])

        if reference_stopped:
            omega_ref[k + 1] = 0.0
        else:
            (
                omega_ref[k + 1],
                reference_speed_integral,
                reference_active_time,
                reference_hit,
            ) = advance_nonnegative(
                omega_ref[k], step_s, -actual_mid_torque / equivalent_inertia
            )
            road_energy += actual_mid_torque * reference_speed_integral
            if reference_hit:
                reference_stopped = True
                reference_stop_time = float(t[k] + reference_active_time)

        (
            omega_internal_ref[k + 1],
            _,
            _,
            _,
        ) = advance_nonnegative(
            omega_internal_ref[k],
            step_s,
            -sensed_mid_torque / design_equivalent_inertia,
        )

        if bench_stopped:
            omega_bench[k + 1] = 0.0
            bench_speed_integral = 0.0
        else:
            (
                omega_bench[k + 1],
                bench_speed_integral,
                bench_active_time,
                bench_hit,
            ) = advance_nonnegative(
                omega_bench[k],
                step_s,
                (command - actual_mid_torque) / mechanical_inertia,
            )
            bench_energy += actual_mid_torque * bench_speed_integral
            motor_energy += command * bench_speed_integral
            if bench_hit:
                bench_stopped = True
                bench_stop_time = float(t[k] + bench_active_time)

        if controller == "robust_predictive":
            if bench_stopped:
                omega_estimate[k + 1] = 0.0
            else:
                omega_estimate[k + 1] = max(
                    0.0,
                    omega_estimate[k]
                    + step_s
                    * (command - sensed_mid_torque)
                    / design_mechanical_inertia,
                )
        else:
            omega_estimate[k + 1] = omega_bench[k + 1]
        previous_command = command

    speed_error_rpm = np.asarray(rad_s_to_rpm(omega_bench - omega_ref), dtype=float)
    current_a = CURRENT_PER_TORQUE_A_PER_NM * motor_torque
    relative_energy_error = (
        (bench_energy - road_energy) / road_energy if road_energy > 1e-12 else 0.0
    )
    finite_equivalent = equivalent_command[np.isfinite(equivalent_command)]
    if reference_stop_time is None and bench_stop_time is None:
        stop_sync_status = "needs_review"
    elif reference_stop_time is None or bench_stop_time is None:
        stop_sync_status = "fail"
    else:
        stop_sync_status = (
            "pass"
            if abs(reference_stop_time - bench_stop_time) <= max(np.diff(t)) + 1e-12
            else "fail"
        )
    state_status = "pass" if float(np.min(omega_bench)) >= -1e-12 else "fail"
    constraint_status = "pass" if int(np.sum(constraint_failures)) == 0 else "fail"

    return {
        "controller": controller,
        "omega_reference_rad_s": omega_ref,
        "omega_internal_reference_rad_s": omega_internal_ref,
        "omega_bench_rad_s": omega_bench,
        "omega_estimate_rad_s": omega_estimate,
        "speed_error_rpm": speed_error_rpm,
        "motor_torque_Nm": motor_torque,
        "raw_motor_torque_Nm": raw_motor_torque,
        "current_A": current_a,
        "predicted_torque_Nm": predicted_torque,
        "equivalent_command_kgm2": equivalent_command,
        "amplitude_limit_Nm": amplitude_limits,
        "metrics": {
            "reference_terminal_rpm": float(rad_s_to_rpm(omega_ref[-1])),
            "bench_terminal_rpm": float(rad_s_to_rpm(omega_bench[-1])),
            "terminal_speed_error_rpm": float(speed_error_rpm[-1]),
            "rmse_speed_error_rpm": float(np.sqrt(np.mean(speed_error_rpm**2))),
            "max_abs_speed_error_rpm": float(np.max(np.abs(speed_error_rpm))),
            "road_brake_energy_J": road_energy,
            "bench_brake_energy_J": bench_energy,
            "signed_energy_error_J": bench_energy - road_energy,
            "absolute_relative_energy_error_pct": 100.0 * abs(relative_energy_error),
            "motor_energy_J": motor_energy,
            "max_abs_current_A": float(np.max(np.abs(current_a))) if len(current_a) else 0.0,
            "rms_current_A": float(np.sqrt(np.mean(current_a**2))) if len(current_a) else 0.0,
            "amplitude_clipped_intervals": int(np.sum(clipped)),
            "constraint_violation_intervals": int(np.sum(constraint_failures)),
            "rate_emergency_override_intervals": int(np.sum(rate_emergency_overrides)),
            "equivalent_command_min_kgm2": float(np.min(finite_equivalent))
            if len(finite_equivalent)
            else 0.0,
            "equivalent_command_max_kgm2": float(np.max(finite_equivalent))
            if len(finite_equivalent)
            else 0.0,
            "min_bench_speed_rpm": float(rad_s_to_rpm(np.min(omega_bench))),
            "reference_stop_time_s": reference_stop_time,
            "bench_stop_time_s": bench_stop_time,
            "state_constraint_status": state_status,
            "command_constraint_status": constraint_status,
            "stop_synchronization_status": stop_sync_status,
            "replay_completion_status": "pass"
            if reference_stop_time is not None and bench_stop_time is not None
            else "needs_review",
            "hardware_feasibility_status": "needs_review",
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


def status_for_stress(
    metrics: dict[str, Any], *, energy_limit_pct: float, speed_limit_rpm: float
) -> str:
    return (
        "pass"
        if metrics["command_constraint_status"] == "pass"
        and metrics["state_constraint_status"] == "pass"
        and metrics["absolute_relative_energy_error_pct"] <= energy_limit_pct
        and metrics["max_abs_speed_error_rpm"] <= speed_limit_rpm
        and metrics["max_abs_current_A"]
        <= CURRENT_PER_TORQUE_A_PER_NM * ABS_MOTOR_TORQUE_LIMIT_NM + 1e-9
        else "fail"
    )


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
    noise_rows: list[dict[str, Any]],
) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.3), constrained_layout=True)
    axes[0].bar(["Flywheel 1", "Flywheel 2", "Flywheel 3"], flywheel_j, color="#4472C4")
    axes[0].set_ylabel(r"Inertia (kg m$^2$)")
    axes[0].set_title("Individual flywheel inertias")
    mechanical = np.asarray([row["mechanical_inertia_kgm2"] for row in combo_rows])
    axes[1].stem(mechanical, np.ones_like(mechanical), basefmt=" ", linefmt="#70AD47", markerfmt="o")
    axes[1].axvline(q1_equivalent_j, color="#C00000", linestyle="--", label="Equivalent inertia")
    axes[1].axvspan(q1_equivalent_j - 30.0, q1_equivalent_j + 30.0, color="#FFC000", alpha=0.18, label="Motor-feasible")
    axes[1].set_yticks([])
    axes[1].set_xlabel(r"Mechanical inertia (kg m$^2$)")
    axes[1].set_title("Eight mechanical combinations")
    axes[1].legend(fontsize=8)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    omega = np.asarray(rpm_to_rad_s(rpm), dtype=float)
    dt = np.diff(t)
    exact_intervals = dt * (
        2.0 * torque[:-1] * omega[:-1]
        + torque[:-1] * omega[1:]
        + torque[1:] * omega[:-1]
        + 2.0 * torque[1:] * omega[1:]
    ) / 6.0
    cumulative_test = np.concatenate(([0.0], np.cumsum(exact_intervals)))
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
    axes[1].plot(t, cumulative_equivalent / 1000.0, label="48 kg m$^2$ endpoint energy", color="#C00000", linestyle="--")
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Cumulative energy (kJ)")
    axes[1].legend()
    axes[1].set_title(f"Terminal relative energy error = {q4['absolute_relative_energy_error_pct']:.4f}%")
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    names = ["motor_off", "speed_difference", "torque_zoh", "robust_predictive"]
    labels = {
        "motor_off": "Motor off",
        "speed_difference": "Speed difference",
        "torque_zoh": "Torque ZOH (Q5)",
        "robust_predictive": "Bounded robust predictor (Q6)",
    }
    colors = ["#7F7F7F", "#A5A5A5", "#4472C4", "#C00000"]
    fig, axes = plt.subplots(3, 1, figsize=(10.5, 9.0), sharex=True, constrained_layout=True)
    for name, color in zip(names, colors):
        axes[0].plot(t, simulations[name]["speed_error_rpm"], label=labels[name], color=color)
    for name, color in zip(names[1:], colors[1:]):
        axes[1].plot(t, simulations[name]["speed_error_rpm"], label=labels[name], color=color)
        axes[2].step(t[:-1], simulations[name]["current_A"], where="post", label=labels[name], color=color)
    axes[0].axhline(0.0, color="black", linewidth=0.7)
    axes[1].axhline(0.0, color="black", linewidth=0.7)
    axes[0].set_ylabel("Speed error (rpm)")
    axes[1].set_ylabel("Zoomed error (rpm)")
    axes[2].set_ylabel("Motor current (A)")
    axes[2].set_xlabel("Time (s)")
    axes[0].legend(fontsize=8)
    axes[1].legend(fontsize=8)
    axes[2].legend(fontsize=8)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    inertia_rows = [row for row in sensitivity_rows if row["scenario"] == "actual_mechanical_inertia"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), constrained_layout=True)
    for controller, marker, color in [("torque_zoh", "o", "#4472C4"), ("robust_predictive", "s", "#C00000")]:
        subset = sorted([row for row in inertia_rows if row["controller"] == controller], key=lambda row: row["value"])
        axes[0].plot([row["value"] for row in subset], [row["absolute_relative_energy_error_pct"] for row in subset], marker=marker, color=color, label=labels[controller])
    axes[0].axvspan(18.0, 78.0, color="#70AD47", alpha=0.08)
    axes[0].set_xlabel(r"Actual mechanical inertia (kg m$^2$)")
    axes[0].set_ylabel("Absolute relative energy error (%)")
    axes[0].set_title("Feasible inertia-domain stress")
    axes[0].legend(fontsize=8)
    axes[1].bar([str(row["sigma_rpm"]) for row in noise_rows], [row["median_peak_current_A"] for row in noise_rows], color="#ED7D31", label="Median peak")
    axes[1].scatter([str(row["sigma_rpm"]) for row in noise_rows], [row["worst_peak_current_A"] for row in noise_rows], color="#C00000", label="Worst of 100 seeds")
    axes[1].axhline(CURRENT_PER_TORQUE_A_PER_NM * ABS_MOTOR_TORQUE_LIMIT_NM, color="black", linestyle="--", label="Software cap")
    axes[1].set_xlabel("Speed-noise sigma (rpm)")
    axes[1].set_ylabel("Peak absolute current (A)")
    axes[1].set_title("Fixed-seed noise stress")
    axes[1].legend(fontsize=8)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Blind revision for CUMCM 2009 A.")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results_dir = workspace / "results"
    figures_dir = workspace / "figures"
    paper_dir = workspace / "paper"
    for directory in [results_dir, figures_dir, paper_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    np.random.seed(SEED)

    observation_path = results_dir / "<SOURCE_FILE_REDACTED>"
    metadata_path = results_dir / "input-metadata.json"
    t, torque, rpm = load_observations(observation_path)
    input_metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    dt = np.diff(t)
    if not np.all(np.isfinite(np.column_stack((t, torque, rpm)))):
        raise ValueError("Non-finite values found in observations.")
    if np.any(dt <= 0.0):
        raise ValueError("Observation times are not strictly increasing.")

    q1_equivalent_j = WHEEL_LOAD_N * WHEEL_RADIUS_M**2 / G
    flywheel_j = annular_flywheel_inertia(THICKNESSES_M)
    combo_rows: list[dict[str, Any]] = []
    for bits in itertools.product((0, 1), repeat=len(flywheel_j)):
        flywheel_sum = float(np.dot(np.asarray(bits), flywheel_j))
        mechanical = BASE_INERTIA_KGM2 + flywheel_sum
        required = q1_equivalent_j - mechanical
        feasible_status = (
            "pass" if -30.0 - 1e-12 <= required <= 30.0 + 1e-12 else "fail"
        )
        combo_rows.append(
            {
                "flywheel_1": bits[0],
                "flywheel_2": bits[1],
                "flywheel_3": bits[2],
                "flywheel_inertia_sum_kgm2": flywheel_sum,
                "mechanical_inertia_kgm2": mechanical,
                "required_compensation_kgm2": required,
                "feasibility_status": feasible_status,
            }
        )
    combo_rows.sort(key=lambda row: row["mechanical_inertia_kgm2"])
    feasible = [row for row in combo_rows if row["feasibility_status"] == "pass"]
    selected_combo = min(
        feasible,
        key=lambda row: (abs(row["required_compensation_kgm2"]), row["mechanical_inertia_kgm2"]),
    )

    initial_linear_speed = 50.0 / 3.6
    initial_omega = initial_linear_speed / WHEEL_RADIUS_M
    angular_acceleration = -initial_omega / 5.0
    motor_torque_q3 = -selected_combo["required_compensation_kgm2"] * angular_acceleration
    current_q3 = CURRENT_PER_TORQUE_A_PER_NM * motor_torque_q3
    brake_torque_q3 = -q1_equivalent_j * angular_acceleration

    omega = np.asarray(rpm_to_rad_s(rpm), dtype=float)
    energy = interval_energy(t, torque, omega)
    nominal_initial_omega = float(rpm_to_rad_s(Q4_INITIAL_RPM))
    nominal_final_omega = float(rpm_to_rad_s(Q4_FINAL_RPM))
    nominal_road_energy = 0.5 * Q4_EQUIVALENT_INERTIA * (
        nominal_initial_omega**2 - nominal_final_omega**2
    )
    observed_endpoint_road_energy = 0.5 * Q4_EQUIVALENT_INERTIA * (
        omega[0] ** 2 - omega[-1] ** 2
    )
    test_energy = energy["piecewise_linear_exact_J"]
    signed_error = test_energy - nominal_road_energy
    integration_values = [
        energy["piecewise_linear_exact_J"],
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
    alpha_interval = np.diff(omega) / dt
    inferred_u_left = torque[:-1] + Q4_MECHANICAL_INERTIA * alpha_interval
    inferred_u_right = torque[1:] + Q4_MECHANICAL_INERTIA * alpha_interval
    inferred_motor_energy = float(
        np.sum(
            dt
            * (
                2.0 * inferred_u_left * omega[:-1]
                + inferred_u_left * omega[1:]
                + inferred_u_right * omega[:-1]
                + 2.0 * inferred_u_right * omega[1:]
            )
            / 6.0
        )
    )
    mechanical_energy_drop = 0.5 * Q4_MECHANICAL_INERTIA * (
        omega[0] ** 2 - omega[-1] ** 2
    )
    balance_residual = test_energy - inferred_motor_energy - mechanical_energy_drop
    inferred_compensation_inertia = 2.0 * inferred_motor_energy / (
        omega[0] ** 2 - omega[-1] ** 2
    )

    q4 = {
        "computation_status": "pass",
        "acceptance_status": "needs_review",
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
        "inferred_current_min_A": float(CURRENT_PER_TORQUE_A_PER_NM * min(np.min(inferred_u_left), np.min(inferred_u_right))),
        "inferred_current_max_A": float(CURRENT_PER_TORQUE_A_PER_NM * max(np.max(inferred_u_left), np.max(inferred_u_right))),
        "dynamic_energy_balance_residual_J": balance_residual,
        "quadrature_span_J": energy["quadrature_span_J"],
        "quadrature_relative_span_pct": 100.0 * energy["quadrature_span_J"] / nominal_road_energy,
        "integration_estimates_J": energy,
        "integration_absolute_relative_error_min_pct": min(integration_relative_errors),
        "integration_absolute_relative_error_max_pct": max(integration_relative_errors),
        "assessment_rule": "No acceptance tolerance is supplied; numerical evaluation passes and engineering acceptance needs review.",
    }

    controller_names = ["motor_off", "speed_difference", "torque_zoh", "robust_predictive"]
    simulations = {name: simulate_controller(t, torque, name) for name in controller_names}
    comparison_rows = [
        {"controller": name, **simulations[name]["metrics"]} for name in controller_names
    ]

    sensitivity_rows: list[dict[str, Any]] = []
    for step in [0.005, 0.01, 0.02, 0.05]:
        resampled_t, resampled_torque = resample_trace(t, torque, step)
        for controller in ["torque_zoh", "robust_predictive"]:
            metrics = simulate_controller(resampled_t, resampled_torque, controller)["metrics"]
            sensitivity_rows.append(
                {
                    "scenario": "step_size",
                    "value": step,
                    "controller": controller,
                    "terminal_speed_error_rpm": metrics["terminal_speed_error_rpm"],
                    "max_abs_speed_error_rpm": metrics["max_abs_speed_error_rpm"],
                    "absolute_relative_energy_error_pct": metrics["absolute_relative_energy_error_pct"],
                    "max_abs_current_A": metrics["max_abs_current_A"],
                    "constraint_status": metrics["command_constraint_status"],
                }
            )
    for actual_inertia in [18.0, 20.0, 26.25, 35.0, 52.5, 70.0, 78.0]:
        for controller in ["torque_zoh", "robust_predictive"]:
            metrics = simulate_controller(
                t, torque, controller, mechanical_inertia=actual_inertia
            )["metrics"]
            sensitivity_rows.append(
                {
                    "scenario": "actual_mechanical_inertia",
                    "value": actual_inertia,
                    "controller": controller,
                    "terminal_speed_error_rpm": metrics["terminal_speed_error_rpm"],
                    "max_abs_speed_error_rpm": metrics["max_abs_speed_error_rpm"],
                    "absolute_relative_energy_error_pct": metrics["absolute_relative_energy_error_pct"],
                    "max_abs_current_A": metrics["max_abs_current_A"],
                    "constraint_status": metrics["command_constraint_status"],
                }
            )
    for scale in [0.95, 1.0, 1.05]:
        for controller in ["torque_zoh", "robust_predictive"]:
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
                    "absolute_relative_energy_error_pct": metrics["absolute_relative_energy_error_pct"],
                    "max_abs_current_A": metrics["max_abs_current_A"],
                    "constraint_status": metrics["command_constraint_status"],
                }
            )

    noise_rows: list[dict[str, Any]] = []
    noise_all_pass = True
    for sigma_rpm in [0.1, 0.5]:
        trials: list[dict[str, Any]] = []
        for seed_offset in range(100):
            noise = np.random.default_rng(SEED + seed_offset).normal(0.0, sigma_rpm, len(t))
            trials.append(
                simulate_controller(
                    t, torque, "robust_predictive", speed_noise_rpm=noise
                )["metrics"]
            )
        energies = np.asarray([row["absolute_relative_energy_error_pct"] for row in trials])
        speed_errors = np.asarray([row["max_abs_speed_error_rpm"] for row in trials])
        currents = np.asarray([row["max_abs_current_A"] for row in trials])
        noise_status = (
            "pass"
            if float(np.max(energies)) <= 0.05
            and float(np.max(speed_errors)) <= 0.5
            and float(np.max(currents)) <= CURRENT_PER_TORQUE_A_PER_NM * ABS_MOTOR_TORQUE_LIMIT_NM + 1e-9
            and all(row["command_constraint_status"] == "pass" for row in trials)
            else "fail"
        )
        noise_all_pass = noise_all_pass and noise_status == "pass"
        noise_rows.append(
            {
                "sigma_rpm": sigma_rpm,
                "seeds": 100,
                "median_energy_error_pct": float(np.median(energies)),
                "worst_energy_error_pct": float(np.max(energies)),
                "median_max_speed_error_rpm": float(np.median(speed_errors)),
                "worst_max_speed_error_rpm": float(np.max(speed_errors)),
                "median_peak_current_A": float(np.median(currents)),
                "worst_peak_current_A": float(np.max(currents)),
                "status": noise_status,
                "rule": "worst energy <= 0.05%, speed error <= 0.5 rpm, current <= software cap, no command-constraint violation",
            }
        )

    constraint_rows: list[dict[str, Any]] = []
    for estimate, raw in [(0.0, 1.0), (0.1, 1.0), (1.0, 1.0), (40.0, 100.0)]:
        projection = project_motor_command(raw, estimate, 0.0, 0.01)
        constraint_rows.append(
            {
                "predicted_brake_torque_Nm": estimate,
                "raw_motor_torque_Nm": raw,
                "command_Nm": projection["command_Nm"],
                "equivalent_command_kgm2": projection["equivalent_command_kgm2"]
                if projection["equivalent_command_kgm2"] is not None
                else "",
                "mode": projection["mode"],
                "constraint_status": projection["constraint_status"],
                "rate_status": projection["rate_status"],
            }
        )
    constraint_status = (
        "pass"
        if all(row["constraint_status"] == "pass" for row in constraint_rows)
        else "fail"
    )

    stress_rows: list[dict[str, Any]] = []
    stop_t = np.arange(0.0, 20.0 + 0.005, 0.01)
    stop_torque = np.full_like(stop_t, 300.0)
    for controller in ["torque_zoh", "robust_predictive"]:
        metrics = simulate_controller(stop_t, stop_torque, controller)["metrics"]
        status = (
            "pass"
            if metrics["state_constraint_status"] == "pass"
            and metrics["stop_synchronization_status"] == "pass"
            and metrics["reference_stop_time_s"] is not None
            and metrics["bench_stop_time_s"] is not None
            else "fail"
        )
        stress_rows.append(
            {
                "scenario": "constant_300Nm_stop_20s",
                "controller": controller,
                "absolute_relative_energy_error_pct": metrics["absolute_relative_energy_error_pct"],
                "max_abs_speed_error_rpm": metrics["max_abs_speed_error_rpm"],
                "max_abs_current_A": metrics["max_abs_current_A"],
                "min_bench_speed_rpm": metrics["min_bench_speed_rpm"],
                "reference_stop_time_s": metrics["reference_stop_time_s"],
                "bench_stop_time_s": metrics["bench_stop_time_s"],
                "status": status,
                "rule": "both stop in horizon, stop-time difference <= one step, omega >= 0",
            }
        )
    alternating_t = np.arange(0.0, 1.0 + 0.005, 0.01)
    alternating_torque = np.where(np.arange(len(alternating_t)) % 2 == 0, 40.0, 300.0)
    for controller in ["torque_zoh", "robust_predictive"]:
        metrics = simulate_controller(alternating_t, alternating_torque, controller)["metrics"]
        status = status_for_stress(metrics, energy_limit_pct=0.02, speed_limit_rpm=0.25)
        stress_rows.append(
            {
                "scenario": "alternating_40_300Nm_10ms",
                "controller": controller,
                "absolute_relative_energy_error_pct": metrics["absolute_relative_energy_error_pct"],
                "max_abs_speed_error_rpm": metrics["max_abs_speed_error_rpm"],
                "max_abs_current_A": metrics["max_abs_current_A"],
                "min_bench_speed_rpm": metrics["min_bench_speed_rpm"],
                "reference_stop_time_s": metrics["reference_stop_time_s"] or "",
                "bench_stop_time_s": metrics["bench_stop_time_s"] or "",
                "status": status,
                "rule": "energy <= 0.02%, speed error <= 0.25 rpm, current cap and state/command constraints pass",
            }
        )
    for actual_inertia in [18.0, 78.0]:
        metrics = simulate_controller(
            t, torque, "robust_predictive", mechanical_inertia=actual_inertia
        )["metrics"]
        status = status_for_stress(metrics, energy_limit_pct=2.0, speed_limit_rpm=10.0)
        stress_rows.append(
            {
                "scenario": f"feasible_inertia_{actual_inertia:.0f}kgm2",
                "controller": "robust_predictive",
                "absolute_relative_energy_error_pct": metrics["absolute_relative_energy_error_pct"],
                "max_abs_speed_error_rpm": metrics["max_abs_speed_error_rpm"],
                "max_abs_current_A": metrics["max_abs_current_A"],
                "min_bench_speed_rpm": metrics["min_bench_speed_rpm"],
                "reference_stop_time_s": metrics["reference_stop_time_s"] or "",
                "bench_stop_time_s": metrics["bench_stop_time_s"] or "",
                "status": status,
                "rule": "energy <= 2%, speed error <= 10 rpm, current cap and state/command constraints pass",
            }
        )
    stress_status = "pass" if all(row["status"] == "pass" for row in stress_rows) else "fail"

    min_pole = 1.0 - DECLARED_STEP_DOMAIN_S[1] * ROBUST_SPEED_GAIN_NMS_PER_RAD / DECLARED_ACTUAL_INERTIA_DOMAIN_KGM2[0]
    max_pole = 1.0 - DECLARED_STEP_DOMAIN_S[0] * ROBUST_SPEED_GAIN_NMS_PER_RAD / DECLARED_ACTUAL_INERTIA_DOMAIN_KGM2[1]
    stability_status = (
        "pass"
        if -1.0 < min_pole < 1.0
        and -1.0 < max_pole < 1.0
        and 0.0 < OBSERVER_CORRECTION_GAIN < 2.0
        else "fail"
    )

    data_audit_status = (
        "pass"
        if input_metadata["source_byte_integrity_status"] == "pass"
        and len(t) == 468
        and np.all(np.diff(t) > 0.0)
        and np.all(np.isfinite(np.column_stack((t, torque, rpm))))
        else "fail"
    )
    data_audit = {
        "status": data_audit_status,
        "source_provenance": input_metadata,
        "observations": q4,
        "facts": [
            "The original workbook hash is captured before Excel starts.",
            "Excel opens only a disposable copy and the original pre/post hashes are compared.",
            "All 468 retained time, torque and speed entries are finite and time is strictly increasing.",
        ],
        "engineering_judgments": [
            "Small upward or flat speed steps are retained because they are not proven data errors.",
            "Piecewise-linear torque and speed imply exact quadratic-power integration.",
        ],
    }

    q6_metrics = simulations["robust_predictive"]["metrics"]
    metrics = {
        "status": "pass"
        if all(
            status == "pass"
            for status in [data_audit_status, constraint_status, stress_status, stability_status]
        )
        else "fail",
        "seed": SEED,
        "question_1": {
            "computation_status": "pass",
            "equivalent_inertia_kgm2": q1_equivalent_j,
            "gravity_m_s2": G,
        },
        "question_2": {
            "computation_status": "pass",
            "flywheel_inertias_kgm2": flywheel_j,
            "mechanical_inertias_kgm2": [row["mechanical_inertia_kgm2"] for row in combo_rows],
            "feasible_compensations": feasible,
            "selected": selected_combo,
        },
        "question_3": {
            "computation_status": "pass",
            "initial_speed_kmh": 50.0,
            "stop_time_s": 5.0,
            "initial_angular_speed_rad_s": initial_omega,
            "angular_acceleration_rad_s2": angular_acceleration,
            "brake_torque_Nm": brake_torque_q3,
            "motor_torque_Nm": motor_torque_q3,
            "drive_current_A": current_q3,
        },
        "question_4": q4,
        "question_5": {
            "computation_status": "pass",
            **simulations["torque_zoh"]["metrics"],
        },
        "question_6": {
            "computation_status": "pass",
            "controller": "robust_predictive",
            "speed_feedback_gain_Nms_per_rad": ROBUST_SPEED_GAIN_NMS_PER_RAD,
            "observer_correction_gain": OBSERVER_CORRECTION_GAIN,
            "low_torque_threshold_Nm": LOW_TORQUE_THRESHOLD_NM,
            "absolute_motor_torque_limit_Nm": ABS_MOTOR_TORQUE_LIMIT_NM,
            "absolute_current_limit_A": CURRENT_PER_TORQUE_A_PER_NM * ABS_MOTOR_TORQUE_LIMIT_NM,
            "motor_torque_rate_limit_Nm_s": MOTOR_TORQUE_RATE_LIMIT_NM_S,
            "declared_step_domain_s": list(DECLARED_STEP_DOMAIN_S),
            "declared_actual_inertia_domain_kgm2": list(DECLARED_ACTUAL_INERTIA_DOMAIN_KGM2),
            "closed_loop_pole_range": [min_pole, max_pole],
            "stability_status": stability_status,
            "noise_stress_status": "pass" if noise_all_pass else "fail",
            "counterexample_stress_status": stress_status,
            "hardware_feasibility_status": "needs_review",
            **q6_metrics,
        },
        "controller_comparison": {row["controller"]: row for row in comparison_rows},
        "validation": {
            "input_provenance_status": input_metadata["source_byte_integrity_status"],
            "low_torque_constraint_status": constraint_status,
            "stability_domain_status": stability_status,
            "speed_noise_status": "pass" if noise_all_pass else "fail",
            "stopping_and_state_status": stress_status,
            "hardware_feasibility_status": "needs_review",
        },
        "data_audit": data_audit,
    }
    write_json(results_dir / "metrics.json", metrics)
    write_json(results_dir / "data-audit.json", data_audit)
    write_csv(results_dir / "<SOURCE_FILE_REDACTED>", list(combo_rows[0].keys()), combo_rows)
    write_csv(results_dir / "<SOURCE_FILE_REDACTED>", list(comparison_rows[0].keys()), comparison_rows)
    write_csv(results_dir / "<SOURCE_FILE_REDACTED>", list(sensitivity_rows[0].keys()), sensitivity_rows)
    write_csv(results_dir / "<SOURCE_FILE_REDACTED>", list(noise_rows[0].keys()), noise_rows)
    write_csv(results_dir / "<SOURCE_FILE_REDACTED>", list(constraint_rows[0].keys()), constraint_rows)
    write_csv(results_dir / "<SOURCE_FILE_REDACTED>", list(stress_rows[0].keys()), stress_rows)

    timeseries_rows: list[dict[str, Any]] = []
    for k in range(len(t)):
        row: dict[str, Any] = {
            "time_s": t[k],
            "observed_torque_Nm": torque[k],
            "observed_speed_rpm": rpm[k],
        }
        for name, result in simulations.items():
            row[f"{name}_reference_rpm"] = float(rad_s_to_rpm(result["omega_reference_rad_s"][k]))
            row[f"{name}_bench_rpm"] = float(rad_s_to_rpm(result["omega_bench_rad_s"][k]))
            row[f"{name}_error_rpm"] = result["speed_error_rpm"][k]
            row[f"{name}_current_A"] = result["current_A"][k] if k < len(t) - 1 else ""
        if k < len(t) - 1:
            row["robust_predicted_torque_Nm"] = simulations["robust_predictive"]["predicted_torque_Nm"][k]
            row["robust_raw_motor_torque_Nm"] = simulations["robust_predictive"]["raw_motor_torque_Nm"][k]
            equivalent = simulations["robust_predictive"]["equivalent_command_kgm2"][k]
            row["robust_equivalent_command_kgm2"] = equivalent if np.isfinite(equivalent) else ""
        else:
            row["robust_predicted_torque_Nm"] = ""
            row["robust_raw_motor_torque_Nm"] = ""
            row["robust_equivalent_command_kgm2"] = ""
        timeseries_rows.append(row)
    write_csv(results_dir / "<SOURCE_FILE_REDACTED>", list(timeseries_rows[0].keys()), timeseries_rows)

    sensitivity_lookup = {
        (row["scenario"], float(row["value"]), row["controller"]): row
        for row in sensitivity_rows
    }
    q5_step_50 = sensitivity_lookup[("step_size", 0.05, "torque_zoh")]
    q6_step_50 = sensitivity_lookup[("step_size", 0.05, "robust_predictive")]
    q6_inertia_worst = max(
        sensitivity_lookup[("actual_mechanical_inertia", value, "robust_predictive")]["absolute_relative_energy_error_pct"]
        for value in (18.0, 78.0)
    )
    q6_sensor_worst = max(
        sensitivity_lookup[("torque_sensor_scale", value, "robust_predictive")]["absolute_relative_energy_error_pct"]
        for value in (0.95, 1.05)
    )
    noise_half = next(row for row in noise_rows if math.isclose(row["sigma_rpm"], 0.5))
    alternating_q6 = next(
        row
        for row in stress_rows
        if row["scenario"] == "alternating_40_300Nm_10ms"
        and row["controller"] == "robust_predictive"
    )
    stop_q6 = next(
        row
        for row in stress_rows
        if row["scenario"] == "constant_300Nm_stop_20s"
        and row["controller"] == "robust_predictive"
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
            rf"\newcommand{{\QFourExactCorrection}}{{{energy['exact_minus_trapezoid_J']:.5f}}}",
            rf"\newcommand{{\BaselineEnergyError}}{{{simulations['motor_off']['metrics']['absolute_relative_energy_error_pct']:.4f}}}",
            rf"\newcommand{{\BaselineTerminalError}}{{{simulations['motor_off']['metrics']['terminal_speed_error_rpm']:.3f}}}",
            rf"\newcommand{{\QFiveEnergyError}}{{{simulations['torque_zoh']['metrics']['absolute_relative_energy_error_pct']:.5f}}}",
            rf"\newcommand{{\QFiveMaxSpeedError}}{{{simulations['torque_zoh']['metrics']['max_abs_speed_error_rpm']:.4f}}}",
            rf"\newcommand{{\QFiveTerminalError}}{{{simulations['torque_zoh']['metrics']['terminal_speed_error_rpm']:.4f}}}",
            rf"\newcommand{{\QFiveReferenceTerminal}}{{{simulations['torque_zoh']['metrics']['reference_terminal_rpm']:.3f}}}",
            rf"\newcommand{{\QFiveSignedEnergyError}}{{{simulations['torque_zoh']['metrics']['signed_energy_error_J']:.3f}}}",
            rf"\newcommand{{\QFiveMaxCurrent}}{{{simulations['torque_zoh']['metrics']['max_abs_current_A']:.3f}}}",
            rf"\newcommand{{\QSixEnergyError}}{{{q6_metrics['absolute_relative_energy_error_pct']:.8f}}}",
            rf"\newcommand{{\QSixMaxSpeedError}}{{{q6_metrics['max_abs_speed_error_rpm']:.4f}}}",
            rf"\newcommand{{\QSixTerminalError}}{{{q6_metrics['terminal_speed_error_rpm']:.4f}}}",
            rf"\newcommand{{\QSixMaxCurrent}}{{{q6_metrics['max_abs_current_A']:.3f}}}",
            rf"\newcommand{{\QSixStepFiftyError}}{{{q6_step_50['absolute_relative_energy_error_pct']:.5f}}}",
            rf"\newcommand{{\QFiveStepFiftyError}}{{{q5_step_50['absolute_relative_energy_error_pct']:.4f}}}",
            rf"\newcommand{{\QSixInertiaWorst}}{{{q6_inertia_worst:.4f}}}",
            rf"\newcommand{{\QSixSensorWorst}}{{{q6_sensor_worst:.4f}}}",
            rf"\newcommand{{\QSixNoiseMedianCurrent}}{{{noise_half['median_peak_current_A']:.2f}}}",
            rf"\newcommand{{\QSixNoiseWorstCurrent}}{{{noise_half['worst_peak_current_A']:.2f}}}",
            rf"\newcommand{{\QSixAlternatingError}}{{{alternating_q6['absolute_relative_energy_error_pct']:.5f}}}",
            rf"\newcommand{{\QSixStopTime}}{{{float(stop_q6['bench_stop_time_s']):.3f}}}",
            rf"\newcommand{{\QSixPoleMinimum}}{{{min_pole:.3f}}}",
            rf"\newcommand{{\QSixPoleMaximum}}{{{max_pole:.3f}}}",
            rf"\newcommand{{\QSixCurrentCap}}{{{CURRENT_PER_TORQUE_A_PER_NM * ABS_MOTOR_TORQUE_LIMIT_NM:.2f}}}",
            rf"\newcommand{{\QSixNoiseGain}}{{{CURRENT_PER_TORQUE_A_PER_NM * ROBUST_SPEED_GAIN_NMS_PER_RAD * OBSERVER_CORRECTION_GAIN * 2.0 * math.pi / 60.0:.3f}}}",
            "",
        ]
    )
    (paper_dir / "generated-results.tex").write_text(tex_macros, encoding="utf-8", newline="\n")

    summary_lines = [
        "# Code-generated result summary",
        "",
        f"- Q1 equivalent inertia: {q1_equivalent_j:.9f} kg·m².",
        "- Q2 individual flywheel inertias: " + ", ".join(f"{value:.9f}" for value in flywheel_j) + " kg·m².",
        f"- Q2 selected compensation: {selected_combo['required_compensation_kgm2']:.9f} kg·m².",
        f"- Q3 drive current: {current_q3:.9f} A.",
        f"- Q4 exact piecewise-linear bench work: {test_energy:.9f} J; signed error: {signed_error:.9f} J; absolute relative error: {q4['absolute_relative_energy_error_pct']:.9f}%.",
        f"- Q5 replay absolute relative energy error: {simulations['torque_zoh']['metrics']['absolute_relative_energy_error_pct']:.9f}%.",
        f"- Q6 bounded robust replay error: {q6_metrics['absolute_relative_energy_error_pct']:.9f}%; max speed error: {q6_metrics['max_abs_speed_error_rpm']:.9f} rpm.",
        f"- Constraint, stability, noise and stopping stress statuses: {constraint_status}, {stability_status}, {'pass' if noise_all_pass else 'fail'}, {stress_status}.",
        "",
        "All signed energy errors use bench brake work minus road brake work.",
    ]
    (results_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8", newline="\n")

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
        noise_rows,
    )

    tracked_files = [
        workspace / "input" / "data" / "<SOURCE_FILE_REDACTED>",
        workspace / "input" / "problem" / "<SOURCE_FILE_REDACTED>",
        workspace / "code" / "extract_data.ps1",
        workspace / "code" / "solve.py",
        observation_path,
        metadata_path,
        results_dir / "metrics.json",
    ]
    manifest = {
        "status": "pass",
        "seed": SEED,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "matplotlib": matplotlib.__version__,
        "encoding_policy": "UTF-8 without BOM and LF for Python-generated CSV/JSON/text",
        "files": {
            path.relative_to(workspace).as_posix(): sha256(path)
            for path in tracked_files
            if path.exists()
        },
    }
    write_json(results_dir / "run-manifest.json", manifest)
    if metrics["status"] != "pass":
        print("[fail] numerical generation completed with failed validation status")
        return 1
    print("[pass] numerical results, constraints, stress tests and figures generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
