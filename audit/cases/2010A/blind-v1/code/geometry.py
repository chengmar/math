"""Deterministic geometric volume models for the two oil tanks."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.polynomial.legendre import leggauss


def circle_segment_area(radius: np.ndarray, level: np.ndarray) -> np.ndarray:
    """Area below z=level in a disk centered at zero.

    Parameters are broadcast with NumPy.  The formula is clipped explicitly at
    the empty/full limits so that boundary tests do not depend on roundoff.
    """

    radius = np.asarray(radius, dtype=float)
    level = np.asarray(level, dtype=float)
    ratio = np.clip(level / radius, -1.0, 1.0)
    middle = radius**2 * (
        ratio * np.sqrt(np.maximum(0.0, 1.0 - ratio**2))
        + np.arcsin(ratio)
        + np.pi / 2.0
    )
    return np.where(
        level <= -radius,
        0.0,
        np.where(level >= radius, np.pi * radius**2, middle),
    )


def ellipse_segment_area(
    horizontal_semiaxis: float,
    vertical_semiaxis: float,
    level: np.ndarray,
) -> np.ndarray:
    """Area below z=level in y^2/a^2 + z^2/b^2 <= 1."""

    level = np.asarray(level, dtype=float)
    ratio = np.clip(level / vertical_semiaxis, -1.0, 1.0)
    middle = horizontal_semiaxis * vertical_semiaxis * (
        ratio * np.sqrt(np.maximum(0.0, 1.0 - ratio**2))
        + np.arcsin(ratio)
        + np.pi / 2.0
    )
    return np.where(
        level <= -vertical_semiaxis,
        0.0,
        np.where(
            level >= vertical_semiaxis,
            np.pi * horizontal_semiaxis * vertical_semiaxis,
            middle,
        ),
    )


def _mapped_legendre(lower: float, upper: float, order: int) -> tuple[np.ndarray, np.ndarray]:
    nodes, weights = leggauss(order)
    mapped_nodes = (lower + upper) / 2.0 + (upper - lower) * nodes / 2.0
    mapped_weights = (upper - lower) * weights / 2.0
    return mapped_nodes, mapped_weights


@dataclass
class SmallEllipticalTank:
    horizontal_semiaxis_m: float = 0.89
    vertical_semiaxis_m: float = 0.60
    length_m: float = 2.45
    probe_x_m: float = 0.40
    quadrature_order: int = 240
    _x: np.ndarray = field(init=False, repr=False)
    _weights: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._x, self._weights = _mapped_legendre(0.0, self.length_m, self.quadrature_order)

    @property
    def capacity_l(self) -> float:
        return (
            np.pi
            * self.horizontal_semiaxis_m
            * self.vertical_semiaxis_m
            * self.length_m
            * 1000.0
        )

    def volume_l(
        self,
        height_m: np.ndarray | float,
        alpha_deg: float,
        level_offset_m: float = 0.0,
    ) -> np.ndarray:
        """Oil volume for a longitudinal tilt.

        Positive alpha means that the right end rises.  Height is the reading
        along the probe's tank-fixed vertical datum; level_offset_m calibrates
        its zero.  The free-surface cut at longitudinal coordinate x is

            z = h + delta - b - tan(alpha) (x - x_probe).
        """

        heights = np.atleast_1d(np.asarray(height_m, dtype=float))
        levels = (
            heights[:, None]
            + level_offset_m
            - self.vertical_semiaxis_m
            - np.tan(np.deg2rad(alpha_deg)) * (self._x[None, :] - self.probe_x_m)
        )
        areas = ellipse_segment_area(
            self.horizontal_semiaxis_m,
            self.vertical_semiaxis_m,
            levels,
        )
        return (areas @ self._weights) * 1000.0


@dataclass
class ActualSphericalCapTank:
    radius_m: float = 1.50
    cylinder_length_m: float = 8.00
    cap_depth_m: float = 1.00
    probe_x_m: float = 2.00
    cap_quadrature_order: int = 180
    cylinder_quadrature_order: int = 240
    _x: np.ndarray = field(init=False, repr=False)
    _weights: np.ndarray = field(init=False, repr=False)
    _rho: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        left_x, left_w = _mapped_legendre(
            -self.cap_depth_m, 0.0, self.cap_quadrature_order
        )
        cylinder_x, cylinder_w = _mapped_legendre(
            0.0, self.cylinder_length_m, self.cylinder_quadrature_order
        )
        right_x, right_w = _mapped_legendre(
            self.cylinder_length_m,
            self.cylinder_length_m + self.cap_depth_m,
            self.cap_quadrature_order,
        )
        self._x = np.concatenate((left_x, cylinder_x, right_x))
        self._weights = np.concatenate((left_w, cylinder_w, right_w))

        sphere_radius = self.sphere_radius_m
        center_offset = sphere_radius - self.cap_depth_m
        left_rho = np.sqrt(
            np.maximum(0.0, sphere_radius**2 - (left_x - center_offset) ** 2)
        )
        right_center = self.cylinder_length_m - center_offset
        right_rho = np.sqrt(
            np.maximum(0.0, sphere_radius**2 - (right_x - right_center) ** 2)
        )
        self._rho = np.concatenate(
            (left_rho, np.full_like(cylinder_x, self.radius_m), right_rho)
        )

    @property
    def sphere_radius_m(self) -> float:
        r = self.radius_m
        d = self.cap_depth_m
        return (r**2 + d**2) / (2.0 * d)

    @property
    def capacity_l(self) -> float:
        cylinder = np.pi * self.radius_m**2 * self.cylinder_length_m
        one_cap = (
            np.pi
            * self.cap_depth_m**2
            * (3.0 * self.sphere_radius_m - self.cap_depth_m)
            / 3.0
        )
        return (cylinder + 2.0 * one_cap) * 1000.0

    def volume_l(
        self,
        height_m: np.ndarray | float,
        alpha_deg: float,
        beta_deg: float,
    ) -> np.ndarray:
        """Oil volume under longitudinal tilt alpha and transverse roll beta.

        In tank coordinates, the gravity direction is
        (sin(alpha), cos(alpha)sin(beta), cos(alpha)cos(beta)).  Rotational
        symmetry reduces each cross-section to a circular segment whose
        effective cut level is

            s(x) = cos(beta) (h-r) - tan(alpha) (x-x_probe).

        Only |beta| is identifiable for an axisymmetric tank.
        """

        heights = np.atleast_1d(np.asarray(height_m, dtype=float))
        alpha = np.deg2rad(alpha_deg)
        beta = np.deg2rad(beta_deg)
        levels = (
            np.cos(beta) * (heights[:, None] - self.radius_m)
            - np.tan(alpha) * (self._x[None, :] - self.probe_x_m)
        )
        areas = circle_segment_area(self._rho[None, :], levels)
        return (areas @ self._weights) * 1000.0
