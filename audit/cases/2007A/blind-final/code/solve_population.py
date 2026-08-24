#!/usr/bin/env python3
"""Blind, reproducible solution for the 2007A population case.

Only local case inputs are consumed.  All numerical tables and figures used by
the paper are produced under results/ and figures/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from scipy.ndimage import gaussian_filter1d
from scipy.optimize import brentq


SEED = 2007
AREAS = ("city", "town", "rural")
SEXES = ("male", "female")
AREA_INDEX = {name: i for i, name in enumerate(AREAS)}
SEX_INDEX = {name: i for i, name in enumerate(SEXES)}
AGES = np.arange(91, dtype=int)  # age 90 is the open 90+ interval
BASE_YEAR = 2005
END_YEAR = 2100
TFR_TARGET_END_YEAR = 2035


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(v) for v in value]
    if isinstance(value, np.ndarray):
        return json_ready(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if not np.isfinite(value):
            return None
        return float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(json_ready(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8", float_format="%.10g", lineterminator="\n")


def parse_number(value: Any) -> float:
    text = str(value).strip()
    if text in {"", "/", "nan", "None"}:
        return float("nan")
    return float(text)


@dataclass
class ParsedData:
    demography: pd.DataFrame
    fertility: pd.DataFrame
    sample_counts: pd.DataFrame
    birth_sex_ratio: pd.DataFrame
    general_fertility: pd.DataFrame
    anomaly_years: list[int]
    quality: dict[str, Any]


def detect_fertility_anomaly_years(
    fertility: pd.DataFrame, years: Iterable[int]
) -> list[int]:
    """Fit the magnitude-anomaly rule using only the supplied years."""
    fitted_years = sorted({int(year) for year in years})
    table = (
        fertility[fertility["year"].isin(fitted_years)]
        .groupby(["year", "area"], as_index=False)["fertility_per_1000"]
        .sum()
        .assign(tfr=lambda frame: frame["fertility_per_1000"] / 1000.0)
    )
    annual_median = table.groupby("year")["tfr"].median()
    if annual_median.empty:
        return []
    reference_median = float(annual_median.median())
    return [
        int(year)
        for year, value in annual_median.items()
        if float(value) < 0.5 * reference_median
    ]


def parse_workbook_csv(path: Path) -> ParsedData:
    raw = pd.read_csv(path, header=None, dtype=object, keep_default_na=False)
    year_rows: dict[int, int] = {}
    for row_index, value in enumerate(raw.iloc[:, 0].astype(str)):
        match = re.fullmatch(r"(20\d{2})年", value.strip())
        if match and int(match.group(1)) in range(2001, 2006):
            year_rows[int(match.group(1))] = row_index
    if sorted(year_rows) != [2001, 2002, 2003, 2004, 2005]:
        raise ValueError(f"Unexpected annual blocks: {sorted(year_rows)}")

    ratio_columns = {
        ("city", "male"): (1, 2),
        ("city", "female"): (3, 4),
        ("town", "male"): (5, 6),
        ("town", "female"): (7, 8),
        ("rural", "male"): (9, 10),
        ("rural", "female"): (11, 12),
    }
    fertility_columns = {"city": 14, "town": 15, "rural": 16}
    count_labels = {
        "城市男": ("city", "male"),
        "城市女": ("city", "female"),
        "镇男": ("town", "male"),
        "镇女": ("town", "female"),
        "乡男": ("rural", "male"),
        "乡女": ("rural", "female"),
    }
    demography_rows: list[dict[str, Any]] = []
    fertility_rows: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []

    for year, start in sorted(year_rows.items()):
        body = raw.iloc[start + 2 : start + 93]
        if len(body) != 91:
            raise ValueError(f"Year {year}: expected 91 age rows, got {len(body)}")
        parsed_ages = []
        for value in body.iloc[:, 0]:
            parsed_ages.append(90 if str(value).strip() == "90+" else int(str(value).strip()))
        if parsed_ages != list(range(91)):
            raise ValueError(f"Year {year}: age labels are not 0..89, 90+")

        for local_row, (_, source_row) in enumerate(body.iterrows()):
            age = parsed_ages[local_row]
            for (area, sex), (share_col, mortality_col) in ratio_columns.items():
                demography_rows.append(
                    {
                        "year": year,
                        "area": area,
                        "sex": sex,
                        "age": age,
                        "open_age": age == 90,
                        "population_share_pct": parse_number(source_row.iloc[share_col]),
                        "mortality_per_1000": parse_number(source_row.iloc[mortality_col]),
                    }
                )
            if 15 <= age <= 49:
                for area, column in fertility_columns.items():
                    fertility_rows.append(
                        {
                            "year": year,
                            "area": area,
                            "age": age,
                            "fertility_per_1000": parse_number(source_row.iloc[column]),
                        }
                    )

        found_counts: set[tuple[str, str]] = set()
        for source_index in range(start, min(start + 15, len(raw))):
            label = str(raw.iat[source_index, 17]).strip()
            if label in count_labels:
                area, sex = count_labels[label]
                count = int(round(parse_number(raw.iat[source_index, 18])))
                sample_rows.append(
                    {"year": year, "area": area, "sex": sex, "sample_count": count}
                )
                found_counts.add((area, sex))
        if found_counts != set(ratio_columns):
            raise ValueError(f"Year {year}: incomplete sample-count labels: {found_counts}")

    summary_row = next(
        (i for i, value in enumerate(raw.iloc[:, 0].astype(str)) if value.strip() == "年代"),
        None,
    )
    if summary_row is None:
        raise ValueError("Birth-sex-ratio summary table not found")
    ratio_rows: list[dict[str, Any]] = []
    gfr_rows: list[dict[str, Any]] = []
    for source_index in range(summary_row + 1, len(raw)):
        year_text = str(raw.iat[source_index, 0]).strip()
        if not year_text.isdigit():
            continue
        year = int(year_text)
        for area, column in zip(AREAS, (1, 2, 3)):
            ratio_rows.append(
                {
                    "year": year,
                    "area": area,
                    "male_births_per_100_female": parse_number(raw.iat[source_index, column]),
                }
            )
        gfr_year_text = str(raw.iat[source_index, 4]).strip()
        if gfr_year_text.isdigit():
            gfr_year = int(gfr_year_text)
            for area, column in zip(AREAS, (5, 6, 7)):
                gfr_rows.append(
                    {
                        "year": gfr_year,
                        "area": area,
                        "general_fertility_per_1000": parse_number(raw.iat[source_index, column]),
                    }
                )

    demography = pd.DataFrame(demography_rows)
    fertility = pd.DataFrame(fertility_rows)
    sample_counts = pd.DataFrame(sample_rows)
    birth_sex_ratio = pd.DataFrame(ratio_rows)
    general_fertility = pd.DataFrame(gfr_rows)

    tfr_table = (
        fertility.groupby(["year", "area"], as_index=False)["fertility_per_1000"]
        .sum()
        .assign(tfr=lambda x: x["fertility_per_1000"] / 1000.0)
    )
    anomaly_years = detect_fertility_anomaly_years(fertility, range(2001, 2006))

    share_sums = (
        demography.groupby(["year", "area"])["population_share_pct"]
        .sum()
        .reset_index(name="sum_pct")
    )
    core_missing = int(
        demography[["population_share_pct", "mortality_per_1000"]].isna().sum().sum()
        + fertility["fertility_per_1000"].isna().sum()
    )
    mortality_zero_count = int((demography["mortality_per_1000"] == 0).sum())
    mortality_high_count = int((demography["mortality_per_1000"] > 300).sum())
    quality = {
        "overall_judgment": "needs_review" if anomaly_years or mortality_zero_count else "pass",
        "raw_shape": [int(raw.shape[0]), int(raw.shape[1])],
        "annual_blocks_judgment": "pass",
        "annual_blocks": sorted(year_rows),
        "age_rows_per_year": 91,
        "core_missing_count": core_missing,
        "core_completeness_judgment": "pass" if core_missing == 0 else "fail",
        "share_sum_min_pct": float(share_sums["sum_pct"].min()),
        "share_sum_max_pct": float(share_sums["sum_pct"].max()),
        "share_sum_judgment": (
            "pass"
            if share_sums["sum_pct"].between(99.8, 100.2).all()
            else "needs_review"
        ),
        "mortality_min_per_1000": float(demography["mortality_per_1000"].min()),
        "mortality_max_per_1000": float(demography["mortality_per_1000"].max()),
        "mortality_zero_count": mortality_zero_count,
        "mortality_over_300_count": mortality_high_count,
        "mortality_noise_judgment": "needs_review" if mortality_zero_count else "pass",
        "fertility_anomaly_years": anomaly_years,
        "fertility_anomaly_judgment": "needs_review" if anomaly_years else "pass",
        "tfr_by_year_area": tfr_table.to_dict(orient="records"),
        "sample_count_min": int(sample_counts["sample_count"].min()),
        "sample_count_max": int(sample_counts["sample_count"].max()),
        "sample_design_judgment": "needs_review",
        "sample_design_note": "Sample capacities vary sharply by year; they are used only for within-year structural weights and exposure weighting, never as a known national sampling fraction.",
    }
    return ParsedData(
        demography=demography,
        fertility=fertility,
        sample_counts=sample_counts,
        birth_sex_ratio=birth_sex_ratio,
        general_fertility=general_fertility,
        anomaly_years=anomaly_years,
        quality=quality,
    )


def isotonic_increasing(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    blocks: list[list[float | int]] = []
    for index, (value, weight) in enumerate(zip(values, weights)):
        blocks.append([index, index, float(value), max(float(weight), 1e-12)])
        while len(blocks) >= 2 and float(blocks[-2][2]) > float(blocks[-1][2]):
            right = blocks.pop()
            left = blocks.pop()
            combined_weight = float(left[3]) + float(right[3])
            combined_value = (
                float(left[2]) * float(left[3]) + float(right[2]) * float(right[3])
            ) / combined_weight
            blocks.append([int(left[0]), int(right[1]), combined_value, combined_weight])
    result = np.empty_like(values)
    for start, end, value, _weight in blocks:
        result[int(start) : int(end) + 1] = float(value)
    return result


def build_observed_state(data: ParsedData, year: int) -> np.ndarray:
    state = np.zeros((len(AREAS), len(SEXES), len(AGES)), dtype=float)
    counts = data.sample_counts[data.sample_counts["year"] == year]
    area_totals = counts.groupby("area")["sample_count"].sum().reindex(AREAS)
    area_weights = area_totals.to_numpy(dtype=float) / float(area_totals.sum())
    for area_index, area in enumerate(AREAS):
        for sex_index, sex in enumerate(SEXES):
            subset = data.demography[
                (data.demography["year"] == year)
                & (data.demography["area"] == area)
                & (data.demography["sex"] == sex)
            ].sort_values("age")
            state[area_index, sex_index, :] = (
                area_weights[area_index]
                * subset["population_share_pct"].to_numpy(dtype=float)
                / 100.0
            )
    state /= state.sum()
    return state


@dataclass
class EstimatedRates:
    mortality: np.ndarray  # area x sex x age, annual probability
    fertility: np.ndarray  # area x age, births per woman-year
    mortality_raw: np.ndarray
    fertility_raw: np.ndarray
    years: list[int]
    anomaly_years: list[int]
    fertility_treatment: str
    prior_exposure: float
    mortality_smoothing_scale: float
    mortality_rate_conversion: str


def estimate_rates(
    data: ParsedData,
    years: Iterable[int],
    fertility_treatment: str = "exclude_anomaly",
    prior_exposure: float = 1000.0,
    anomaly_years: Iterable[int] | None = None,
    mortality_smoothing_scale: float = 1.0,
    mortality_rate_conversion: str = "direct_probability",
) -> EstimatedRates:
    years = sorted(int(y) for y in years)
    if mortality_smoothing_scale <= 0:
        raise ValueError("mortality_smoothing_scale must be positive")
    if mortality_rate_conversion not in {"direct_probability", "hazard"}:
        raise ValueError(f"Unknown mortality-rate conversion: {mortality_rate_conversion}")
    fitted_anomaly_years = (
        detect_fertility_anomaly_years(data.fertility, years)
        if anomaly_years is None
        else sorted({int(year) for year in anomaly_years if int(year) in years})
    )
    area_total_lookup = (
        data.sample_counts.groupby(["year", "area"])["sample_count"].sum()
    )

    mortality_raw = np.zeros((len(AREAS), len(SEXES), len(AGES)), dtype=float)
    exposure_array = np.zeros_like(mortality_raw)
    death_array = np.zeros_like(mortality_raw)
    for area_index, area in enumerate(AREAS):
        for sex_index, sex in enumerate(SEXES):
            subset = data.demography[
                data.demography["year"].isin(years)
                & (data.demography["area"] == area)
                & (data.demography["sex"] == sex)
            ]
            for _, row in subset.iterrows():
                age = int(row["age"])
                area_total = float(area_total_lookup.loc[(int(row["year"]), area)])
                # Appendix 2 defines every age-sex percentage over the whole
                # area population, so its denominator must be the area total.
                exposure = area_total * float(row["population_share_pct"]) / 100.0
                reported_rate = float(row["mortality_per_1000"]) / 1000.0
                q = (
                    reported_rate
                    if mortality_rate_conversion == "direct_probability"
                    else 1.0 - math.exp(-reported_rate)
                )
                exposure_array[area_index, sex_index, age] += exposure
                death_array[area_index, sex_index, age] += exposure * q

    national_prior = np.divide(
        death_array.sum(axis=0),
        exposure_array.sum(axis=0),
        out=np.full((len(SEXES), len(AGES)), 1e-5),
        where=exposure_array.sum(axis=0) > 0,
    )
    mortality = np.zeros_like(mortality_raw)
    for area_index in range(len(AREAS)):
        for sex_index in range(len(SEXES)):
            raw = np.divide(
                death_array[area_index, sex_index],
                exposure_array[area_index, sex_index],
                out=national_prior[sex_index].copy(),
                where=exposure_array[area_index, sex_index] > 0,
            )
            mortality_raw[area_index, sex_index] = raw
            shrunk = (
                death_array[area_index, sex_index]
                + prior_exposure * national_prior[sex_index]
            ) / (exposure_array[area_index, sex_index] + prior_exposure)
            shrunk = np.clip(shrunk, 1e-7, 0.95)
            log_q = np.log(shrunk)
            smooth_log = log_q.copy()
            smooth_log[1:15] = gaussian_filter1d(
                log_q[1:15], sigma=0.8 * mortality_smoothing_scale, mode="nearest"
            )
            old_log = gaussian_filter1d(
                log_q[10:], sigma=1.0 * mortality_smoothing_scale, mode="nearest"
            )
            old_weights = exposure_array[area_index, sex_index, 10:] + prior_exposure
            smooth_log[10:] = isotonic_increasing(old_log, old_weights)
            smooth_log[0] = log_q[0]
            mortality[area_index, sex_index] = np.clip(np.exp(smooth_log), 0.0, 0.95)

    fertility_raw = np.zeros((len(AREAS), len(AGES)), dtype=float)
    fertility = np.zeros_like(fertility_raw)
    for area_index, area in enumerate(AREAS):
        numerator = np.zeros(len(AGES), dtype=float)
        denominator = np.zeros(len(AGES), dtype=float)
        for year in years:
            if fertility_treatment == "exclude_anomaly" and year in fitted_anomaly_years:
                continue
            multiplier = (
                10.0
                if fertility_treatment == "correct_x10" and year in fitted_anomaly_years
                else 1.0
            )
            f_rows = data.fertility[
                (data.fertility["year"] == year) & (data.fertility["area"] == area)
            ].set_index("age")
            female_rows = data.demography[
                (data.demography["year"] == year)
                & (data.demography["area"] == area)
                & (data.demography["sex"] == "female")
            ].set_index("age")
            area_total = float(area_total_lookup.loc[(year, area)])
            for age in range(15, 50):
                exposure = (
                    area_total
                    * float(female_rows.loc[age, "population_share_pct"])
                    / 100.0
                )
                rate = float(f_rows.loc[age, "fertility_per_1000"]) * multiplier / 1000.0
                numerator[age] += exposure * rate
                denominator[age] += exposure
        raw = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)
        fertility_raw[area_index] = raw
        smoothed = raw.copy()
        segment = gaussian_filter1d(raw[15:50], sigma=0.9, mode="nearest")
        if segment.sum() > 0:
            segment *= raw[15:50].sum() / segment.sum()
        smoothed[15:50] = segment
        smoothed[:15] = 0.0
        smoothed[50:] = 0.0
        fertility[area_index] = smoothed

    return EstimatedRates(
        mortality=mortality,
        fertility=fertility,
        mortality_raw=mortality_raw,
        fertility_raw=fertility_raw,
        years=years,
        anomaly_years=fitted_anomaly_years,
        fertility_treatment=fertility_treatment,
        prior_exposure=float(prior_exposure),
        mortality_smoothing_scale=float(mortality_smoothing_scale),
        mortality_rate_conversion=mortality_rate_conversion,
    )


def birth_ratios_for_year(data: ParsedData, year: int) -> np.ndarray:
    rows = (
        data.birth_sex_ratio[data.birth_sex_ratio["year"] == year]
        .set_index("area")
        .reindex(AREAS)
    )
    if rows["male_births_per_100_female"].isna().any():
        raise ValueError(f"Birth sex ratio is incomplete for {year}")
    return rows["male_births_per_100_female"].to_numpy(dtype=float)


def legacy_area_weighted_tfr(state: np.ndarray, fertility: np.ndarray) -> float:
    female_reproductive = state[:, SEX_INDEX["female"], 15:50].sum(axis=1)
    if female_reproductive.sum() <= 0:
        return float("nan")
    area_weights = female_reproductive / female_reproductive.sum()
    area_tfr = fertility[:, 15:50].sum(axis=1)
    return float(np.dot(area_weights, area_tfr))


def national_period_tfr(state: np.ndarray, fertility: np.ndarray) -> float:
    """Standard national period TFR: aggregate ASFR within each age first."""
    exposure = np.asarray(state, dtype=float)[:, SEX_INDEX["female"], 15:50]
    denominator = exposure.sum(axis=0)
    numerator = (exposure * np.asarray(fertility, dtype=float)[:, 15:50]).sum(axis=0)
    if float(denominator.sum()) <= 0:
        return float("nan")
    national_asfr = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > 0,
    )
    return float(national_asfr.sum())


def scale_fertility_to_tfr(
    state: np.ndarray, fertility: np.ndarray, target_tfr: float
) -> tuple[np.ndarray, float, float]:
    unscaled = national_period_tfr(state, fertility)
    if not np.isfinite(unscaled) or unscaled <= 0:
        raise ValueError("Cannot scale a non-positive fertility schedule")
    factor = float(target_tfr / unscaled)
    return fertility * factor, factor, unscaled


def projected_birth_ratios(
    base_ratios: np.ndarray,
    year: int,
    normalize: bool,
    target_ratio: float = 107.0,
    target_year: int = 2020,
) -> np.ndarray:
    if not normalize or year <= BASE_YEAR:
        return base_ratios.copy()
    if year >= target_year:
        return np.full_like(base_ratios, target_ratio, dtype=float)
    progress = (year - BASE_YEAR) / float(target_year - BASE_YEAR)
    return base_ratios + progress * (target_ratio - base_ratios)


def migration_age_profile(profile: str) -> np.ndarray:
    if profile == "uniform":
        return np.ones(len(AGES), dtype=float)
    if profile == "youth_weighted":
        weights = np.full(len(AGES), 0.25, dtype=float)
        weights[15:40] = 2.0
        weights[40:60] = 1.0
        weights[60:] = 0.20
        return weights
    raise ValueError(f"Unknown migration age profile: {profile}")


def step_state(
    state: np.ndarray,
    mortality: np.ndarray,
    fertility: np.ndarray,
    birth_ratios: np.ndarray,
    migration_rate: float,
    city_destination_share: float,
    mortality_factor: float = 1.0,
    migration_age_weights: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    q = np.clip(mortality * mortality_factor, 0.0, 0.999999)
    next_state = np.zeros_like(state, dtype=float)
    gross_births_by_area = (
        state[:, SEX_INDEX["female"], :] * fertility
    ).sum(axis=1)
    infant_deaths = 0.0
    male_births_total = 0.0
    female_births_total = 0.0
    for area_index in range(len(AREAS)):
        male_fraction = birth_ratios[area_index] / (100.0 + birth_ratios[area_index])
        female_fraction = 1.0 - male_fraction
        male_births = gross_births_by_area[area_index] * male_fraction
        female_births = gross_births_by_area[area_index] * female_fraction
        male_births_total += float(male_births)
        female_births_total += float(female_births)
        male_surviving = male_births * (1.0 - q[area_index, SEX_INDEX["male"], 0])
        female_surviving = female_births * (1.0 - q[area_index, SEX_INDEX["female"], 0])
        next_state[area_index, SEX_INDEX["male"], 0] = male_surviving
        next_state[area_index, SEX_INDEX["female"], 0] = female_surviving
        infant_deaths += male_births - male_surviving + female_births - female_surviving

    existing_deaths = float((state * q).sum())
    for age in range(90):
        next_state[:, :, age + 1] += state[:, :, age] * (1.0 - q[:, :, age])
    next_state[:, :, 90] += state[:, :, 90] * (1.0 - q[:, :, 90])

    rural_index = AREA_INDEX["rural"]
    city_index = AREA_INDEX["city"]
    town_index = AREA_INDEX["town"]
    age_weights = (
        np.ones(len(AGES), dtype=float)
        if migration_age_weights is None
        else np.asarray(migration_age_weights, dtype=float)
    )
    if age_weights.shape != (len(AGES),) or np.any(age_weights < 0):
        raise ValueError("migration_age_weights must be a nonnegative length-91 vector")
    age_specific_rate = np.clip(migration_rate * age_weights, 0.0, 1.0)
    moved = next_state[rural_index] * age_specific_rate[np.newaxis, :]
    next_state[rural_index] -= moved
    next_state[city_index] += city_destination_share * moved
    next_state[town_index] += (1.0 - city_destination_share) * moved

    gross_births = float(gross_births_by_area.sum())
    expected_total = float(state.sum() - existing_deaths + gross_births - infant_deaths)
    residual = float(next_state.sum() - expected_total)
    return next_state, {
        "gross_births": gross_births,
        "male_births": male_births_total,
        "female_births": female_births_total,
        "actual_birth_sex_ratio": (
            100.0 * male_births_total / female_births_total
            if female_births_total > 0
            else float("nan")
        ),
        "existing_deaths": existing_deaths,
        "infant_deaths": float(infant_deaths),
        "total_deaths": float(existing_deaths + infant_deaths),
        "migrants_reclassified": float(moved.sum()),
        "conservation_residual": residual,
    }


def summarize_state(state: np.ndarray) -> dict[str, float]:
    total = float(state.sum())
    male = float(state[:, SEX_INDEX["male"], :].sum())
    female = float(state[:, SEX_INDEX["female"], :].sum())
    child = float(state[:, :, :15].sum())
    working = float(state[:, :, 15:65].sum())
    older = float(state[:, :, 65:].sum())
    older60 = float(state[:, :, 60:].sum())
    older80 = float(state[:, :, 80:].sum())
    city = float(state[AREA_INDEX["city"]].sum())
    town = float(state[AREA_INDEX["town"]].sum())
    rural = float(state[AREA_INDEX["rural"]].sum())
    def safe_ratio(numerator: float, denominator: float) -> float:
        if denominator > 0:
            return numerator / denominator
        if numerator > 0:
            return float("inf")
        return float("nan")

    return {
        "population": total,
        "male_population": male,
        "female_population": female,
        "child_0_14_population": child,
        "working_15_64_population": working,
        "older_65_population": older,
        "older_60_population": older60,
        "older_80_population": older80,
        "child_share": safe_ratio(child, total),
        "working_share": safe_ratio(working, total),
        "older_65_share": safe_ratio(older, total),
        "older_60_share": safe_ratio(older60, total),
        "older_80_share": safe_ratio(older80, total),
        "dependency_ratio": safe_ratio(child + older, working),
        "sex_ratio_male_per_100_female": 100.0 * safe_ratio(male, female),
        "city_share": safe_ratio(city, total),
        "town_share": safe_ratio(town, total),
        "rural_share": safe_ratio(rural, total),
        "urban_share": safe_ratio(city + town, total),
    }


def simulate(
    initial_state: np.ndarray,
    rates: EstimatedRates,
    fertility_shape: np.ndarray,
    base_birth_ratios: np.ndarray,
    migration_rate: float,
    city_destination_share: float,
    end_year: int = END_YEAR,
    target_standard_tfr: float | None = None,
    tfr_target_end_year: int = TFR_TARGET_END_YEAR,
    fixed_fertility_scale: float = 1.0,
    mortality_improvement: float = 0.01,
    mortality_improvement_end: int = 2030,
    normalize_birth_ratio: bool = True,
    birth_ratio_target: float = 107.0,
    birth_ratio_target_year: int = 2020,
    migration_age_weights: np.ndarray | None = None,
) -> tuple[pd.DataFrame, np.ndarray, dict[str, float]]:
    state = np.asarray(initial_state, dtype=float).copy()
    rows: list[dict[str, Any]] = []
    max_conservation_residual = 0.0
    min_state_value = float(state.min())
    max_tfr_target_error = 0.0
    initial_fertility_scale = float("nan")
    post_target_fertility_scale: float | None = None
    for year in range(BASE_YEAR, end_year + 1):
        if target_standard_tfr is None:
            fertility_scale = float(fixed_fertility_scale)
        elif year <= tfr_target_end_year:
            raw_standard_tfr = national_period_tfr(state, fertility_shape)
            if not np.isfinite(raw_standard_tfr) or raw_standard_tfr <= 0:
                raise ValueError("Cannot calibrate a non-positive national period TFR")
            fertility_scale = float(target_standard_tfr / raw_standard_tfr)
            if year == tfr_target_end_year:
                post_target_fertility_scale = fertility_scale
        else:
            if post_target_fertility_scale is None:
                raise RuntimeError("Post-target fertility scale was not initialized")
            fertility_scale = post_target_fertility_scale
        if year == BASE_YEAR:
            initial_fertility_scale = fertility_scale
        fertility = np.asarray(fertility_shape, dtype=float) * fertility_scale
        standard_tfr = national_period_tfr(state, fertility)
        legacy_tfr = legacy_area_weighted_tfr(state, fertility)
        if target_standard_tfr is not None and year <= tfr_target_end_year:
            max_tfr_target_error = max(
                max_tfr_target_error, abs(standard_tfr - target_standard_tfr)
            )
        row: dict[str, Any] = {"year": year, **summarize_state(state)}
        row["target_standard_tfr"] = (
            float(target_standard_tfr) if target_standard_tfr is not None else float("nan")
        )
        row["tfr_target_active"] = bool(
            target_standard_tfr is not None and year <= tfr_target_end_year
        )
        row["fertility_scale_factor"] = fertility_scale
        row["national_standard_tfr"] = standard_tfr
        row["legacy_area_weighted_tfr"] = legacy_tfr
        for area_index, area in enumerate(AREAS):
            row[f"regional_tfr_{area}"] = float(fertility[area_index, 15:50].sum())
        ratios = projected_birth_ratios(
            base_birth_ratios,
            year,
            normalize=normalize_birth_ratio,
            target_ratio=birth_ratio_target,
            target_year=birth_ratio_target_year,
        )
        gross_births_by_area = (
            state[:, SEX_INDEX["female"], :] * fertility
        ).sum(axis=1)
        male_fractions = ratios / (100.0 + ratios)
        male_births = float(np.dot(gross_births_by_area, male_fractions))
        female_births = float(np.dot(gross_births_by_area, 1.0 - male_fractions))
        row["birth_sex_ratio"] = (
            100.0 * male_births / female_births if female_births > 0 else float("nan")
        )
        if year < end_year:
            elapsed = min(year - BASE_YEAR, mortality_improvement_end - BASE_YEAR)
            mortality_factor = (1.0 - mortality_improvement) ** max(0, elapsed)
            next_state, transition = step_state(
                state,
                rates.mortality,
                fertility,
                ratios,
                migration_rate,
                city_destination_share,
                mortality_factor=mortality_factor,
                migration_age_weights=migration_age_weights,
            )
            row.update(transition)
            max_conservation_residual = max(
                max_conservation_residual, abs(float(transition["conservation_residual"]))
            )
            min_state_value = min(min_state_value, float(next_state.min()))
            state = next_state
        else:
            row.update(
                {
                    "gross_births": float("nan"),
                    "male_births": float("nan"),
                    "female_births": float("nan"),
                    "actual_birth_sex_ratio": float("nan"),
                    "existing_deaths": float("nan"),
                    "infant_deaths": float("nan"),
                    "total_deaths": float("nan"),
                    "migrants_reclassified": float("nan"),
                    "conservation_residual": float("nan"),
                }
            )
        rows.append(row)
    diagnostics = {
        "max_absolute_conservation_residual": max_conservation_residual,
        "minimum_state_value": min_state_value,
        "max_tfr_target_absolute_error": max_tfr_target_error,
        "initial_fertility_scale_factor": initial_fertility_scale,
        "post_target_fertility_scale_factor": post_target_fertility_scale,
    }
    return pd.DataFrame(rows), state, diagnostics


def calibrate_migration_rate(
    initial_state: np.ndarray,
    rates: EstimatedRates,
    fertility_shape: np.ndarray,
    base_birth_ratios: np.ndarray,
    city_destination_share: float,
    target_urban_share: float = 0.53,
    target_year: int = 2020,
    target_standard_tfr: float | None = 1.8,
    tfr_target_end_year: int = TFR_TARGET_END_YEAR,
    fixed_fertility_scale: float = 1.0,
    mortality_improvement: float = 0.01,
    migration_age_weights: np.ndarray | None = None,
) -> tuple[float, dict[str, float | str]]:
    def urban_at(rate: float) -> float:
        trajectory, _, _ = simulate(
            initial_state,
            rates,
            fertility_shape,
            base_birth_ratios,
            migration_rate=rate,
            city_destination_share=city_destination_share,
            end_year=target_year,
            target_standard_tfr=target_standard_tfr,
            tfr_target_end_year=tfr_target_end_year,
            fixed_fertility_scale=fixed_fertility_scale,
            mortality_improvement=mortality_improvement,
            migration_age_weights=migration_age_weights,
        )
        return float(trajectory.loc[trajectory["year"] == target_year, "urban_share"].iloc[0])

    share_at_zero = urban_at(0.0)
    share_at_high = urban_at(0.15)
    if target_urban_share <= share_at_zero:
        rate = 0.0
        judgment = "needs_review"
    elif target_urban_share >= share_at_high:
        rate = 0.15
        judgment = "needs_review"
    else:
        rate = float(brentq(lambda value: urban_at(value) - target_urban_share, 0.0, 0.15))
        judgment = "pass"
    achieved = urban_at(rate)
    return rate, {
        "judgment": judgment,
        "target_year": target_year,
        "target_urban_share": target_urban_share,
        "achieved_urban_share": achieved,
        "annual_rural_reclassification_rate": rate,
        "urban_share_without_reclassification": share_at_zero,
        "upper_bracket_urban_share": share_at_high,
    }


def fit_training_migration_rate(data: ParsedData, years: Iterable[int]) -> float:
    years = sorted(years)
    rural_shares = []
    for year in years:
        rural_shares.append(float(build_observed_state(data, year)[AREA_INDEX["rural"]].sum()))
    slope = float(np.polyfit(np.asarray(years) - years[0], np.log(rural_shares), 1)[0])
    return float(np.clip(1.0 - math.exp(slope), 0.0, 0.15))


def state_metrics(predicted: np.ndarray, observed: np.ndarray) -> dict[str, float]:
    predicted = predicted / predicted.sum()
    observed = observed / observed.sum()
    aggregate_predicted = predicted.sum(axis=0)
    aggregate_observed = observed.sum(axis=0)
    predicted_areas = predicted.sum(axis=(1, 2))
    observed_areas = observed.sum(axis=(1, 2))

    def bands(state: np.ndarray) -> np.ndarray:
        return np.array(
            [state[:, :, :15].sum(), state[:, :, 15:65].sum(), state[:, :, 65:].sum()]
        )

    p_summary = summarize_state(predicted)
    o_summary = summarize_state(observed)
    full_tv = 50.0 * float(np.abs(predicted - observed).sum())
    national_tv = 50.0 * float(np.abs(aggregate_predicted - aggregate_observed).sum())
    area_mae = 100.0 * float(np.abs(predicted_areas - observed_areas).mean())
    age_band_mae = 100.0 * float(np.abs(bands(predicted) - bands(observed)).mean())
    return {
        "full_state_tv_pp": full_tv,
        "national_age_sex_tv_pp": national_tv,
        "area_share_mae_pp": area_mae,
        "age_band_mae_pp": age_band_mae,
        "urban_share_abs_error_pp": 100.0
        * abs(p_summary["urban_share"] - o_summary["urban_share"]),
        "sex_ratio_abs_error": abs(
            p_summary["sex_ratio_male_per_100_female"]
            - o_summary["sex_ratio_male_per_100_female"]
        ),
        "composite_score": national_tv + area_mae + age_band_mae,
    }


def run_rolling_comparison(
    data: ParsedData,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    display_names = {
        "persistence_baseline": "Persistence baseline",
        "cohort_no_urbanization": "Cohort model without urbanization",
        "multistate_cohort": "Three-area two-sex cohort model",
    }
    fold_records: list[dict[str, Any]] = []
    for holdout_year in (2003, 2004, 2005):
        training_years = list(range(2001, holdout_year))
        origin_year = holdout_year - 1
        fold_anomalies = detect_fertility_anomaly_years(data.fertility, training_years)
        rates = estimate_rates(
            data,
            training_years,
            fertility_treatment="exclude_anomaly",
            anomaly_years=fold_anomalies,
        )
        origin_state = build_observed_state(data, origin_year)
        observed = build_observed_state(data, holdout_year)
        origin_ratios = birth_ratios_for_year(data, origin_year)
        city_destination_share = float(
            origin_state[AREA_INDEX["city"]].sum()
            / (
                origin_state[AREA_INDEX["city"]].sum()
                + origin_state[AREA_INDEX["town"]].sum()
            )
        )
        fitted_migration = fit_training_migration_rate(data, training_years)
        predictions: dict[str, np.ndarray] = {
            "persistence_baseline": origin_state.copy()
        }
        predictions["cohort_no_urbanization"], _ = step_state(
            origin_state,
            rates.mortality,
            rates.fertility,
            origin_ratios,
            migration_rate=0.0,
            city_destination_share=city_destination_share,
        )
        predictions["multistate_cohort"], _ = step_state(
            origin_state,
            rates.mortality,
            rates.fertility,
            origin_ratios,
            migration_rate=fitted_migration,
            city_destination_share=city_destination_share,
        )
        fold_start = len(rows)
        for model_id, prediction in predictions.items():
            rows.append(
                {
                    "holdout_year": holdout_year,
                    "origin_year": origin_year,
                    "training_years": "-".join(str(year) for year in training_years),
                    "training_max_year": max(training_years),
                    "fold_anomaly_years": ";".join(str(year) for year in fold_anomalies),
                    "fitted_annual_rural_reclassification_rate": fitted_migration,
                    "model_id": model_id,
                    "model": display_names[model_id],
                    **state_metrics(prediction, observed),
                }
            )
        fold_frame = pd.DataFrame(rows[fold_start:]).sort_values("composite_score")
        fold_frame["fold_rank"] = np.arange(1, len(fold_frame) + 1)
        rank_lookup = dict(zip(fold_frame["model_id"], fold_frame["fold_rank"]))
        for row in rows[fold_start:]:
            row["fold_rank"] = int(rank_lookup[str(row["model_id"])])
        fold_records.append(
            {
                "training_years": training_years,
                "training_max_year": max(training_years),
                "holdout_year": holdout_year,
                "anomaly_years_fitted_in_fold": fold_anomalies,
                "anomaly_rule_training_only_judgment": (
                    "pass" if all(year < holdout_year for year in fold_anomalies) else "fail"
                ),
                "winner": str(fold_frame.iloc[0]["model_id"]),
            }
        )

    rolling = pd.DataFrame(rows).sort_values(["holdout_year", "fold_rank"]).reset_index(drop=True)
    metric_columns = [
        "full_state_tv_pp",
        "national_age_sex_tv_pp",
        "area_share_mae_pp",
        "age_band_mae_pp",
        "urban_share_abs_error_pp",
        "sex_ratio_abs_error",
        "composite_score",
    ]
    aggregate_rows: list[dict[str, Any]] = []
    for model_id, subset in rolling.groupby("model_id", sort=False):
        aggregate_row: dict[str, Any] = {
            "model_id": model_id,
            "model": display_names[str(model_id)],
            "fold_count": int(len(subset)),
            "fold_wins": int((subset["fold_rank"] == 1).sum()),
            "composite_score_std": float(subset["composite_score"].std(ddof=0)),
            "composite_score_worst": float(subset["composite_score"].max()),
        }
        for column in metric_columns:
            aggregate_row[column] = float(subset[column].mean())
        aggregate_rows.append(aggregate_row)
    comparison = pd.DataFrame(aggregate_rows).sort_values("composite_score").reset_index(drop=True)
    comparison["rank"] = np.arange(1, len(comparison) + 1)
    comparison["automatic_judgment"] = "needs_review"

    winners = [str(record["winner"]) for record in fold_records]
    m1 = rolling[rolling["model_id"] == "cohort_no_urbanization"].sort_values("holdout_year")
    m2 = rolling[rolling["model_id"] == "multistate_cohort"].sort_values("holdout_year")
    national_equivalence_error = float(
        np.max(
            np.abs(
                m1[
                    ["national_age_sex_tv_pp", "age_band_mae_pp", "sex_ratio_abs_error"]
                ].to_numpy()
                - m2[
                    ["national_age_sex_tv_pp", "age_band_mae_pp", "sex_ratio_abs_error"]
                ].to_numpy()
            )
        )
    )
    diagnostics = {
        "folds": fold_records,
        "holdout_years": [2003, 2004, 2005],
        "all_training_years_precede_holdout_judgment": (
            "pass"
            if all(record["training_max_year"] < record["holdout_year"] for record in fold_records)
            else "fail"
        ),
        "fold_winners": winners,
        "ranking_stability_judgment": (
            "pass" if len(set(winners)) == 1 else "needs_review"
        ),
        "national_one_step_m1_m2_equivalence_max_abs_error": national_equivalence_error,
        "national_one_step_mechanism_judgment": "needs_review",
        "multi_step_national_validation_judgment": "needs_review",
        "lowest_mean_score_model": str(comparison.iloc[0]["model_id"]),
        "selected_model": "multistate_cohort",
        "selection_judgment": "needs_review",
        "selection_note": (
            "M2 has the lowest mean score and uniquely represents urban reclassification, "
            "but fold winners reverse and one-step national metrics cannot distinguish M1 from M2."
        ),
    }
    return comparison, rolling, diagnostics


def peak_summary(trajectory: pd.DataFrame) -> dict[str, Any]:
    index = int(trajectory["population"].idxmax())
    peak_row = trajectory.loc[index]
    last_year = int(trajectory["year"].max())
    return {
        "peak_year": int(peak_row["year"]),
        "peak_population": float(peak_row["population"]),
        "peak_within_horizon": int(peak_row["year"]) < last_year,
    }


def value_at(trajectory: pd.DataFrame, year: int, column: str) -> float:
    values = trajectory.loc[trajectory["year"] == year, column]
    if len(values) != 1:
        raise ValueError(f"Expected one value for {year=} {column=}")
    return float(values.iloc[0])


def trajectory_metrics(trajectory: pd.DataFrame) -> dict[str, Any]:
    return {
        **peak_summary(trajectory),
        "population_2020": value_at(trajectory, 2020, "population"),
        "population_2050": value_at(trajectory, 2050, "population"),
        "population_2100": value_at(trajectory, 2100, "population"),
        "older_65_share_2050": value_at(trajectory, 2050, "older_65_share"),
        "dependency_ratio_2050": value_at(trajectory, 2050, "dependency_ratio"),
        "urban_share_2050": value_at(trajectory, 2050, "urban_share"),
        "sex_ratio_2050": value_at(
            trajectory, 2050, "sex_ratio_male_per_100_female"
        ),
    }


def run_factorial_sensitivity(
    initial_unit: np.ndarray,
    initial_absolute: np.ndarray,
    rates: EstimatedRates,
    base_ratios: np.ndarray,
    city_destination_share: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    trajectories: list[pd.DataFrame] = []
    combinations = list(product((1.5, 1.8, 2.1), (0.0, 0.01, 0.02), (0.50, 0.53, 0.56), (False, True)))
    for index, (target_tfr, mortality_improvement, urban_target, normalize_ratio) in enumerate(
        combinations, start=1
    ):
        migration_rate, migration_diagnostics = calibrate_migration_rate(
            initial_unit,
            rates,
            rates.fertility,
            base_ratios,
            city_destination_share,
            target_urban_share=urban_target,
            target_standard_tfr=target_tfr,
            mortality_improvement=mortality_improvement,
        )
        trajectory, _, diagnostics = simulate(
            initial_absolute,
            rates,
            rates.fertility,
            base_ratios,
            migration_rate,
            city_destination_share,
            target_standard_tfr=target_tfr,
            mortality_improvement=mortality_improvement,
            normalize_birth_ratio=normalize_ratio,
        )
        case_id = f"factorial_{index:02d}"
        trajectory_copy = trajectory.copy()
        trajectory_copy.insert(0, "case_id", case_id)
        trajectories.append(trajectory_copy)
        case_pass = (
            diagnostics["minimum_state_value"] >= -1e-10
            and diagnostics["max_absolute_conservation_residual"]
            / max(float(initial_absolute.sum()), 1.0)
            < 1e-12
            and migration_diagnostics["judgment"] == "pass"
        )
        rows.append(
            {
                "case_id": case_id,
                "target_standard_tfr": target_tfr,
                "mortality_improvement": mortality_improvement,
                "target_urban_share_2020": urban_target,
                "normalize_birth_sex_ratio": normalize_ratio,
                "annual_rural_reclassification_rate": migration_rate,
                "judgment": "pass" if case_pass else "fail",
                **trajectory_metrics(trajectory),
            }
        )
    sensitivity = pd.DataFrame(rows)
    all_trajectories = pd.concat(trajectories, ignore_index=True)
    envelope = (
        all_trajectories.groupby("year", as_index=False)
        .agg(
            population_min=("population", "min"),
            population_max=("population", "max"),
            older_65_share_min=("older_65_share", "min"),
            older_65_share_max=("older_65_share", "max"),
            dependency_ratio_min=("dependency_ratio", "min"),
            dependency_ratio_max=("dependency_ratio", "max"),
        )
    )
    max_peak_row = sensitivity.loc[sensitivity["peak_population"].idxmax()].to_dict()
    min_2100_row = sensitivity.loc[sensitivity["population_2100"].idxmin()].to_dict()
    main_levels = (1.8, 0.01, 0.53, True)
    difference_count = (
        (sensitivity["target_standard_tfr"] != main_levels[0]).astype(int)
        + (sensitivity["mortality_improvement"] != main_levels[1]).astype(int)
        + (sensitivity["target_urban_share_2020"] != main_levels[2]).astype(int)
        + (sensitivity["normalize_birth_sex_ratio"] != main_levels[3]).astype(int)
    )
    one_at_a_time = sensitivity[difference_count <= 1]
    oat_max_peak = float(one_at_a_time["peak_population"].max())
    diagnostics = {
        "expected_case_count": 54,
        "observed_case_count": int(len(sensitivity)),
        "case_count_judgment": "pass" if len(sensitivity) == 54 else "fail",
        "all_cases_judgment": (
            "pass" if (sensitivity["judgment"] == "pass").all() else "fail"
        ),
        "maximum_case": max_peak_row,
        "minimum_2100_case": min_2100_row,
        "peak_over_1_5_billion_count": int((sensitivity["peak_population"] > 1.5e9).sum()),
        "one_at_a_time_maximum_peak_population": oat_max_peak,
        "joint_minus_one_at_a_time_maximum_peak": float(
            max_peak_row["peak_population"] - oat_max_peak
        ),
    }
    diagnostics["judgment"] = (
        "pass"
        if diagnostics["case_count_judgment"] == "pass"
        and diagnostics["all_cases_judgment"] == "pass"
        else "fail"
    )
    return sensitivity, envelope, diagnostics


def run_additional_sensitivity(
    data: ParsedData,
    initial_unit: np.ndarray,
    initial_absolute: np.ndarray,
    base_ratios: np.ndarray,
    city_destination_share: float,
) -> pd.DataFrame:
    cases: list[dict[str, Any]] = []
    for level in (0.0, 100.0, 1000.0, 10000.0, 100000.0):
        cases.append({"parameter": "prior_exposure", "level": str(level), "prior": level})
    for level in (0.5, 1.0, 1.5):
        cases.append(
            {"parameter": "mortality_smoothing_scale", "level": str(level), "smoothing": level}
        )
    for level in ("direct_probability", "hazard"):
        cases.append(
            {"parameter": "mortality_rate_conversion", "level": level, "conversion": level}
        )
    for level in ("uniform", "youth_weighted"):
        cases.append({"parameter": "migration_age_profile", "level": level, "migration_profile": level})
    for level in ("exclude_anomaly", "correct_x10"):
        cases.append(
            {"parameter": "fertility_anomaly_shape", "level": level, "fertility_treatment": level}
        )
        cases.append(
            {
                "parameter": "fertility_anomaly_level_unscaled",
                "level": level,
                "fertility_treatment": level,
                "target_standard_tfr": None,
            }
        )

    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        rates = estimate_rates(
            data,
            range(2001, 2006),
            fertility_treatment=str(case.get("fertility_treatment", "exclude_anomaly")),
            prior_exposure=float(case.get("prior", 1000.0)),
            mortality_smoothing_scale=float(case.get("smoothing", 1.0)),
            mortality_rate_conversion=str(case.get("conversion", "direct_probability")),
        )
        target_tfr = case.get("target_standard_tfr", 1.8)
        age_weights = migration_age_profile(str(case.get("migration_profile", "uniform")))
        migration_rate, migration_diagnostics = calibrate_migration_rate(
            initial_unit,
            rates,
            rates.fertility,
            base_ratios,
            city_destination_share,
            target_urban_share=0.53,
            target_standard_tfr=(None if target_tfr is None else float(target_tfr)),
            fixed_fertility_scale=1.0,
            mortality_improvement=0.01,
            migration_age_weights=age_weights,
        )
        trajectory, _, diagnostics = simulate(
            initial_absolute,
            rates,
            rates.fertility,
            base_ratios,
            migration_rate,
            city_destination_share,
            target_standard_tfr=(None if target_tfr is None else float(target_tfr)),
            fixed_fertility_scale=1.0,
            mortality_improvement=0.01,
            normalize_birth_ratio=True,
            migration_age_weights=age_weights,
        )
        passed = (
            migration_diagnostics["judgment"] == "pass"
            and diagnostics["minimum_state_value"] >= -1e-10
            and diagnostics["max_absolute_conservation_residual"]
            / max(float(initial_absolute.sum()), 1.0)
            < 1e-12
        )
        rows.append(
            {
                "case_id": f"additional_{index:02d}",
                "parameter": case["parameter"],
                "level": case["level"],
                "fertility_treatment": rates.fertility_treatment,
                "initial_standard_tfr": value_at(
                    trajectory, BASE_YEAR, "national_standard_tfr"
                ),
                "annual_rural_reclassification_rate": migration_rate,
                "judgment": "pass" if passed else "fail",
                **trajectory_metrics(trajectory),
            }
        )
    return pd.DataFrame(rows)


def configure_plotting() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": 220,
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.facecolor": "white",
        }
    )


def save_figure(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", metadata={"Software": "solve_population.py"})
    plt.close(fig)


def make_figures(
    figures_dir: Path,
    initial_unit: np.ndarray,
    data: ParsedData,
    rates: EstimatedRates,
    medium_fertility: np.ndarray,
    projections: pd.DataFrame,
    rolling_validation: pd.DataFrame,
    factorial_envelope: pd.DataFrame,
) -> None:
    configure_plotting()
    age_male = initial_unit[:, SEX_INDEX["male"], :].sum(axis=0) * 100.0
    age_female = initial_unit[:, SEX_INDEX["female"], :].sum(axis=0) * 100.0
    fig, ax = plt.subplots(figsize=(6.4, 7.2))
    ax.barh(AGES, -age_male, height=0.86, label="Male", color="#4472C4")
    ax.barh(AGES, age_female, height=0.86, label="Female", color="#ED7D31")
    ticks = ax.get_xticks()
    ax.set_xticks(ticks, [f"{abs(x):.1f}" for x in ticks])
    ax.set_xlabel("Share of 2005 population (%)")
    ax.set_ylabel("Age (90 denotes 90+)")
    ax.set_title("Observed 2005 population pyramid")
    ax.legend(frameon=False)
    save_figure(fig, figures_dir / "<SOURCE_FILE_REDACTED>")

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.1))
    area_weights = initial_unit.sum(axis=(1, 2))
    for sex_index, sex in enumerate(SEXES):
        national_q = np.average(rates.mortality[:, sex_index, :], axis=0, weights=area_weights)
        axes[0].plot(AGES, 1000.0 * national_q, label=sex.title())
    axes[0].set_yscale("log")
    axes[0].set_xlabel("Age")
    axes[0].set_ylabel("Smoothed mortality (per 1,000)")
    axes[0].set_title("Mortality schedule")
    axes[0].legend(frameon=False)
    for area_index, area in enumerate(AREAS):
        axes[1].plot(AGES[15:50], 1000.0 * medium_fertility[area_index, 15:50], label=area.title())
    axes[1].set_xlabel("Age of woman")
    axes[1].set_ylabel("Births per 1,000 women")
    axes[1].set_title("Age-specific fertility, medium scenario")
    axes[1].legend(frameon=False)
    save_figure(fig, figures_dir / "<SOURCE_FILE_REDACTED>")

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    colors = {"low": "#70AD47", "medium": "#4472C4", "high": "#C55A11"}
    for scenario, subset in projections.groupby("scenario", sort=False):
        ax.plot(
            subset["year"],
            subset["population"] / 1e8,
            label=f"{scenario.title()} TFR={subset['target_standard_tfr'].iloc[0]:.1f}",
            color=colors[str(scenario)],
            linewidth=2.0 if scenario == "medium" else 1.5,
        )
    ax.axvline(2020, color="0.55", linewidth=0.8, linestyle="--")
    ax.axhline(15.0, color="0.55", linewidth=0.8, linestyle=":")
    ax.set_xlabel("Year")
    ax.set_ylabel("Population (100 million)")
    ax.set_title("Population projections: scenario envelope, not a confidence interval")
    ax.legend(frameon=False, ncol=3)
    save_figure(fig, figures_dir / "<SOURCE_FILE_REDACTED>")

    medium = projections[projections["scenario"] == "medium"]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.stackplot(
        medium["year"],
        100 * medium["child_share"],
        100 * medium["working_share"],
        100 * medium["older_65_share"],
        labels=["0-14", "15-64", "65+"],
        colors=["#9DC3E6", "#70AD47", "#ED7D31"],
        alpha=0.9,
    )
    ax.set_ylim(0, 100)
    ax.set_xlabel("Year")
    ax.set_ylabel("Population share (%)")
    ax.set_title("Age-structure transition, medium scenario")
    ax.legend(frameon=False, loc="upper center", ncol=3)
    save_figure(fig, figures_dir / "<SOURCE_FILE_REDACTED>")

    fig, ax_left = plt.subplots(figsize=(7.2, 4.4))
    ax_right = ax_left.twinx()
    line1 = ax_left.plot(
        medium["year"], 100 * medium["dependency_ratio"], color="#C55A11", label="Dependency ratio"
    )
    line2 = ax_right.plot(
        medium["year"], 100 * medium["urban_share"], color="#4472C4", label="Urban share"
    )
    ax_left.set_xlabel("Year")
    ax_left.set_ylabel("Dependency ratio (%)", color="#C55A11")
    ax_right.set_ylabel("City + town share (%)", color="#4472C4")
    ax_left.set_title("Dependency burden and urbanization")
    lines = line1 + line2
    ax_left.legend(lines, [line.get_label() for line in lines], frameon=False)
    save_figure(fig, figures_dir / "<SOURCE_FILE_REDACTED>")

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.2), sharex=True)
    styles = {
        "persistence_baseline": ("#A5A5A5", "--", "Persistence"),
        "cohort_no_urbanization": ("#70AD47", "-.", "Cohort without urbanization"),
        "multistate_cohort": ("#4472C4", "-", "Multistate cohort"),
    }
    for model_id, (color, linestyle, label) in styles.items():
        subset = rolling_validation[rolling_validation["model_id"] == model_id].sort_values(
            "holdout_year"
        )
        axes[0].plot(
            subset["holdout_year"],
            subset["composite_score"],
            marker="o",
            color=color,
            linestyle=linestyle,
            label=label,
        )
        axes[1].plot(
            subset["holdout_year"],
            subset["area_share_mae_pp"],
            marker="o",
            color=color,
            linestyle=linestyle,
            label=label,
        )
    axes[0].set_ylabel("Composite score (percentage points)")
    axes[0].set_title("Rolling one-step score")
    axes[1].set_ylabel("Area-share MAE (percentage points)")
    axes[1].set_title("Regional component")
    for ax in axes:
        ax.set_xlabel("Held-out year")
        ax.set_xticks([2003, 2004, 2005])
    axes[0].legend(frameon=False, fontsize=8)
    save_figure(fig, figures_dir / "<SOURCE_FILE_REDACTED>")

    main = projections[projections["scenario"] == "medium"].sort_values("year")
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    ax.fill_between(
        factorial_envelope["year"],
        factorial_envelope["population_min"] / 1e8,
        factorial_envelope["population_max"] / 1e8,
        color="#9DC3E6",
        alpha=0.65,
        label="54-case joint envelope",
    )
    ax.plot(
        main["year"],
        main["population"] / 1e8,
        color="#1F4E79",
        linewidth=2.0,
        label="Main scenario",
    )
    ax.axhline(15.0, color="0.45", linewidth=0.8, linestyle=":")
    ax.set_xlabel("Year")
    ax.set_ylabel("Population (100 million)")
    ax.set_title("Joint sensitivity envelope across 54 parameter combinations")
    ax.legend(frameon=False)
    save_figure(fig, figures_dir / "<SOURCE_FILE_REDACTED>")


def build_rate_table(rates: EstimatedRates, medium_fertility: np.ndarray) -> pd.DataFrame:
    rows = []
    for area_index, area in enumerate(AREAS):
        for sex_index, sex in enumerate(SEXES):
            for age in AGES:
                rows.append(
                    {
                        "area": area,
                        "sex": sex,
                        "age": int(age),
                        "mortality_raw_probability": rates.mortality_raw[area_index, sex_index, age],
                        "mortality_smoothed_probability": rates.mortality[area_index, sex_index, age],
                        "fertility_raw_births_per_woman": (
                            rates.fertility_raw[area_index, age] if sex == "female" else 0.0
                        ),
                        "fertility_medium_births_per_woman": (
                            medium_fertility[area_index, age] if sex == "female" else 0.0
                        ),
                    }
                )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Solve-stage workspace",
    )
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results_dir = workspace / "results"
    figures_dir = workspace / "figures"
    results_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    np.random.seed(SEED)

    converted_csv = workspace / "_work" / "converted" / "<SOURCE_FILE_REDACTED>"
    if not converted_csv.is_file():
        raise FileNotFoundError(
            f"Converted workbook CSV is missing: {converted_csv}. Run code/convert_inputs.ps1 first."
        )
    data = parse_workbook_csv(converted_csv)
    write_csv(data.demography, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(data.fertility, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(data.sample_counts, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(data.birth_sex_ratio, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(data.general_fertility, results_dir / "<SOURCE_FILE_REDACTED>")
    write_json(results_dir / "data_quality.json", data.quality)

    comparison, rolling_validation, holdout_diagnostics = run_rolling_comparison(data)
    write_csv(comparison, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(rolling_validation, results_dir / "<SOURCE_FILE_REDACTED>")
    write_json(
        results_dir / "model_comparison.json",
        {
            **holdout_diagnostics,
            "models": comparison.to_dict(orient="records"),
            "rolling_metrics": rolling_validation.to_dict(orient="records"),
        },
    )

    initial_unit = build_observed_state(data, BASE_YEAR)
    rates = estimate_rates(data, range(2001, 2006), fertility_treatment="exclude_anomaly")
    medium_fertility, medium_scale, unscaled_standard_tfr = scale_fertility_to_tfr(
        initial_unit, rates.fertility, 1.8
    )
    unscaled_legacy_tfr = legacy_area_weighted_tfr(initial_unit, rates.fertility)
    base_ratios = birth_ratios_for_year(data, BASE_YEAR)
    city_destination_share = float(
        initial_unit[AREA_INDEX["city"]].sum()
        / (
            initial_unit[AREA_INDEX["city"]].sum()
            + initial_unit[AREA_INDEX["town"]].sum()
        )
    )
    migration_rate, migration_diagnostics = calibrate_migration_rate(
        initial_unit,
        rates,
        rates.fertility,
        base_ratios,
        city_destination_share,
        target_urban_share=0.53,
        target_year=2020,
        target_standard_tfr=1.8,
        mortality_improvement=0.01,
    )
    unit_to_2010, _, _ = simulate(
        initial_unit,
        rates,
        rates.fertility,
        base_ratios,
        migration_rate,
        city_destination_share,
        end_year=2010,
        target_standard_tfr=1.8,
    )
    growth_factor_2005_2010 = value_at(unit_to_2010, 2010, "population")
    population_anchor_2010 = 1.36e9
    base_population_2005 = population_anchor_2010 / growth_factor_2005_2010
    initial_absolute = initial_unit * base_population_2005

    scenario_specs = {"low": 1.5, "medium": 1.8, "high": 2.1}
    scenario_frames: list[pd.DataFrame] = []
    scenario_diagnostics: dict[str, Any] = {}
    for scenario, target_tfr in scenario_specs.items():
        trajectory, _, diagnostics = simulate(
            initial_absolute,
            rates,
            rates.fertility,
            base_ratios,
            migration_rate,
            city_destination_share,
            target_standard_tfr=target_tfr,
        )
        trajectory.insert(0, "scenario", scenario)
        scenario_frames.append(trajectory)
        scenario_diagnostics[scenario] = {
            **diagnostics,
            "raw_standard_tfr_2005": unscaled_standard_tfr,
            "raw_legacy_area_weighted_tfr_2005": unscaled_legacy_tfr,
            "target_standard_tfr": target_tfr,
            "tfr_target_end_year": TFR_TARGET_END_YEAR,
        }
    projections = pd.concat(scenario_frames, ignore_index=True)
    write_csv(projections, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(build_rate_table(rates, medium_fertility), results_dir / "<SOURCE_FILE_REDACTED>")
    write_json(results_dir / "migration_calibration.json", migration_diagnostics)

    sensitivity, factorial_envelope, sensitivity_diagnostics = run_factorial_sensitivity(
        initial_unit,
        initial_absolute,
        rates,
        base_ratios,
        city_destination_share,
    )
    write_csv(sensitivity, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(sensitivity, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(factorial_envelope, results_dir / "<SOURCE_FILE_REDACTED>")
    additional_sensitivity = run_additional_sensitivity(
        data,
        initial_unit,
        initial_absolute,
        base_ratios,
        city_destination_share,
    )
    write_csv(additional_sensitivity, results_dir / "<SOURCE_FILE_REDACTED>")
    sensitivity_diagnostics["additional_case_count"] = int(len(additional_sensitivity))
    sensitivity_diagnostics["additional_cases_judgment"] = (
        "pass" if (additional_sensitivity["judgment"] == "pass").all() else "fail"
    )
    write_json(results_dir / "sensitivity_summary.json", sensitivity_diagnostics)

    key_years = [2005, 2010, 2020, 2030, 2050, 2100]
    key_rows = projections[projections["year"].isin(key_years)].copy()
    write_csv(key_rows, results_dir / "<SOURCE_FILE_REDACTED>")
    scenario_summaries: dict[str, Any] = {}
    for scenario in scenario_specs:
        subset = projections[projections["scenario"] == scenario]
        scenario_summaries[scenario] = {
            **peak_summary(subset),
            "key_years": (
                subset[subset["year"].isin(key_years)][
                    [
                        "year",
                        "population",
                        "older_65_share",
                        "older_60_share",
                        "dependency_ratio",
                        "sex_ratio_male_per_100_female",
                        "birth_sex_ratio",
                        "urban_share",
                        "national_standard_tfr",
                        "legacy_area_weighted_tfr",
                    ]
                ].to_dict(orient="records")
            ),
        }
    medium_projection = projections[projections["scenario"] == "medium"]
    key_results = {
        "judgment": "needs_review",
        "base_year": BASE_YEAR,
        "horizon_year": END_YEAR,
        "population_scale": {
            "calibration_type": "input_anchor",
            "anchor_year": 2010,
            "anchor_population": population_anchor_2010,
            "growth_factor_2005_to_2010": growth_factor_2005_2010,
            "implied_population_2005": base_population_2005,
            "note": "The workbook provides structure and rates but no national base total; the appendix's 2010 value is used only to set scale.",
        },
        "initial_structure": summarize_state(initial_absolute),
        "fertility_definition": {
            "national_standard_tfr_raw_2005": unscaled_standard_tfr,
            "legacy_area_weighted_tfr_raw_2005": unscaled_legacy_tfr,
            "medium_initial_scale_factor": medium_scale,
            "target_maintained_through_year": TFR_TARGET_END_YEAR,
            "post_target_rule": "hold the 2035 area-age fertility schedule fixed",
        },
        "exposure_denominator": "area_total_sample_count_times_area_total_population_share",
        "annual_rural_reclassification_rate": migration_rate,
        "city_share_of_rural_destinations": city_destination_share,
        "scenarios": scenario_summaries,
        "main_selected_scenario": "medium",
        "model_selection_judgment": "needs_review",
        "factorial_sensitivity_judgment": sensitivity_diagnostics["judgment"],
    }
    write_json(results_dir / "key_results.json", key_results)

    zero_mortality = np.zeros_like(rates.mortality)
    zero_fertility = np.zeros_like(rates.fertility)
    zero_rates = EstimatedRates(
        mortality=zero_mortality,
        fertility=zero_fertility,
        mortality_raw=zero_mortality,
        fertility_raw=zero_fertility,
        years=rates.years,
        anomaly_years=[],
        fertility_treatment="boundary",
        prior_exposure=0.0,
        mortality_smoothing_scale=1.0,
        mortality_rate_conversion="direct_probability",
    )
    conserved_state, conserved_stats = step_state(
        initial_absolute,
        zero_mortality,
        zero_fertility,
        base_ratios,
        migration_rate=0.0,
        city_destination_share=city_destination_share,
    )
    migrated_state, migrated_stats = step_state(
        initial_absolute,
        zero_mortality,
        zero_fertility,
        base_ratios,
        migration_rate=migration_rate,
        city_destination_share=city_destination_share,
    )
    zero_tfr_projection, _, _ = simulate(
        initial_absolute,
        rates,
        zero_fertility,
        base_ratios,
        migration_rate,
        city_destination_share,
    )
    max_abs_residual = max(
        float(values["max_absolute_conservation_residual"])
        for values in scenario_diagnostics.values()
    )
    residual_relative = max_abs_residual / base_population_2005
    scenario_order_2050 = [
        value_at(projections[projections["scenario"] == name], 2050, "population")
        for name in ("low", "medium", "high")
    ]
    context_2020_total = value_at(medium_projection, 2020, "population")
    context_2020_old65 = value_at(medium_projection, 2020, "older_65_population")
    context_2020_old60 = value_at(medium_projection, 2020, "older_60_population")
    context_checks = {
        "total_population_2020": {
            "appendix_value": 1.45e9,
            "model_value": context_2020_total,
            "relative_error": abs(context_2020_total - 1.45e9) / 1.45e9,
        },
        "older_65_population_2020": {
            "appendix_value": 1.64e8,
            "model_value": context_2020_old65,
            "relative_error": abs(context_2020_old65 - 1.64e8) / 1.64e8,
        },
        "older_60_population_2020": {
            "appendix_value": 2.34e8,
            "model_value": context_2020_old60,
            "relative_error": abs(context_2020_old60 - 2.34e8) / 2.34e8,
        },
    }
    for check in context_checks.values():
        error = float(check["relative_error"])
        check["judgment"] = "pass" if error <= 0.05 else ("needs_review" if error <= 0.10 else "fail")
    counterexample_state = np.zeros((len(AREAS), len(SEXES), len(AGES)), dtype=float)
    counterexample_fertility = np.zeros((len(AREAS), len(AGES)), dtype=float)
    counterexample_state[AREA_INDEX["city"], SEX_INDEX["female"], 20] = 100.0
    counterexample_state[AREA_INDEX["town"], SEX_INDEX["female"], 40] = 100.0
    counterexample_fertility[AREA_INDEX["city"], 20] = 0.1
    counterexample_fertility[AREA_INDEX["town"], 40] = 0.1
    counterexample_standard = national_period_tfr(
        counterexample_state, counterexample_fertility
    )
    counterexample_legacy = legacy_area_weighted_tfr(
        counterexample_state, counterexample_fertility
    )

    city_2005_total_sample = float(
        data.sample_counts[
            (data.sample_counts["year"] == 2005)
            & (data.sample_counts["area"] == "city")
        ]["sample_count"].sum()
    )
    city_male_age20_share = float(
        data.demography[
            (data.demography["year"] == 2005)
            & (data.demography["area"] == "city")
            & (data.demography["sex"] == "male")
            & (data.demography["age"] == 20)
        ]["population_share_pct"].iloc[0]
    )
    city_male_age20_exposure = city_2005_total_sample * city_male_age20_share / 100.0
    hand_exposure_expected = (2357679.0 + 2350224.0) * 0.0069

    birth_ratio_rows = medium_projection[medium_projection["actual_birth_sex_ratio"].notna()]
    birth_ratio_max_error = float(
        np.max(
            np.abs(
                birth_ratio_rows["birth_sex_ratio"].to_numpy()
                - birth_ratio_rows["actual_birth_sex_ratio"].to_numpy()
            )
        )
    )
    max_tfr_target_error = max(
        float(values["max_tfr_target_absolute_error"])
        for values in scenario_diagnostics.values()
    )

    internal_conditions = {
        "conservation": residual_relative < 1e-12,
        "nonnegative": all(
            float(values["minimum_state_value"]) >= -1e-10
            for values in scenario_diagnostics.values()
        ),
        "zero_dynamics_conserves_total": abs(conserved_state.sum() - initial_absolute.sum())
        / initial_absolute.sum()
        < 1e-12,
        "migration_conserves_total": abs(migrated_state.sum() - initial_absolute.sum())
        / initial_absolute.sum()
        < 1e-12,
        "scenario_ordering_2050": scenario_order_2050[0]
        <= scenario_order_2050[1]
        <= scenario_order_2050[2],
        "zero_tfr_declines_by_2100": value_at(zero_tfr_projection, 2100, "population")
        < value_at(zero_tfr_projection, 2005, "population"),
    }
    scientific_definition_conditions = {
        "standard_tfr_counterexample": (
            abs(counterexample_standard - 0.2) < 1e-12
            and abs(counterexample_legacy - 0.1) < 1e-12
        ),
        "standard_tfr_targets_through_2035": max_tfr_target_error < 1e-12,
        "area_total_exposure_hand_check": abs(city_male_age20_exposure - hand_exposure_expected)
        < 1e-9,
        "birth_sex_ratio_uses_actual_births": birth_ratio_max_error < 1e-12,
        "rolling_training_precedes_holdout": holdout_diagnostics[
            "all_training_years_precede_holdout_judgment"
        ]
        == "pass",
        "factorial_54_cases": sensitivity_diagnostics["judgment"] == "pass",
        "additional_sensitivity_cases": sensitivity_diagnostics[
            "additional_cases_judgment"
        ]
        == "pass",
    }
    execution_judgment = "pass"
    internal_invariants_judgment = "pass" if all(internal_conditions.values()) else "fail"
    context_judgments = [str(check["judgment"]) for check in context_checks.values()]
    contextual_judgment = (
        "fail"
        if "fail" in context_judgments
        else ("needs_review" if "needs_review" in context_judgments else "pass")
    )
    scientific_validation_judgment = (
        "fail"
        if not all(scientific_definition_conditions.values())
        else "needs_review"
    )
    top_level_judgments = [
        execution_judgment,
        internal_invariants_judgment,
        scientific_validation_judgment,
    ]
    overall_validation_judgment = (
        "fail"
        if "fail" in top_level_judgments
        else ("needs_review" if "needs_review" in top_level_judgments else "pass")
    )
    validation = {
        "overall_judgment": overall_validation_judgment,
        "execution_judgment": execution_judgment,
        "internal_invariants_judgment": internal_invariants_judgment,
        "scientific_validation_judgment": scientific_validation_judgment,
        "contextual_judgment": contextual_judgment,
        "internal_invariants": {
            name: "pass" if passed else "fail"
            for name, passed in internal_conditions.items()
        },
        "scientific_definition_checks": {
            name: "pass" if passed else "fail"
            for name, passed in scientific_definition_conditions.items()
        },
        "max_absolute_conservation_residual_people": max_abs_residual,
        "max_relative_conservation_residual": residual_relative,
        "zero_dynamics_residual_people": float(conserved_stats["conservation_residual"]),
        "migration_residual_people": float(migrated_stats["conservation_residual"]),
        "zero_tfr_population_2100": value_at(zero_tfr_projection, 2100, "population"),
        "tfr_definition_counterexample": {
            "standard_national_tfr": counterexample_standard,
            "legacy_area_weighted_tfr": counterexample_legacy,
            "judgment": (
                "pass"
                if scientific_definition_conditions["standard_tfr_counterexample"]
                else "fail"
            ),
        },
        "tfr_policy": {
            "target_end_year": TFR_TARGET_END_YEAR,
            "maximum_absolute_target_error": max_tfr_target_error,
            "post_target_rule": "hold the 2035 area-age fertility schedule fixed",
            "judgment": (
                "pass"
                if scientific_definition_conditions["standard_tfr_targets_through_2035"]
                else "fail"
            ),
        },
        "exposure_denominator_regression": {
            "case": "2005 city male age 20",
            "area_total_sample_count": city_2005_total_sample,
            "population_share_pct": city_male_age20_share,
            "calculated_exposure": city_male_age20_exposure,
            "hand_expected_exposure": hand_exposure_expected,
            "judgment": (
                "pass"
                if scientific_definition_conditions["area_total_exposure_hand_check"]
                else "fail"
            ),
        },
        "birth_sex_ratio_aggregation": {
            "maximum_absolute_error": birth_ratio_max_error,
            "definition": "100 * sum(area male births) / sum(area female births)",
            "judgment": (
                "pass"
                if scientific_definition_conditions["birth_sex_ratio_uses_actual_births"]
                else "fail"
            ),
        },
        "rolling_validation": {
            **holdout_diagnostics,
            "aggregate_metrics": comparison.to_dict(orient="records"),
            "fold_metrics": rolling_validation.to_dict(orient="records"),
        },
        "robustness": sensitivity_diagnostics,
        "appendix_context_checks": context_checks,
        "appendix_context_note": "These are contextual checks, not independent validation: the appendix also supplies the 2010 scaling anchor and the medium-scenario assumptions.",
        "scientific_validation_note": (
            "Definitions, fold isolation, and robustness execution pass, but model rankings reverse "
            "across folds, multi-step national validation is unavailable, sampling design is unknown, "
            "and appendix aging checks fail; scientific status therefore remains needs_review."
        ),
    }
    write_json(results_dir / "validation.json", validation)

    make_figures(
        figures_dir,
        initial_unit,
        data,
        rates,
        medium_fertility,
        projections,
        rolling_validation,
        factorial_envelope,
    )

    medium_peak = scenario_summaries["medium"]
    low_peak = scenario_summaries["low"]
    high_peak = scenario_summaries["high"]
    working_peak_row = medium_projection.loc[
        medium_projection["working_15_64_population"].idxmax()
    ]
    dependency_min_row = medium_projection.loc[
        medium_projection["dependency_ratio"].idxmin()
    ]
    factorial_maximum = sensitivity_diagnostics["maximum_case"]
    factorial_minimum_2100 = sensitivity_diagnostics["minimum_2100_case"]
    macros = {
        "ImpliedBasePopulation": base_population_2005 / 1e8,
        "RawStandardTFR": unscaled_standard_tfr,
        "RawLegacyWeightedTFR": unscaled_legacy_tfr,
        "InitialMediumFertilityScale": medium_scale,
        "TFRTargetMaximumError": max_tfr_target_error,
        "MigrationRatePercent": 100.0 * migration_rate,
        "MediumPeakYear": int(medium_peak["peak_year"]),
        "MediumPeakPopulation": float(medium_peak["peak_population"]) / 1e8,
        "LowPeakYear": int(low_peak["peak_year"]),
        "LowPeakPopulation": float(low_peak["peak_population"]) / 1e8,
        "LowPopulationTwentyFifty": value_at(
            projections[projections["scenario"] == "low"], 2050, "population"
        )
        / 1e8,
        "LowPopulationTwentyOneHundred": value_at(
            projections[projections["scenario"] == "low"], 2100, "population"
        )
        / 1e8,
        "HighPeakYear": int(high_peak["peak_year"]),
        "HighPeakPopulation": float(high_peak["peak_population"]) / 1e8,
        "HighPopulationTwentyFifty": value_at(
            projections[projections["scenario"] == "high"], 2050, "population"
        )
        / 1e8,
        "HighPopulationTwentyOneHundred": value_at(
            projections[projections["scenario"] == "high"], 2100, "population"
        )
        / 1e8,
        "MediumPopulationTwentyTwenty": value_at(medium_projection, 2020, "population") / 1e8,
        "MediumPopulationTwentyThirty": value_at(medium_projection, 2030, "population") / 1e8,
        "MediumPopulationTwentyFifty": value_at(medium_projection, 2050, "population") / 1e8,
        "MediumPopulationTwentyOneHundred": value_at(medium_projection, 2100, "population") / 1e8,
        "MediumOlderShareTwentyTwenty": 100.0 * value_at(medium_projection, 2020, "older_65_share"),
        "MediumOlderShareTwentyFifty": 100.0 * value_at(medium_projection, 2050, "older_65_share"),
        "MediumOlderShareTwentyOneHundred": 100.0
        * value_at(medium_projection, 2100, "older_65_share"),
        "MediumDependencyTwentyTwenty": 100.0
        * value_at(medium_projection, 2020, "dependency_ratio"),
        "MediumDependencyTwentyFifty": 100.0 * value_at(medium_projection, 2050, "dependency_ratio"),
        "MediumDependencyTwentyOneHundred": 100.0
        * value_at(medium_projection, 2100, "dependency_ratio"),
        "MediumUrbanShareTwentyTwenty": 100.0
        * value_at(medium_projection, 2020, "urban_share"),
        "MediumUrbanShareTwentyFifty": 100.0 * value_at(medium_projection, 2050, "urban_share"),
        "MediumUrbanShareTwentyOneHundred": 100.0
        * value_at(medium_projection, 2100, "urban_share"),
        "MediumSexRatioTwentyTwenty": value_at(
            medium_projection, 2020, "sex_ratio_male_per_100_female"
        ),
        "MediumSexRatioTwentyFifty": value_at(
            medium_projection, 2050, "sex_ratio_male_per_100_female"
        ),
        "MediumSexRatioTwentyOneHundred": value_at(
            medium_projection, 2100, "sex_ratio_male_per_100_female"
        ),
        "MediumBirthSexRatioTwoThousandFive": value_at(
            medium_projection, 2005, "birth_sex_ratio"
        ),
        "MediumStandardTFRTwentyFifty": value_at(
            medium_projection, 2050, "national_standard_tfr"
        ),
        "MediumWorkingPeakYear": int(working_peak_row["year"]),
        "MediumWorkingPeakPopulation": float(working_peak_row["working_15_64_population"]) / 1e8,
        "MediumDependencyMinimumYear": int(dependency_min_row["year"]),
        "MediumDependencyMinimum": 100.0 * float(dependency_min_row["dependency_ratio"]),
        "RollingSelectedMeanScore": float(
            comparison.loc[comparison["model_id"] == "multistate_cohort", "composite_score"].iloc[0]
        ),
        "RollingBaselineMeanScore": float(
            comparison.loc[comparison["model_id"] == "persistence_baseline", "composite_score"].iloc[0]
        ),
        "RollingMOneMeanScore": float(
            comparison.loc[
                comparison["model_id"] == "cohort_no_urbanization", "composite_score"
            ].iloc[0]
        ),
        "RollingSelectedStdScore": float(
            comparison.loc[
                comparison["model_id"] == "multistate_cohort", "composite_score_std"
            ].iloc[0]
        ),
        "RollingMOneStdScore": float(
            comparison.loc[
                comparison["model_id"] == "cohort_no_urbanization", "composite_score_std"
            ].iloc[0]
        ),
        "RollingBaselineStdScore": float(
            comparison.loc[
                comparison["model_id"] == "persistence_baseline", "composite_score_std"
            ].iloc[0]
        ),
        "FoldTwoThousandThreeMZero": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2003)
                & (rolling_validation["model_id"] == "persistence_baseline"),
                "composite_score",
            ].iloc[0]
        ),
        "FoldTwoThousandThreeMOne": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2003)
                & (rolling_validation["model_id"] == "cohort_no_urbanization"),
                "composite_score",
            ].iloc[0]
        ),
        "FoldTwoThousandThreeMTwo": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2003)
                & (rolling_validation["model_id"] == "multistate_cohort"),
                "composite_score",
            ].iloc[0]
        ),
        "FoldTwoThousandFourMZero": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2004)
                & (rolling_validation["model_id"] == "persistence_baseline"),
                "composite_score",
            ].iloc[0]
        ),
        "FoldTwoThousandFourMOne": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2004)
                & (rolling_validation["model_id"] == "cohort_no_urbanization"),
                "composite_score",
            ].iloc[0]
        ),
        "FoldTwoThousandFourMTwo": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2004)
                & (rolling_validation["model_id"] == "multistate_cohort"),
                "composite_score",
            ].iloc[0]
        ),
        "FoldTwoThousandFiveMZero": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2005)
                & (rolling_validation["model_id"] == "persistence_baseline"),
                "composite_score",
            ].iloc[0]
        ),
        "FoldTwoThousandFiveMOne": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2005)
                & (rolling_validation["model_id"] == "cohort_no_urbanization"),
                "composite_score",
            ].iloc[0]
        ),
        "FoldTwoThousandFiveMTwo": float(
            rolling_validation.loc[
                (rolling_validation["holdout_year"] == 2005)
                & (rolling_validation["model_id"] == "multistate_cohort"),
                "composite_score",
            ].iloc[0]
        ),
        "FactorialMaximumPeakYear": int(factorial_maximum["peak_year"]),
        "FactorialMaximumPeakPopulation": float(factorial_maximum["peak_population"]) / 1e8,
        "FactorialMaximumPopulationTwentyFifty": float(
            factorial_maximum["population_2050"]
        )
        / 1e8,
        "FactorialMaximumPopulationTwentyOneHundred": float(
            factorial_maximum["population_2100"]
        )
        / 1e8,
        "FactorialMinimumPopulationTwentyOneHundred": float(
            factorial_minimum_2100["population_2100"]
        )
        / 1e8,
        "FactorialPeakOverFifteenCount": int(
            sensitivity_diagnostics["peak_over_1_5_billion_count"]
        ),
        "JointPeakExcessOverOAT": float(
            sensitivity_diagnostics["joint_minus_one_at_a_time_maximum_peak"]
        )
        / 1e8,
        "ExposureCityMaleAgeTwenty": city_male_age20_exposure,
        "ContextTotalErrorTwentyTwenty": 100.0
        * float(context_checks["total_population_2020"]["relative_error"]),
        "ContextOlderSixtyErrorTwentyTwenty": 100.0
        * float(context_checks["older_60_population_2020"]["relative_error"]),
        "ContextOlderSixtyFiveErrorTwentyTwenty": 100.0
        * float(context_checks["older_65_population_2020"]["relative_error"]),
        "MaximumConservationResidual": max_abs_residual,
    }
    macro_lines = ["% Generated by code/solve_population.py; do not edit manually."]
    for name, value in macros.items():
        formatted = str(value) if isinstance(value, int) else f"{float(value):.3f}"
        macro_lines.append(f"\\newcommand{{\\{name}}}{{{formatted}}}")
    (results_dir / "generated_numbers.tex").write_text(
        "\n".join(macro_lines) + "\n", encoding="utf-8"
    )

    input_paths = {
        "problem_doc": workspace / "input" / "problem" / "<SOURCE_FILE_REDACTED>",
        "attachment_rar": workspace
        / "input"
        / "attachments"
        / "<SOURCE_FILE_REDACTED>",
        "converted_csv": converted_csv,
    }
    output_files = sorted(
        [
            path
            for path in results_dir.iterdir()
            if path.is_file()
            and path.name
            not in {
                "run_manifest.json",
                "consistency_check.json",
                "reproducibility_check.json",
            }
        ]
        + [path for path in figures_dir.iterdir() if path.is_file()]
    )
    manifest = {
        "judgment": validation["overall_judgment"],
        "execution_judgment": validation["execution_judgment"],
        "internal_invariants_judgment": validation["internal_invariants_judgment"],
        "scientific_validation_judgment": validation["scientific_validation_judgment"],
        "command": "python code/solve_population.py --workspace .",
        "random_seed": SEED,
        "stochastic_components_used": False,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "inputs": {
            name: {
                "relative_path": str(path.relative_to(workspace)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for name, path in input_paths.items()
        },
        "code": {
            "relative_path": "code/solve_population.py",
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "outputs": [
            {
                "relative_path": str(path.relative_to(workspace)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in output_files
        ],
    }
    write_json(results_dir / "run_manifest.json", manifest)

    print(json.dumps(json_ready({
        "execution": manifest["execution_judgment"],
        "overall": manifest["judgment"],
        "base_population_2005": base_population_2005,
        "medium_peak_year": medium_peak["peak_year"],
        "medium_peak_population": medium_peak["peak_population"],
        "validation": validation["overall_judgment"],
        "model_comparison_best_mean": holdout_diagnostics["lowest_mean_score_model"],
        "model_selection": holdout_diagnostics["selection_judgment"],
    }), ensure_ascii=False, sort_keys=True))
    return 0 if (
        manifest["execution_judgment"] == "pass"
        and manifest["internal_invariants_judgment"] != "fail"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
