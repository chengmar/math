"""Standalone raw-input verification for the blind-revision solution.

This module deliberately does not import ``thermal_model`` or ``run_all``.
It parses attachment 1 again, independently assembles the finite-volume ODE,
checks selected trajectories with Radau, and regenerates every point of the
0.1 mm Q2/Q3 design grids.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import openpyxl
import pandas as pd
import yaml
from scipy.integrate import solve_ivp
from scipy.linalg import eigh_tridiagonal


WORKSPACE = Path(__file__).resolve().parents[1]
WORKBOOK = WORKSPACE / "input" / "data" / "<SOURCE_FILE_REDACTED>"
RESULTS = WORKSPACE / "results"
BODY_C = 37.0
CELLS = (6, 24, 12, 12)


@dataclass(frozen=True)
class RawMaterial:
    layer: str
    density: float
    heat_capacity: float
    conductivity: float


@dataclass
class RawSystem:
    capacity: np.ndarray
    conductance: np.ndarray
    matrix: np.ndarray
    forcing: np.ndarray
    h_in: float
    inner_g: float
    steady: np.ndarray
    eigenvalues: np.ndarray
    eigenvectors: np.ndarray
    modal_initial: np.ndarray

    def cells_at(self, times_s: float | Iterable[float]) -> np.ndarray:
        times = np.atleast_1d(np.asarray(times_s, dtype=float))
        modes = np.exp(np.outer(times, self.eigenvalues)) * self.modal_initial
        transformed = modes @ self.eigenvectors.T
        return self.steady[None, :] + transformed / np.sqrt(self.capacity)[None, :]

    def skin_at(self, times_s: float | Iterable[float]) -> np.ndarray:
        cells = self.cells_at(times_s)
        return BODY_C + (self.inner_g / self.h_in) * (cells[:, -1] - BODY_C)


def load_materials() -> tuple[RawMaterial, ...]:
    workbook = openpyxl.load_workbook(WORKBOOK, data_only=True, read_only=True)
    rows = list(workbook["附件1"].iter_rows(min_row=3, max_row=6, values_only=True))
    workbook.close()
    materials = tuple(
        RawMaterial(
            str(row[0]).removesuffix("层"),
            float(row[1]),
            float(row[2]),
            float(row[3]),
        )
        for row in rows
    )
    if tuple(item.layer for item in materials) != ("I", "II", "III", "IV"):
        raise ValueError("attachment 1 layer order is invalid")
    return materials


def thomas(lower: np.ndarray, diagonal: np.ndarray, upper: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    lower = lower.astype(float, copy=True)
    diagonal = diagonal.astype(float, copy=True)
    upper = upper.astype(float, copy=True)
    rhs = rhs.astype(float, copy=True)
    for index in range(1, len(diagonal)):
        factor = lower[index - 1] / diagonal[index - 1]
        diagonal[index] -= factor * upper[index - 1]
        rhs[index] -= factor * rhs[index - 1]
    solution = np.empty_like(rhs)
    solution[-1] = rhs[-1] / diagonal[-1]
    for index in range(len(diagonal) - 2, -1, -1):
        solution[index] = (rhs[index] - upper[index] * solution[index + 1]) / diagonal[index]
    return solution


def assemble(
    materials: tuple[RawMaterial, ...],
    environment_c: float,
    d2_mm: float,
    d4_mm: float,
    h_out: float,
    h_in: float,
) -> RawSystem:
    thickness_mm = (0.6, float(d2_mm), 3.6, float(d4_mm))
    density: list[float] = []
    heat_capacity: list[float] = []
    conductivity: list[float] = []
    dx: list[float] = []
    for material, width_mm, count in zip(materials, thickness_mm, CELLS):
        density.extend([material.density] * count)
        heat_capacity.extend([material.heat_capacity] * count)
        conductivity.extend([material.conductivity] * count)
        dx.extend([width_mm / 1000.0 / count] * count)
    density_a = np.asarray(density)
    heat_capacity_a = np.asarray(heat_capacity)
    conductivity_a = np.asarray(conductivity)
    dx_a = np.asarray(dx)
    capacity = density_a * heat_capacity_a * dx_a
    internal_g = 1.0 / (
        dx_a[:-1] / (2.0 * conductivity_a[:-1])
        + dx_a[1:] / (2.0 * conductivity_a[1:])
    )
    outer_g = 1.0 / (1.0 / h_out + dx_a[0] / (2.0 * conductivity_a[0]))
    inner_g = 1.0 / (dx_a[-1] / (2.0 * conductivity_a[-1]) + 1.0 / h_in)

    k_diagonal = np.empty(len(capacity))
    k_diagonal[0] = -(outer_g + internal_g[0])
    k_diagonal[-1] = -(internal_g[-1] + inner_g)
    k_diagonal[1:-1] = -(internal_g[:-1] + internal_g[1:])
    forcing_rise = np.zeros(len(capacity))
    forcing_rise[0] = outer_g * (environment_c - BODY_C)
    steady_rise = thomas(
        -internal_g,
        -k_diagonal,
        -internal_g,
        forcing_rise,
    )
    steady = BODY_C + steady_rise

    symmetric_diagonal = k_diagonal / capacity
    symmetric_off = internal_g / np.sqrt(capacity[:-1] * capacity[1:])
    eigenvalues, eigenvectors = eigh_tridiagonal(symmetric_diagonal, symmetric_off)
    modal_initial = eigenvectors.T @ (-np.sqrt(capacity) * steady_rise)

    matrix = np.zeros((len(capacity), len(capacity)))
    matrix[np.arange(len(capacity)), np.arange(len(capacity))] = k_diagonal / capacity
    index = np.arange(len(capacity) - 1)
    matrix[index, index + 1] = internal_g / capacity[:-1]
    matrix[index + 1, index] = internal_g / capacity[1:]
    forcing = np.zeros(len(capacity))
    forcing[0] = outer_g * environment_c / capacity[0]
    forcing[-1] = inner_g * BODY_C / capacity[-1]

    return RawSystem(
        capacity=capacity,
        conductance=internal_g,
        matrix=matrix,
        forcing=forcing,
        h_in=h_in,
        inner_g=inner_g,
        steady=steady,
        eigenvalues=eigenvalues,
        eigenvectors=eigenvectors,
        modal_initial=modal_initial,
    )


def main() -> None:
    materials = load_materials()
    calibrated = yaml.safe_load(
        (RESULTS / "calibrated_parameters.yaml").read_text(encoding="utf-8")
    )
    h_out = float(calibrated["h_out_w_m2k"])
    h_in = float(calibrated["h_in_w_m2k"])
    checks: list[dict[str, object]] = []

    def add(check_id: str, status: str, evidence: dict[str, object]) -> None:
        checks.append({"id": check_id, "status": status, "evidence": evidence})

    q1 = assemble(materials, 75.0, 6.0, 5.0, h_out, h_in)
    sample_times = np.asarray([0.0, 1.0, 60.0, 300.0, 900.0, 1800.0, 3300.0, 4200.0, 5400.0])
    radau = solve_ivp(
        lambda _time, state: q1.matrix @ state + q1.forcing,
        (0.0, 5400.0),
        np.full(len(q1.capacity), BODY_C),
        method="Radau",
        t_eval=sample_times,
        rtol=2e-11,
        atol=2e-12,
    )
    radau_skin = BODY_C + (q1.inner_g / q1.h_in) * (radau.y[-1, :] - BODY_C)
    main_skin = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>").set_index("time_s")
    expected = main_skin.loc[sample_times.astype(int), "predicted_c"].to_numpy(dtype=float)
    radau_difference = float(np.max(np.abs(radau_skin - expected)))
    add(
        "raw_attachment_radau_q1",
        "pass" if radau.success and radau_difference <= 1e-8 else "fail",
        {
            "sample_count": len(sample_times),
            "max_abs_difference_c": radau_difference,
            "radau_success": bool(radau.success),
        },
    )

    resistance = 1.0 / h_out + sum(
        width / 1000.0 / material.conductivity
        for width, material in zip((0.6, 6.0, 3.6, 5.0), materials)
    ) + 1.0 / h_in
    analytic_flux = (75.0 - BODY_C) / resistance
    analytic_skin = BODY_C + analytic_flux / h_in
    modal_steady_skin = float(BODY_C + (q1.inner_g / h_in) * (q1.steady[-1] - BODY_C))
    steady_difference = abs(analytic_skin - modal_steady_skin)
    add(
        "analytic_steady_resistance",
        "pass" if steady_difference <= 1e-10 else "fail",
        {
            "total_resistance_m2k_w": resistance,
            "heat_flux_w_m2": analytic_flux,
            "analytic_skin_c": analytic_skin,
            "assembled_skin_c": modal_steady_skin,
            "absolute_difference_c": steady_difference,
        },
    )

    grid_started = time.perf_counter()
    q2_rows: list[tuple[float, bool]] = []
    for d2_index in range(6, 251):
        d2 = d2_index / 10.0
        system = assemble(materials, 65.0, d2, 5.5, h_out, h_in)
        temperatures = system.skin_at((3300.0, 3600.0))
        q2_rows.append((d2, bool(temperatures[0] <= 44.0 + 1e-9 and temperatures[1] <= 47.0 + 1e-9)))
    q2_feasible = [d2 for d2, feasible in q2_rows if feasible]
    q2_minimum = min(q2_feasible)
    q2_result = yaml.safe_load((RESULTS / "optimization_q2.yaml").read_text(encoding="utf-8"))
    q2_reported = float(q2_result["practical_0p1mm_design"]["d2_mm"])
    add(
        "raw_parameter_q2_full_grid",
        "pass" if abs(q2_minimum - q2_reported) <= 1e-12 else "fail",
        {
            "evaluated_points": len(q2_rows),
            "minimum_feasible_d2_mm": q2_minimum,
            "reported_d2_mm": q2_reported,
        },
    )

    q3_feasible: list[tuple[float, float, float]] = []
    evaluated_q3 = 0
    for d4_index in range(6, 65):
        d4 = d4_index / 10.0
        for d2_index in range(6, 251):
            d2 = d2_index / 10.0
            system = assemble(materials, 80.0, d2, d4, h_out, h_in)
            temperatures = system.skin_at((1500.0, 1800.0))
            evaluated_q3 += 1
            if temperatures[0] <= 44.0 + 1e-9 and temperatures[1] <= 47.0 + 1e-9:
                q3_feasible.append((round(d2 + d4, 10), d2, d4))
    minimum_total = min(item[0] for item in q3_feasible)
    tied = sorted(
        [[item[1], item[2]] for item in q3_feasible if abs(item[0] - minimum_total) <= 1e-12]
    )
    q3_result = yaml.safe_load((RESULTS / "optimization_q3.yaml").read_text(encoding="utf-8"))
    q3_reported_total = float(q3_result["practical_0p1mm_design"]["total_mm"])
    q3_reported_pair = sorted(
        [
            float(q3_result["practical_0p1mm_design"]["d2_mm"]),
            float(q3_result["practical_0p1mm_design"]["d4_mm"]),
        ]
    )
    pair_present = any(
        abs(pair[0] - q3_reported_pair[0]) <= 1e-12
        and abs(pair[1] - q3_reported_pair[1]) <= 1e-12
        for pair in [sorted(value) for value in tied]
    )
    add(
        "raw_parameter_q3_full_grid",
        "pass"
        if abs(minimum_total - q3_reported_total) <= 1e-9 and pair_present
        else "fail",
        {
            "evaluated_points": evaluated_q3,
            "minimum_total_mm": minimum_total,
            "reported_total_mm": q3_reported_total,
            "tied_minimum_pairs_d2_d4_mm": tied,
            "reported_pair_present": pair_present,
        },
    )

    failed = [item["id"] for item in checks if item["status"] != "pass"]
    report = {
        "schema_version": 1,
        "phase": "blind-revision",
        "status": "pass" if not failed else "fail",
        "independence": {
            "imports_main_solver": False,
            "material_source": "raw attachment 1",
            "q1_integrator": "scipy solve_ivp Radau",
            "q2_q3_grid_builder": "standalone finite-volume assembly and tridiagonal eigensolution",
        },
        "grid_elapsed_s": float(time.perf_counter() - grid_started),
        "checks": checks,
        "failed_checks": failed,
        "interpretation": "Agreement verifies an independently assembled discretization and exhaustive manufacturing grid, not external physical validity.",
    }
    (RESULTS / "independent_verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "failed_checks": failed}, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
