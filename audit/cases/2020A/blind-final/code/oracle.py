"""Independent curve-geometry oracle for the blind revision.

This module intentionally imports neither ``core`` nor ``solve``.  It starts
from raw time, temperature, and optional slope arrays, then re-derives the
crossings, interpolated peak, Q3 shaded area, Q4 symmetry metric, and every
process slack.  Keeping this implementation separate prevents a shared metric
definition from making the solver and its verifier agree on the same mistake.
"""

from __future__ import annotations

import math

import numpy as np


def _first_up(t: np.ndarray, y: np.ndarray, level: float, stop: int) -> float:
    hits = np.flatnonzero((y[:stop] < level) & (y[1 : stop + 1] >= level))
    if not len(hits):
        return float("nan")
    i = int(hits[0])
    return float(t[i] + (level - y[i]) * (t[i + 1] - t[i]) / (y[i + 1] - y[i]))


def _first_down(t: np.ndarray, y: np.ndarray, level: float, start: int) -> float:
    hits = np.flatnonzero((y[start:-1] >= level) & (y[start + 1 :] < level))
    if not len(hits):
        return float("nan")
    i = int(hits[0] + start)
    return float(t[i] + (level - y[i]) * (t[i + 1] - t[i]) / (y[i + 1] - y[i]))


def _peak(
    t: np.ndarray, y: np.ndarray, slope: np.ndarray, index: int
) -> tuple[float, float]:
    crossings = np.flatnonzero((slope[:-1] > 0.0) & (slope[1:] <= 0.0))
    if len(crossings):
        i = int(crossings[np.argmin(np.abs(t[crossings] - t[index]))])
        denominator = slope[i] - slope[i + 1]
        if denominator > 0.0:
            u = float(slope[i] / denominator)
            if 0.0 <= u <= 1.0:
                h = float(t[i + 1] - t[i])
                h00 = 2.0 * u**3 - 3.0 * u**2 + 1.0
                h10 = u**3 - 2.0 * u**2 + u
                h01 = -2.0 * u**3 + 3.0 * u**2
                h11 = u**3 - u**2
                value = float(
                    h00 * y[i]
                    + h10 * h * slope[i]
                    + h01 * y[i + 1]
                    + h11 * h * slope[i + 1]
                )
                return float(t[i] + u * h), value
    if 0 < index < len(y) - 1:
        local_t = t[index - 1 : index + 2] - t[index]
        a, b, c = np.polyfit(local_t, y[index - 1 : index + 2], 2)
        if a < 0.0:
            offset = float(-b / (2.0 * a))
            if float(local_t[0]) <= offset <= float(local_t[-1]):
                value = float(a * offset**2 + b * offset + c)
                if value >= float(y[index]) - 1e-10:
                    return float(t[index] + offset), value
    return float(t[index]), float(y[index])


def _area_between(
    t: np.ndarray,
    y: np.ndarray,
    start: float,
    end: float,
    start_value: float,
    end_value: float,
    level: float = 217.0,
) -> float:
    if not (math.isfinite(start) and math.isfinite(end)) or end < start:
        return float("nan")
    mask = (t > start) & (t < end)
    times = np.concatenate(([start], t[mask], [end]))
    values = np.interp(times, t, y)
    values[0] = start_value
    values[-1] = end_value
    return float(np.trapezoid(np.maximum(values - level, 0.0), times))


def curve_metrics_oracle(
    time_s: np.ndarray,
    temperature_C: np.ndarray,
    slope_C_s: np.ndarray | None = None,
) -> dict[str, float]:
    """Recompute all curve metrics from raw arrays without solver helpers."""
    t = np.asarray(time_s, dtype=float)
    temp = np.asarray(temperature_C, dtype=float)
    if t.ndim != 1 or temp.ndim != 1 or len(t) != len(temp) or len(t) < 3:
        raise ValueError("time and temperature must be equal one-dimensional arrays of length >= 3")
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(temp)) or not np.all(np.diff(t) > 0.0):
        raise ValueError("time/temperature arrays must be finite and time must increase strictly")
    if slope_C_s is None:
        slope = np.gradient(temp, t)
    else:
        slope = np.asarray(slope_C_s, dtype=float)
        if slope.shape != temp.shape:
            raise ValueError("slope must have the same shape as temperature")

    peak_i = int(np.argmax(temp))
    peak_t, peak_C = _peak(t, temp, slope, peak_i)
    t150 = _first_up(t, temp, 150.0, peak_i)
    t190 = _first_up(t, temp, 190.0, peak_i)
    t217_up = _first_up(t, temp, 217.0, peak_i)
    t217_down = _first_down(t, temp, 217.0, peak_i)
    rise_time = t190 - t150 if math.isfinite(t150) and math.isfinite(t190) else float("nan")
    above_time = (
        t217_down - t217_up
        if math.isfinite(t217_up) and math.isfinite(t217_down)
        else float("nan")
    )
    rising_area = _area_between(t, temp, t217_up, peak_t, 217.0, peak_C)
    cooling_area = _area_between(t, temp, peak_t, t217_down, peak_C, 217.0)
    total_area = rising_area + cooling_area

    if math.isfinite(t217_up) and math.isfinite(t217_down):
        left_duration = peak_t - t217_up
        right_duration = t217_down - peak_t
        step = float(np.median(np.diff(t)))
        span = max(left_duration, right_duration)
        u = np.arange(0.0, span + step / 2.0, step)
        left = np.maximum(np.interp(peak_t - u, t, temp) - 217.0, 0.0)
        right = np.maximum(np.interp(peak_t + u, t, temp) - 217.0, 0.0)
        symmetry_area = float(np.trapezoid(np.abs(left - right), u))
        symmetry_ratio = symmetry_area / total_area if total_area > 0.0 else float("nan")
        duration_imbalance = abs(left_duration - right_duration)
    else:
        left_duration = right_duration = symmetry_area = symmetry_ratio = duration_imbalance = float("nan")

    return {
        "rise_slope_max_C_s": float(np.max(slope[: peak_i + 1])),
        "fall_slope_min_C_s": float(np.min(slope[peak_i:])),
        "rise_150_190_s": float(rise_time),
        "above_217_s": float(above_time),
        "peak_C": float(peak_C),
        "peak_time_s": float(peak_t),
        "t150_up_s": float(t150),
        "t190_up_s": float(t190),
        "t217_up_s": float(t217_up),
        "t217_down_s": float(t217_down),
        "q3_rising_area_C_s": float(rising_area),
        "cooling_area_217_C_s": float(cooling_area),
        "total_area_above_217_C_s": float(total_area),
        "left_duration_s": float(left_duration),
        "right_duration_s": float(right_duration),
        "symmetry_area_abs_C_s": float(symmetry_area),
        "symmetry_ratio": float(symmetry_ratio),
        "duration_imbalance_s": float(duration_imbalance),
    }


def process_slacks_oracle(metrics: dict[str, float]) -> dict[str, float]:
    """Hard-coded transcription of the eight original process inequalities."""
    return {
        "rise_slope_upper": 3.0 - metrics["rise_slope_max_C_s"],
        "fall_slope_lower": metrics["fall_slope_min_C_s"] + 3.0,
        "rise_150_190_lower": metrics["rise_150_190_s"] - 60.0,
        "rise_150_190_upper": 120.0 - metrics["rise_150_190_s"],
        "above_217_lower": metrics["above_217_s"] - 40.0,
        "above_217_upper": 90.0 - metrics["above_217_s"],
        "peak_lower": metrics["peak_C"] - 240.0,
        "peak_upper": 250.0 - metrics["peak_C"],
    }


def process_status_oracle(metrics: dict[str, float], tolerance: float = 0.0) -> str:
    values = np.asarray(list(process_slacks_oracle(metrics).values()), dtype=float)
    if not np.all(np.isfinite(values)):
        return "fail"
    return "pass" if float(np.min(values)) >= -float(tolerance) else "fail"


def analytic_oracle_tests() -> dict:
    """Known-geometry tests, including a mutation that recreates the V1 bug."""
    t = np.arange(5.0)
    temp = np.array([200.0, 220.0, 240.0, 220.0, 200.0])
    metrics = curve_metrics_oracle(t, temp)
    expected_half = 0.5 * (2.0 - 17.0 / 20.0) * (240.0 - 217.0)
    triangle_checks = {
        "rising_area_known_triangle": "pass"
        if abs(metrics["q3_rising_area_C_s"] - expected_half) <= 1e-10
        else "fail",
        "cooling_area_known_triangle": "pass"
        if abs(metrics["cooling_area_217_C_s"] - expected_half) <= 1e-10
        else "fail",
        "total_area_known_triangle": "pass"
        if abs(metrics["total_area_above_217_C_s"] - 2.0 * expected_half) <= 1e-10
        else "fail",
        "symmetric_triangle": "pass"
        if abs(metrics["symmetry_area_abs_C_s"]) <= 1e-10
        else "fail",
    }

    flat_metrics = curve_metrics_oracle(
        np.array([0.0, 1.0, 2.0]), np.array([25.0, 25.0, 25.0])
    )
    flat_status = "pass" if process_status_oracle(flat_metrics) == "fail" else "fail"

    # Mutation trap: substituting the full two-sided area for the shaded Q3
    # value must be rejected.  This is the exact semantic mutation missed by
    # the V1 shared validator.
    mutated_reported_q3 = metrics["total_area_above_217_C_s"]
    mutation_status = (
        "pass"
        if abs(mutated_reported_q3 - metrics["q3_rising_area_C_s"]) > 1e-6
        else "fail"
    )
    checks = {
        **triangle_checks,
        "constant_curve_rejected": flat_status,
        "full_area_as_q3_mutation_rejected": mutation_status,
    }
    return {
        "status": "pass" if all(value == "pass" for value in checks.values()) else "fail",
        "checks": checks,
        "known_triangle_expected_rising_area_C_s": expected_half,
    }
