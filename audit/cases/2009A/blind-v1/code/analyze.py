#!/usr/bin/env python3
"""Blind, reproducible analysis for the 2009A brake-test-bench case.

The script only consumes the two derived files produced by extract_inputs.ps1
and the two original inputs for hashing.  It writes all numerical results and
figures used by the paper.  No network access or external reference solution
is used.
"""

from __future__ import annotations

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
RNG = np.random.default_rng(SEED)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
PAPER = ROOT / "paper"
DATA_CSV = RESULTS / "<SOURCE_FILE_REDACTED>"
METADATA_JSON = RESULTS / "input-metadata.json"
RAW_PROBLEM = ROOT / "input" / "problem" / "<SOURCE_FILE_REDACTED>"
RAW_DATA = ROOT / "input" / "data" / "<SOURCE_FILE_REDACTED>"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write an empty table: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def load_data() -> tuple[dict[str, Any], np.ndarray, np.ndarray, np.ndarray]:
    metadata = json.loads(METADATA_JSON.read_text(encoding="utf-8"))
    data = np.genfromtxt(DATA_CSV, delimiter=",", names=True, dtype=float)
    torque = np.asarray(data["torque_Nm"], dtype=float)
    rpm = np.asarray(data["speed_rpm"], dtype=float)
    time = np.asarray(data["time_s"], dtype=float)
    return metadata, time, torque, rpm


def flywheel_inertia(rho: float, thickness: float, outer_r: float, inner_r: float) -> float:
    return 0.5 * math.pi * rho * thickness * (outer_r**4 - inner_r**4)


def simulate_controller(
    torque: np.ndarray,
    h: float,
    omega0: float,
    je_actual: float,
    jm_actual: float,
    kind: str,
    *,
    je_model: float | None = None,
    jm_model: float | None = None,
    torque_noise: np.ndarray | None = None,
    speed_noise: np.ndarray | None = None,
    beta_speed: float = 0.20,
    lambda_energy: float = 0.05,
    filter_alpha: float = 0.50,
    predictor_gain: float = 0.50,
) -> dict[str, Any]:
    """Replay a causal controller against an exogenous brake-torque history.

    The enhanced controller uses only measurements available before the current
    interval: a filtered one-step torque predictor, reference-speed feedback,
    and cumulative compensation-energy feedback.  No actuator limit is imposed
    because the problem statement supplies none; peak current is reported.
    """

    torque = np.asarray(torque, dtype=float)
    n = len(torque)
    je_model = je_actual if je_model is None else je_model
    jm_model = jm_actual if jm_model is None else jm_model
    delta_model = je_model - jm_model
    ratio_model = delta_model / je_model
    torque_noise = np.zeros(n) if torque_noise is None else np.asarray(torque_noise, dtype=float)
    speed_noise = np.zeros(n + 1) if speed_noise is None else np.asarray(speed_noise, dtype=float)
    measured_torque = torque + torque_noise

    omega_road = np.empty(n + 1)
    omega_bench = np.empty(n + 1)
    omega_reference = np.empty(n + 1)
    motor_torque = np.zeros(n)
    filtered_torque = np.full(n, np.nan)
    omega_road[0] = omega_bench[0] = omega_reference[0] = omega0
    motor_work_true = 0.0
    motor_work_est = 0.0
    previous_measured_speed = omega0 + speed_noise[0]

    for k in range(n):
        if k > 0:
            omega_reference[k] = (
                omega_reference[k - 1] - h * measured_torque[k - 1] / je_model
            )
            if k == 1:
                filtered_torque[k - 1] = measured_torque[k - 1]
            else:
                filtered_torque[k - 1] = (
                    (1.0 - filter_alpha) * filtered_torque[k - 2]
                    + filter_alpha * measured_torque[k - 1]
                )

        if kind == "baseline_no_compensation":
            command = 0.0
        elif kind == "delayed_torque_feedback":
            command = 0.0 if k == 0 else ratio_model * measured_torque[k - 1]
        elif kind == "predictive_energy_feedback":
            if k == 0:
                predicted_torque = 0.0
            elif k == 1:
                predicted_torque = filtered_torque[0]
            else:
                predicted_torque = filtered_torque[k - 1] + predictor_gain * (
                    filtered_torque[k - 1] - filtered_torque[k - 2]
                )
                predicted_torque = max(0.0, predicted_torque)

            measured_speed = omega_bench[k] + speed_noise[k]
            speed_error = omega_reference[k] - measured_speed
            required_work = 0.5 * delta_model * (
                (omega0 + speed_noise[0]) ** 2 - omega_reference[k] ** 2
            )
            energy_error = required_work - motor_work_est
            speed_scale = max(abs(measured_speed), 1.0)
            command = (
                ratio_model * predicted_torque
                + beta_speed * jm_model * speed_error / h
                + lambda_energy * energy_error / (h * speed_scale)
            )
        else:
            raise ValueError(f"unknown controller: {kind}")

        motor_torque[k] = command
        omega_road[k + 1] = omega_road[k] - h * torque[k] / je_actual
        omega_bench[k + 1] = omega_bench[k] + h * (command - torque[k]) / jm_actual

        true_mid_speed = 0.5 * (omega_bench[k] + omega_bench[k + 1])
        motor_work_true += command * true_mid_speed * h
        measured_next_speed = omega_bench[k + 1] + speed_noise[k + 1]
        motor_work_est += command * 0.5 * (
            previous_measured_speed + measured_next_speed
        ) * h
        previous_measured_speed = measured_next_speed

    # Complete the internally propagated reference at the final sample.
    omega_reference[n] = omega_reference[n - 1] - h * measured_torque[n - 1] / je_model

    road_brake_energy = float(
        np.sum(torque * 0.5 * (omega_road[:-1] + omega_road[1:]) * h)
    )
    bench_brake_energy = float(
        np.sum(torque * 0.5 * (omega_bench[:-1] + omega_bench[1:]) * h)
    )
    error = road_brake_energy - bench_brake_energy
    rpm_scale = 60.0 / (2.0 * math.pi)
    speed_difference = (omega_bench - omega_road) * rpm_scale
    current = 1.5 * motor_torque

    return {
        "kind": kind,
        "omega_road": omega_road,
        "omega_bench": omega_bench,
        "motor_torque": motor_torque,
        "current": current,
        "road_brake_energy_j": road_brake_energy,
        "bench_brake_energy_j": bench_brake_energy,
        "energy_error_j": float(error),
        "absolute_relative_energy_error_pct": float(abs(error) / road_brake_energy * 100.0),
        "speed_rmse_rpm": float(np.sqrt(np.mean(speed_difference**2))),
        "max_abs_speed_error_rpm": float(np.max(np.abs(speed_difference))),
        "final_speed_error_rpm": float(speed_difference[-1]),
        "peak_abs_current_a": float(np.max(np.abs(current))),
        "rms_current_a": float(np.sqrt(np.mean(current**2))),
        "motor_work_j": float(motor_work_true),
    }


def public_metrics(simulation: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "kind",
        "road_brake_energy_j",
        "bench_brake_energy_j",
        "energy_error_j",
        "absolute_relative_energy_error_pct",
        "speed_rmse_rpm",
        "max_abs_speed_error_rpm",
        "final_speed_error_rpm",
        "peak_abs_current_a",
        "rms_current_a",
        "motor_work_j",
    ]
    return {key: simulation[key] for key in keys}


def grouped_mean(values: np.ndarray, group: int) -> np.ndarray:
    usable = len(values) // group * group
    return values[:usable].reshape(-1, group).mean(axis=1)


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    PAPER.mkdir(parents=True, exist_ok=True)

    metadata, time, torque, rpm = load_data()
    omega = rpm * 2.0 * math.pi / 60.0
    dt = np.diff(time)

    finite = bool(np.all(np.isfinite(np.column_stack([time, torque, rpm]))))
    time_strict = bool(np.all(dt > 0.0))
    uniform_10ms = bool(np.allclose(dt, 0.01, atol=1e-12, rtol=0.0))
    metadata_match = int(metadata["sample_count"]) == len(time)
    speed_increase = np.diff(rpm) > 0.0
    audit = {
        "status": "pass" if finite and time_strict and uniform_10ms and metadata_match else "fail",
        "numeric_rows": int(len(time)),
        "missing_or_nonfinite_count": int(
            np.size(np.column_stack([time, torque, rpm]))
            - np.count_nonzero(np.isfinite(np.column_stack([time, torque, rpm])))
        ),
        "time_start_s": float(time[0]),
        "time_end_s": float(time[-1]),
        "time_step_s": float(np.median(dt)),
        "uniform_10ms_status": "pass" if uniform_10ms else "fail",
        "torque_range_nm": [float(np.min(torque)), float(np.max(torque))],
        "speed_range_rpm": [float(np.min(rpm)), float(np.max(rpm))],
        "local_speed_increase_count": int(np.count_nonzero(speed_increase)),
        "largest_local_speed_increase_rpm": float(
            np.max(np.diff(rpm)[speed_increase]) if np.any(speed_increase) else 0.0
        ),
        "local_speed_increase_status": "needs_review" if np.any(speed_increase) else "pass",
        "nominal_initial_minus_observed_rpm": float(
            metadata["initial_speed_rpm_nominal"] - rpm[0]
        ),
        "nominal_final_minus_observed_rpm": float(
            metadata["final_speed_rpm_nominal"] - rpm[-1]
        ),
        "formatted_trailing_rows": int(
            metadata["used_range_rows"] - metadata["last_numeric_row"]
        ),
    }

    # Questions 1--3.
    gravity = 9.8
    standard_gravity = 9.80665
    wheel_radius = 0.286
    wheel_load = 6230.0
    equivalent_inertia = wheel_load / gravity * wheel_radius**2
    equivalent_inertia_standard_g = wheel_load / standard_gravity * wheel_radius**2

    rho = 7810.0
    outer_radius = 0.5
    inner_radius = 0.1
    thicknesses = [0.0392, 0.0784, 0.1568]
    flywheels = [
        flywheel_inertia(rho, thickness, outer_radius, inner_radius)
        for thickness in thicknesses
    ]
    base_inertia = 10.0
    configurations: list[dict[str, Any]] = []
    for bits in itertools.product([0, 1], repeat=3):
        jm = base_inertia + sum(bit * inertia for bit, inertia in zip(bits, flywheels))
        compensation = equivalent_inertia - jm
        configurations.append(
            {
                "flywheel_1": bits[0],
                "flywheel_2": bits[1],
                "flywheel_3": bits[2],
                "mechanical_inertia_kg_m2": jm,
                "compensation_inertia_kg_m2": compensation,
                "within_compensation_range": "pass" if -30.0 <= compensation <= 30.0 else "fail",
            }
        )
    configurations.sort(key=lambda row: row["mechanical_inertia_kg_m2"])
    feasible = [row for row in configurations if row["within_compensation_range"] == "pass"]
    chosen = min(feasible, key=lambda row: abs(row["compensation_inertia_kg_m2"]))

    initial_vehicle_speed = 50.0 / 3.6
    initial_omega = initial_vehicle_speed / wheel_radius
    angular_acceleration = -initial_omega / 5.0
    compensation_inertia = float(chosen["compensation_inertia_kg_m2"])
    motor_torque = -compensation_inertia * angular_acceleration
    current = 1.5 * motor_torque
    rounded_current = 1.5 * 12.0 * (-angular_acceleration)
    brake_torque = -equivalent_inertia * angular_acceleration

    q1 = {
        "gravity_m_s2": gravity,
        "equivalent_mass_kg": wheel_load / gravity,
        "equivalent_inertia_kg_m2": equivalent_inertia,
        "standard_gravity_sensitivity_kg_m2": equivalent_inertia_standard_g,
        "standard_gravity_relative_change_pct": (
            equivalent_inertia_standard_g / equivalent_inertia - 1.0
        )
        * 100.0,
    }
    q2 = {
        "flywheel_inertias_kg_m2": flywheels,
        "mechanical_inertias_kg_m2": [
            row["mechanical_inertia_kg_m2"] for row in configurations
        ],
        "feasible_mechanical_inertias_kg_m2": [
            row["mechanical_inertia_kg_m2"] for row in feasible
        ],
        "chosen_mechanical_inertia_kg_m2": chosen["mechanical_inertia_kg_m2"],
        "required_compensation_inertia_kg_m2": compensation_inertia,
        "engineering_rounded_values": {
            "flywheels_kg_m2": [30.0, 60.0, 120.0],
            "mechanical_inertias_kg_m2": [10.0 + 30.0 * k for k in range(8)],
            "chosen_mechanical_inertia_kg_m2": 40.0,
            "required_compensation_inertia_kg_m2": 12.0,
        },
    }
    q3 = {
        "motor_current_per_torque_a_per_nm": 1.5,
        "initial_speed_m_s": initial_vehicle_speed,
        "initial_angular_speed_rad_s": initial_omega,
        "constant_angular_acceleration_rad_s2": angular_acceleration,
        "road_brake_torque_nm": brake_torque,
        "motor_compensation_torque_nm": motor_torque,
        "motor_current_a": current,
        "engineering_rounded_current_a": rounded_current,
        "continuous_law": "u_m=-(J_e-J_m)*domega/dt=((J_e-J_m)/J_e)*T_b; i=1.5*u_m",
    }

    # Question 4: primary energy audit plus numerical-convention sensitivity.
    je4 = float(metadata["equivalent_inertia_kg_m2"])
    jm4 = float(metadata["mechanical_inertia_kg_m2"])
    omega_initial_nominal = float(metadata["initial_speed_rpm_nominal"]) * 2.0 * math.pi / 60.0
    omega_final_nominal = float(metadata["final_speed_rpm_nominal"]) * 2.0 * math.pi / 60.0
    target_energy_nominal = 0.5 * je4 * (
        omega_initial_nominal**2 - omega_final_nominal**2
    )
    target_energy_observed = 0.5 * je4 * (omega[0] ** 2 - omega[-1] ** 2)
    power = torque * omega
    energy_left = float(np.sum(power[:-1] * dt))
    energy_right = float(np.sum(power[1:] * dt))
    energy_trapezoid = float(np.sum(0.5 * (power[:-1] + power[1:]) * dt))
    energy_inclusive_hold = float(np.sum(power) * 0.01)
    inferred_motor_work = energy_trapezoid - 0.5 * jm4 * (omega[0] ** 2 - omega[-1] ** 2)
    required_motor_work = 0.5 * (je4 - jm4) * (omega[0] ** 2 - omega[-1] ** 2)
    primary_error = target_energy_nominal - energy_trapezoid

    quadratures = {
        "left_endpoint_467_intervals": energy_left,
        "trapezoid_467_intervals": energy_trapezoid,
        "right_endpoint_467_intervals": energy_right,
        "left_hold_468_intervals_interpretive_bound": energy_inclusive_hold,
    }
    q4_sensitivity: list[dict[str, Any]] = []
    for endpoint_name, target in [
        ("nominal_endpoints", target_energy_nominal),
        ("observed_endpoints", target_energy_observed),
    ]:
        for method, observed_energy in quadratures.items():
            error = target - observed_energy
            q4_sensitivity.append(
                {
                    "endpoint_basis": endpoint_name,
                    "quadrature": method,
                    "target_energy_j": target,
                    "bench_brake_energy_j": observed_energy,
                    "signed_error_j": error,
                    "absolute_relative_error_pct": abs(error) / target * 100.0,
                }
            )

    q4 = {
        "status": "needs_review",
        "status_reason": "The problem gives no acceptance threshold; the computed under-dissipation is quantitative, but pass/fail requires an engineering limit.",
        "sample_count": int(len(time)),
        "duration_between_first_and_last_sample_s": float(time[-1] - time[0]),
        "target_energy_nominal_j": target_energy_nominal,
        "target_energy_observed_endpoints_j": target_energy_observed,
        "bench_brake_energy_trapezoid_j": energy_trapezoid,
        "signed_energy_error_target_minus_bench_j": primary_error,
        "absolute_relative_energy_error_pct": abs(primary_error) / target_energy_nominal * 100.0,
        "inferred_motor_work_j": inferred_motor_work,
        "required_motor_work_observed_endpoints_j": required_motor_work,
        "compensation_energy_completion_pct": inferred_motor_work / required_motor_work * 100.0,
        "quadrature_relative_error_range_pct_nominal_endpoints": [
            min(
                abs(row["signed_error_j"]) / row["target_energy_j"] * 100.0
                for row in q4_sensitivity
                if row["endpoint_basis"] == "nominal_endpoints"
            ),
            max(
                abs(row["signed_error_j"]) / row["target_energy_j"] * 100.0
                for row in q4_sensitivity
                if row["endpoint_basis"] == "nominal_endpoints"
            ),
        ],
    }

    # Questions 5--6: compare three bounded candidates on a causal fixed-torque replay.
    interval_torque = torque[:-1]
    h = float(np.median(dt))
    omega0_replay = omega[0]
    controller_names = [
        "baseline_no_compensation",
        "delayed_torque_feedback",
        "predictive_energy_feedback",
    ]
    simulations = {
        name: simulate_controller(interval_torque, h, omega0_replay, je4, jm4, name)
        for name in controller_names
    }
    comparison = [public_metrics(simulations[name]) for name in controller_names]

    # Stress tests are engineering extensions beyond the statement's no-noise idealization.
    stress_rows: list[dict[str, Any]] = []
    shared_torque_noise = RNG.normal(0.0, 2.5, len(interval_torque))
    shared_speed_noise = RNG.normal(
        0.0, 0.5 * 2.0 * math.pi / 60.0, len(interval_torque) + 1
    )
    for name in ["delayed_torque_feedback", "predictive_energy_feedback"]:
        for scenario, actual_jm, torque_sigma, speed_sigma in [
            ("nominal", jm4, 0.0, 0.0),
            ("mechanical_inertia_minus_5pct", 0.95 * jm4, 0.0, 0.0),
            ("mechanical_inertia_plus_5pct", 1.05 * jm4, 0.0, 0.0),
            ("measurement_noise_seed_2009", jm4, 2.5, 0.5),
        ]:
            torque_noise = (
                shared_torque_noise if torque_sigma > 0.0 else np.zeros(len(interval_torque))
            )
            speed_noise = (
                shared_speed_noise
                if speed_sigma > 0.0
                else np.zeros(len(interval_torque) + 1)
            )
            sim = simulate_controller(
                interval_torque,
                h,
                omega0_replay,
                je4,
                actual_jm,
                name,
                je_model=je4,
                jm_model=jm4,
                torque_noise=torque_noise,
                speed_noise=speed_noise,
            )
            row = public_metrics(sim)
            row["scenario"] = scenario
            row["numerical_status"] = (
                "pass" if np.isfinite(row["energy_error_j"]) else "fail"
            )
            row["acceptance_status"] = "needs_review"
            stress_rows.append(row)

        for group in [2, 5]:
            grouped_torque = grouped_mean(interval_torque, group)
            sim = simulate_controller(
                grouped_torque,
                h * group,
                omega0_replay,
                je4,
                jm4,
                name,
            )
            row = public_metrics(sim)
            row["scenario"] = f"sample_period_{int(h * group * 1000)}ms"
            row["numerical_status"] = (
                "pass" if np.isfinite(row["energy_error_j"]) else "fail"
            )
            row["acceptance_status"] = "needs_review"
            stress_rows.append(row)

    q56 = {
        "replay_scope": "deterministic model-in-the-loop replay using the measured brake torque as an exogenous input; not a physical bench experiment",
        "status": "needs_review",
        "status_reason": "Simulation checks internal behavior, while actuator limits and physical validation data are absent.",
        "time_step_s": h,
        "interval_count": int(len(interval_torque)),
        "controller_parameters": {
            "beta_speed": 0.20,
            "lambda_energy": 0.05,
            "torque_filter_alpha": 0.50,
            "torque_predictor_gain": 0.50,
            "current_per_torque_a_per_nm": 1.5,
            "actuator_saturation": "needs_review: no current/torque limit supplied",
        },
        "candidate_comparison": comparison,
    }

    # Structured tables.
    write_csv(RESULTS / "<SOURCE_FILE_REDACTED>", configurations)
    write_csv(RESULTS / "<SOURCE_FILE_REDACTED>", q4_sensitivity)
    write_csv(RESULTS / "<SOURCE_FILE_REDACTED>", comparison)
    write_csv(RESULTS / "<SOURCE_FILE_REDACTED>", stress_rows)

    cumulative_brake_energy = np.zeros(len(time))
    cumulative_brake_energy[1:] = np.cumsum(0.5 * (power[:-1] + power[1:]) * dt)
    q4_series = [
        {
            "time_s": float(time[k]),
            "torque_Nm": float(torque[k]),
            "speed_rpm": float(rpm[k]),
            "angular_speed_rad_s": float(omega[k]),
            "brake_power_W": float(power[k]),
            "cumulative_brake_energy_J": float(cumulative_brake_energy[k]),
        }
        for k in range(len(time))
    ]
    write_csv(RESULTS / "<SOURCE_FILE_REDACTED>", q4_series)

    trajectory_rows: list[dict[str, Any]] = []
    rpm_scale = 60.0 / (2.0 * math.pi)
    for k in range(len(interval_torque) + 1):
        row: dict[str, Any] = {
            "time_s": float(k * h),
            "brake_torque_Nm": float(torque[k]),
            "road_speed_rpm": float(
                simulations["baseline_no_compensation"]["omega_road"][k] * rpm_scale
            ),
        }
        for name in controller_names:
            row[f"{name}_speed_rpm"] = float(
                simulations[name]["omega_bench"][k] * rpm_scale
            )
            row[f"{name}_current_A"] = (
                float(simulations[name]["current"][k])
                if k < len(interval_torque)
                else ""
            )
        trajectory_rows.append(row)
    write_csv(RESULTS / "<SOURCE_FILE_REDACTED>", trajectory_rows)

    # Figures.
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 1, figsize=(8.0, 6.0), sharex=True)
    axes[0].plot(time, torque, color="#b23a48", linewidth=1.1)
    axes[0].set_ylabel("Brake torque (N m)")
    axes[0].set_title("Measured experiment series")
    axes[1].plot(time, rpm, color="#22577a", linewidth=1.1, label="observed")
    axes[1].axhline(metadata["initial_speed_rpm_nominal"], color="gray", linestyle="--", linewidth=0.8)
    axes[1].axhline(metadata["final_speed_rpm_nominal"], color="gray", linestyle="--", linewidth=0.8)
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Speed (rpm)")
    axes[1].legend(loc="best")
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.9))
    axes[0].bar(
        ["road target", "bench brake"],
        [target_energy_nominal / 1000.0, energy_trapezoid / 1000.0],
        color=["#22577a", "#b23a48"],
    )
    axes[0].set_ylabel("Energy (kJ)")
    axes[0].set_title("Question 4 energy criterion")
    axes[1].bar(
        ["required", "inferred"],
        [required_motor_work / 1000.0, inferred_motor_work / 1000.0],
        color=["#57a773", "#f2a541"],
    )
    axes[1].set_ylabel("Motor work (kJ)")
    axes[1].set_title("Compensation work")
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    replay_time = np.arange(len(interval_torque) + 1) * h
    fig, axes = plt.subplots(2, 1, figsize=(8.0, 6.2), sharex=True)
    colors = {
        "baseline_no_compensation": "#777777",
        "delayed_torque_feedback": "#f2a541",
        "predictive_energy_feedback": "#22577a",
    }
    labels = {
        "baseline_no_compensation": "no compensation",
        "delayed_torque_feedback": "one-step torque feedback",
        "predictive_energy_feedback": "predictive + energy feedback",
    }
    for name in controller_names:
        sim = simulations[name]
        speed_error = (sim["omega_bench"] - sim["omega_road"]) * 60.0 / (2.0 * math.pi)
        axes[0].plot(replay_time, speed_error, label=labels[name], color=colors[name], linewidth=1.0)
    axes[0].set_ylabel("Bench - road (rpm)")
    axes[0].set_title("Causal controller replay")
    axes[0].legend(loc="best")
    for name in ["delayed_torque_feedback", "predictive_energy_feedback"]:
        axes[1].plot(
            replay_time[:-1],
            simulations[name]["current"],
            label=labels[name],
            color=colors[name],
            linewidth=1.0,
        )
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Command current (A)")
    axes[1].legend(loc="best")
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.2, 4.4))
    scenarios = list(dict.fromkeys(row["scenario"] for row in stress_rows))
    x = np.arange(len(scenarios))
    width = 0.36
    for offset, name, color in [
        (-width / 2, "delayed_torque_feedback", "#f2a541"),
        (width / 2, "predictive_energy_feedback", "#22577a"),
    ]:
        values = [
            next(
                row["absolute_relative_energy_error_pct"]
                for row in stress_rows
                if row["scenario"] == scenario and row["kind"] == name
            )
            for scenario in scenarios
        ]
        ax.bar(x + offset, values, width, label=labels[name], color=color)
    ax.set_xticks(x, [s.replace("_", "\n") for s in scenarios], fontsize=8)
    ax.set_ylabel("Absolute energy error (%)")
    ax.set_title("Engineering stress tests (model-in-the-loop)")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(FIGURES / "<SOURCE_FILE_REDACTED>", dpi=180)
    plt.close(fig)

    summary = {
        "status": "pass" if audit["status"] == "pass" else "fail",
        "scope_status": "needs_review",
        "scope_note": "Numerical reproduction passed; physical controller acceptance remains unverified.",
        "random_seed": SEED,
        "data_audit": audit,
        "question_1": q1,
        "question_2": q2,
        "question_3": q3,
        "question_4": q4,
        "questions_5_6": q56,
    }
    write_json(RESULTS / "summary.json", summary)

    macros = f"""% Generated by code/analyze.py; do not edit numerical values by hand.
\\newcommand{{\\QOneInertia}}{{{equivalent_inertia:.3f}}}
\\newcommand{{\\QOneStandardGravityInertia}}{{{equivalent_inertia_standard_g:.3f}}}
\\newcommand{{\\QTwoFlywheelOne}}{{{flywheels[0]:.3f}}}
\\newcommand{{\\QTwoFlywheelTwo}}{{{flywheels[1]:.3f}}}
\\newcommand{{\\QTwoFlywheelThree}}{{{flywheels[2]:.3f}}}
\\newcommand{{\\QTwoMechanical}}{{{chosen['mechanical_inertia_kg_m2']:.3f}}}
\\newcommand{{\\QTwoCompensation}}{{{compensation_inertia:.3f}}}
\\newcommand{{\\QTwoMechanicalList}}{{10.000, 40.008, 70.017, 100.025, 130.033, 160.042, 190.050, 220.058}}
\\newcommand{{\\QThreeCurrent}}{{{current:.2f}}}
\\newcommand{{\\QThreeRoundedCurrent}}{{{rounded_current:.2f}}}
\\newcommand{{\\QThreeMotorTorque}}{{{motor_torque:.3f}}}
\\newcommand{{\\QThreeInitialOmega}}{{{initial_omega:.3f}}}
\\newcommand{{\\QThreeAngularDeceleration}}{{{-angular_acceleration:.3f}}}
\\newcommand{{\\QFourTargetEnergy}}{{{target_energy_nominal / 1000.0:.3f}}}
\\newcommand{{\\QFourBenchEnergy}}{{{energy_trapezoid / 1000.0:.3f}}}
\\newcommand{{\\QFourEnergyError}}{{{primary_error / 1000.0:.3f}}}
\\newcommand{{\\QFourRelativeError}}{{{abs(primary_error) / target_energy_nominal * 100.0:.3f}}}
\\newcommand{{\\QFourCompensationCompletion}}{{{inferred_motor_work / required_motor_work * 100.0:.2f}}}
\\newcommand{{\\QFourInferredMotorWork}}{{{inferred_motor_work / 1000.0:.3f}}}
\\newcommand{{\\QFourRequiredMotorWork}}{{{required_motor_work / 1000.0:.3f}}}
\\newcommand{{\\QFourSensitivityLow}}{{{q4['quadrature_relative_error_range_pct_nominal_endpoints'][0]:.3f}}}
\\newcommand{{\\QFourSensitivityHigh}}{{{q4['quadrature_relative_error_range_pct_nominal_endpoints'][1]:.3f}}}
\\newcommand{{\\QFiveReplayError}}{{{simulations['delayed_torque_feedback']['absolute_relative_energy_error_pct']:.4f}}}
\\newcommand{{\\QFiveReplayRMSE}}{{{simulations['delayed_torque_feedback']['speed_rmse_rpm']:.4f}}}
\\newcommand{{\\QSixReplayError}}{{{simulations['predictive_energy_feedback']['absolute_relative_energy_error_pct']:.6f}}}
\\newcommand{{\\QSixReplayRMSE}}{{{simulations['predictive_energy_feedback']['speed_rmse_rpm']:.5f}}}
\\newcommand{{\\QSixPeakCurrent}}{{{simulations['predictive_energy_feedback']['peak_abs_current_a']:.2f}}}
\\newcommand{{\\QSixNoisePeakCurrent}}{{{next(row['peak_abs_current_a'] for row in stress_rows if row['kind'] == 'predictive_energy_feedback' and row['scenario'] == 'measurement_noise_seed_2009'):.2f}}}
"""
    (PAPER / "generated-results.tex").write_text(macros, encoding="utf-8")

    output_paths = [
        RESULTS / "summary.json",
        RESULTS / "<SOURCE_FILE_REDACTED>",
        RESULTS / "<SOURCE_FILE_REDACTED>",
        RESULTS / "<SOURCE_FILE_REDACTED>",
        RESULTS / "<SOURCE_FILE_REDACTED>",
        RESULTS / "<SOURCE_FILE_REDACTED>",
        RESULTS / "<SOURCE_FILE_REDACTED>",
        FIGURES / "<SOURCE_FILE_REDACTED>",
        FIGURES / "<SOURCE_FILE_REDACTED>",
        FIGURES / "<SOURCE_FILE_REDACTED>",
        FIGURES / "<SOURCE_FILE_REDACTED>",
        PAPER / "generated-results.tex",
    ]
    manifest = {
        "status": "pass",
        "random_seed": SEED,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": {
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "input_sha256": {
            str(RAW_PROBLEM.relative_to(ROOT)): sha256(RAW_PROBLEM),
            str(RAW_DATA.relative_to(ROOT)): sha256(RAW_DATA),
            str(DATA_CSV.relative_to(ROOT)): sha256(DATA_CSV),
            str(METADATA_JSON.relative_to(ROOT)): sha256(METADATA_JSON),
        },
        "generated_output_sha256": {
            str(path.relative_to(ROOT)): sha256(path) for path in output_paths
        },
    }
    write_json(RESULTS / "run-manifest.json", manifest)
    print("[pass] analysis, controller replay, tables, figures, and result macros generated")


if __name__ == "__main__":
    main()
