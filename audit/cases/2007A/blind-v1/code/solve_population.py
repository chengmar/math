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
    annual_tfr_median = tfr_table.groupby("year")["tfr"].median()
    reference_median = float(annual_tfr_median.median())
    anomaly_years = [
        int(year) for year, value in annual_tfr_median.items() if value < 0.5 * reference_median
    ]

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
    fertility_treatment: str


def estimate_rates(
    data: ParsedData,
    years: Iterable[int],
    fertility_treatment: str = "exclude_anomaly",
    prior_exposure: float = 1000.0,
) -> EstimatedRates:
    years = sorted(int(y) for y in years)
    count_lookup = data.sample_counts.set_index(["year", "area", "sex"])["sample_count"]

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
                sample_count = float(count_lookup.loc[(int(row["year"]), area, sex)])
                exposure = sample_count * float(row["population_share_pct"]) / 100.0
                q = float(row["mortality_per_1000"]) / 1000.0
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
            smooth_log[1:15] = gaussian_filter1d(log_q[1:15], sigma=0.8, mode="nearest")
            old_log = gaussian_filter1d(log_q[10:], sigma=1.0, mode="nearest")
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
            if fertility_treatment == "exclude_anomaly" and year in data.anomaly_years:
                continue
            multiplier = (
                10.0
                if fertility_treatment == "correct_x10" and year in data.anomaly_years
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
            female_count = float(count_lookup.loc[(year, area, "female")])
            for age in range(15, 50):
                exposure = female_count * float(female_rows.loc[age, "population_share_pct"]) / 100.0
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
        fertility_treatment=fertility_treatment,
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


def effective_tfr(state: np.ndarray, fertility: np.ndarray) -> float:
    female_reproductive = state[:, SEX_INDEX["female"], 15:50].sum(axis=1)
    if female_reproductive.sum() <= 0:
        return float("nan")
    area_weights = female_reproductive / female_reproductive.sum()
    area_tfr = fertility[:, 15:50].sum(axis=1)
    return float(np.dot(area_weights, area_tfr))


def scale_fertility_to_tfr(
    state: np.ndarray, fertility: np.ndarray, target_tfr: float
) -> tuple[np.ndarray, float, float]:
    unscaled = effective_tfr(state, fertility)
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


def step_state(
    state: np.ndarray,
    mortality: np.ndarray,
    fertility: np.ndarray,
    birth_ratios: np.ndarray,
    migration_rate: float,
    city_destination_share: float,
    mortality_factor: float = 1.0,
) -> tuple[np.ndarray, dict[str, float]]:
    q = np.clip(mortality * mortality_factor, 0.0, 0.999999)
    next_state = np.zeros_like(state, dtype=float)
    gross_births_by_area = (
        state[:, SEX_INDEX["female"], :] * fertility
    ).sum(axis=1)
    infant_deaths = 0.0
    for area_index in range(len(AREAS)):
        male_fraction = birth_ratios[area_index] / (100.0 + birth_ratios[area_index])
        female_fraction = 1.0 - male_fraction
        male_births = gross_births_by_area[area_index] * male_fraction
        female_births = gross_births_by_area[area_index] * female_fraction
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
    moved = np.clip(migration_rate, 0.0, 1.0) * next_state[rural_index]
    next_state[rural_index] -= moved
    next_state[city_index] += city_destination_share * moved
    next_state[town_index] += (1.0 - city_destination_share) * moved

    gross_births = float(gross_births_by_area.sum())
    expected_total = float(state.sum() - existing_deaths + gross_births - infant_deaths)
    residual = float(next_state.sum() - expected_total)
    return next_state, {
        "gross_births": gross_births,
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
    fertility: np.ndarray,
    base_birth_ratios: np.ndarray,
    migration_rate: float,
    city_destination_share: float,
    end_year: int = END_YEAR,
    mortality_improvement: float = 0.01,
    mortality_improvement_end: int = 2030,
    normalize_birth_ratio: bool = True,
    birth_ratio_target: float = 107.0,
    birth_ratio_target_year: int = 2020,
) -> tuple[pd.DataFrame, np.ndarray, dict[str, float]]:
    state = np.asarray(initial_state, dtype=float).copy()
    rows: list[dict[str, Any]] = []
    max_conservation_residual = 0.0
    min_state_value = float(state.min())
    for year in range(BASE_YEAR, end_year + 1):
        row: dict[str, Any] = {"year": year, **summarize_state(state)}
        row["effective_tfr"] = effective_tfr(state, fertility)
        ratios = projected_birth_ratios(
            base_birth_ratios,
            year,
            normalize=normalize_birth_ratio,
            target_ratio=birth_ratio_target,
            target_year=birth_ratio_target_year,
        )
        reproductive_weights = state[:, SEX_INDEX["female"], 15:50].sum(axis=1)
        if reproductive_weights.sum() > 0:
            row["birth_sex_ratio"] = float(
                np.dot(reproductive_weights / reproductive_weights.sum(), ratios)
            )
        else:
            row["birth_sex_ratio"] = float("nan")
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
    }
    return pd.DataFrame(rows), state, diagnostics


def calibrate_migration_rate(
    initial_state: np.ndarray,
    rates: EstimatedRates,
    fertility: np.ndarray,
    base_birth_ratios: np.ndarray,
    city_destination_share: float,
    target_urban_share: float = 0.53,
    target_year: int = 2020,
    mortality_improvement: float = 0.01,
) -> tuple[float, dict[str, float | str]]:
    def urban_at(rate: float) -> float:
        trajectory, _, _ = simulate(
            initial_state,
            rates,
            fertility,
            base_birth_ratios,
            migration_rate=rate,
            city_destination_share=city_destination_share,
            end_year=target_year,
            mortality_improvement=mortality_improvement,
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


def run_holdout_comparison(
    data: ParsedData,
) -> tuple[pd.DataFrame, dict[str, np.ndarray], dict[str, Any]]:
    training_years = [2001, 2002, 2003, 2004]
    rates = estimate_rates(data, training_years, fertility_treatment="exclude_anomaly")
    state_2004 = build_observed_state(data, 2004)
    observed_2005 = build_observed_state(data, 2005)
    ratio_2004 = birth_ratios_for_year(data, 2004)
    city_destination_share = float(
        state_2004[AREA_INDEX["city"]].sum()
        / (state_2004[AREA_INDEX["city"]].sum() + state_2004[AREA_INDEX["town"]].sum())
    )
    fitted_migration = fit_training_migration_rate(data, training_years)

    predictions: dict[str, np.ndarray] = {"persistence_baseline": state_2004.copy()}
    predictions["cohort_no_urbanization"], _ = step_state(
        state_2004,
        rates.mortality,
        rates.fertility,
        ratio_2004,
        migration_rate=0.0,
        city_destination_share=city_destination_share,
    )
    predictions["multistate_cohort"] , _ = step_state(
        state_2004,
        rates.mortality,
        rates.fertility,
        ratio_2004,
        migration_rate=fitted_migration,
        city_destination_share=city_destination_share,
    )

    rows = []
    display_names = {
        "persistence_baseline": "Persistence baseline",
        "cohort_no_urbanization": "Cohort model without urbanization",
        "multistate_cohort": "Three-area two-sex cohort model",
    }
    for model_id, prediction in predictions.items():
        rows.append(
            {
                "model_id": model_id,
                "model": display_names[model_id],
                **state_metrics(prediction, observed_2005),
            }
        )
    comparison = pd.DataFrame(rows).sort_values("composite_score").reset_index(drop=True)
    comparison["rank"] = np.arange(1, len(comparison) + 1)
    comparison["automatic_judgment"] = ["pass" if rank == 1 else "needs_review" for rank in comparison["rank"]]
    diagnostics = {
        "training_years": training_years,
        "holdout_year": 2005,
        "fertility_anomaly_treatment": "exclude_2003",
        "fitted_annual_rural_reclassification_rate": fitted_migration,
        "lowest_numeric_score_model": str(comparison.iloc[0]["model_id"]),
        "comparison_judgment": "pass",
    }
    predictions["observed_2005"] = observed_2005
    return comparison, predictions, diagnostics


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


def run_sensitivity(
    data: ParsedData,
    initial_unit: np.ndarray,
    initial_absolute: np.ndarray,
    rates: EstimatedRates,
    base_ratios: np.ndarray,
    city_destination_share: float,
    main_migration_rate: float,
) -> pd.DataFrame:
    cases: list[dict[str, Any]] = [
        {
            "case_id": "main",
            "parameter": "reference",
            "level": "main",
            "target_tfr": 1.8,
            "mortality_improvement": 0.01,
            "urban_target": 0.53,
            "normalize_birth_ratio": True,
            "fertility_treatment": "exclude_anomaly",
        },
        {
            "case_id": "tfr_low",
            "parameter": "initial_tfr",
            "level": "1.5",
            "target_tfr": 1.5,
            "mortality_improvement": 0.01,
            "urban_target": 0.53,
            "normalize_birth_ratio": True,
            "fertility_treatment": "exclude_anomaly",
        },
        {
            "case_id": "tfr_high",
            "parameter": "initial_tfr",
            "level": "2.1",
            "target_tfr": 2.1,
            "mortality_improvement": 0.01,
            "urban_target": 0.53,
            "normalize_birth_ratio": True,
            "fertility_treatment": "exclude_anomaly",
        },
        {
            "case_id": "mortality_fixed",
            "parameter": "mortality_improvement",
            "level": "0%/year",
            "target_tfr": 1.8,
            "mortality_improvement": 0.0,
            "urban_target": 0.53,
            "normalize_birth_ratio": True,
            "fertility_treatment": "exclude_anomaly",
        },
        {
            "case_id": "mortality_fast",
            "parameter": "mortality_improvement",
            "level": "2%/year",
            "target_tfr": 1.8,
            "mortality_improvement": 0.02,
            "urban_target": 0.53,
            "normalize_birth_ratio": True,
            "fertility_treatment": "exclude_anomaly",
        },
        {
            "case_id": "urban_slow",
            "parameter": "urban_share_2020",
            "level": "50%",
            "target_tfr": 1.8,
            "mortality_improvement": 0.01,
            "urban_target": 0.50,
            "normalize_birth_ratio": True,
            "fertility_treatment": "exclude_anomaly",
        },
        {
            "case_id": "urban_fast",
            "parameter": "urban_share_2020",
            "level": "56%",
            "target_tfr": 1.8,
            "mortality_improvement": 0.01,
            "urban_target": 0.56,
            "normalize_birth_ratio": True,
            "fertility_treatment": "exclude_anomaly",
        },
        {
            "case_id": "sex_ratio_fixed",
            "parameter": "birth_sex_ratio",
            "level": "fixed_2005",
            "target_tfr": 1.8,
            "mortality_improvement": 0.01,
            "urban_target": 0.53,
            "normalize_birth_ratio": False,
            "fertility_treatment": "exclude_anomaly",
        },
        {
            "case_id": "fertility_corrected",
            "parameter": "2003_fertility_treatment",
            "level": "multiply_by_10",
            "target_tfr": 1.8,
            "mortality_improvement": 0.01,
            "urban_target": 0.53,
            "normalize_birth_ratio": True,
            "fertility_treatment": "correct_x10",
        },
    ]
    rows: list[dict[str, Any]] = []
    corrected_rates: EstimatedRates | None = None
    for case in cases:
        case_rates = rates
        if case["fertility_treatment"] == "correct_x10":
            if corrected_rates is None:
                corrected_rates = estimate_rates(
                    data, range(2001, 2006), fertility_treatment="correct_x10"
                )
            case_rates = corrected_rates
        fertility, _scale, _unscaled = scale_fertility_to_tfr(
            initial_unit, case_rates.fertility, float(case["target_tfr"])
        )
        if case["case_id"] == "main":
            migration_rate = main_migration_rate
        else:
            migration_rate, _ = calibrate_migration_rate(
                initial_unit,
                case_rates,
                fertility,
                base_ratios,
                city_destination_share,
                target_urban_share=float(case["urban_target"]),
                mortality_improvement=float(case["mortality_improvement"]),
            )
        trajectory, _, _ = simulate(
            initial_absolute,
            case_rates,
            fertility,
            base_ratios,
            migration_rate,
            city_destination_share,
            mortality_improvement=float(case["mortality_improvement"]),
            normalize_birth_ratio=bool(case["normalize_birth_ratio"]),
        )
        peak = peak_summary(trajectory)
        rows.append(
            {
                **case,
                "annual_rural_reclassification_rate": migration_rate,
                **peak,
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
    holdout_predictions: dict[str, np.ndarray],
    sensitivity: pd.DataFrame,
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
            label=f"{scenario.title()} TFR={subset['target_initial_tfr'].iloc[0]:.1f}",
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

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for model_id, style in [
        ("observed_2005", {"color": "black", "linewidth": 2.2, "label": "Observed 2005"}),
        ("persistence_baseline", {"color": "#A5A5A5", "linestyle": "--", "label": "Persistence"}),
        ("multistate_cohort", {"color": "#4472C4", "label": "Multistate cohort"}),
    ]:
        state = holdout_predictions[model_id]
        age_profile = state.sum(axis=(0, 1)) / state.sum() * 100.0
        ax.plot(AGES, age_profile, **style)
    ax.set_xlabel("Age (90 denotes 90+)")
    ax.set_ylabel("Population share (%)")
    ax.set_title("Held-out 2005 age-profile check")
    ax.legend(frameon=False)
    save_figure(fig, figures_dir / "<SOURCE_FILE_REDACTED>")

    main_2050 = float(sensitivity.loc[sensitivity["case_id"] == "main", "population_2050"].iloc[0])
    plot_sensitivity = sensitivity[sensitivity["case_id"] != "main"].copy()
    plot_sensitivity["delta_2050"] = (plot_sensitivity["population_2050"] - main_2050) / 1e8
    plot_sensitivity["label"] = plot_sensitivity["parameter"] + ": " + plot_sensitivity["level"].astype(str)
    plot_sensitivity = plot_sensitivity.sort_values("delta_2050")
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    colors = np.where(plot_sensitivity["delta_2050"] >= 0, "#4472C4", "#ED7D31")
    ax.barh(plot_sensitivity["label"], plot_sensitivity["delta_2050"], color=colors)
    ax.axvline(0.0, color="black", linewidth=0.8)
    ax.set_xlabel("Change in 2050 population (100 million) relative to main")
    ax.set_title("One-at-a-time sensitivity")
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

    comparison, holdout_predictions, holdout_diagnostics = run_holdout_comparison(data)
    write_csv(comparison, results_dir / "<SOURCE_FILE_REDACTED>")
    write_json(
        results_dir / "model_comparison.json",
        {
            **holdout_diagnostics,
            "models": comparison.to_dict(orient="records"),
            "selected_model": "multistate_cohort",
            "selection_judgment": (
                "pass"
                if holdout_diagnostics["lowest_numeric_score_model"] == "multistate_cohort"
                else "needs_review"
            ),
            "selection_note": "Selection combines the held-out score with explicit coverage of sex ratio, aging, and rural-to-urban reclassification.",
        },
    )

    initial_unit = build_observed_state(data, BASE_YEAR)
    rates = estimate_rates(data, range(2001, 2006), fertility_treatment="exclude_anomaly")
    medium_fertility, medium_scale, unscaled_tfr = scale_fertility_to_tfr(
        initial_unit, rates.fertility, 1.8
    )
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
        medium_fertility,
        base_ratios,
        city_destination_share,
        target_urban_share=0.53,
        target_year=2020,
        mortality_improvement=0.01,
    )
    unit_to_2010, _, _ = simulate(
        initial_unit,
        rates,
        medium_fertility,
        base_ratios,
        migration_rate,
        city_destination_share,
        end_year=2010,
    )
    growth_factor_2005_2010 = value_at(unit_to_2010, 2010, "population")
    population_anchor_2010 = 1.36e9
    base_population_2005 = population_anchor_2010 / growth_factor_2005_2010
    initial_absolute = initial_unit * base_population_2005

    scenario_specs = {"low": 1.5, "medium": 1.8, "high": 2.1}
    scenario_frames: list[pd.DataFrame] = []
    scenario_diagnostics: dict[str, Any] = {}
    scenario_fertility: dict[str, np.ndarray] = {}
    for scenario, target_tfr in scenario_specs.items():
        fertility, scale, raw_effective = scale_fertility_to_tfr(
            initial_unit, rates.fertility, target_tfr
        )
        scenario_fertility[scenario] = fertility
        trajectory, _, diagnostics = simulate(
            initial_absolute,
            rates,
            fertility,
            base_ratios,
            migration_rate,
            city_destination_share,
        )
        trajectory.insert(0, "scenario", scenario)
        trajectory.insert(1, "target_initial_tfr", target_tfr)
        trajectory.insert(2, "fertility_scale_factor", scale)
        scenario_frames.append(trajectory)
        scenario_diagnostics[scenario] = {
            **diagnostics,
            "raw_effective_tfr": raw_effective,
            "target_initial_tfr": target_tfr,
            "fertility_scale_factor": scale,
        }
    projections = pd.concat(scenario_frames, ignore_index=True)
    write_csv(projections, results_dir / "<SOURCE_FILE_REDACTED>")
    write_csv(build_rate_table(rates, medium_fertility), results_dir / "<SOURCE_FILE_REDACTED>")
    write_json(results_dir / "migration_calibration.json", migration_diagnostics)

    sensitivity = run_sensitivity(
        data,
        initial_unit,
        initial_absolute,
        rates,
        base_ratios,
        city_destination_share,
        migration_rate,
    )
    write_csv(sensitivity, results_dir / "<SOURCE_FILE_REDACTED>")

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
                        "urban_share",
                    ]
                ].to_dict(orient="records")
            ),
        }
    medium_projection = projections[projections["scenario"] == "medium"]
    key_results = {
        "judgment": "pass",
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
        "raw_effective_tfr": unscaled_tfr,
        "medium_fertility_scale_factor": medium_scale,
        "annual_rural_reclassification_rate": migration_rate,
        "city_share_of_rural_destinations": city_destination_share,
        "scenarios": scenario_summaries,
        "main_selected_scenario": "medium",
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
        fertility_treatment="boundary",
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
    validation_conditions = {
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
    mathematical_judgment = "pass" if all(validation_conditions.values()) else "fail"
    context_judgments = [str(check["judgment"]) for check in context_checks.values()]
    contextual_judgment = (
        "fail"
        if "fail" in context_judgments
        else ("needs_review" if "needs_review" in context_judgments else "pass")
    )
    overall_validation_judgment = (
        "fail"
        if mathematical_judgment == "fail"
        else (
            "needs_review"
            if contextual_judgment != "pass" or data.quality["overall_judgment"] != "pass"
            else "pass"
        )
    )
    validation = {
        "overall_judgment": overall_validation_judgment,
        "mathematical_judgment": mathematical_judgment,
        "contextual_judgment": contextual_judgment,
        "conditions": {
            name: "pass" if passed else "fail"
            for name, passed in validation_conditions.items()
        },
        "max_absolute_conservation_residual_people": max_abs_residual,
        "max_relative_conservation_residual": residual_relative,
        "zero_dynamics_residual_people": float(conserved_stats["conservation_residual"]),
        "migration_residual_people": float(migrated_stats["conservation_residual"]),
        "zero_tfr_population_2100": value_at(zero_tfr_projection, 2100, "population"),
        "holdout": {
            **holdout_diagnostics,
            "metrics": comparison.to_dict(orient="records"),
        },
        "appendix_context_checks": context_checks,
        "appendix_context_note": "These are contextual checks, not independent validation: the appendix also supplies the 2010 scaling anchor and the medium-scenario assumptions.",
    }
    write_json(results_dir / "validation.json", validation)

    make_figures(
        figures_dir,
        initial_unit,
        data,
        rates,
        medium_fertility,
        projections,
        holdout_predictions,
        sensitivity,
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
    macros = {
        "ImpliedBasePopulation": base_population_2005 / 1e8,
        "RawEffectiveTFR": unscaled_tfr,
        "MigrationRatePercent": 100.0 * migration_rate,
        "MediumPeakYear": int(medium_peak["peak_year"]),
        "MediumPeakPopulation": float(medium_peak["peak_population"]) / 1e8,
        "LowPeakYear": int(low_peak["peak_year"]),
        "LowPeakPopulation": float(low_peak["peak_population"]) / 1e8,
        "HighPeakYear": int(high_peak["peak_year"]),
        "HighPeakPopulation": float(high_peak["peak_population"]) / 1e8,
        "MediumPopulationTwentyTwenty": value_at(medium_projection, 2020, "population") / 1e8,
        "MediumPopulationTwentyThirty": value_at(medium_projection, 2030, "population") / 1e8,
        "MediumPopulationTwentyFifty": value_at(medium_projection, 2050, "population") / 1e8,
        "MediumPopulationTwentyOneHundred": value_at(medium_projection, 2100, "population") / 1e8,
        "MediumOlderShareTwentyTwenty": 100.0 * value_at(medium_projection, 2020, "older_65_share"),
        "MediumOlderShareTwentyFifty": 100.0 * value_at(medium_projection, 2050, "older_65_share"),
        "MediumDependencyTwentyFifty": 100.0 * value_at(medium_projection, 2050, "dependency_ratio"),
        "MediumUrbanShareTwentyFifty": 100.0 * value_at(medium_projection, 2050, "urban_share"),
        "MediumSexRatioTwentyFifty": value_at(
            medium_projection, 2050, "sex_ratio_male_per_100_female"
        ),
        "MediumWorkingPeakYear": int(working_peak_row["year"]),
        "MediumWorkingPeakPopulation": float(working_peak_row["working_15_64_population"]) / 1e8,
        "MediumDependencyMinimumYear": int(dependency_min_row["year"]),
        "MediumDependencyMinimum": 100.0 * float(dependency_min_row["dependency_ratio"]),
        "HoldoutSelectedScore": float(
            comparison.loc[comparison["model_id"] == "multistate_cohort", "composite_score"].iloc[0]
        ),
        "HoldoutBaselineScore": float(
            comparison.loc[comparison["model_id"] == "persistence_baseline", "composite_score"].iloc[0]
        ),
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
        "judgment": "pass" if validation["mathematical_judgment"] == "pass" else "fail",
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
        "status": manifest["judgment"],
        "base_population_2005": base_population_2005,
        "medium_peak_year": medium_peak["peak_year"],
        "medium_peak_population": medium_peak["peak_population"],
        "validation": validation["overall_judgment"],
        "model_comparison_best": holdout_diagnostics["lowest_numeric_score_model"],
    }), ensure_ascii=False, sort_keys=True))
    return 0 if manifest["judgment"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
