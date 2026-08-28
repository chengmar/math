"""Core furnace model and metric definitions for the blind solution.

All distances are in cm, time in s, temperature in degC, and speed in cm/min.
The module contains no optimization state so that it can also be used by the
independent verification script.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.signal import lfilter


AMBIENT_C = 25.0
FRONT_CM = 25.0
REAR_CM = 25.0
ZONE_LENGTH_CM = 30.5
GAP_CM = 5.0
N_ZONES = 11
FURNACE_LENGTH_CM = FRONT_CM + N_ZONES * ZONE_LENGTH_CM + (N_ZONES - 1) * GAP_CM + REAR_CM
ZONE_START_CM = np.array([FRONT_CM + i * (ZONE_LENGTH_CM + GAP_CM) for i in range(N_ZONES)], dtype=float)
ZONE_END_CM = ZONE_START_CM + ZONE_LENGTH_CM
ZONE_MID_CM = (ZONE_START_CM + ZONE_END_CM) / 2.0
EXPERIMENT_SETPOINTS_C = np.array([175.0] * 5 + [195.0, 235.0, 255.0, 255.0, 25.0, 25.0])
EXPERIMENT_SPEED_CM_MIN = 70.0
PROCESS_LIMITS = {
    "rise_slope_max_C_s": (0.0, 3.0),
    "fall_slope_min_C_s": (-3.0, 0.0),
    "rise_150_190_s": (60.0, 120.0),
    "above_217_s": (40.0, 90.0),
    "peak_C": (240.0, 250.0),
}


@dataclass(frozen=True)
class ModelParameters:
    lambda_up_cm: float
    lambda_down_cm: float
    k_ref_s_inv: float
    beta: float
    k_cool_s_inv: float

    def as_dict(self) -> dict[str, float]:
        return {
            "lambda_up_cm": float(self.lambda_up_cm),
            "lambda_down_cm": float(self.lambda_down_cm),
            "k_ref_s_inv": float(self.k_ref_s_inv),
            "beta": float(self.beta),
            "k_cool_s_inv": float(self.k_cool_s_inv),
        }


def grouped_setpoints(values: Iterable[float]) -> np.ndarray:
    """Expand (zones 1-5, zone 6, zone 7, zones 8-9) to 11 zones."""
    t15, t6, t7, t89 = [float(v) for v in values]
    return np.array([t15] * 5 + [t6, t7, t89, t89, 25.0, 25.0], dtype=float)


def load_experiment(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    if df.shape[1] != 2:
        raise ValueError(f"Expected two experimental columns, got {df.shape[1]}")
    df = df.copy()
    df.columns = ["time_s", "temperature_C"]
    return df.astype(float)


def spatial_grid(dx_cm: float = 0.1) -> np.ndarray:
    n = int(round(FURNACE_LENGTH_CM / dx_cm))
    return np.linspace(0.0, FURNACE_LENGTH_CM, n + 1)


def programmed_field(setpoints_C: np.ndarray, x_cm: np.ndarray) -> np.ndarray:
    """Construct the simple interpretable programmed-temperature baseline.

    Controlled zones are constant. The front region and five-centimetre gaps
    are linearly interpolated because they are not independently controlled.
    The rear remains at workshop temperature since zones 10--11 are fixed at
    25 degC.
    """
    setpoints_C = np.asarray(setpoints_C, dtype=float)
    if setpoints_C.shape != (N_ZONES,):
        raise ValueError("setpoints_C must contain 11 zone values")
    x_cm = np.asarray(x_cm, dtype=float)
    field = np.full_like(x_cm, AMBIENT_C)
    front = (x_cm >= 0.0) & (x_cm < ZONE_START_CM[0])
    field[front] = AMBIENT_C + (setpoints_C[0] - AMBIENT_C) * x_cm[front] / FRONT_CM
    for i in range(N_ZONES):
        inside = (x_cm >= ZONE_START_CM[i]) & (x_cm <= ZONE_END_CM[i])
        field[inside] = setpoints_C[i]
        if i < N_ZONES - 1:
            gap = (x_cm > ZONE_END_CM[i]) & (x_cm < ZONE_START_CM[i + 1])
            fraction = (x_cm[gap] - ZONE_END_CM[i]) / GAP_CM
            field[gap] = setpoints_C[i] + fraction * (setpoints_C[i + 1] - setpoints_C[i])
    return field


def directional_air_field(
    setpoints_C: np.ndarray,
    lambda_up_cm: float,
    lambda_down_cm: float,
    dx_cm: float = 0.1,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return x, programmed field, and effective air field.

    The effective field follows dT_a/dx=(T_set-T_a)/lambda. Separate lengths
    for upward and downward transitions represent unequal upstream/downstream
    thermal influence without introducing a separate parameter for every gap.
    """
    if lambda_up_cm <= 0.0 or lambda_down_cm <= 0.0:
        raise ValueError("Spatial relaxation lengths must be positive")
    x = spatial_grid(dx_cm)
    target = programmed_field(setpoints_C, x)
    step = x[1] - x[0]
    decay_up = np.exp(-step / lambda_up_cm)
    # First compute the all-heating recurrence.  Its first target<air index is
    # exactly the point at which the original directional recurrence switches.
    up_tail, _ = lfilter(
        [1.0 - decay_up], [1.0, -decay_up], target[:-1], zi=[decay_up * AMBIENT_C]
    )
    air_up = np.concatenate(([AMBIENT_C], up_tail))
    switch_candidates = np.flatnonzero(target[:-1] < air_up[:-1])
    if not len(switch_candidates):
        air = air_up
    else:
        switch = int(switch_candidates[0])
        decay_down = np.exp(-step / lambda_down_cm)
        down_tail, _ = lfilter(
            [1.0 - decay_down],
            [1.0, -decay_down],
            target[switch:-1],
            zi=[decay_down * air_up[switch]],
        )
        air = air_up.copy()
        air[switch + 1 :] = down_tail
    return x, target, air


def _affine_recurrence(initial: float, forcing: np.ndarray, decay: np.ndarray) -> np.ndarray:
    """Vectorized y[i+1]=decay[i]*y[i]+(1-decay[i])*forcing[i]."""
    forcing = np.asarray(forcing, dtype=float)
    decay = np.asarray(decay, dtype=float)
    if forcing.shape != decay.shape:
        raise ValueError("forcing and decay must have the same shape")
    products = np.concatenate(([1.0], np.cumprod(decay)))
    weighted = (1.0 - decay) * forcing / products[1:]
    cumulative = np.concatenate(([0.0], np.cumsum(weighted)))
    return products * (float(initial) + cumulative)


def _time_grid(speed_cm_min: float, dt_s: float) -> np.ndarray:
    if not 65.0 - 1e-12 <= speed_cm_min <= 100.0 + 1e-12:
        raise ValueError("speed_cm_min is outside the problem range [65, 100]")
    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")
    duration = FURNACE_LENGTH_CM / (speed_cm_min / 60.0)
    n_full = int(np.floor(duration / dt_s))
    t = np.arange(n_full + 1, dtype=float) * dt_s
    if duration - t[-1] > 1e-10:
        t = np.append(t, duration)
    else:
        t[-1] = duration
    return t


def simulate(
    params: ModelParameters,
    setpoints_C: np.ndarray,
    speed_cm_min: float,
    dt_s: float = 0.1,
    dx_cm: float = 0.1,
) -> dict[str, np.ndarray]:
    """Simulate the selected M2 model with a frozen-air exact time step."""
    x_air, target_air, effective_air = directional_air_field(
        setpoints_C, params.lambda_up_cm, params.lambda_down_cm, dx_cm=dx_cm
    )
    t = _time_grid(speed_cm_min, dt_s)
    x = np.minimum((speed_cm_min / 60.0) * t, FURNACE_LENGTH_CM)
    target = np.interp(x, x_air, target_air)
    air = np.interp(x, x_air, effective_air)
    delta = np.diff(t)
    # Use midpoint forcing in time.  The V1 left-endpoint forcing was only
    # first-order accurate and its rising-side area moved materially when the
    # time step was halved.  Midpoint forcing retains the fast exact affine
    # recurrence while making the time integration second-order for a locally
    # linear air field.
    air_mid = 0.5 * (air[:-1] + air[1:])
    heat_rate_steps = params.k_ref_s_inv * np.exp(params.beta * (air_mid - 175.0) / 80.0)
    heat_decay = np.exp(-heat_rate_steps * delta)
    temp_heat = _affine_recurrence(AMBIENT_C, air_mid, heat_decay)
    air_decline = np.flatnonzero(np.diff(air) < -1e-9)
    decline_start = int(air_decline[0]) if len(air_decline) else len(air)
    all_indices = np.arange(len(air) - 1)
    switch_candidates = np.flatnonzero(
        (all_indices >= decline_start) & (air_mid < temp_heat[:-1] - 1e-10)
    )
    if not len(switch_candidates):
        temp = temp_heat
    else:
        switch = int(switch_candidates[0])
        cool_decay = np.exp(-params.k_cool_s_inv * delta[switch:])
        cool_tail = _affine_recurrence(temp_heat[switch], air_mid[switch:], cool_decay)
        temp = temp_heat.copy()
        temp[switch:] = cool_tail
    rate = np.where(
        air >= temp,
        params.k_ref_s_inv * np.exp(params.beta * (air - 175.0) / 80.0),
        params.k_cool_s_inv,
    )
    slope = rate * (air - temp)
    return {
        "time_s": t,
        "position_cm": x,
        "programmed_air_C": target,
        "effective_air_C": air,
        "temperature_C": temp,
        "slope_C_s": slope,
        "rate_s_inv": rate,
    }


def _first_up_crossing(t: np.ndarray, y: np.ndarray, level: float, stop: int) -> float:
    indices = np.flatnonzero((y[:stop] < level) & (y[1 : stop + 1] >= level))
    if not len(indices):
        return float("nan")
    i = int(indices[0])
    return float(t[i] + (level - y[i]) * (t[i + 1] - t[i]) / (y[i + 1] - y[i]))


def _first_down_crossing(t: np.ndarray, y: np.ndarray, level: float, start: int) -> float:
    indices = np.flatnonzero((y[start:-1] >= level) & (y[start + 1 :] < level))
    if not len(indices):
        return float("nan")
    i = int(indices[0] + start)
    return float(t[i] + (level - y[i]) * (t[i + 1] - t[i]) / (y[i + 1] - y[i]))


def _interpolated_peak(
    t: np.ndarray, y: np.ndarray, slope: np.ndarray, peak_i: int
) -> tuple[float, float]:
    """Estimate the continuous peak from a zero-slope crossing.

    A cubic Hermite segment uses temperature and model derivative at the two
    samples bracketing ``dT/dt=0``.  This is more stable for left/right area
    splitting than fitting three sampled temperatures.  A quadratic fallback
    is retained for externally supplied curves without a clean sign change.
    """
    crossings = np.flatnonzero((slope[:-1] > 0.0) & (slope[1:] <= 0.0))
    if len(crossings):
        i = int(crossings[np.argmin(np.abs(t[crossings] - t[peak_i]))])
        denominator = slope[i] - slope[i + 1]
        if denominator > 0.0:
            fraction = float(slope[i] / denominator)
            if 0.0 <= fraction <= 1.0:
                h = float(t[i + 1] - t[i])
                u = fraction
                h00 = 2.0 * u**3 - 3.0 * u**2 + 1.0
                h10 = u**3 - 2.0 * u**2 + u
                h01 = -2.0 * u**3 + 3.0 * u**2
                h11 = u**3 - u**2
                peak_C = float(
                    h00 * y[i]
                    + h10 * h * slope[i]
                    + h01 * y[i + 1]
                    + h11 * h * slope[i + 1]
                )
                return float(t[i] + u * h), peak_C
    if 0 < peak_i < len(y) - 1:
        local_t = t[peak_i - 1 : peak_i + 2] - t[peak_i]
        a, b, c = np.polyfit(local_t, y[peak_i - 1 : peak_i + 2], 2)
        if a < 0.0:
            offset = float(-b / (2.0 * a))
            if float(local_t[0]) <= offset <= float(local_t[-1]):
                peak_C = float(a * offset**2 + b * offset + c)
                if peak_C >= float(y[peak_i]) - 1e-10:
                    return float(t[peak_i] + offset), peak_C
    return float(t[peak_i]), float(y[peak_i])


def _threshold_area(
    t: np.ndarray,
    y: np.ndarray,
    start_s: float,
    end_s: float,
    level_C: float,
    *,
    start_C: float | None = None,
    end_C: float | None = None,
) -> float:
    """Integrate a threshold excess over interpolated endpoints.

    Interior samples are joined linearly.  Exact crossing temperatures and
    the interpolated peak value can be supplied for the two endpoints.
    """
    if not (np.isfinite(start_s) and np.isfinite(end_s)) or end_s < start_s:
        return float("nan")
    inside = (t > start_s) & (t < end_s)
    times = np.concatenate(([start_s], t[inside], [end_s]))
    values = np.interp(times, t, y)
    if start_C is not None:
        values[0] = float(start_C)
    if end_C is not None:
        values[-1] = float(end_C)
    return float(np.trapezoid(np.maximum(values - level_C, 0.0), times))


def curve_metrics(curve: dict[str, np.ndarray]) -> dict[str, float]:
    t = np.asarray(curve["time_s"], dtype=float)
    temp = np.asarray(curve["temperature_C"], dtype=float)
    slope = np.asarray(curve["slope_C_s"], dtype=float)
    peak_i = int(np.argmax(temp))
    peak_t, peak_C = _interpolated_peak(t, temp, slope, peak_i)
    t150 = _first_up_crossing(t, temp, 150.0, peak_i)
    t190 = _first_up_crossing(t, temp, 190.0, peak_i)
    t217_up = _first_up_crossing(t, temp, 217.0, peak_i)
    t217_down = _first_down_crossing(t, temp, 217.0, peak_i)
    rise_time = t190 - t150 if np.isfinite(t150) and np.isfinite(t190) else float("nan")
    above_time = t217_down - t217_up if np.isfinite(t217_up) and np.isfinite(t217_down) else float("nan")
    rising_area = _threshold_area(
        t, temp, t217_up, peak_t, 217.0, start_C=217.0, end_C=peak_C
    )
    cooling_area = _threshold_area(
        t, temp, peak_t, t217_down, 217.0, start_C=peak_C, end_C=217.0
    )
    total_area = rising_area + cooling_area
    if np.isfinite(t217_up) and np.isfinite(t217_down):
        left_duration = peak_t - t217_up
        right_duration = t217_down - peak_t
        max_duration = max(left_duration, right_duration)
        # Match the simulation resolution.  Optimization uses a coarse grid;
        # final reporting and verification use 0.05 s.
        # The final transit step can be an arbitrarily small remainder; using
        # the minimum would create a pathologically dense symmetry grid for
        # some speeds.  The median is the intended regular simulation step.
        du = float(np.median(np.diff(t)))
        u = np.arange(0.0, max_duration + du / 2.0, du)
        left_excess = np.maximum(np.interp(peak_t - u, t, temp) - 217.0, 0.0)
        right_excess = np.maximum(np.interp(peak_t + u, t, temp) - 217.0, 0.0)
        symmetry_area = float(np.trapezoid(np.abs(left_excess - right_excess), u))
        symmetry_ratio = symmetry_area / total_area if total_area > 0.0 else float("nan")
        duration_imbalance = abs(left_duration - right_duration)
    else:
        left_duration = right_duration = symmetry_area = symmetry_ratio = duration_imbalance = float("nan")
    return {
        "rise_slope_max_C_s": float(np.max(slope[: peak_i + 1])),
        "fall_slope_min_C_s": float(np.min(slope[peak_i:])),
        "rise_150_190_s": float(rise_time),
        "above_217_s": float(above_time),
        "peak_C": peak_C,
        "peak_time_s": peak_t,
        "t150_up_s": float(t150),
        "t190_up_s": float(t190),
        "t217_up_s": float(t217_up),
        "t217_down_s": float(t217_down),
        # Q3 is explicitly the shaded rising-side area from the 217 degC
        # up-crossing to the peak.  The cooling and total areas are diagnostic
        # quantities only and are deliberately named so they cannot be
        # mistaken for the Q3 objective.
        "q3_rising_area_C_s": float(rising_area),
        "cooling_area_217_C_s": float(cooling_area),
        "total_area_above_217_C_s": float(total_area),
        "left_duration_s": float(left_duration),
        "right_duration_s": float(right_duration),
        "symmetry_area_abs_C_s": float(symmetry_area),
        "symmetry_ratio": float(symmetry_ratio),
        "duration_imbalance_s": float(duration_imbalance),
    }


def process_slacks(metrics: dict[str, float]) -> dict[str, float]:
    """Positive values satisfy a process boundary; negative values violate it."""
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


def process_status(metrics: dict[str, float], tolerance: float = 0.0) -> str:
    slacks = np.array(list(process_slacks(metrics).values()), dtype=float)
    if not np.all(np.isfinite(slacks)):
        return "fail"
    return "pass" if float(np.min(slacks)) >= -tolerance else "fail"
