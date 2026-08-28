"""Production geometry for the blind-revision solution.

All lengths are metres and public volume methods return litres.  Numerical
verification deliberately lives in ``verify_independent.py`` and does not
import this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
from numpy.polynomial.legendre import leggauss


def composite_gauss_nodes(
    intervals: list[tuple[float, float]],
    cell_width: float,
    order: int = 8,
) -> tuple[np.ndarray, np.ndarray]:
    """Return composite Gauss--Legendre nodes and weights."""

    base_x, base_w = leggauss(order)
    all_x: list[np.ndarray] = []
    all_w: list[np.ndarray] = []
    for lower, upper in intervals:
        cells = int(np.ceil((upper - lower) / cell_width))
        edges = np.linspace(lower, upper, cells + 1)
        middle = (edges[:-1] + edges[1:]) / 2.0
        half = (edges[1:] - edges[:-1]) / 2.0
        all_x.append((middle[:, None] + half[:, None] * base_x).ravel())
        all_w.append((half[:, None] * base_w).ravel())
    return np.concatenate(all_x), np.concatenate(all_w)


def ellipse_segment_area(
    cut: np.ndarray,
    vertical_semiaxis: float,
    horizontal_semiaxis: float,
) -> np.ndarray:
    """Area below ``z=cut`` in ``y^2/b^2 + z^2/a^2 <= 1``."""

    a = vertical_semiaxis
    b = horizontal_semiaxis
    clipped = np.clip(cut, -a, a)
    middle = (
        a * b * (np.arcsin(clipped / a) + np.pi / 2.0)
        + b * clipped * np.sqrt(np.maximum(0.0, 1.0 - (clipped / a) ** 2))
    )
    return np.where(cut <= -a, 0.0, np.where(cut >= a, np.pi * a * b, middle))


def circle_segment_area(radius: np.ndarray, cut: np.ndarray) -> np.ndarray:
    """Area below a chord at signed centre distance ``cut``."""

    clipped = np.clip(cut, -radius, radius)
    ratio = np.divide(
        clipped,
        radius,
        out=np.zeros_like(clipped, dtype=float),
        where=radius > 0.0,
    )
    middle = (
        clipped * np.sqrt(np.maximum(0.0, radius**2 - clipped**2))
        + radius**2 * (np.arcsin(np.clip(ratio, -1.0, 1.0)) + np.pi / 2.0)
    )
    return np.where(
        cut <= -radius,
        0.0,
        np.where(cut >= radius, np.pi * radius**2, middle),
    )


@dataclass(frozen=True)
class SmallEllipticalTank:
    length: float = 2.45
    vertical_semiaxis: float = 0.60
    horizontal_semiaxis: float = 0.89
    probe_x: float = 0.40
    cell_width: float = 0.01
    quadrature_order: int = 8

    @cached_property
    def integration_grid(self) -> tuple[np.ndarray, np.ndarray]:
        return composite_gauss_nodes(
            [(0.0, self.length)], self.cell_width, self.quadrature_order
        )

    def volume_l(
        self,
        height_m: np.ndarray | list[float] | float,
        alpha_deg: float,
        horizontal_semiaxis: float | None = None,
        level_offset_m: float = 0.0,
    ) -> np.ndarray:
        height = np.atleast_1d(np.asarray(height_m, dtype=float))
        x, weight = self.integration_grid
        cut = (
            height[:, None]
            + level_offset_m
            - self.vertical_semiaxis
            - np.tan(np.deg2rad(alpha_deg)) * (x[None, :] - self.probe_x)
        )
        b = self.horizontal_semiaxis if horizontal_semiaxis is None else horizontal_semiaxis
        return ellipse_segment_area(cut, self.vertical_semiaxis, b) @ weight * 1000.0

    def capacity_l(self, horizontal_semiaxis: float | None = None) -> float:
        b = self.horizontal_semiaxis if horizontal_semiaxis is None else horizontal_semiaxis
        return float(np.pi * self.vertical_semiaxis * b * self.length * 1000.0)

    def empty_full_readings_m(
        self,
        alpha_deg: float,
        level_offset_m: float = 0.0,
    ) -> tuple[float, float]:
        slope = np.tan(np.deg2rad(alpha_deg))
        empty = -level_offset_m - slope * self.probe_x
        full = (
            2.0 * self.vertical_semiaxis
            - level_offset_m
            + slope * (self.length - self.probe_x)
        )
        return float(empty), float(full)


@dataclass(frozen=True)
class ActualTank:
    cylinder_length: float = 8.0
    cylinder_radius: float = 1.5
    cap_depth: float = 1.0
    probe_x: float = 2.0
    cell_width: float = 0.025
    quadrature_order: int = 8

    @property
    def sphere_radius(self) -> float:
        radius = self.cylinder_radius
        depth = self.cap_depth
        return (radius * radius + depth * depth) / (2.0 * depth)

    @cached_property
    def integration_grid(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        length = self.cylinder_length
        depth = self.cap_depth
        x, weight = composite_gauss_nodes(
            [(-depth, 0.0), (0.0, length), (length, length + depth)],
            self.cell_width,
            self.quadrature_order,
        )
        sphere_radius = self.sphere_radius
        left_centre = -depth + sphere_radius
        right_centre = length + depth - sphere_radius
        radius = np.where(
            x < 0.0,
            np.sqrt(np.maximum(0.0, sphere_radius**2 - (x - left_centre) ** 2)),
            np.where(
                x > length,
                np.sqrt(np.maximum(0.0, sphere_radius**2 - (x - right_centre) ** 2)),
                self.cylinder_radius,
            ),
        )
        return x, weight, radius

    def volume_l(
        self,
        height_m: np.ndarray | list[float] | float,
        alpha_deg: float,
        beta_deg: float,
        level_offset_m: float = 0.0,
    ) -> np.ndarray:
        height = np.atleast_1d(np.asarray(height_m, dtype=float))
        x, weight, radius = self.integration_grid
        cut = (
            np.cos(np.deg2rad(beta_deg))
            * (height[:, None] + level_offset_m - self.cylinder_radius)
            - np.tan(np.deg2rad(alpha_deg)) * (x[None, :] - self.probe_x)
        )
        return circle_segment_area(radius[None, :], cut) @ weight * 1000.0

    def capacity_l(self) -> float:
        radius = self.cylinder_radius
        depth = self.cap_depth
        cap_volume = np.pi * depth**2 * (self.sphere_radius - depth / 3.0)
        return float(
            (np.pi * radius**2 * self.cylinder_length + 2.0 * cap_volume) * 1000.0
        )

    def empty_full_readings_m(
        self,
        alpha_deg: float,
        beta_deg: float,
        level_offset_m: float = 0.0,
    ) -> tuple[float, float]:
        x, _, radius = self.integration_grid
        slope_term = -np.tan(np.deg2rad(alpha_deg)) * (x - self.probe_x)
        beta_scale = np.cos(np.deg2rad(beta_deg))
        empty = (
            self.cylinder_radius
            - level_offset_m
            - np.max(slope_term + radius) / beta_scale
        )
        full = (
            self.cylinder_radius
            - level_offset_m
            - np.min(slope_term - radius) / beta_scale
        )
        return float(empty), float(full)
