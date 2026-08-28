"""Deterministic one-dimensional multilayer heat-transfer models.

The module contains no case-specific output code.  It implements the empirical
baseline, a four-node RC model, and the conservative finite-volume model used
for the final design.  All calculations are per unit area.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np
from scipy.linalg import eigh_tridiagonal, solve_banded
from scipy.optimize import brentq, least_squares


BODY_TEMPERATURE_C = 37.0


@dataclass(frozen=True)
class Material:
    layer: str
    density_kg_m3: float
    heat_capacity_j_kgk: float
    conductivity_w_mk: float


DEFAULT_THICKNESS_MM = (0.6, 6.0, 3.6, 5.0)
RC_CELLS = (1, 1, 1, 1)
MEDIUM_CELLS = (6, 24, 12, 12)
HIGH_CELLS = (8, 32, 16, 16)
EXPECTED_LAYER_ORDER = ("I", "II", "III", "IV")
REFERENCE_GAP_MEAN_K = (75.0 + BODY_TEMPERATURE_C) / 2.0 + 273.15


@dataclass
class FitResult:
    model: str
    parameters: dict[str, float]
    objective_sse: float
    sampled_points: int
    nfev: int
    optimality: float
    status: str


@dataclass
class StepResponse:
    """Exact-in-time solution of the semi-discrete linear heat equation."""

    environment_c: float
    body_c: float
    initial_clothing_c: float
    h_out_w_m2k: float
    h_in_w_m2k: float
    gap_parallel_conductance_w_m2k: float
    gap_temperature_exponent: float
    effective_gap_parallel_conductance_w_m2k: float
    thickness_mm: tuple[float, float, float, float]
    cells_per_layer: tuple[int, int, int, int]
    layer_ids: np.ndarray
    dx_m: np.ndarray
    conductivity_w_mk: np.ndarray
    capacity_j_m2k: np.ndarray
    internal_conductance_w_m2k: np.ndarray
    outer_conductance_w_m2k: float
    inner_conductance_w_m2k: float
    eigenvalues: np.ndarray
    eigenvectors: np.ndarray
    modal_initial: np.ndarray
    steady_cell_rise_c: np.ndarray

    @property
    def delta_environment_c(self) -> float:
        return self.environment_c - self.body_c

    @property
    def cell_centers_mm(self) -> np.ndarray:
        left_edges = np.r_[0.0, np.cumsum(self.dx_m[:-1])]
        return (left_edges + self.dx_m / 2.0) * 1000.0

    @property
    def interface_indices(self) -> np.ndarray:
        return np.flatnonzero(self.layer_ids[:-1] != self.layer_ids[1:])

    def cell_temperature(self, times_s: float | Iterable[float]) -> float | np.ndarray:
        scalar = np.ndim(times_s) == 0
        times = np.atleast_1d(np.asarray(times_s, dtype=float))
        if np.any(times < 0):
            raise ValueError("times_s must be nonnegative")
        exp_modes = np.exp(np.outer(times, self.eigenvalues))
        transformed = (exp_modes * self.modal_initial[None, :]) @ self.eigenvectors.T
        rise = self.steady_cell_rise_c[None, :] + transformed / np.sqrt(
            self.capacity_j_m2k[None, :]
        )
        values = self.body_c + rise
        values[np.isclose(times, 0.0, rtol=0.0, atol=1e-14), :] = (
            self.initial_clothing_c
        )
        return values[0] if scalar else values

    def skin_temperature(self, times_s: float | Iterable[float]) -> float | np.ndarray:
        scalar = np.ndim(times_s) == 0
        cells = np.atleast_2d(self.cell_temperature(times_s))
        last_rise = cells[:, -1] - self.body_c
        skin_rise = (
            self.inner_conductance_w_m2k / self.h_in_w_m2k
        ) * last_rise
        values = self.body_c + skin_rise
        zero = np.isclose(np.atleast_1d(times_s), 0.0, rtol=0.0, atol=1e-14)
        values[zero] = self.body_c
        return float(values[0]) if scalar else values

    def outer_surface_temperature(
        self, times_s: float | Iterable[float]
    ) -> float | np.ndarray:
        scalar = np.ndim(times_s) == 0
        cells = np.atleast_2d(self.cell_temperature(times_s))
        first_rise = cells[:, 0] - self.body_c
        q_inward = self.outer_conductance_w_m2k * (
            self.delta_environment_c - first_rise
        )
        values = self.environment_c - q_inward / self.h_out_w_m2k
        zero = np.isclose(np.atleast_1d(times_s), 0.0, rtol=0.0, atol=1e-14)
        values[zero] = self.initial_clothing_c
        return float(values[0]) if scalar else values

    def field(self, times_s: Iterable[float]) -> tuple[list[dict[str, object]], np.ndarray]:
        """Return boundary/interface/cell temperatures sorted by x position."""

        times = np.asarray(list(times_s), dtype=float)
        cells = np.asarray(self.cell_temperature(times))
        entries: list[tuple[float, int, dict[str, object], np.ndarray]] = []

        outer = np.asarray(self.outer_surface_temperature(times))
        entries.append(
            (
                0.0,
                0,
                {"x_mm": 0.0, "node_type": "outer_surface", "layer": "I"},
                outer,
            )
        )

        for j, (x, layer) in enumerate(zip(self.cell_centers_mm, self.layer_ids)):
            entries.append(
                (
                    float(x),
                    1,
                    {
                        "x_mm": float(x),
                        "node_type": "cell_center",
                        "layer": str(layer),
                        "cell_index": j + 1,
                    },
                    cells[:, j],
                )
            )

        for idx in self.interface_indices:
            q_inward = self.internal_conductance_w_m2k[idx] * (
                cells[:, idx] - cells[:, idx + 1]
            )
            face = cells[:, idx] - q_inward * self.dx_m[idx] / (
                2.0 * self.conductivity_w_mk[idx]
            )
            x_mm = float(np.sum(self.dx_m[: idx + 1]) * 1000.0)
            left_layer = str(self.layer_ids[idx])
            right_layer = str(self.layer_ids[idx + 1])
            entries.append(
                (
                    x_mm,
                    2,
                    {
                        "x_mm": x_mm,
                        "node_type": "layer_interface",
                        "layer": f"{left_layer}/{right_layer}",
                    },
                    face,
                )
            )

        skin_x_mm = float(np.sum(self.dx_m) * 1000.0)
        skin = np.asarray(self.skin_temperature(times))
        entries.append(
            (
                skin_x_mm,
                3,
                {
                    "x_mm": skin_x_mm,
                    "node_type": "skin_surface",
                    "layer": "IV/skin",
                },
                skin,
            )
        )

        entries.sort(key=lambda item: (item[0], item[1]))
        metadata = [item[2] for item in entries]
        values = np.column_stack([item[3] for item in entries])
        return metadata, values

    def first_crossing_s(self, threshold_c: float, end_s: float) -> float | None:
        start_value = float(self.skin_temperature(0.0))
        end_value = float(self.skin_temperature(end_s))
        if start_value > threshold_c:
            return 0.0
        if end_value <= threshold_c:
            return None
        return float(
            brentq(
                lambda value: float(self.skin_temperature(value)) - threshold_c,
                0.0,
                float(end_s),
                xtol=1e-10,
                rtol=1e-13,
            )
        )

    def duration_above_s(self, threshold_c: float, end_s: float) -> float:
        crossing = self.first_crossing_s(threshold_c, end_s)
        if crossing is None:
            return 0.0
        return float(end_s - crossing)

    def steady_fluxes_w_m2(self) -> tuple[float, float]:
        outer = self.outer_conductance_w_m2k * (
            self.delta_environment_c - self.steady_cell_rise_c[0]
        )
        inner = self.inner_conductance_w_m2k * self.steady_cell_rise_c[-1]
        return float(outer), float(inner)


def build_step_response(
    environment_c: float,
    d2_mm: float,
    d4_mm: float,
    h_out_w_m2k: float,
    h_in_w_m2k: float,
    cells_per_layer: Sequence[int] = MEDIUM_CELLS,
    *,
    materials: Sequence[Material],
    body_c: float = BODY_TEMPERATURE_C,
    initial_clothing_c: float = BODY_TEMPERATURE_C,
    conductivity_scales: Mapping[str, float] | None = None,
    capacity_scales: Mapping[str, float] | None = None,
    gap_parallel_conductance_w_m2k: float = 0.0,
    gap_temperature_exponent: float = 0.0,
    gap_reference_mean_k: float = REFERENCE_GAP_MEAN_K,
) -> StepResponse:
    """Construct the semi-discrete model and diagonalize it once."""

    cells = tuple(int(value) for value in cells_per_layer)
    if len(cells) != 4 or any(value <= 0 for value in cells):
        raise ValueError("cells_per_layer must contain four positive integers")
    if not 0.6 <= d2_mm <= 25.0:
        raise ValueError("d2_mm is outside [0.6, 25]")
    if not 0.6 <= d4_mm <= 6.4:
        raise ValueError("d4_mm is outside [0.6, 6.4]")
    if h_out_w_m2k <= 0 or h_in_w_m2k <= 0:
        raise ValueError("heat-transfer coefficients must be positive")
    material_tuple = tuple(materials)
    if tuple(item.layer for item in material_tuple) != EXPECTED_LAYER_ORDER:
        raise ValueError("materials must contain I, II, III, IV in that order")
    if any(
        item.density_kg_m3 <= 0
        or item.heat_capacity_j_kgk <= 0
        or item.conductivity_w_mk <= 0
        for item in material_tuple
    ):
        raise ValueError("all material properties must be positive")
    if gap_parallel_conductance_w_m2k < 0:
        raise ValueError("gap_parallel_conductance_w_m2k must be nonnegative")
    if gap_reference_mean_k <= 0:
        raise ValueError("gap_reference_mean_k must be positive")

    k_scales = conductivity_scales or {}
    c_scales = capacity_scales or {}
    thickness = (0.6, float(d2_mm), 3.6, float(d4_mm))
    layer_ids: list[str] = []
    density: list[float] = []
    heat_capacity: list[float] = []
    conductivity: list[float] = []
    dx_m: list[float] = []

    mean_boundary_k = (float(environment_c) + float(body_c)) / 2.0 + 273.15
    effective_gap_parallel = float(gap_parallel_conductance_w_m2k) * (
        mean_boundary_k / float(gap_reference_mean_k)
    ) ** float(gap_temperature_exponent)

    for material, width_mm, count in zip(material_tuple, thickness, cells):
        layer_ids.extend([material.layer] * count)
        density.extend([material.density_kg_m3] * count)
        heat_capacity.extend(
            [
                material.heat_capacity_j_kgk
                * float(c_scales.get(material.layer, 1.0))
            ]
            * count
        )
        base_conductivity = material.conductivity_w_mk
        if material.layer == "IV":
            # A full-gap conductance g_p in parallel with conduction is
            # equivalent to k_eff = k_air + g_p*d4.  The optional exponent
            # changes g_p with mean absolute boundary temperature and is an
            # explicitly labelled structural scenario, not a fitted fact.
            base_conductivity += effective_gap_parallel * width_mm / 1000.0
        conductivity.extend(
            [base_conductivity * float(k_scales.get(material.layer, 1.0))]
            * count
        )
        dx_m.extend([width_mm / 1000.0 / count] * count)

    layer_array = np.asarray(layer_ids, dtype="U8")
    density_array = np.asarray(density, dtype=float)
    heat_capacity_array = np.asarray(heat_capacity, dtype=float)
    conductivity_array = np.asarray(conductivity, dtype=float)
    dx_array = np.asarray(dx_m, dtype=float)
    capacity = density_array * heat_capacity_array * dx_array

    internal_g = 1.0 / (
        dx_array[:-1] / (2.0 * conductivity_array[:-1])
        + dx_array[1:] / (2.0 * conductivity_array[1:])
    )
    outer_g = 1.0 / (
        1.0 / h_out_w_m2k + dx_array[0] / (2.0 * conductivity_array[0])
    )
    inner_g = 1.0 / (
        dx_array[-1] / (2.0 * conductivity_array[-1]) + 1.0 / h_in_w_m2k
    )

    diagonal = np.empty_like(capacity)
    diagonal[0] = -(outer_g + internal_g[0])
    diagonal[-1] = -(internal_g[-1] + inner_g)
    if len(capacity) > 2:
        diagonal[1:-1] = -(internal_g[:-1] + internal_g[1:])

    symmetric_diagonal = diagonal / capacity
    symmetric_off_diagonal = internal_g / np.sqrt(capacity[:-1] * capacity[1:])

    # Solve -K*y_inf = f*DeltaT with the symmetric conductance matrix K.
    banded = np.zeros((3, len(capacity)), dtype=float)
    banded[1, :] = -diagonal
    banded[0, 1:] = -internal_g
    banded[2, :-1] = -internal_g
    forcing = np.zeros(len(capacity), dtype=float)
    forcing[0] = outer_g * (environment_c - body_c)
    steady_rise = solve_banded((1, 1), banded, forcing)

    eigenvalues, eigenvectors = eigh_tridiagonal(
        symmetric_diagonal, symmetric_off_diagonal
    )
    initial_rise = float(initial_clothing_c) - float(body_c)
    modal_initial = eigenvectors.T @ (
        np.sqrt(capacity) * (initial_rise - steady_rise)
    )

    return StepResponse(
        environment_c=float(environment_c),
        body_c=float(body_c),
        initial_clothing_c=float(initial_clothing_c),
        h_out_w_m2k=float(h_out_w_m2k),
        h_in_w_m2k=float(h_in_w_m2k),
        gap_parallel_conductance_w_m2k=float(gap_parallel_conductance_w_m2k),
        gap_temperature_exponent=float(gap_temperature_exponent),
        effective_gap_parallel_conductance_w_m2k=effective_gap_parallel,
        thickness_mm=thickness,
        cells_per_layer=cells,
        layer_ids=layer_array,
        dx_m=dx_array,
        conductivity_w_mk=conductivity_array,
        capacity_j_m2k=capacity,
        internal_conductance_w_m2k=internal_g,
        outer_conductance_w_m2k=float(outer_g),
        inner_conductance_w_m2k=float(inner_g),
        eigenvalues=eigenvalues,
        eigenvectors=eigenvectors,
        modal_initial=modal_initial,
        steady_cell_rise_c=steady_rise,
    )


def exponential_temperature(
    times_s: float | Iterable[float], amplitude_c: float, time_constant_s: float
) -> float | np.ndarray:
    scalar = np.ndim(times_s) == 0
    times = np.atleast_1d(np.asarray(times_s, dtype=float))
    values = BODY_TEMPERATURE_C + amplitude_c * (
        1.0 - np.exp(-times / time_constant_s)
    )
    return float(values[0]) if scalar else values


def fit_exponential(times_s: np.ndarray, observed_c: np.ndarray) -> FitResult:
    sample = np.arange(0, len(times_s), 2, dtype=int)
    if sample[-1] != len(times_s) - 1:
        sample = np.r_[sample, len(times_s) - 1]
    fit_t = np.asarray(times_s[sample], dtype=float)
    fit_y = np.asarray(observed_c[sample], dtype=float)

    def residual(log_parameters: np.ndarray) -> np.ndarray:
        amplitude, tau = np.exp(log_parameters)
        return np.asarray(exponential_temperature(fit_t, amplitude, tau)) - fit_y

    result = least_squares(
        residual,
        np.log([11.0, 300.0]),
        bounds=(np.log([0.1, 1.0]), np.log([100.0, 100000.0])),
        xtol=1e-12,
        ftol=1e-12,
        gtol=1e-12,
        max_nfev=300,
    )
    amplitude, tau = np.exp(result.x)
    return FitResult(
        model="empirical_exponential",
        parameters={"amplitude_c": float(amplitude), "time_constant_s": float(tau)},
        objective_sse=float(2.0 * result.cost),
        sampled_points=int(len(sample)),
        nfev=int(result.nfev),
        optimality=float(result.optimality),
        status="pass" if result.success else "fail",
    )


def fit_boundary_coefficients(
    times_s: np.ndarray,
    observed_c: np.ndarray,
    cells_per_layer: Sequence[int],
    *,
    materials: Sequence[Material],
    initial_h: tuple[float, float] = (80.0, 8.0),
    gap_parallel_conductance_w_m2k: float = 0.0,
    gap_temperature_exponent: float = 0.0,
) -> FitResult:
    sample = np.arange(0, len(times_s), 2, dtype=int)
    if sample[-1] != len(times_s) - 1:
        sample = np.r_[sample, len(times_s) - 1]
    fit_t = np.asarray(times_s[sample], dtype=float)
    fit_y = np.asarray(observed_c[sample], dtype=float)

    def residual(log_parameters: np.ndarray) -> np.ndarray:
        h_out, h_in = np.exp(log_parameters)
        model = build_step_response(
            75.0,
            6.0,
            5.0,
            h_out,
            h_in,
            cells_per_layer,
            materials=materials,
            gap_parallel_conductance_w_m2k=gap_parallel_conductance_w_m2k,
            gap_temperature_exponent=gap_temperature_exponent,
        )
        return np.asarray(model.skin_temperature(fit_t)) - fit_y

    result = least_squares(
        residual,
        np.log(initial_h),
        bounds=(np.log([2.0, 1.0]), np.log([500.0, 100.0])),
        xtol=1e-11,
        ftol=1e-11,
        gtol=1e-11,
        max_nfev=300,
    )
    h_out, h_in = np.exp(result.x)
    model_name = "four_node_rc" if tuple(cells_per_layer) == RC_CELLS else "finite_volume"
    return FitResult(
        model=model_name,
        parameters={"h_out_w_m2k": float(h_out), "h_in_w_m2k": float(h_in)},
        objective_sse=float(2.0 * result.cost),
        sampled_points=int(len(sample)),
        nfev=int(result.nfev),
        optimality=float(result.optimality),
        status="pass" if result.success else "fail",
    )


def error_metrics(predicted_c: np.ndarray, observed_c: np.ndarray) -> dict[str, float]:
    residual = np.asarray(predicted_c, dtype=float) - np.asarray(observed_c, dtype=float)
    return {
        "rmse_c": float(np.sqrt(np.mean(residual**2))),
        "mae_c": float(np.mean(np.abs(residual))),
        "max_abs_error_c": float(np.max(np.abs(residual))),
        "bias_c": float(np.mean(residual)),
    }
