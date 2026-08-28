#!/usr/bin/env python3
"""Blind, reproducible solution for the 2014 CUMCM A lunar-landing case.

Only Python's standard library plus NumPy and Pillow are required.  The
program reads the copied problem attachments, builds a simple baseline,
optimises a bounded-thrust reference trajectory, independently re-integrates
the dynamics, performs deterministic sensitivity checks, and writes all
numerical results and figures used by the paper.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "input"
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"
PAPER = ROOT / "paper"

COARSE_TIF = INPUT / "attachments" / "file-cde3e1f9ab7216a1dfd1.tif"
FINE_TIF = INPUT / "attachments" / "file-b91e320690f088c807c0.tif"
PROBLEM_DOC = INPUT / "problem" / "<SOURCE_FILE_REDACTED>"
BACKGROUND_DOC = INPUT / "attachments" / "<SOURCE_FILE_REDACTED>"
STAGES_DOC = INPUT / "attachments" / "<SOURCE_FILE_REDACTED>"

SEED = 2014
RNG = np.random.default_rng(SEED)

# Facts supplied in the problem, except G which is a stated modelling constant.
G = 6.67430e-11
MOON_MASS = 7.3477e22
MU = G * MOON_MASS
R_MEAN = 1737.013e3
LANDING_ELEVATION = -2641.0
R_LOCAL = R_MEAN + LANDING_ELEVATION
MASS0 = 2400.0
EXHAUST_VELOCITY = 2940.0
THRUST_MIN = 1500.0
THRUST_MAX = 7500.0

# Five percent operational reserve for acceptance, plus a slightly tighter
# numerical target used inside the optimiser.  Actual feasibility is also
# checked against the exact problem bounds.
DESIGN_THRUST_MIN = 1575.0
DESIGN_THRUST_MAX = 7125.0
OPT_THRUST_MIN = 1600.0
OPT_THRUST_MAX = 7100.0

TARGET_LAT_DEG = 44.12
TARGET_LON_DEG = -19.51  # east-positive; 19.51 W

STAGE_NAMES = ["main_deceleration", "rapid_adjustment", "coarse_avoidance",
               "fine_avoidance", "slow_descent"]


def ensure_dirs() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    PAPER.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def json_dump(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def csv_dump(path: Path, rows: Iterable[dict], fieldnames: Sequence[str] | None = None) -> None:
    rows = list(rows)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def finite(value: float) -> float:
    return float(value) if math.isfinite(float(value)) else float("nan")


def box_mean(a: np.ndarray, k: int) -> np.ndarray:
    """Centred square mean with reflection padding and O(n) memory/time."""
    k = int(k) | 1
    q = k // 2
    p = np.pad(a.astype(np.float32, copy=False), ((q, q), (q, q)), mode="reflect")
    s = np.pad(p, ((1, 0), (1, 0))).cumsum(0, dtype=np.float64).cumsum(1, dtype=np.float64)
    out = (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) / (k * k)
    return out.astype(np.float32)


def robust_norm(q: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, float, float]:
    lo, hi = np.percentile(q[mask], [10.0, 90.0])
    z = np.clip((q - lo) / (hi - lo + 1e-12), 0.0, 3.0)
    return z.astype(np.float32), float(lo), float(hi)


@dataclass
class HazardField:
    name: str
    height: np.ndarray
    resolution_m: float
    smooth_m: float
    smooth: np.ndarray
    slope: np.ndarray
    residual: np.ndarray
    cx: float
    cy: float


def build_hazard_field(name: str, path: Path, height_scale: float,
                       resolution_m: float, smooth_m: float) -> HazardField:
    raw = np.asarray(Image.open(path), dtype=np.float32)
    height = raw * np.float32(height_scale)
    k = max(3, int(round(smooth_m / resolution_m)) | 1)
    smooth = box_mean(height, k)
    gy, gx = np.gradient(smooth, resolution_m, resolution_m)
    slope = np.hypot(gx, gy).astype(np.float32)
    residual = (height - smooth).astype(np.float32)
    rows, cols = height.shape
    return HazardField(name, height, resolution_m, smooth_m, smooth, slope,
                       residual, (cols - 1) / 2.0, (rows - 1) / 2.0)


def metrics_for_footprint(field: HazardField, footprint_radius_m: float) -> dict[str, np.ndarray]:
    k = max(3, int(round(2.0 * footprint_radius_m / field.resolution_m)) | 1)
    mean_slope_sq = box_mean(field.slope * field.slope, k)
    residual_mean = box_mean(field.residual, k)
    rough_var = np.maximum(0.0, box_mean(field.residual * field.residual, k)
                           - residual_mean * residual_mean)
    smooth_mean = box_mean(field.smooth, k)
    relief_var = np.maximum(0.0, box_mean(field.smooth * field.smooth, k)
                            - smooth_mean * smooth_mean)
    return {
        "rms_slope": np.sqrt(mean_slope_sq).astype(np.float32),
        "roughness": np.sqrt(rough_var).astype(np.float32),
        "relief_sd": np.sqrt(relief_var).astype(np.float32),
        "window_px": np.asarray(k),
    }


def point_metrics(field: HazardField, metrics: dict[str, np.ndarray], row: int, col: int) -> dict:
    x = (col - field.cx) * field.resolution_m
    y = (field.cy - row) * field.resolution_m
    rms = float(metrics["rms_slope"][row, col])
    return {
        "row_zero_based": int(row),
        "column_zero_based": int(col),
        "row_one_based": int(row + 1),
        "column_one_based": int(col + 1),
        "east_offset_m": float(x),
        "north_offset_m": float(y),
        "distance_from_image_center_m": float(math.hypot(x, y)),
        "height_m": float(field.height[row, col]),
        "rms_slope_deg": float(math.degrees(math.atan(rms))),
        "roughness_rms_m": float(metrics["roughness"][row, col]),
        "relief_sd_m": float(metrics["relief_sd"][row, col]),
    }


def select_site(field: HazardField, footprint_radius_m: float, max_distance_m: float,
                weights: Sequence[float] = (0.50, 0.25, 0.15, 0.10)) -> tuple[dict, dict, np.ndarray]:
    metrics = metrics_for_footprint(field, footprint_radius_m)
    rows, cols = field.height.shape
    yy, xx = np.indices((rows, cols), dtype=np.float32)
    dist = np.hypot((xx - field.cx) * field.resolution_m,
                    (field.cy - yy) * field.resolution_m)
    margin = int(metrics["window_px"]) // 2
    mask = ((dist <= max_distance_m) & (xx >= margin) & (xx < cols - margin)
            & (yy >= margin) & (yy < rows - margin))
    ns, _, _ = robust_norm(metrics["rms_slope"], mask)
    nr, _, _ = robust_norm(metrics["roughness"], mask)
    nv, _, _ = robust_norm(metrics["relief_sd"], mask)
    w = np.asarray(weights, dtype=float)
    w = w / w.sum()
    score = (w[0] * ns + w[1] * nr + w[2] * nv
             + w[3] * (dist / max_distance_m)).astype(np.float32)
    score[~mask] = np.inf
    row, col = np.unravel_index(int(np.argmin(score)), score.shape)
    selected = point_metrics(field, metrics, row, col)
    selected["score"] = float(score[row, col])
    selected["footprint_radius_m"] = float(footprint_radius_m)
    center_row, center_col = int(round(field.cy)), int(round(field.cx))
    center = point_metrics(field, metrics, center_row, center_col)
    center["score"] = float(score[center_row, center_col])
    center["footprint_radius_m"] = float(footprint_radius_m)
    return selected, center, score


def hazard_checks(site: dict, kind: str) -> dict:
    if kind == "coarse":
        limits = {"rms_slope_deg": 12.0, "roughness_rms_m": 3.0, "relief_sd_m": 5.0}
    else:
        limits = {"rms_slope_deg": 12.0, "roughness_rms_m": 0.50, "relief_sd_m": 0.50}
    checks = {key: {"value": float(site[key]), "limit": limit,
                    "status": "pass" if float(site[key]) <= limit else "fail"}
              for key, limit in limits.items()}
    checks["overall"] = "pass" if all(v["status"] == "pass" for v in checks.values()) else "fail"
    return checks


def hermite(p0: float, v0: float, p1: float, v1: float, duration: float,
            n: int = 161) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    u = np.linspace(0.0, 1.0, n)
    u2, u3 = u * u, u * u * u
    p = ((2*u3 - 3*u2 + 1)*p0 + (u3 - 2*u2 + u)*duration*v0
         + (-2*u3 + 3*u2)*p1 + (u3 - u2)*duration*v1)
    v = (((6*u2 - 6*u)*p0 + (3*u2 - 4*u + 1)*duration*v0
          + (-6*u2 + 6*u)*p1 + (3*u2 - 2*u)*duration*v1) / duration)
    a = (((12*u - 6)*p0 + (6*u - 4)*duration*v0
          + (-12*u + 6)*p1 + (6*u - 2)*duration*v1) / duration**2)
    return u * duration, p, v, a


def dense_n(duration: float, max_step_s: float = 0.25) -> int:
    """Final-output grid; optimisation itself uses a cheaper grid."""
    return max(201, int(math.ceil(duration / max_step_s)) + 1)


def mass_from_acceleration(t: np.ndarray, acceleration: np.ndarray, mass0: float,
                           exhaust_velocity: float = EXHAUST_VELOCITY) -> tuple[np.ndarray, np.ndarray]:
    delta_v = np.zeros_like(t)
    delta_v[1:] = np.cumsum(0.5 * (acceleration[1:] + acceleration[:-1]) * np.diff(t))
    mass = mass0 * np.exp(-delta_v / exhaust_velocity)
    return mass, delta_v


@dataclass
class Segment:
    name: str
    mode: str
    t: np.ndarray
    position: np.ndarray       # columns: x/downrange, y, h
    velocity: np.ndarray       # columns: vx/tangential, vy, vh
    thrust_acc: np.ndarray     # same axes as position
    mass: np.ndarray
    delta_v: np.ndarray
    thrust: np.ndarray

    @property
    def duration(self) -> float:
        return float(self.t[-1])


def make_main_segment(duration: float, downrange: float, mass0: float,
                      mode: str = "central", n: int = 161,
                      mu: float = MU, exhaust_velocity: float = EXHAUST_VELOCITY) -> Segment:
    rp = R_LOCAL + 15000.0
    ra = R_LOCAL + 100000.0
    semi_major = 0.5 * (rp + ra)
    vp = math.sqrt(mu * (2.0 / rp - 1.0 / semi_major))
    qv0 = vp * R_LOCAL / (R_LOCAL + 15000.0)
    qv1 = 57.0 * R_LOCAL / (R_LOCAL + 3000.0)
    t, h, hd, hdd = hermite(15000.0, 0.0, 3000.0, 0.0, duration, n)
    _, q, qd, qdd = hermite(0.0, qv0, downrange, qv1, duration, n)
    if mode == "central":
        r = R_LOCAL + h
        omega = qd / R_LOCAL
        thrust_r = hdd - r * omega * omega + mu / (r * r)
        thrust_t = r * qdd / R_LOCAL + 2.0 * hd * omega
        vt = r * omega
    elif mode == "flat":
        thrust_r = hdd + mu / R_LOCAL**2
        thrust_t = qdd
        vt = qd
    else:
        raise ValueError(mode)
    thrust_acc = np.column_stack([thrust_t, np.zeros_like(t), thrust_r])
    amag = np.linalg.norm(thrust_acc, axis=1)
    mass, dv = mass_from_acceleration(t, amag, mass0, exhaust_velocity)
    position = np.column_stack([q, np.zeros_like(q), h])
    velocity = np.column_stack([vt, np.zeros_like(vt), hd])
    return Segment("main_deceleration", mode, t, position, velocity,
                   thrust_acc, mass, dv, mass * amag)


def make_adjust_segment(duration: float, end_down_speed: float, q_start: float,
                        mass0: float, n: int = 161, mu: float = MU,
                        exhaust_velocity: float = EXHAUST_VELOCITY) -> Segment:
    qv0 = 57.0 * R_LOCAL / (R_LOCAL + 3000.0)
    q_distance = duration * qv0 / 3.0  # makes terminal tangential acceleration zero
    t, h, hd, hdd = hermite(3000.0, 0.0, 2400.0, -end_down_speed, duration, n)
    _, q, qd, qdd = hermite(q_start, qv0, q_start + q_distance, 0.0, duration, n)
    r = R_LOCAL + h
    omega = qd / R_LOCAL
    thrust_r = hdd - r * omega * omega + mu / (r * r)
    thrust_t = r * qdd / R_LOCAL + 2.0 * hd * omega
    vt = r * omega
    thrust_acc = np.column_stack([thrust_t, np.zeros_like(t), thrust_r])
    amag = np.linalg.norm(thrust_acc, axis=1)
    mass, dv = mass_from_acceleration(t, amag, mass0, exhaust_velocity)
    position = np.column_stack([q, np.zeros_like(q), h])
    velocity = np.column_stack([vt, np.zeros_like(vt), hd])
    return Segment("rapid_adjustment", "central", t, position, velocity,
                   thrust_acc, mass, dv, mass * amag)


def make_flat_segment(name: str, duration: float, p0: Sequence[float], v0: Sequence[float],
                      p1: Sequence[float], v1: Sequence[float], mass0: float,
                      n: int = 161, mu: float = MU,
                      exhaust_velocity: float = EXHAUST_VELOCITY) -> Segment:
    pieces = [hermite(float(p0[j]), float(v0[j]), float(p1[j]), float(v1[j]), duration, n)
              for j in range(3)]
    t = pieces[0][0]
    position = np.column_stack([z[1] for z in pieces])
    velocity = np.column_stack([z[2] for z in pieces])
    total_acc = np.column_stack([z[3] for z in pieces])
    thrust_acc = total_acc.copy()
    thrust_acc[:, 2] += mu / (R_LOCAL + position[:, 2])**2
    amag = np.linalg.norm(thrust_acc, axis=1)
    mass, dv = mass_from_acceleration(t, amag, mass0, exhaust_velocity)
    return Segment(name, "flat_local", t, position, velocity, thrust_acc,
                   mass, dv, mass * amag)


def constraint_vector(segments: Sequence[Segment], design: bool = True,
                      optimisation: bool = False) -> np.ndarray:
    if optimisation:
        lo, hi = OPT_THRUST_MIN, OPT_THRUST_MAX
    else:
        lo = DESIGN_THRUST_MIN if design else THRUST_MIN
        hi = DESIGN_THRUST_MAX if design else THRUST_MAX
    violations: list[float] = []
    for seg in segments:
        violations.append(max(0.0, float(seg.thrust.max()) - hi) / 1000.0)
        violations.append(max(0.0, lo - float(seg.thrust.min())) / 1000.0)
        violations.append(max(0.0, float(seg.velocity[:, 2].max())) / 5.0)
        violations.append(max(0.0, -float(seg.position[:, 2].min())) / 100.0)
        if seg.mode == "central":
            violations.append(max(0.0, -float(seg.velocity[:, 0].min())) / 10.0)
    return np.asarray(violations, dtype=float)


def penalised_value(segments: Sequence[Segment], start_mass: float,
                    design: bool = True) -> float:
    fuel = start_mass - float(segments[-1].mass[-1])
    v = constraint_vector(segments, design, optimisation=design)
    return float(fuel + 2.0e4 * np.sum(v) + 8.0e4 * np.sum(v * v))


def differential_evolution(func: Callable[[np.ndarray], float], bounds: Sequence[tuple[float, float]],
                           seed: int, generations: int = 160,
                           population_size: int | None = None,
                           seeds: Sequence[Sequence[float]] = ()) -> tuple[np.ndarray, float, list[dict]]:
    rng = np.random.default_rng(seed)
    dim = len(bounds)
    pop_n = population_size or max(24, 12 * dim)
    low = np.asarray([b[0] for b in bounds], dtype=float)
    high = np.asarray([b[1] for b in bounds], dtype=float)
    pop = low + rng.random((pop_n, dim)) * (high - low)
    for i, x in enumerate(seeds[:pop_n]):
        pop[i] = np.clip(np.asarray(x, dtype=float), low, high)
    values = np.asarray([func(x) for x in pop])
    history: list[dict] = []
    for generation in range(generations):
        for i in range(pop_n):
            pool = np.delete(np.arange(pop_n), i)
            a, b, c = rng.choice(pool, 3, replace=False)
            mutant = np.clip(pop[a] + 0.72 * (pop[b] - pop[c]), low, high)
            mask = rng.random(dim) < 0.88
            mask[rng.integers(dim)] = True
            trial = np.where(mask, mutant, pop[i])
            f_trial = func(trial)
            if f_trial <= values[i]:
                pop[i], values[i] = trial, f_trial
        j = int(np.argmin(values))
        history.append({"generation": generation + 1, "best_objective": float(values[j])})
    j = int(np.argmin(values))
    best = pop[j].copy()
    best_value = float(values[j])
    step = 0.04 * (high - low)
    for _ in range(11):
        improved = True
        while improved:
            improved = False
            for d in range(dim):
                for sign in (-1.0, 1.0):
                    trial = best.copy()
                    trial[d] = np.clip(trial[d] + sign * step[d], low[d], high[d])
                    value = func(trial)
                    if value + 1e-12 < best_value:
                        best, best_value, improved = trial, float(value), True
        step *= 0.5
    return best, best_value, history


def optimise_schedule(coarse_xy: tuple[float, float], fine_xy: tuple[float, float],
                      main_mode: str = "central") -> tuple[dict, list[Segment], list[dict]]:
    histories: list[dict] = []

    def main_obj(x: np.ndarray) -> float:
        seg = make_main_segment(x[0], x[1], MASS0, main_mode, n=121)
        return penalised_value([seg], MASS0, design=True)

    rp = R_LOCAL + 15000.0
    ra = R_LOCAL + 100000.0
    vp = math.sqrt(MU * (2.0 / rp - 1.0 / ((rp + ra) / 2.0)))
    qv0 = vp * R_LOCAL / rp
    qv1 = 57.0 * R_LOCAL / (R_LOCAL + 3000.0)
    main_seed = [450.0, 0.575 * (qv0 + qv1) * 450.0]
    main_x, _, h = differential_evolution(
        main_obj, [(420.0, 600.0), (350e3, 650e3)], SEED + (1 if main_mode == "central" else 11),
        generations=150, population_size=32, seeds=[main_seed])
    histories.extend({"block": f"main_{main_mode}", **row} for row in h)
    main = make_main_segment(main_x[0], main_x[1], MASS0, main_mode,
                             n=dense_n(float(main_x[0])))

    def stages34_obj(x: np.ndarray) -> float:
        adjust = make_adjust_segment(x[0], x[1], main_x[1], float(main.mass[-1]), n=121)
        cx, cy = coarse_xy
        coarse = make_flat_segment(
            "coarse_avoidance", x[2], (0.0, 0.0, 2400.0), (0.0, 0.0, -x[1]),
            (cx, cy, 100.0), (0.0, 0.0, 0.0), float(adjust.mass[-1]), n=121)
        return penalised_value([adjust, coarse], float(main.mass[-1]), design=True)

    x34, _, h = differential_evolution(
        stages34_obj, [(18.0, 42.0), (25.0, 70.0), (38.0, 105.0)], SEED + 2,
        generations=190, population_size=42,
        seeds=[[24.0, 40.0, 72.0], [25.0, 54.0, 57.0], [22.0, 60.0, 53.0]])
    histories.extend({"block": "stages_3_4", **row} for row in h)
    adjust = make_adjust_segment(x34[0], x34[1], main_x[1], float(main.mass[-1]),
                                 n=dense_n(float(x34[0])))
    cx, cy = coarse_xy
    coarse = make_flat_segment(
        "coarse_avoidance", x34[2], (0.0, 0.0, 2400.0), (0.0, 0.0, -x34[1]),
        (cx, cy, 100.0), (0.0, 0.0, 0.0), float(adjust.mass[-1]),
        n=dense_n(float(x34[2])))

    fx, fy = fine_xy
    final_x, final_y = cx + fx, cy + fy

    def stages56_obj(x: np.ndarray) -> float:
        fine = make_flat_segment(
            "fine_avoidance", x[0], (cx, cy, 100.0), (0.0, 0.0, 0.0),
            (final_x, final_y, 30.0), (0.0, 0.0, -x[1]), float(coarse.mass[-1]), n=121)
        slow = make_flat_segment(
            "slow_descent", x[2], (final_x, final_y, 30.0), (0.0, 0.0, -x[1]),
            (final_x, final_y, 4.0), (0.0, 0.0, 0.0), float(fine.mass[-1]), n=121)
        return penalised_value([fine, slow], float(coarse.mass[-1]), design=True)

    x56, _, h = differential_evolution(
        stages56_obj, [(6.0, 45.0), (2.0, 20.0), (3.0, 25.0)], SEED + 3,
        generations=210, population_size=42,
        seeds=[[12.0, 10.0, 7.0], [16.0, 8.0, 9.0], [10.0, 14.0, 5.0], [25.0, 7.0, 10.0]])
    histories.extend({"block": "stages_5_6", **row} for row in h)
    fine = make_flat_segment(
        "fine_avoidance", x56[0], (cx, cy, 100.0), (0.0, 0.0, 0.0),
        (final_x, final_y, 30.0), (0.0, 0.0, -x56[1]), float(coarse.mass[-1]),
        n=dense_n(float(x56[0])))
    slow = make_flat_segment(
        "slow_descent", x56[2], (final_x, final_y, 30.0), (0.0, 0.0, -x56[1]),
        (final_x, final_y, 4.0), (0.0, 0.0, 0.0), float(fine.mass[-1]),
        n=dense_n(float(x56[2])))
    segments = [main, adjust, coarse, fine, slow]
    schedule = {
        "main_duration_s": float(main_x[0]),
        "main_downrange_m": float(main_x[1]),
        "adjust_duration_s": float(x34[0]),
        "adjust_terminal_down_speed_mps": float(x34[1]),
        "coarse_duration_s": float(x34[2]),
        "fine_duration_s": float(x56[0]),
        "fine_terminal_down_speed_mps": float(x56[1]),
        "slow_duration_s": float(x56[2]),
        "main_mode": main_mode,
    }
    return schedule, segments, histories


def make_baseline(coarse_xy: tuple[float, float], fine_xy: tuple[float, float]) -> tuple[dict, list[Segment]]:
    rp = R_LOCAL + 15000.0
    ra = R_LOCAL + 100000.0
    vp = math.sqrt(MU * (2.0 / rp - 1.0 / ((rp + ra) / 2.0)))
    qv0 = vp * R_LOCAL / rp
    qv1 = 57.0 * R_LOCAL / (R_LOCAL + 3000.0)
    t2 = 550.0
    s2 = 0.575 * (qv0 + qv1) * t2
    main = make_main_segment(t2, s2, MASS0, "flat", n=301)
    adjust = make_adjust_segment(25.0, 38.0, s2, float(main.mass[-1]), n=301)
    cx, cy = coarse_xy
    coarse = make_flat_segment("coarse_avoidance", 75.0, (0, 0, 2400), (0, 0, -38),
                               (cx, cy, 100), (0, 0, 0), float(adjust.mass[-1]), n=301)
    fx, fy = fine_xy
    final = (cx + fx, cy + fy)
    fine = make_flat_segment("fine_avoidance", 14.0, (cx, cy, 100), (0, 0, 0),
                             (final[0], final[1], 30), (0, 0, -10), float(coarse.mass[-1]), n=301)
    slow = make_flat_segment("slow_descent", 8.0, (final[0], final[1], 30), (0, 0, -10),
                             (final[0], final[1], 4), (0, 0, 0), float(fine.mass[-1]), n=301)
    return {"description": "fixed-time cubic, constant-g main baseline"}, [main, adjust, coarse, fine, slow]


def schedule_metrics(segments: Sequence[Segment]) -> dict:
    total_time = sum(seg.duration for seg in segments)
    final_mass = float(segments[-1].mass[-1])
    all_thrust = np.concatenate([seg.thrust for seg in segments])
    actual_v = constraint_vector(segments, design=False)
    design_v = constraint_vector(segments, design=True)
    return {
        "total_powered_time_s": float(total_time),
        "fuel_consumed_kg": float(MASS0 - final_mass),
        "final_mass_at_4m_kg": final_mass,
        "max_thrust_N": float(all_thrust.max()),
        "min_thrust_N": float(all_thrust.min()),
        "actual_constraint_status": "pass" if float(actual_v.max(initial=0.0)) <= 1e-8 else "fail",
        "design_margin_status": "pass" if float(design_v.max(initial=0.0)) <= 1e-8 else "fail",
        "total_delta_v_mps": float(sum(seg.delta_v[-1] for seg in segments)),
    }


def segment_rows(segments: Sequence[Segment]) -> list[dict]:
    rows: list[dict] = []
    for i, seg in enumerate(segments, 2):
        speed0 = float(np.linalg.norm(seg.velocity[0]))
        speed1 = float(np.linalg.norm(seg.velocity[-1]))
        rows.append({
            "stage_number": i,
            "stage": seg.name,
            "duration_s": seg.duration,
            "start_height_m": float(seg.position[0, 2]),
            "end_height_m": float(seg.position[-1, 2]),
            "start_speed_mps": speed0,
            "end_speed_mps": speed1,
            "start_mass_kg": float(seg.mass[0]),
            "end_mass_kg": float(seg.mass[-1]),
            "fuel_kg": float(seg.mass[0] - seg.mass[-1]),
            "min_thrust_N": float(seg.thrust.min()),
            "max_thrust_N": float(seg.thrust.max()),
            "delta_v_mps": float(seg.delta_v[-1]),
        })
    return rows


def transform_main_downrange(segments: Sequence[Segment]) -> None:
    total = float(segments[1].position[-1, 0])
    segments[0].position[:, 0] -= total
    segments[1].position[:, 0] -= total


def trajectory_rows(segments: Sequence[Segment]) -> list[dict]:
    rows: list[dict] = []
    elapsed = 0.0
    for stage_no, seg in enumerate(segments, 2):
        for j in range(len(seg.t)):
            if j == 0 and rows:
                continue
            force = seg.thrust_acc[j] * seg.mass[j]
            rows.append({
                "time_s": elapsed + float(seg.t[j]),
                "stage_number": stage_no,
                "stage": seg.name,
                "east_or_downrange_m": float(seg.position[j, 0]),
                "north_m": float(seg.position[j, 1]),
                "height_m": float(seg.position[j, 2]),
                "east_or_tangential_speed_mps": float(seg.velocity[j, 0]),
                "north_speed_mps": float(seg.velocity[j, 1]),
                "vertical_speed_mps": float(seg.velocity[j, 2]),
                "mass_kg": float(seg.mass[j]),
                "thrust_N": float(seg.thrust[j]),
                "thrust_east_or_tangential_N": float(force[0]),
                "thrust_north_N": float(force[1]),
                "thrust_vertical_N": float(force[2]),
            })
        elapsed += seg.duration
    return rows


def rk4_reference(seg: Segment, dt_max: float = 0.05, mu: float = MU,
                  mass_scale: float = 1.0, thrust_scale: float = 1.0,
                  direction_bias_deg: float = 0.0,
                  initial_velocity_bias: Sequence[float] = (0.0, 0.0, 0.0),
                  integration_mode: str | None = None) -> dict:
    """Independent forward integration using interpolated commanded force."""
    n_steps = int(math.ceil(seg.duration / dt_max))
    dt = seg.duration / n_steps
    force_ref = seg.thrust_acc * seg.mass[:, None]

    def force_at(t: float) -> np.ndarray:
        f = np.asarray([np.interp(t, seg.t, force_ref[:, j]) for j in range(3)], dtype=float)
        angle = math.radians(direction_bias_deg)
        if abs(angle) > 0.0:
            # Rotate in the downrange/vertical plane; north is unchanged.
            x, z = f[0], f[2]
            f[0] = math.cos(angle) * x + math.sin(angle) * z
            f[2] = -math.sin(angle) * x + math.cos(angle) * z
        return thrust_scale * f

    mode = integration_mode or seg.mode
    if mode == "central":
        h0, q0 = float(seg.position[0, 2]), float(seg.position[0, 0])
        r0 = R_LOCAL + h0
        qd0 = float(seg.velocity[0, 0]) * R_LOCAL / r0
        state = np.asarray([q0, h0, qd0, float(seg.velocity[0, 2]),
                            float(seg.mass[0]) * mass_scale], dtype=float)
        state[2] += float(initial_velocity_bias[0]) * R_LOCAL / r0
        state[3] += float(initial_velocity_bias[2])

        def deriv(t: float, y: np.ndarray) -> np.ndarray:
            q, h, qd, hd, mass = y
            force = force_at(t)
            r = R_LOCAL + h
            omega = qd / R_LOCAL
            qdd = R_LOCAL / r * (force[0] / mass - 2.0 * hd * omega)
            hdd = r * omega * omega - mu / (r * r) + force[2] / mass
            md = -float(np.linalg.norm(force)) / EXHAUST_VELOCITY
            return np.asarray([qd, hd, qdd, hdd, md])
    else:
        state = np.r_[seg.position[0], seg.velocity[0], float(seg.mass[0]) * mass_scale].astype(float)
        state[3:6] += np.asarray(initial_velocity_bias, dtype=float)

        def deriv(t: float, y: np.ndarray) -> np.ndarray:
            force = force_at(t)
            acc = force / y[6]
            acc[2] -= mu / (R_LOCAL + y[2])**2
            md = -float(np.linalg.norm(force)) / EXHAUST_VELOCITY
            return np.r_[y[3:6], acc, md]

    t = 0.0
    for _ in range(n_steps):
        k1 = deriv(t, state)
        k2 = deriv(t + 0.5*dt, state + 0.5*dt*k1)
        k3 = deriv(t + 0.5*dt, state + 0.5*dt*k2)
        k4 = deriv(t + dt, state + dt*k3)
        state += dt * (k1 + 2*k2 + 2*k3 + k4) / 6.0
        t += dt
    if mode == "central":
        q, h, qd, hd, mass = state
        velocity = np.asarray([(R_LOCAL + h) * qd / R_LOCAL, 0.0, hd])
        position = np.asarray([q, 0.0, h])
    else:
        position, velocity, mass = state[:3], state[3:6], state[6]
    return {"position": position, "velocity": velocity, "mass": float(mass)}


def reintegration_checks(segments: Sequence[Segment]) -> list[dict]:
    rows = []
    for seg in segments:
        out = rk4_reference(seg)
        pos_err = out["position"] - seg.position[-1]
        vel_err = out["velocity"] - seg.velocity[-1]
        mass_err = out["mass"] - float(seg.mass[-1])
        max_pos = float(np.max(np.abs(pos_err)))
        max_vel = float(np.max(np.abs(vel_err)))
        status = "pass" if max_pos <= 0.50 and max_vel <= 0.02 and abs(mass_err) <= 0.02 else "fail"
        rows.append({
            "stage": seg.name,
            "max_abs_position_error_m": max_pos,
            "max_abs_velocity_error_mps": max_vel,
            "mass_error_kg": float(mass_err),
            "status": status,
        })
    return rows


def orbit_solution(total_approach_m: float, final_east_m: float, final_north_m: float) -> dict:
    lat = math.radians(TARGET_LAT_DEG)
    lon = math.radians(TARGET_LON_DEG)
    r_target = np.asarray([math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat)])
    east = np.asarray([-math.sin(lon), math.cos(lon), 0.0])
    north = np.cross(r_target, east)
    north /= np.linalg.norm(north)
    delta = total_approach_m / R_LOCAL
    rpu = math.cos(delta) * r_target - math.sin(delta) * east
    tpu = math.sin(delta) * r_target + math.cos(delta) * east
    rau = -rpu
    tau = -tpu
    rp = R_LOCAL + 15000.0
    ra = R_LOCAL + 100000.0
    semi_major = 0.5 * (rp + ra)
    vp = math.sqrt(MU * (2.0/rp - 1.0/semi_major))
    va = math.sqrt(MU * (2.0/ra - 1.0/semi_major))

    def lon_lat(u: np.ndarray) -> tuple[float, float]:
        return math.degrees(math.atan2(u[1], u[0])), math.degrees(math.asin(u[2]))

    plon, plat = lon_lat(rpu)
    alon, alat = lon_lat(rau)
    final_u = r_target + (final_east_m/R_LOCAL)*east + (final_north_m/R_LOCAL)*north
    final_u /= np.linalg.norm(final_u)
    flon, flat = lon_lat(final_u)
    normal = np.cross(rpu, tpu)
    normal /= np.linalg.norm(normal)
    inclination = math.degrees(math.acos(np.clip(normal[2], -1.0, 1.0)))
    return {
        "mu_m3_s2": MU,
        "local_reference_radius_m": R_LOCAL,
        "semi_major_axis_m": semi_major,
        "eccentricity": (ra-rp)/(ra+rp),
        "approach_central_angle_deg": math.degrees(delta),
        "orbit_plane_assumption": "prograde due-east tangent at nominal landing point (minimum inclination)",
        "inclination_deg": inclination,
        "pericenter": {
            "radius_m": rp, "altitude_above_local_datum_m": 15000.0,
            "longitude_deg_east": plon, "latitude_deg_north": plat,
            "position_moon_fixed_km": (rpu * rp / 1000.0).tolist(),
            "speed_mps": vp, "direction_unit_moon_fixed": tpu.tolist(),
            "velocity_mps_moon_fixed": (tpu * vp).tolist(),
        },
        "apocenter": {
            "radius_m": ra, "altitude_above_local_datum_m": 100000.0,
            "longitude_deg_east": alon, "latitude_deg_north": alat,
            "position_moon_fixed_km": (rau * ra / 1000.0).tolist(),
            "speed_mps": va, "direction_unit_moon_fixed": tau.tolist(),
            "velocity_mps_moon_fixed": (tau * va).tolist(),
        },
        "nominal_landing_point": {"longitude_deg_east": TARGET_LON_DEG,
                                  "latitude_deg_north": TARGET_LAT_DEG,
                                  "elevation_m": LANDING_ELEVATION},
        "dem_selected_landing_point_if_image_axes_are_east_north": {
            "longitude_deg_east": flon, "latitude_deg_north": flat,
            "east_offset_m": final_east_m, "north_offset_m": final_north_m,
            "status": "needs_review",
            "reason": "attachments do not state TIFF north/east orientation",
        },
    }


def path_parameter_sensitivity(schedule: dict, coarse_xy: tuple[float, float],
                               fine_xy: tuple[float, float]) -> list[dict]:
    cases = [
        ("nominal", 1.0, 1.0, 1.0),
        ("initial_mass_-5pct", 0.95, 1.0, 1.0),
        ("initial_mass_+5pct", 1.05, 1.0, 1.0),
        ("mu_-0.5pct", 1.0, 0.995, 1.0),
        ("mu_+0.5pct", 1.0, 1.005, 1.0),
        ("ve_-3pct", 1.0, 1.0, 0.97),
        ("ve_+3pct", 1.0, 1.0, 1.03),
    ]
    rows = []
    for name, mscale, muscale, vescale in cases:
        m0 = MASS0 * mscale
        mu = MU * muscale
        ve = EXHAUST_VELOCITY * vescale
        main = make_main_segment(schedule["main_duration_s"], schedule["main_downrange_m"],
                                 m0, "central", n=301, mu=mu, exhaust_velocity=ve)
        adjust = make_adjust_segment(schedule["adjust_duration_s"],
                                     schedule["adjust_terminal_down_speed_mps"],
                                     schedule["main_downrange_m"], float(main.mass[-1]), n=301,
                                     mu=mu, exhaust_velocity=ve)
        cx, cy = coarse_xy
        coarse = make_flat_segment("coarse_avoidance", schedule["coarse_duration_s"],
                                   (0, 0, 2400), (0, 0, -schedule["adjust_terminal_down_speed_mps"]),
                                   (cx, cy, 100), (0, 0, 0), float(adjust.mass[-1]), n=301,
                                   mu=mu, exhaust_velocity=ve)
        fx, fy = fine_xy
        final = (cx+fx, cy+fy)
        fine = make_flat_segment("fine_avoidance", schedule["fine_duration_s"],
                                 (cx, cy, 100), (0, 0, 0), (final[0], final[1], 30),
                                 (0, 0, -schedule["fine_terminal_down_speed_mps"]),
                                 float(coarse.mass[-1]), n=301, mu=mu, exhaust_velocity=ve)
        slow = make_flat_segment("slow_descent", schedule["slow_duration_s"],
                                 (final[0], final[1], 30),
                                 (0, 0, -schedule["fine_terminal_down_speed_mps"]),
                                 (final[0], final[1], 4), (0, 0, 0), float(fine.mass[-1]),
                                 n=301, mu=mu, exhaust_velocity=ve)
        segs = [main, adjust, coarse, fine, slow]
        thrust = np.concatenate([s.thrust for s in segs])
        status = "pass" if thrust.min() >= THRUST_MIN and thrust.max() <= THRUST_MAX else "fail"
        rows.append({
            "case": name,
            "fuel_kg": m0 - float(slow.mass[-1]),
            "final_mass_kg": float(slow.mass[-1]),
            "min_thrust_N": float(thrust.min()),
            "max_thrust_N": float(thrust.max()),
            "problem_thrust_bounds_status": status,
        })
    return rows


def open_loop_main_sensitivity(main: Segment) -> list[dict]:
    cases = [
        ("tangential_velocity_+1mps", 1.0, 1.0, 0.0, (1.0, 0.0, 0.0), 1.0),
        ("tangential_velocity_-1mps", 1.0, 1.0, 0.0, (-1.0, 0.0, 0.0), 1.0),
        ("initial_mass_+1pct", 1.01, 1.0, 0.0, (0.0, 0.0, 0.0), 1.0),
        ("initial_mass_-1pct", 0.99, 1.0, 0.0, (0.0, 0.0, 0.0), 1.0),
        ("thrust_+1pct", 1.0, 1.01, 0.0, (0.0, 0.0, 0.0), 1.0),
        ("thrust_-1pct", 1.0, 0.99, 0.0, (0.0, 0.0, 0.0), 1.0),
        ("direction_+0.2deg", 1.0, 1.0, 0.2, (0.0, 0.0, 0.0), 1.0),
        ("direction_-0.2deg", 1.0, 1.0, -0.2, (0.0, 0.0, 0.0), 1.0),
        ("mu_+0.1pct", 1.0, 1.0, 0.0, (0.0, 0.0, 0.0), 1.001),
        ("mu_-0.1pct", 1.0, 1.0, 0.0, (0.0, 0.0, 0.0), 0.999),
    ]
    rows = []
    for name, mscale, tscale, angle, vbias, muscale in cases:
        out = rk4_reference(main, dt_max=0.15, mu=MU*muscale, mass_scale=mscale,
                            thrust_scale=tscale, direction_bias_deg=angle,
                            initial_velocity_bias=vbias)
        pos_err = out["position"] - main.position[-1]
        vel_err = out["velocity"] - main.velocity[-1]
        rows.append({
            "case": name,
            "downrange_error_m_at_nominal_time": float(pos_err[0]),
            "height_error_m_at_nominal_time": float(pos_err[2]),
            "tangential_speed_error_mps": float(vel_err[0]),
            "vertical_speed_error_mps": float(vel_err[2]),
            "mass_error_kg": float(out["mass"] - main.mass[-1]),
            "evidence_scope": "open_loop_fixed_time",
        })
    return rows


def dem_robustness(field: HazardField, nominal: dict) -> list[dict]:
    rows = []
    weight_sets = {
        "nominal": (0.50, 0.25, 0.15, 0.10),
        "slope_heavy": (0.65, 0.15, 0.10, 0.10),
        "roughness_heavy": (0.35, 0.40, 0.15, 0.10),
    }
    for radius in (3.0, 4.0, 5.0, 6.0, 7.0):
        for label, weights in weight_sets.items():
            site, _, _ = select_site(field, radius, 45.0, weights)
            shift = math.hypot(site["east_offset_m"] - nominal["east_offset_m"],
                               site["north_offset_m"] - nominal["north_offset_m"])
            checks = hazard_checks(site, "fine")
            rows.append({
                "footprint_radius_m": radius,
                "weight_case": label,
                "east_offset_m": site["east_offset_m"],
                "north_offset_m": site["north_offset_m"],
                "shift_from_nominal_m": shift,
                "rms_slope_deg": site["rms_slope_deg"],
                "roughness_rms_m": site["roughness_rms_m"],
                "relief_sd_m": site["relief_sd_m"],
                "hard_threshold_status": checks["overall"],
            })
    return rows


def draw_hazard_map(field: HazardField, selected: dict, center: dict, path: Path,
                    title: <REFERENCE_TITLE_REDACTED>
    size = 850
    gray = Image.fromarray(np.clip(field.height / max(1e-9, field.height.max()) * 255, 0, 255).astype(np.uint8))
    gray = gray.resize((size, size), Image.Resampling.BILINEAR).convert("RGB")
    canvas = Image.new("RGB", (size, size + 70), "white")
    canvas.paste(gray, (0, 70))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((15, 15), title, fill="black", font=font)
    draw.text((15, 37), "red: image centre; cyan: selected site", fill="black", font=font)
    rows, cols = field.height.shape
    def map_point(site: dict) -> tuple[int, int]:
        return int(site["column_zero_based"] / (cols - 1) * (size - 1)), 70 + int(site["row_zero_based"] / (rows - 1) * (size - 1))
    for site, color, radius in [(center, "red", 9), (selected, "cyan", 11)]:
        x, y = map_point(site)
        draw.line((x-radius, y, x+radius, y), fill=color, width=3)
        draw.line((x, y-radius, x, y+radius), fill=color, width=3)
    foot_px = selected["footprint_radius_m"] / (cols * field.resolution_m) * size
    x, y = map_point(selected)
    draw.ellipse((x-foot_px, y-foot_px, x+foot_px, y+foot_px), outline="cyan", width=2)
    canvas.save(path)


def draw_line_panels(segments: Sequence[Segment], path: Path) -> None:
    rows = trajectory_rows(segments)
    t = np.asarray([r["time_s"] for r in rows])
    series = [
        ("Height (km)", np.asarray([r["height_m"] for r in rows]) / 1000.0, "#1f77b4"),
        ("Speed (m/s)", np.asarray([math.sqrt(r["east_or_tangential_speed_mps"]**2 + r["north_speed_mps"]**2 + r["vertical_speed_mps"]**2) for r in rows]), "#d62728"),
        ("Thrust (kN)", np.asarray([r["thrust_N"] for r in rows]) / 1000.0, "#2ca02c"),
        ("Mass (kg)", np.asarray([r["mass_kg"] for r in rows]), "#9467bd"),
    ]
    W, H = 1200, 800
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    panels = [(60, 50, 570, 360), (630, 50, 1140, 360), (60, 440, 570, 750), (630, 440, 1140, 750)]
    for (title, y, color), (x0, y0, x1, y1) in zip(series, panels):
        ymin, ymax = float(np.min(y)), float(np.max(y))
        pad = 0.05 * max(1e-9, ymax-ymin)
        ymin, ymax = ymin-pad, ymax+pad
        draw.rectangle((x0, y0, x1, y1), outline="black", width=1)
        draw.text((x0+6, y0+6), title, fill="black", font=font)
        pts = []
        for ti, yi in zip(t, y):
            px = x0 + (ti-t[0])/(t[-1]-t[0])*(x1-x0)
            py = y1 - (yi-ymin)/(ymax-ymin)*(y1-y0)
            pts.append((float(px), float(py)))
        draw.line(pts, fill=color, width=2)
        draw.text((x0, y1+5), f"0 s", fill="black", font=font)
        draw.text((x1-55, y1+5), f"{t[-1]:.0f} s", fill="black", font=font)
        draw.text((x0+5, y0+24), f"max {np.max(y):.3g}", fill="black", font=font)
        draw.text((x0+5, y0+40), f"min {np.min(y):.3g}", fill="black", font=font)
    canvas.save(path)


def draw_trajectory(segments: Sequence[Segment], path: Path) -> None:
    W, H = 1400, 690
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]

    panels = [
        {"box": (65, 70, 620, 585), "segments": (0, 1), "xscale": 1000.0,
         "xlim": (-450.0, 1.0), "ylim": (0.0, 15.5),
         "title": "Overall approach", "xlabel": "downrange (km)", "ylabel": "height (km)",
         "yscale": 1000.0},
        {"box": (685, 70, 1035, 585), "segments": (1, 2), "xscale": 1.0,
         "xlim": (-470.0, 15.0), "ylim": (0.0, 3100.0),
         "title": "3 km to 100 m", "xlabel": "local east (m)", "ylabel": "height (m)",
         "yscale": 1.0},
        {"box": (1100, 70, 1360, 585), "segments": (3, 4), "xscale": 1.0,
         "xlim": (-1.5, 8.0), "ylim": (0.0, 110.0),
         "title": "100 m to 4 m", "xlabel": "local east (m)", "ylabel": "height (m)",
         "yscale": 1.0},
    ]

    for panel in panels:
        x0, y0, x1, y1 = panel["box"]
        xmin, xmax = panel["xlim"]
        ymin, ymax = panel["ylim"]

        def pt(xx: float, yy: float) -> tuple[float, float]:
            return (x0 + (xx-xmin)/(xmax-xmin)*(x1-x0),
                    y1 - (yy-ymin)/(ymax-ymin)*(y1-y0))

        draw.rectangle((x0, y0, x1, y1), outline="black", width=2)
        draw.text((x0+5, y0-25), panel["title"], fill="black", font=font)
        draw.text((x0+(x1-x0)//2-45, y1+28), panel["xlabel"], fill="black", font=font)
        draw.text((x0+5, y0+5), panel["ylabel"], fill="black", font=font)
        draw.text((x0, y1+5), f"{xmin:g}", fill="black", font=font)
        draw.text((x1-35, y1+5), f"{xmax:g}", fill="black", font=font)
        draw.text((x0-42, y1-5), f"{ymin:g}", fill="black", font=font)
        draw.text((x0-42, y0), f"{ymax:g}", fill="black", font=font)
        for idx in panel["segments"]:
            seg = segments[idx]
            xx = seg.position[:, 0] / panel["xscale"]
            yy = seg.position[:, 2] / panel["yscale"]
            pts = [pt(float(a), float(b)) for a, b in zip(xx, yy)
                   if xmin-1e-9 <= float(a) <= xmax+1e-9 and ymin-1e-9 <= float(b) <= ymax+1e-9]
            if len(pts) >= 2:
                draw.line(pts, fill=colors[idx], width=4)

    legend_y = 645
    names = ["main deceleration", "rapid adjustment", "coarse avoidance",
             "fine avoidance", "slow descent"]
    x = 80
    for name, color in zip(names, colors):
        draw.line((x, legend_y, x+28, legend_y), fill=color, width=5)
        draw.text((x+35, legend_y-7), name, fill="black", font=font)
        x += 255
    canvas.save(path)


def draw_sensitivity(rows: Sequence[dict], path: Path) -> None:
    labels = [r["case"] for r in rows]
    values = [abs(float(r["height_error_m_at_nominal_time"])) for r in rows]
    W, H = 1200, 650
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    left, top, bottom = 300, 45, 45
    vmax = max(values) * 1.05 if values else 1.0
    bar_h = (H-top-bottom) / max(1, len(values))
    draw.text((20, 15), "Open-loop main-stage height error at nominal terminal time (absolute)", fill="black", font=font)
    for i, (label, value) in enumerate(zip(labels, values)):
        y0 = top + i*bar_h + 3
        y1 = top + (i+1)*bar_h - 3
        draw.text((10, y0), label, fill="black", font=font)
        x1 = left + value/vmax*(W-left-80)
        draw.rectangle((left, y0, x1, y1), fill="#4c78a8")
        draw.text((x1+5, y0), f"{value:.1f} m", fill="black", font=font)
    canvas.save(path)


def write_result_macros(path: Path, orbit: dict, metrics: dict, stage_table: Sequence[dict],
                        coarse_site: dict, fine_site: dict, final_east: float,
                        final_north: float, impact_speed: float,
                        reintegration: Sequence[dict], model_comparison: Sequence[dict]) -> None:
    stages = {row["stage"]: row for row in stage_table}
    macros = {
        "PericenterLonW": -orbit["pericenter"]["longitude_deg_east"],
        "PericenterLatN": orbit["pericenter"]["latitude_deg_north"],
        "ApocenterLonE": orbit["apocenter"]["longitude_deg_east"],
        "ApocenterLatS": -orbit["apocenter"]["latitude_deg_north"],
        "PericenterSpeed": orbit["pericenter"]["speed_mps"],
        "ApocenterSpeed": orbit["apocenter"]["speed_mps"],
        "ApproachAngle": orbit["approach_central_angle_deg"],
        "FuelMass": metrics["fuel_consumed_kg"],
        "FinalMass": metrics["final_mass_at_4m_kg"],
        "PoweredTime": metrics["total_powered_time_s"],
        "ThrustMinimum": metrics["min_thrust_N"] / 1000.0,
        "ThrustMaximum": metrics["max_thrust_N"] / 1000.0,
        "MainTime": stages["main_deceleration"]["duration_s"],
        "AdjustTime": stages["rapid_adjustment"]["duration_s"],
        "CoarseTime": stages["coarse_avoidance"]["duration_s"],
        "FineTime": stages["fine_avoidance"]["duration_s"],
        "SlowTime": stages["slow_descent"]["duration_s"],
        "MainFuel": stages["main_deceleration"]["fuel_kg"],
        "AdjustFuel": stages["rapid_adjustment"]["fuel_kg"],
        "CoarseFuel": stages["coarse_avoidance"]["fuel_kg"],
        "FineFuel": stages["fine_avoidance"]["fuel_kg"],
        "SlowFuel": stages["slow_descent"]["fuel_kg"],
        "AdjustDownSpeed": stages["rapid_adjustment"]["end_speed_mps"],
        "FineDownSpeed": stages["fine_avoidance"]["end_speed_mps"],
        "CoarseRow": coarse_site["row_one_based"],
        "CoarseColumn": coarse_site["column_one_based"],
        "FineRow": fine_site["row_one_based"],
        "FineColumn": fine_site["column_one_based"],
        "FineEast": fine_site["east_offset_m"],
        "FineNorth": fine_site["north_offset_m"],
        "FineDistance": fine_site["distance_from_image_center_m"],
        "FinalEast": final_east,
        "FinalNorth": final_north,
        "FinalOffset": math.hypot(final_east, final_north),
        "FineSlope": fine_site["rms_slope_deg"],
        "FineRoughness": fine_site["roughness_rms_m"],
        "ImpactSpeed": impact_speed,
        "ReintegrationPositionError": max(r["max_abs_position_error_m"] for r in reintegration),
        "BaselineFuel": model_comparison[0]["fuel_kg"],
        "FlatFuel": model_comparison[1]["fuel_kg"],
    }
    integer_names = {"CoarseRow", "CoarseColumn", "FineRow", "FineColumn"}
    lines = ["% Generated by code/solve.py; do not edit numerical values by hand."]
    for name, value in macros.items():
        if name in integer_names:
            rendered = str(int(value))
        elif abs(float(value)) >= 1000:
            rendered = f"{float(value):.3f}"
        else:
            rendered = f"{float(value):.4f}"
        lines.append(f"\\newcommand{{\\{name}}}{{{rendered}}}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ensure_dirs()

    coarse_field = build_hazard_field("coarse_2400m", COARSE_TIF, 1.0, 1.0, 20.0)
    coarse_site, coarse_center, _ = select_site(coarse_field, 50.0, 900.0)
    coarse_checks = hazard_checks(coarse_site, "coarse")
    draw_hazard_map(coarse_field, coarse_site, coarse_center, FIGURES / "<SOURCE_FILE_REDACTED>",
                    "Coarse DEM: 1 m/pixel, 50 m assessment radius")
    del coarse_field

    fine_field = build_hazard_field("fine_100m", FINE_TIF, 0.1, 0.1, 2.0)
    fine_site, fine_center, _ = select_site(fine_field, 5.0, 45.0)
    fine_checks = hazard_checks(fine_site, "fine")
    draw_hazard_map(fine_field, fine_site, fine_center, FIGURES / "<SOURCE_FILE_REDACTED>",
                    "Fine DEM: 0.1 m/pixel, 5 m landing footprint radius")
    dem_sensitivity = dem_robustness(fine_field, fine_site)
    csv_dump(RESULTS / "<SOURCE_FILE_REDACTED>", dem_sensitivity)
    del fine_field

    coarse_xy = (coarse_site["east_offset_m"], coarse_site["north_offset_m"])
    fine_xy = (fine_site["east_offset_m"], fine_site["north_offset_m"])

    baseline_info, baseline_segments = make_baseline(coarse_xy, fine_xy)
    baseline_metrics = schedule_metrics(baseline_segments)

    flat_schedule, flat_segments, flat_history = optimise_schedule(coarse_xy, fine_xy, "flat")
    flat_metrics = schedule_metrics(flat_segments)

    schedule, segments, history = optimise_schedule(coarse_xy, fine_xy, "central")
    transform_main_downrange(segments)
    selected_metrics = schedule_metrics(segments)
    stage_table = segment_rows(segments)
    trajectory = trajectory_rows(segments)
    csv_dump(RESULTS / "<SOURCE_FILE_REDACTED>", trajectory)
    csv_dump(RESULTS / "<SOURCE_FILE_REDACTED>", stage_table)
    csv_dump(RESULTS / "<SOURCE_FILE_REDACTED>", flat_history + history)

    total_approach = abs(float(segments[0].position[0, 0]))
    final_east = coarse_xy[0] + fine_xy[0]
    final_north = coarse_xy[1] + fine_xy[1]
    orbit = orbit_solution(total_approach, final_east, final_north)

    reintegration = reintegration_checks(segments)
    csv_dump(RESULTS / "<SOURCE_FILE_REDACTED>", reintegration)
    open_loop = open_loop_main_sensitivity(segments[0])
    csv_dump(RESULTS / "<SOURCE_FILE_REDACTED>", open_loop)
    parameter_rows = path_parameter_sensitivity(schedule, coarse_xy, fine_xy)
    csv_dump(RESULTS / "<SOURCE_FILE_REDACTED>", parameter_rows)

    g_surface = MU / R_LOCAL**2
    free_fall_time = math.sqrt(2.0 * 4.0 / g_surface)
    impact_speed = g_surface * free_fall_time

    # Apply the flat candidate's force history to central dynamics to expose its
    # curvature-model mismatch.  The segment still has unshifted coordinates.
    flat_main_check = rk4_reference(flat_segments[0], dt_max=0.05, mu=MU,
                                    integration_mode="central")
    flat_main_position_error = flat_main_check["position"] - flat_segments[0].position[-1]
    flat_main_velocity_error = flat_main_check["velocity"] - flat_segments[0].velocity[-1]

    model_comparison = [
        {
            "model": "fixed_schedule_flat_baseline",
            "fuel_kg": baseline_metrics["fuel_consumed_kg"],
            "powered_time_s": baseline_metrics["total_powered_time_s"],
            "max_thrust_N": baseline_metrics["max_thrust_N"],
            "min_thrust_N": baseline_metrics["min_thrust_N"],
            "constraint_status": baseline_metrics["actual_constraint_status"],
            "curvature_validation": "not_applicable_baseline",
            "selected": "no",
        },
        {
            "model": "optimised_flat_gravity",
            "fuel_kg": flat_metrics["fuel_consumed_kg"],
            "powered_time_s": flat_metrics["total_powered_time_s"],
            "max_thrust_N": flat_metrics["max_thrust_N"],
            "min_thrust_N": flat_metrics["min_thrust_N"],
            "constraint_status": flat_metrics["actual_constraint_status"],
            "curvature_validation": "fail" if max(abs(flat_main_position_error[2]), abs(flat_main_velocity_error[0])) > 1.0 else "pass",
            "selected": "no",
        },
        {
            "model": "robust_central_local_direct_transcription",
            "fuel_kg": selected_metrics["fuel_consumed_kg"],
            "powered_time_s": selected_metrics["total_powered_time_s"],
            "max_thrust_N": selected_metrics["max_thrust_N"],
            "min_thrust_N": selected_metrics["min_thrust_N"],
            "constraint_status": selected_metrics["actual_constraint_status"],
            "curvature_validation": "pass" if all(r["status"] == "pass" for r in reintegration[:2]) else "fail",
            "selected": "yes",
        },
    ]
    csv_dump(RESULTS / "<SOURCE_FILE_REDACTED>", model_comparison)
    write_result_macros(PAPER / "result_macros.tex", orbit, selected_metrics, stage_table,
                        coarse_site, fine_site, final_east, final_north, impact_speed,
                        reintegration, model_comparison)

    dem = {
        "coarse": {
            "source": str(COARSE_TIF.relative_to(ROOT)),
            "shape": [2300, 2300], "horizontal_resolution_m_per_pixel": 1.0,
            "height_unit_m_per_value": 1.0,
            "selected": coarse_site, "image_center": coarse_center,
            "hard_checks": coarse_checks,
        },
        "fine": {
            "source": str(FINE_TIF.relative_to(ROOT)),
            "shape": [1000, 1000], "horizontal_resolution_m_per_pixel": 0.1,
            "height_unit_m_per_value": 0.1,
            "selected": fine_site, "image_center": fine_center,
            "hard_checks": fine_checks,
        },
        "combined_final_offset": {"east_m": final_east, "north_m": final_north,
                                  "distance_m": math.hypot(final_east, final_north)},
        "axis_orientation_status": "needs_review",
    }
    json_dump(RESULTS / "dem_sites.json", dem)
    json_dump(RESULTS / "orbit_solution.json", orbit)

    source_files = [PROBLEM_DOC, BACKGROUND_DOC, STAGES_DOC, COARSE_TIF, FINE_TIF]
    evidence = {
        "input_identity": {
            "status": "pass",
            "files": {str(p.relative_to(ROOT)).replace("\\", "/"): sha256_file(p)
                      for p in source_files},
        },
        "execution_reintegration": {
            "status": "pass" if all(r["status"] == "pass" for r in reintegration) else "fail",
            "checks": reintegration,
        },
        "internal_constraints": {
            "status": selected_metrics["actual_constraint_status"],
            "design_margin_status": selected_metrics["design_margin_status"],
        },
        "dem_internal_safety": {
            "status": "pass" if coarse_checks["overall"] == "pass" and fine_checks["overall"] == "pass" else "fail",
        },
        "external_flight_validity": {
            "status": "needs_review",
            "reason": "no flight telemetry, engine dynamics, navigation covariance, or stated TIFF orientation is supplied",
        },
    }
    summary = {
        "case_id": "2014A",
        "phase": "solve",
        "seed": SEED,
        "status_vocabulary": ["pass", "fail", "needs_review"],
        "selected_model": "robust_central_local_direct_transcription",
        "schedule": schedule,
        "metrics": selected_metrics,
        "stage_summary": stage_table,
        "orbit": orbit,
        "dem": dem,
        "free_fall_from_4m": {
            "gravity_mps2": g_surface,
            "time_s": free_fall_time,
            "impact_speed_mps": impact_speed,
            "status": "needs_review",
            "reason": "problem states a buffer mechanism but supplies no allowable impact-speed limit",
        },
        "model_comparison": model_comparison,
        "flat_candidate_central_mismatch": {
            "position_error_m": flat_main_position_error.tolist(),
            "velocity_error_mps": flat_main_velocity_error.tolist(),
        },
        "evidence": evidence,
        "limitations": [
            "spherical Moon with local radius equal to mean radius plus nominal-site elevation",
            "prograde due-east minimum-inclination approach is an engineering choice; the problem gives no incoming orbit plane",
            "attitude-engine propellant, actuator lag, lunar rotation, mascons, and navigation delay are omitted",
            "DEM rows/columns are treated as north/east only for an illustrative geographic offset; orientation is not stated",
            "the hard terrain limits are engineering assumptions, not supplied spacecraft certification limits",
        ],
    }
    json_dump(RESULTS / "summary.json", summary)
    json_dump(RESULTS / "evidence_matrix.json", evidence)

    solution_report = {
        "case_id": "2014A",
        "phase": "solve",
        "overall_status": "needs_review",
        "overall_status_reason": "internal numerical checks pass; external flight validity and TIFF compass orientation lack supplied evidence",
        "problem_coverage": {
            "question_1_orbit_points_and_velocities": "pass",
            "question_2_six_stage_trajectory_and_control": "pass",
            "question_3_error_and_sensitivity": "pass",
        },
        "key_results": {
            "pericenter": orbit["pericenter"],
            "apocenter": orbit["apocenter"],
            "approach_central_angle_deg": orbit["approach_central_angle_deg"],
            "fuel_consumed_kg": selected_metrics["fuel_consumed_kg"],
            "mass_at_4m_kg": selected_metrics["final_mass_at_4m_kg"],
            "powered_time_s": selected_metrics["total_powered_time_s"],
            "thrust_range_N": [selected_metrics["min_thrust_N"], selected_metrics["max_thrust_N"]],
            "coarse_site": coarse_site,
            "fine_site": fine_site,
            "combined_landing_offset_m": dem["combined_final_offset"],
            "free_fall_impact_speed_mps": impact_speed,
        },
        "validation": evidence,
        "traceability": {
            "question_1": ["results/orbit_solution.json", "results/summary.json", "paper/main.tex"],
            "question_2": ["results/<SOURCE_FILE_REDACTED>", "results/<SOURCE_FILE_REDACTED>", "figures/<SOURCE_FILE_REDACTED>", "paper/main.tex"],
            "question_3": ["results/<SOURCE_FILE_REDACTED>", "results/<SOURCE_FILE_REDACTED>", "results/<SOURCE_FILE_REDACTED>", "paper/main.tex"],
        },
        "limitations": summary["limitations"],
        "freeze_status": "not_frozen",
        "reflection_status": "not_performed",
    }
    # JSON syntax is valid YAML 1.2 and avoids an undeclared PyYAML dependency.
    json_dump(ROOT / "solution-report.yaml", solution_report)

    draw_line_panels(segments, FIGURES / "<SOURCE_FILE_REDACTED>")
    draw_trajectory(segments, FIGURES / "<SOURCE_FILE_REDACTED>")
    draw_sensitivity(open_loop, FIGURES / "<SOURCE_FILE_REDACTED>")

    manifest_files = [
        RESULTS / "summary.json", RESULTS / "<SOURCE_FILE_REDACTED>", RESULTS / "<SOURCE_FILE_REDACTED>",
        RESULTS / "dem_sites.json", RESULTS / "orbit_solution.json",
        RESULTS / "<SOURCE_FILE_REDACTED>", RESULTS / "<SOURCE_FILE_REDACTED>",
        RESULTS / "<SOURCE_FILE_REDACTED>", RESULTS / "<SOURCE_FILE_REDACTED>",
        RESULTS / "<SOURCE_FILE_REDACTED>", FIGURES / "<SOURCE_FILE_REDACTED>",
        FIGURES / "<SOURCE_FILE_REDACTED>", FIGURES / "<SOURCE_FILE_REDACTED>",
        FIGURES / "<SOURCE_FILE_REDACTED>", FIGURES / "<SOURCE_FILE_REDACTED>",
        ROOT / "solution-report.yaml",
        PAPER / "result_macros.tex",
    ]
    manifest = {
        "generated_by": "code/solve.py",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "seed": SEED,
        "files": {str(p.relative_to(ROOT)).replace("\\", "/"): sha256_file(p) for p in manifest_files},
    }
    json_dump(RESULTS / "manifest.json", manifest)
    reproducibility = {
        "case_id": "2014A",
        "phase": "solve",
        "status": "pass" if evidence["execution_reintegration"]["status"] == "pass" else "fail",
        "entrypoint": "code/solve.py",
        "command": "python code/solve.py",
        "verification_command": "python code/verify.py",
        "seed": SEED,
        "declared_dependencies": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pillow": getattr(sys.modules.get("PIL"), "__version__", "12.3.0"),
        },
        "input_sha256": evidence["input_identity"]["files"],
        "output_manifest": "results/manifest.json",
        "output_manifest_sha256": sha256_file(RESULTS / "manifest.json"),
        "determinism_scope": "same inputs, dependency versions, and platform; verified separately by repeated execution",
        "external_validity": "needs_review",
    }
    json_dump(ROOT / "reproducibility.yaml", reproducibility)
    print(json.dumps({
        "status": evidence["internal_constraints"]["status"],
        "fuel_kg": selected_metrics["fuel_consumed_kg"],
        "final_mass_kg": selected_metrics["final_mass_at_4m_kg"],
        "powered_time_s": selected_metrics["total_powered_time_s"],
        "pericenter_lon_deg_east": orbit["pericenter"]["longitude_deg_east"],
        "pericenter_lat_deg_north": orbit["pericenter"]["latitude_deg_north"],
        "fine_site_offset_m": fine_site["distance_from_image_center_m"],
        "reintegration": evidence["execution_reintegration"]["status"],
    }, ensure_ascii=False, indent=2))
    overall_ok = (evidence["internal_constraints"]["status"] == "pass"
                  and evidence["execution_reintegration"]["status"] == "pass")
    return 0 if overall_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
