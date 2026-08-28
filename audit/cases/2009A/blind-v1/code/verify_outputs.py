#!/usr/bin/env python3
"""Independent numerical and artifact checks for generated solve outputs."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
PAPER = ROOT / "paper"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def close(a: float, b: float, *, atol: float = 1e-8, rtol: float = 1e-10) -> bool:
    return abs(a - b) <= atol + rtol * max(abs(a), abs(b))


def main() -> None:
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((RESULTS / "run-manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((RESULTS / "input-metadata.json").read_text(encoding="utf-8"))
    experiment = rows(RESULTS / "<SOURCE_FILE_REDACTED>")
    comparison = {row["kind"]: row for row in rows(RESULTS / "<SOURCE_FILE_REDACTED>")}
    trajectories = rows(RESULTS / "<SOURCE_FILE_REDACTED>")
    checks: list[dict[str, str | float]] = []

    def record(name: str, condition: bool, detail: str) -> None:
        checks.append(
            {"name": name, "status": "pass" if condition else "fail", "detail": detail}
        )

    # Actual input hashes, not asserted placeholders.
    for relative, expected in manifest["input_sha256"].items():
        path = ROOT / relative
        actual = digest(path)
        record(
            f"sha256:{relative}",
            actual == expected,
            f"actual={actual}; expected={expected}",
        )

    # Question 1: derive from translational energy independently.
    load = 6230.0
    radius = 0.286
    gravity = 9.8
    mass = load / gravity
    inertia = mass * radius**2
    omega_test = 17.3
    translational = 0.5 * mass * (radius * omega_test) ** 2
    rotational = 0.5 * inertia * omega_test**2
    q1_value = float(summary["question_1"]["equivalent_inertia_kg_m2"])
    record("q1:formula", close(inertia, q1_value), f"recomputed={inertia:.12f}")
    record(
        "q1:energy-equivalence",
        close(translational, rotational),
        f"translational={translational:.12f}; rotational={rotational:.12f}",
    )

    # Question 2: ring formula and all eight subset sums.
    rho = 7810.0
    ro = 0.5
    ri = 0.1
    thicknesses = [0.0392, 0.0784, 0.1568]
    flywheels = [
        0.5 * math.pi * rho * h * (ro**4 - ri**4) for h in thicknesses
    ]
    reported_flywheels = [
        float(value) for value in summary["question_2"]["flywheel_inertias_kg_m2"]
    ]
    record(
        "q2:ring-inertias",
        all(close(a, b) for a, b in zip(flywheels, reported_flywheels)),
        f"recomputed={flywheels}",
    )
    subset_sums = sorted(
        10.0
        + bit0 * flywheels[0]
        + bit1 * flywheels[1]
        + bit2 * flywheels[2]
        for bit0 in [0, 1]
        for bit1 in [0, 1]
        for bit2 in [0, 1]
    )
    reported_sums = [
        float(value) for value in summary["question_2"]["mechanical_inertias_kg_m2"]
    ]
    record(
        "q2:eight-unique-configurations",
        len(subset_sums) == len(set(round(value, 10) for value in subset_sums)) == 8
        and all(close(a, b) for a, b in zip(subset_sums, reported_sums)),
        f"count={len(subset_sums)}",
    )
    feasible = [value for value in subset_sums if -30.0 <= inertia - value <= 30.0]
    selected = min(feasible, key=lambda value: abs(inertia - value))
    record(
        "q2:minimum-absolute-feasible-compensation",
        close(selected, float(summary["question_2"]["chosen_mechanical_inertia_kg_m2"])),
        f"feasible={feasible}; selected={selected:.12f}",
    )

    # Question 3: independently check both equations of motion and current gain.
    q3 = summary["question_3"]
    alpha = float(q3["constant_angular_acceleration_rad_s2"])
    brake = float(q3["road_brake_torque_nm"])
    motor = float(q3["motor_compensation_torque_nm"])
    jm = selected
    road_residual = inertia * alpha + brake
    bench_residual = jm * alpha - (motor - brake)
    record(
        "q3:road-dynamics",
        abs(road_residual) < 1e-9,
        f"residual={road_residual:.3e}",
    )
    record(
        "q3:bench-dynamics",
        abs(bench_residual) < 1e-9,
        f"residual={bench_residual:.3e}",
    )
    record(
        "q3:current-gain",
        close(float(q3["motor_current_a"]), 1.5 * motor),
        f"recomputed_current={1.5 * motor:.12f}",
    )

    # Data structure and Question 4 energy integral, using scalar loops only.
    t = [float(row["time_s"]) for row in experiment]
    torque = [float(row["torque_Nm"]) for row in experiment]
    rpm = [float(row["speed_rpm"]) for row in experiment]
    omega = [value * 2.0 * math.pi / 60.0 for value in rpm]
    record(
        "data:row-count",
        len(experiment) == int(metadata["sample_count"]) == 468,
        f"rows={len(experiment)}",
    )
    step_errors = [abs((t[index + 1] - t[index]) - 0.01) for index in range(len(t) - 1)]
    record(
        "data:uniform-10ms",
        max(step_errors) < 1e-12,
        f"max_step_error={max(step_errors):.3e}",
    )
    brake_energy = 0.0
    for index in range(len(t) - 1):
        p0 = torque[index] * omega[index]
        p1 = torque[index + 1] * omega[index + 1]
        brake_energy += 0.5 * (p0 + p1) * (t[index + 1] - t[index])
    q4 = summary["question_4"]
    record(
        "q4:trapezoid-energy",
        close(brake_energy, float(q4["bench_brake_energy_trapezoid_j"]), atol=1e-7),
        f"recomputed={brake_energy:.12f}",
    )
    je4 = float(metadata["equivalent_inertia_kg_m2"])
    jm4 = float(metadata["mechanical_inertia_kg_m2"])
    target = 0.5 * je4 * (
        (float(metadata["initial_speed_rpm_nominal"]) * 2.0 * math.pi / 60.0) ** 2
        - (float(metadata["final_speed_rpm_nominal"]) * 2.0 * math.pi / 60.0) ** 2
    )
    record(
        "q4:target-energy",
        close(target, float(q4["target_energy_nominal_j"])),
        f"recomputed={target:.12f}",
    )
    kinetic_drop = 0.5 * jm4 * (omega[0] ** 2 - omega[-1] ** 2)
    inferred_work = float(q4["inferred_motor_work_j"])
    record(
        "q4:bench-energy-balance",
        close(kinetic_drop + inferred_work, brake_energy, atol=1e-7),
        f"kinetic_drop+motor_work={kinetic_drop + inferred_work:.12f}",
    )

    # Independent recurrence and energy checks for every replay trajectory.
    names = [
        "baseline_no_compensation",
        "delayed_torque_feedback",
        "predictive_energy_feedback",
    ]
    road_speed = [float(row["road_speed_rpm"]) * 2.0 * math.pi / 60.0 for row in trajectories]
    road_residuals = []
    for index in range(len(trajectories) - 1):
        expected = road_speed[index] - 0.01 * torque[index] / je4
        road_residuals.append(road_speed[index + 1] - expected)
    record(
        "replay:road-recurrence",
        max(abs(value) for value in road_residuals) < 1e-11,
        f"max_residual={max(abs(value) for value in road_residuals):.3e}",
    )
    for name in names:
        bench_speed = [
            float(row[f"{name}_speed_rpm"]) * 2.0 * math.pi / 60.0
            for row in trajectories
        ]
        current = [
            float(row[f"{name}_current_A"])
            for row in trajectories[:-1]
        ]
        residuals = []
        for index in range(len(current)):
            expected = bench_speed[index] + 0.01 * (
                current[index] / 1.5 - torque[index]
            ) / jm4
            residuals.append(bench_speed[index + 1] - expected)
        record(
            f"replay:{name}:bench-recurrence",
            max(abs(value) for value in residuals) < 1e-11,
            f"max_residual={max(abs(value) for value in residuals):.3e}",
        )
        road_energy = sum(
            torque[index]
            * 0.5
            * (road_speed[index] + road_speed[index + 1])
            * 0.01
            for index in range(len(current))
        )
        bench_energy = sum(
            torque[index]
            * 0.5
            * (bench_speed[index] + bench_speed[index + 1])
            * 0.01
            for index in range(len(current))
        )
        reported = comparison[name]
        record(
            f"replay:{name}:energy-metrics",
            close(road_energy, float(reported["road_brake_energy_j"]), atol=1e-7)
            and close(bench_energy, float(reported["bench_brake_energy_j"]), atol=1e-7),
            f"road={road_energy:.9f}; bench={bench_energy:.9f}",
        )
    baseline_current = [
        float(row["baseline_no_compensation_current_A"]) for row in trajectories[:-1]
    ]
    record(
        "replay:baseline-command-zero",
        max(abs(value) for value in baseline_current) == 0.0,
        f"max_abs={max(abs(value) for value in baseline_current):.3e}",
    )
    delayed_current = [
        float(row["delayed_torque_feedback_current_A"]) for row in trajectories[:-1]
    ]
    ratio = (je4 - jm4) / je4
    delayed_errors = [abs(delayed_current[0])]
    delayed_errors.extend(
        abs(delayed_current[index] / 1.5 - ratio * torque[index - 1])
        for index in range(1, len(delayed_current))
    )
    record(
        "q5:one-step-control-law",
        max(delayed_errors) < 1e-10,
        f"max_torque_command_error={max(delayed_errors):.3e}",
    )

    # Generated LaTeX macros must equal the structured summary at displayed precision.
    macro_text = (PAPER / "generated-results.tex").read_text(encoding="utf-8")
    macros = {
        key: float(value)
        for key, value in re.findall(r"\\newcommand\{\\([^}]+)\}\{([-+0-9.]+)\}", macro_text)
    }
    macro_expectations = {
        "QOneInertia": round(float(summary["question_1"]["equivalent_inertia_kg_m2"]), 3),
        "QThreeCurrent": round(float(summary["question_3"]["motor_current_a"]), 2),
        "QFourTargetEnergy": round(float(q4["target_energy_nominal_j"]) / 1000.0, 3),
        "QFourBenchEnergy": round(float(q4["bench_brake_energy_trapezoid_j"]) / 1000.0, 3),
        "QFourRelativeError": round(float(q4["absolute_relative_energy_error_pct"]), 3),
    }
    record(
        "paper:generated-macros",
        all(close(macros.get(key, math.nan), value) for key, value in macro_expectations.items()),
        f"checked={list(macro_expectations)}",
    )

    for figure_name in [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]:
        path = FIGURES / figure_name
        valid_png = (
            path.is_file()
            and path.stat().st_size > 10_000
            and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        )
        record(
            f"figure:{figure_name}",
            valid_png,
            f"bytes={path.stat().st_size if path.exists() else 0}",
        )

    failures = [check for check in checks if check["status"] == "fail"]
    verification = {
        "status": "pass" if not failures else "fail",
        "checks": checks,
        "physical_validation_status": "needs_review",
        "physical_validation_note": "No actuator limit or independent physical-bench run is supplied.",
    }
    (RESULTS / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if failures:
        print(f"[fail] {len(failures)} verification check(s) failed")
        sys.exit(1)
    print(f"[pass] {len(checks)} independent numerical/artifact checks passed")


if __name__ == "__main__":
    main()
