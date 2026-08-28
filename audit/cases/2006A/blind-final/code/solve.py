#!/usr/bin/env python3
"""Reproducible blind solution pipeline for CUMCM 2006 A.

The program reads only the legacy-file conversions under ``input/converted``
and creates every quantitative result and figure used by the paper.  It does
not use network access or reference solutions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from scipy.stats import spearmanr


SEED = 2006
YEARS = (2001, 2002, 2003, 2004, 2005)
A_PUBLISHER = "P115"
SURVEY_FIELDS = 24
PRIOR_SHARE_STRENGTH = 30.0
PRIOR_SAT_STRENGTH = 10.0
RISK_Z = 0.5
MC_DRAWS = 300

CATEGORY_ORDER = [
    "计算机类",
    "经管类",
    "数学类",
    "英语类",
    "两课类",
    "机械、能源类",
    "化学、化工类",
    "地理、地质类",
    "环境类",
]

KNOWN_PUBLISHERS = {
    "P030", "P044", "P063", "P091", "P102", "P106", "P110", "P115",
    "P118", "P131", "P196", "P199", "P210", "P246", "P293", "P304",
    "P307", "P357", "P390", "P405", "P416", "P432", "P511", "P534",
}


def scalar(value: Any) -> Any:
    """Convert numpy/pandas scalars recursively for JSON serialization."""
    if isinstance(value, Mapping):
        return {str(k): scalar(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scalar(v) for v in value]
    if isinstance(value, np.ndarray):
        return [scalar(v) for v in value.tolist()]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if np.isnan(value):
            return None
        return float(value)
    if pd.isna(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(scalar(value), ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_tsv_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-16", newline="") as stream:
        return [[cell.strip() for cell in row] for row in csv.reader(stream, delimiter="\t")]


def normalize_category(value: str) -> str:
    text = str(value).strip().replace("机械能源类", "机械、能源类")
    text = text.replace("机械，能源类", "机械、能源类")
    return text


def parse_int(value: Any) -> int | None:
    text = str(value).strip()
    if re.fullmatch(r"[+-]?\d+(?:\.0+)?", text):
        return int(float(text))
    return None


def parse_float(value: Any) -> float | None:
    text = str(value).strip()
    if text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def locate_one(root: Path, pattern: str) -> Path:
    matches = sorted(root.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one file for {pattern!r}, got {len(matches)}")
    return matches[0]


def load_sales(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = read_tsv_rows(path)
    header_index = next(i for i, row in enumerate(rows) if row and row[0] == "学科类别")
    records: list[dict[str, Any]] = []
    current_category = ""
    pending: dict[str, Any] | None = None
    skipped_total_rows = 0

    for raw in rows[header_index + 1 :]:
        row = raw + [""] * max(0, 9 - len(raw))
        if row[0]:
            current_category = normalize_category(row[0])
        if row[1] == "总计":
            skipped_total_rows += 1
            pending = None
            continue
        label = row[3]
        code = parse_int(row[2])
        if label == "计划销售量" and code is not None:
            pending = {
                "category": current_category,
                "course": row[1],
                "course_code": code,
                "plan": [parse_float(v) for v in row[4:9]],
            }
        elif label == "实际销售量" and pending is not None:
            actual = [parse_float(v) for v in row[4:9]]
            if any(v is None for v in pending["plan"] + actual):
                raise RuntimeError(f"Missing sales value near course {pending['course_code']}")
            for year, plan, realized in zip(YEARS, pending["plan"], actual):
                records.append(
                    {
                        "category": pending["category"],
                        "course": pending["course"],
                        "course_code": pending["course_code"],
                        "year": year,
                        "plan_sales": float(plan),
                        "actual_sales": float(realized),
                    }
                )
            pending = None

    frame = pd.DataFrame.from_records(records).sort_values(["course_code", "year"])
    if len(frame) != 72 * 5 or frame["course_code"].nunique() != 72:
        raise RuntimeError(f"Sales parser expected 360 records/72 courses, got {len(frame)}")
    audit = {
        "records": len(frame),
        "courses": int(frame["course_code"].nunique()),
        "years": sorted(int(v) for v in frame["year"].unique()),
        "skipped_published_total_rows": skipped_total_rows,
        "missing_values": int(frame.isna().sum().sum()),
        "nonpositive_plan": int((frame["plan_sales"] <= 0).sum()),
        "nonpositive_actual": int((frame["actual_sales"] <= 0).sum()),
        "actual_exceeds_plan": int((frame["actual_sales"] > frame["plan_sales"]).sum()),
    }
    return frame, audit


def load_isbn(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = read_tsv_rows(path)
    header_index = next(i for i, row in enumerate(rows) if row and row[0] == "学科名称")
    records: list[dict[str, Any]] = []
    current_category = ""
    published_totals: dict[str, list[int]] = {}

    for raw in rows[header_index + 1 :]:
        row = raw + [""] * max(0, 10 - len(raw))
        if row[0]:
            current_category = normalize_category(row[0])
        if row[1] == "总计":
            published_totals[current_category] = [parse_int(v) or 0 for v in row[3:9]]
            continue
        code = parse_int(row[2])
        if code is None:
            continue
        values = [parse_int(v) for v in row[3:9]]
        price = parse_float(row[9])
        if any(v is None for v in values) or price is None:
            raise RuntimeError(f"Invalid ISBN/price row for course {code}")
        records.append(
            {
                "category": current_category,
                "course": row[1],
                "course_code": code,
                **{f"isbn_{year}": int(values[i]) for i, year in enumerate(YEARS)},
                "request_2006": int(values[5]),
                "price": float(price),
            }
        )

    frame = pd.DataFrame.from_records(records).sort_values("course_code").reset_index(drop=True)
    if len(frame) != 72 or frame["course_code"].nunique() != 72:
        raise RuntimeError(f"ISBN parser expected 72 courses, got {len(frame)}")
    computed_totals = {year: int(frame[f"isbn_{year}"].sum()) for year in YEARS}
    request_total = int(frame["request_2006"].sum())
    computed_branch_totals: dict[str, dict[str, int]] = {}
    published_branch_totals: dict[str, dict[str, int]] = {}
    branch_total_differences: list[dict[str, Any]] = []
    for category in CATEGORY_ORDER:
        part = frame[frame["category"] == category]
        computed_values = [int(part[f"isbn_{year}"].sum()) for year in YEARS] + [int(part["request_2006"].sum())]
        published_values = published_totals.get(category, [0] * 6)
        computed_branch_totals[category] = {
            **{str(year): computed_values[i] for i, year in enumerate(YEARS)},
            "2006_request": computed_values[5],
        }
        published_branch_totals[category] = {
            **{str(year): int(published_values[i]) for i, year in enumerate(YEARS)},
            "2006_request": int(published_values[5]),
        }
        for i, label in enumerate([*(str(y) for y in YEARS), "2006_request"]):
            if computed_values[i] != published_values[i]:
                branch_total_differences.append(
                    {
                        "category": category,
                        "period": label,
                        "detail_sum": computed_values[i],
                        "published_total_row": int(published_values[i]),
                        "difference_total_minus_detail": int(published_values[i] - computed_values[i]),
                    }
                )
    published_request_total = sum(value["2006_request"] for value in published_branch_totals.values())
    all_even = bool((frame["request_2006"] % 2 == 0).all())
    audit = {
        "courses": len(frame),
        "missing_values": int(frame.isna().sum().sum()),
        "historical_totals": computed_totals,
        "historical_total_constant": len(set(computed_totals.values())) == 1,
        "request_2006_total": request_total,
        "published_request_total_2006": published_request_total,
        "request_total_difference": published_request_total - request_total,
        "computed_branch_totals": computed_branch_totals,
        "published_branch_totals": published_branch_totals,
        "branch_total_differences": branch_total_differences,
        "all_requests_even": all_even,
        "published_branch_totals_match": len(branch_total_differences) == 0,
    }
    return frame, audit


def load_hr(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = read_tsv_rows(path)
    header_index = next(i for i, row in enumerate(rows) if row and row[0] == "所属分社")
    records: list[dict[str, Any]] = []
    for raw in rows[header_index + 1 :]:
        row = raw + [""] * max(0, 7 - len(raw))
        category = normalize_category(row[0])
        values = [parse_int(v) for v in row[1:7]]
        if not category or any(v is None for v in values):
            continue
        planner = int(values[0] * values[1])
        editor = int(values[2] * values[3])
        proof = int(values[4] * values[5])
        records.append(
            {
                "category": category,
                "planner_count": values[0],
                "planner_rate": values[1],
                "editor_count": values[2],
                "editor_rate": values[3],
                "proof_count": values[4],
                "proof_rate": values[5],
                "planner_capacity": planner,
                "editor_capacity": editor,
                "proof_capacity": proof,
                "bottleneck_capacity": min(planner, editor, proof),
            }
        )
    frame = pd.DataFrame.from_records(records)
    if len(frame) != 9 or set(frame["category"]) != set(CATEGORY_ORDER):
        raise RuntimeError("HR parser did not recover exactly the nine branches")
    frame["category"] = pd.Categorical(frame["category"], CATEGORY_ORDER, ordered=True)
    frame = frame.sort_values("category").reset_index(drop=True)
    audit = {
        "branches": len(frame),
        "missing_values": int(frame.isna().sum().sum()),
        "total_bottleneck_capacity": int(frame["bottleneck_capacity"].sum()),
        "bottleneck_stage_counts": {
            "planning": int((frame["planner_capacity"] == frame["bottleneck_capacity"]).sum()),
            "editing": int((frame["editor_capacity"] == frame["bottleneck_capacity"]).sum()),
            "proofreading": int((frame["proof_capacity"] == frame["bottleneck_capacity"]).sum()),
        },
    }
    return frame, audit


def load_surveys(directory: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    records: list[dict[str, Any]] = []
    year_audits: list[dict[str, Any]] = []
    files = sorted(directory.glob("*_20??.tsv"))
    if len(files) != 5:
        raise RuntimeError(f"Expected five survey year files, got {len(files)}")

    for path in files:
        match = re.search(r"(20\d{2})", path.stem)
        if not match:
            raise RuntimeError(f"Cannot infer survey year from {path.name}")
        year = int(match.group(1))
        raw_rows = 0
        retained_rows = 0
        exported_columns = 0
        rows_with_nonempty_extra = 0
        id_values: list[str] = []
        respondent_values: list[str] = []
        exact_signatures: list[tuple[str, ...]] = []
        invalid_sat_cells = 0

        with path.open("r", encoding="utf-16", newline="") as stream:
            reader = csv.reader(stream, delimiter="\t")
            for row_number, row in enumerate(reader, start=1):
                raw_rows += 1
                exported_columns = max(exported_columns, len(row))
                if row_number <= 2:
                    continue
                row = [str(v).strip() for v in row]
                if not any(row[:SURVEY_FIELDS]):
                    continue
                retained_rows += 1
                if any(v != "" for v in row[SURVEY_FIELDS:]):
                    rows_with_nonempty_extra += 1
                row = (row + [""] * SURVEY_FIELDS)[:SURVEY_FIELDS]
                course_code = parse_int(row[7])
                if course_code is not None and not (1 <= course_code <= 72):
                    course_code = None
                publisher = re.sub(r"\s+", "", row[14].upper())
                sat_values = [parse_float(v) for v in row[20:24]]
                for raw_sat, sat in zip(row[20:24], sat_values):
                    if raw_sat and (sat is None or not (1 <= sat <= 5)):
                        invalid_sat_cells += 1
                valid_sat = [v for v in sat_values if v is not None and 1 <= v <= 5]
                q1 = parse_float(row[6])
                price = parse_float(row[18])
                id_values.append(row[0])
                respondent_values.append(row[1])
                exact_signatures.append(tuple(row[1:24]))
                records.append(
                    {
                        "year": year,
                        "row_id": row[0],
                        "respondent": row[1],
                        "school": row[2],
                        "q1_rank": q1,
                        "course_raw": row[7],
                        "course_code": course_code,
                        "category_raw": normalize_category(row[10]),
                        "publisher": publisher,
                        "price": price,
                        "sat_mean": float(np.mean(valid_sat)) if valid_sat else np.nan,
                        "is_a": publisher == A_PUBLISHER,
                        "publisher_known": publisher in KNOWN_PUBLISHERS,
                    }
                )

        nonempty_ids = [value for value in id_values if value]
        nonempty_respondents = [value for value in respondent_values if value]
        year_audits.append(
            {
                "year": year,
                "exported_rows": raw_rows,
                "data_rows": retained_rows,
                "exported_columns": exported_columns,
                "rows_with_nonempty_columns_after_24": rows_with_nonempty_extra,
                "duplicate_nonempty_id_count": len(nonempty_ids) - len(set(nonempty_ids)),
                "unique_respondents": len(set(nonempty_respondents)),
                "exact_duplicate_rows_excluding_id": len(exact_signatures) - len(set(exact_signatures)),
                "invalid_satisfaction_cells": invalid_sat_cells,
            }
        )

    survey = pd.DataFrame.from_records(records)
    survey["course_code"] = pd.array(survey["course_code"], dtype="Int64")
    survey["mapped_course"] = survey["course_code"].between(1, 72, inclusive="both").fillna(False).astype(bool)
    survey["publisher_present"] = survey["publisher"] != ""
    survey["q1_valid"] = survey["q1_rank"].between(1, 24, inclusive="both")
    survey["price_valid"] = survey["price"].gt(0)

    course_rows: list[dict[str, Any]] = []
    for year in YEARS:
        year_frame = survey[survey["year"] == year]
        mapped = year_frame[year_frame["mapped_course"]]
        denominator_global = mapped[mapped["publisher_present"]]
        global_share = float(denominator_global["is_a"].mean()) if len(denominator_global) else 0.0
        a_sat_global = mapped[mapped["is_a"]]["sat_mean"].dropna()
        global_sat = float(a_sat_global.mean()) if len(a_sat_global) else 3.0
        mapped_count = max(len(mapped), 1)
        for code in range(1, 73):
            subset = mapped[mapped["course_code"] == code]
            with_publisher = subset[subset["publisher_present"]]
            n = len(with_publisher)
            a_count = int(with_publisher["is_a"].sum())
            share_raw = a_count / n if n else np.nan
            share_smoothed = (a_count + PRIOR_SHARE_STRENGTH * global_share) / (
                n + PRIOR_SHARE_STRENGTH
            )
            a_sat = subset[subset["is_a"]]["sat_mean"].dropna()
            sat_n = len(a_sat)
            sat_raw = float(a_sat.mean()) if sat_n else np.nan
            sat_smoothed = (
                float(a_sat.sum()) + PRIOR_SAT_STRENGTH * global_sat
            ) / (sat_n + PRIOR_SAT_STRENGTH)
            course_rows.append(
                {
                    "year": year,
                    "course_code": code,
                    "survey_course_rows": len(subset),
                    "survey_demand_share": len(subset) / mapped_count,
                    "publisher_denominator": n,
                    "a_publisher_rows": a_count,
                    "market_share_raw": share_raw,
                    "market_share_smoothed": share_smoothed,
                    "a_satisfaction_n": sat_n,
                    "a_satisfaction_raw": sat_raw,
                    "a_satisfaction_smoothed": sat_smoothed,
                    "global_market_share": global_share,
                    "global_a_satisfaction": global_sat,
                }
            )

    course_stats = pd.DataFrame.from_records(course_rows)
    aggregate_audit = {
        "year_tables": year_audits,
        "data_rows_total": len(survey),
        "mapped_numeric_course_rows": int(survey["mapped_course"].sum()),
        "mapped_numeric_course_rate": float(survey["mapped_course"].mean()),
        "publisher_missing_rows": int((~survey["publisher_present"]).sum()),
        "known_publisher_code_rows": int(survey["publisher_known"].sum()),
        "a_publisher_rows": int(survey["is_a"].sum()),
        "valid_q1_rows": int(survey["q1_valid"].sum()),
        "valid_price_rows": int(survey["price_valid"].sum()),
        "valid_satisfaction_row_means": int(survey["sat_mean"].notna().sum()),
        "smoothing_priors": {
            "market_share_pseudo_count": PRIOR_SHARE_STRENGTH,
            "satisfaction_pseudo_count": PRIOR_SAT_STRENGTH,
        },
    }
    return survey, course_stats, aggregate_audit


def build_panel(sales: pd.DataFrame, isbn: pd.DataFrame) -> pd.DataFrame:
    long_isbn = isbn[["course_code", *[f"isbn_{year}" for year in YEARS]]].melt(
        id_vars="course_code", var_name="year_label", value_name="isbn"
    )
    long_isbn["year"] = long_isbn["year_label"].str.extract(r"(\d{4})").astype(int)
    panel = sales.merge(long_isbn[["course_code", "year", "isbn"]], on=["course_code", "year"])
    panel = panel.merge(isbn[["course_code", "price"]], on="course_code")
    panel["productivity"] = panel["actual_sales"] / panel["isbn"]
    panel["plan_realization"] = panel["actual_sales"] / panel["plan_sales"]
    panel["gross_value_proxy"] = panel["price"] * panel["actual_sales"]
    return panel.sort_values(["course_code", "year"]).reset_index(drop=True)


@dataclass(frozen=True)
class ForecastCoefficients:
    yield_trend: float
    share_momentum: float
    satisfaction: float

    @property
    def complexity(self) -> int:
        return sum(abs(v) > 1e-12 for v in (self.yield_trend, self.share_momentum, self.satisfaction))


def forecast_productivity(
    panel: pd.DataFrame,
    survey_stats: pd.DataFrame,
    target_year: int,
    coefficients: ForecastCoefficients,
) -> pd.Series:
    predictions: dict[int, float] = {}
    previous_year = target_year - 1
    for code in range(1, 73):
        history = panel[(panel["course_code"] == code) & (panel["year"] < target_year)].sort_values("year")
        if history.empty or int(history.iloc[-1]["year"]) != previous_year:
            raise RuntimeError(f"Missing productivity history for course {code}, target {target_year}")
        logs = np.log(history["productivity"].to_numpy(dtype=float))
        growths = np.diff(logs)
        robust_growth = float(np.median(growths[-3:])) if len(growths) else 0.0

        share_rows = survey_stats[
            (survey_stats["course_code"] == code)
            & (survey_stats["year"].isin([previous_year - 1, previous_year]))
        ].sort_values("year")
        if len(share_rows) == 2:
            shares = np.clip(share_rows["market_share_smoothed"].to_numpy(dtype=float), 1e-6, 1.0)
            share_momentum = float(np.log(shares[1] / shares[0]))
        else:
            share_momentum = 0.0

        sat_row = survey_stats[
            (survey_stats["course_code"] == code) & (survey_stats["year"] == previous_year)
        ]
        if len(sat_row):
            satisfaction_delta = float(
                sat_row.iloc[0]["a_satisfaction_smoothed"] - sat_row.iloc[0]["global_a_satisfaction"]
            )
        else:
            satisfaction_delta = 0.0

        log_change = (
            coefficients.yield_trend * robust_growth
            + coefficients.share_momentum * share_momentum
            + coefficients.satisfaction * satisfaction_delta
        )
        # One-year productivity changes beyond this range are treated as
        # extrapolation failure rather than evidence of unbounded growth.
        log_change = float(np.clip(log_change, math.log(0.60), math.log(1.60)))
        predictions[code] = float(np.exp(logs[-1] + log_change))
    return pd.Series(predictions, name=f"productivity_forecast_{target_year}")


def forecast_metrics(
    panel: pd.DataFrame,
    survey_stats: pd.DataFrame,
    target_years: Sequence[int],
    coefficients: ForecastCoefficients,
) -> dict[str, float]:
    actual_parts: list[np.ndarray] = []
    predicted_parts: list[np.ndarray] = []
    for target in target_years:
        q_hat = forecast_productivity(panel, survey_stats, target, coefficients)
        actual = panel[panel["year"] == target].sort_values("course_code")
        predicted = q_hat.loc[actual["course_code"]].to_numpy() * actual["isbn"].to_numpy()
        actual_parts.append(actual["actual_sales"].to_numpy(dtype=float))
        predicted_parts.append(predicted)
    actual_all = np.concatenate(actual_parts)
    predicted_all = np.concatenate(predicted_parts)
    wape = float(np.abs(predicted_all - actual_all).sum() / actual_all.sum())
    rmsle = float(np.sqrt(np.mean((np.log1p(predicted_all) - np.log1p(actual_all)) ** 2)))
    rho = float(spearmanr(actual_all, predicted_all).statistic)
    return {"wape": wape, "rmsle": rmsle, "spearman": rho}


def tune_forecasts(panel: pd.DataFrame, survey_stats: pd.DataFrame) -> dict[str, Any]:
    tuning_years = (2003, 2004)
    holdout_years = (2005,)
    persistence = ForecastCoefficients(0.0, 0.0, 0.0)

    trend_grid = [ForecastCoefficients(v, 0.0, 0.0) for v in (0.0, 0.25, 0.5, 0.75, 1.0)]
    augmented_grid = [
        ForecastCoefficients(y, s, a)
        for y in (0.0, 0.25, 0.5, 0.75, 1.0)
        for s in (0.0, 0.25, 0.5)
        for a in (0.0, 0.05, 0.10)
    ]

    def select(grid: Sequence[ForecastCoefficients]) -> tuple[ForecastCoefficients, list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        for coeff in grid:
            metrics = forecast_metrics(panel, survey_stats, tuning_years, coeff)
            rows.append(
                {
                    "yield_trend": coeff.yield_trend,
                    "share_momentum": coeff.share_momentum,
                    "satisfaction": coeff.satisfaction,
                    "complexity": coeff.complexity,
                    **metrics,
                }
            )
        rows.sort(key=lambda row: (row["wape"], row["complexity"], row["rmsle"], row["yield_trend"], row["share_momentum"], row["satisfaction"]))
        best_row = rows[0]
        return (
            ForecastCoefficients(
                best_row["yield_trend"], best_row["share_momentum"], best_row["satisfaction"]
            ),
            rows,
        )

    trend_coeff, trend_rows = select(trend_grid)
    augmented_coeff, augmented_rows = select(augmented_grid)
    validation = {
        "persistence": {
            "coefficients": persistence.__dict__,
            "tuning": forecast_metrics(panel, survey_stats, tuning_years, persistence),
            "holdout_2005": forecast_metrics(panel, survey_stats, holdout_years, persistence),
        },
        "trend": {
            "coefficients": trend_coeff.__dict__,
            "tuning": forecast_metrics(panel, survey_stats, tuning_years, trend_coeff),
            "holdout_2005": forecast_metrics(panel, survey_stats, holdout_years, trend_coeff),
        },
        "survey_augmented": {
            "coefficients": augmented_coeff.__dict__,
            "tuning": forecast_metrics(panel, survey_stats, tuning_years, augmented_coeff),
            "holdout_2005": forecast_metrics(panel, survey_stats, holdout_years, augmented_coeff),
        },
    }
    # The 2005 block was untouched during tuning.  Prefer the simpler trend
    # rule unless the augmented rule has a strictly smaller holdout WAPE.
    if validation["survey_augmented"]["holdout_2005"]["wape"] < validation["trend"]["holdout_2005"]["wape"] - 1e-12:
        selected_name = "survey_augmented"
        selected_coeff = augmented_coeff
    else:
        selected_name = "trend"
        selected_coeff = trend_coeff
    return {
        "tuning_years": list(tuning_years),
        "holdout_year": 2005,
        "variants": validation,
        "selected": selected_name,
        "selected_coefficients": selected_coeff,
        "trend_grid": trend_rows,
        "augmented_grid": augmented_rows,
    }


def estimate_elasticity(panel: pd.DataFrame, draws: int = 1000) -> dict[str, Any]:
    actual = panel.pivot(index="course_code", columns="year", values="actual_sales").loc[range(1, 73), YEARS]
    isbn = panel.pivot(index="course_code", columns="year", values="isbn").loc[range(1, 73), YEARS]
    y = np.log(actual.to_numpy(dtype=float))
    x = np.log(isbn.to_numpy(dtype=float))

    def two_way_slope(x_matrix: np.ndarray, y_matrix: np.ndarray) -> float:
        x_centered = x_matrix - x_matrix.mean(axis=1, keepdims=True) - x_matrix.mean(axis=0, keepdims=True) + x_matrix.mean()
        y_centered = y_matrix - y_matrix.mean(axis=1, keepdims=True) - y_matrix.mean(axis=0, keepdims=True) + y_matrix.mean()
        denominator = float(np.sum(x_centered**2))
        return float(np.sum(x_centered * y_centered) / denominator) if denominator > 0 else np.nan

    raw = two_way_slope(x, y)
    rng = np.random.default_rng(SEED)
    boot = np.empty(draws, dtype=float)
    for draw in range(draws):
        sample = rng.integers(0, x.shape[0], size=x.shape[0])
        boot[draw] = two_way_slope(x[sample], y[sample])
    finite = boot[np.isfinite(boot)]
    used = float(np.clip(raw, 0.50, 0.95))
    return {
        "method": "balanced-panel two-way fixed-effect descriptive elasticity",
        "raw_estimate": raw,
        "bootstrap_draws": draws,
        "bootstrap_p025": float(np.quantile(finite, 0.025)),
        "bootstrap_p50": float(np.quantile(finite, 0.5)),
        "bootstrap_p975": float(np.quantile(finite, 0.975)),
        "used_elasticity": used,
        "used_bounds": [0.50, 0.95],
        "used_reason": <LONG_QUOTE_REDACTED>,
        "interpretation": "descriptive association; allocation is endogenous, so this is not a causal estimate",
    }


def rolling_forecast_errors(
    panel: pd.DataFrame,
    survey_stats: pd.DataFrame,
    coefficients: ForecastCoefficients,
    isbn: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series]:
    rows: list[dict[str, Any]] = []
    category_map = isbn.set_index("course_code")["category"].to_dict()
    for target in (2003, 2004, 2005):
        pred = forecast_productivity(panel, survey_stats, target, coefficients)
        actual = panel[panel["year"] == target].set_index("course_code")["productivity"]
        for code in range(1, 73):
            error = float(np.log(actual.loc[code]) - np.log(pred.loc[code]))
            rows.append(
                {
                    "target_year": target,
                    "course_code": code,
                    "category": category_map[code],
                    "log_error": error,
                }
            )
    errors = pd.DataFrame.from_records(rows)
    global_mse = float(np.mean(errors["log_error"] ** 2))
    branch_mse = errors.assign(sq=errors["log_error"] ** 2).groupby("category")["sq"].mean()
    sigma: dict[int, float] = {}
    for code in range(1, 73):
        course = errors[errors["course_code"] == code]
        category = category_map[code]
        prior_mse = 0.5 * float(branch_mse.loc[category]) + 0.5 * global_mse
        shrunk_mse = (float(np.sum(course["log_error"] ** 2)) + 3.0 * prior_mse) / (len(course) + 3.0)
        sigma[code] = math.sqrt(max(shrunk_mse, 0.0))
    return errors, pd.Series(sigma, name="forecast_log_sigma")


def check_cross_file_consistency(sales: pd.DataFrame, isbn: pd.DataFrame) -> dict[str, Any]:
    sales_courses = sales[["course_code", "course", "category"]].drop_duplicates("course_code").sort_values("course_code")
    isbn_courses = isbn[["course_code", "course", "category"]].sort_values("course_code")
    merged = sales_courses.merge(isbn_courses, on="course_code", suffixes=("_sales", "_isbn"))
    name_mismatch = merged[merged["course_sales"] != merged["course_isbn"]]
    category_mismatch = merged[merged["category_sales"] != merged["category_isbn"]]
    return {
        "course_code_sets_equal": set(sales_courses["course_code"]) == set(isbn_courses["course_code"]),
        "course_name_mismatch_count": len(name_mismatch),
        "category_mismatch_count_after_normalization": len(category_mismatch),
        "name_mismatches": name_mismatch[["course_code", "course_sales", "course_isbn"]].to_dict("records"),
        "category_mismatches": category_mismatch[["course_code", "category_sales", "category_isbn"]].to_dict("records"),
    }


def make_capacity_maps(hr: pd.DataFrame, isbn: pd.DataFrame, factor: float = 1.0) -> dict[str, int]:
    request_by_branch = isbn.groupby("category")["request_2006"].sum().to_dict()
    capacities: dict[str, int] = {}
    for row in hr.itertuples(index=False):
        category = str(row.category)
        scaled = int(math.floor(float(row.bottleneck_capacity) * factor + 1e-12))
        capacities[category] = min(int(request_by_branch[category]), scaled)
    return capacities


def validate_feasibility(
    request: np.ndarray,
    lower: np.ndarray,
    categories: Sequence[str],
    branch_minimum: Mapping[str, int],
    branch_capacity: Mapping[str, int],
    budget: int,
) -> None:
    if np.any(lower < 0) or np.any(lower > request):
        raise ValueError("Course lower bounds are outside [0, request]")
    branch_lower = defaultdict(int)
    branch_upper = defaultdict(int)
    for i, category in enumerate(categories):
        branch_lower[category] += int(lower[i])
        branch_upper[category] += int(request[i])
    for category in branch_minimum:
        if branch_minimum[category] > branch_capacity[category]:
            raise ValueError(f"Branch minimum exceeds capacity for {category}")
        if branch_lower[category] > branch_capacity[category]:
            raise ValueError(f"Course lower bounds exceed capacity for {category}")
        if branch_upper[category] < branch_minimum[category]:
            raise ValueError(f"Requests cannot meet branch minimum for {category}")
    minimum_total = max(int(lower.sum()), sum(int(v) for v in branch_minimum.values()))
    maximum_total = sum(min(int(branch_upper[c]), int(branch_capacity[c])) for c in branch_capacity)
    if not (minimum_total <= budget <= maximum_total):
        raise ValueError(f"Budget {budget} outside feasible interval [{minimum_total}, {maximum_total}]")


def allocate_greedy(
    request: np.ndarray,
    lower: np.ndarray,
    categories: Sequence[str],
    branch_minimum: Mapping[str, int],
    branch_capacity: Mapping[str, int],
    budget: int,
    marginal_value,
) -> np.ndarray:
    request = np.asarray(request, dtype=int)
    allocation = np.asarray(lower, dtype=int).copy()
    validate_feasibility(request, allocation, categories, branch_minimum, branch_capacity, budget)
    branch_total = defaultdict(int)
    for value, category in zip(allocation, categories):
        branch_total[category] += int(value)

    def eligible_indices(restrict_to_minimum: bool) -> list[int]:
        result = []
        for i, category in enumerate(categories):
            if allocation[i] >= request[i] or branch_total[category] >= branch_capacity[category]:
                continue
            if restrict_to_minimum and branch_total[category] >= branch_minimum[category]:
                continue
            result.append(i)
        return result

    while any(branch_total[c] < branch_minimum[c] for c in branch_minimum):
        eligible = eligible_indices(True)
        if not eligible:
            raise RuntimeError("Unable to meet branch minimum during greedy allocation")
        chosen = max(eligible, key=lambda i: (float(marginal_value(i, int(allocation[i]))), -i))
        allocation[chosen] += 1
        branch_total[categories[chosen]] += 1

    while int(allocation.sum()) < budget:
        eligible = eligible_indices(False)
        if not eligible:
            raise RuntimeError("No eligible course before budget was exhausted")
        chosen = max(eligible, key=lambda i: (float(marginal_value(i, int(allocation[i]))), -i))
        allocation[chosen] += 1
        branch_total[categories[chosen]] += 1
    return allocation


def proportional_allocation(
    request: np.ndarray,
    lower: np.ndarray,
    categories: Sequence[str],
    branch_minimum: Mapping[str, int],
    branch_capacity: Mapping[str, int],
    budget: int,
) -> np.ndarray:
    def marginal(i: int, current: int) -> float:
        # Equalize the filled fraction; the small code term makes ties stable.
        return -((current + 1) / request[i])

    return allocate_greedy(request, lower, categories, branch_minimum, branch_capacity, budget, marginal)


def linear_allocation(
    request: np.ndarray,
    lower: np.ndarray,
    categories: Sequence[str],
    branch_minimum: Mapping[str, int],
    branch_capacity: Mapping[str, int],
    budget: int,
    score: np.ndarray,
) -> np.ndarray:
    return allocate_greedy(
        request,
        lower,
        categories,
        branch_minimum,
        branch_capacity,
        budget,
        lambda i, current: score[i],
    )


def concave_allocation(
    request: np.ndarray,
    lower: np.ndarray,
    categories: Sequence[str],
    branch_minimum: Mapping[str, int],
    branch_capacity: Mapping[str, int],
    budget: int,
    value: np.ndarray,
    reference_isbn: np.ndarray,
    elasticity: float,
) -> np.ndarray:
    def utility(i: int, x: int) -> float:
        if x <= 0:
            return 0.0
        return float(value[i] * reference_isbn[i] * (x / reference_isbn[i]) ** elasticity)

    return allocate_greedy(
        request,
        lower,
        categories,
        branch_minimum,
        branch_capacity,
        budget,
        lambda i, current: utility(i, current + 1) - utility(i, current),
    )


def allocation_value(
    allocation: np.ndarray,
    productivity: np.ndarray,
    price: np.ndarray,
    reference_isbn: np.ndarray,
    elasticity: float,
) -> tuple[np.ndarray, np.ndarray]:
    predicted_sales = productivity * reference_isbn * (allocation / reference_isbn) ** elasticity
    gross_value = price * predicted_sales
    return predicted_sales, gross_value


def constraint_checks(
    allocation: np.ndarray,
    request: np.ndarray,
    lower: np.ndarray,
    categories: Sequence[str],
    branch_minimum: Mapping[str, int],
    branch_capacity: Mapping[str, int],
    budget: int,
) -> dict[str, Any]:
    branch_totals = defaultdict(int)
    for x, category in zip(allocation, categories):
        branch_totals[category] += int(x)
    checks = {
        "integer": bool(np.issubdtype(allocation.dtype, np.integer)),
        "total_exact": int(allocation.sum()) == budget,
        "course_lower_bounds": bool(np.all(allocation >= lower)),
        "course_request_upper_bounds": bool(np.all(allocation <= request)),
        "branch_minimums": all(branch_totals[c] >= branch_minimum[c] for c in branch_minimum),
        "branch_capacities": all(branch_totals[c] <= branch_capacity[c] for c in branch_capacity),
    }
    return {"status": "pass" if all(checks.values()) else "fail", "checks": checks, "branch_totals": dict(branch_totals)}


def hhi(allocation: np.ndarray) -> float:
    shares = allocation / allocation.sum()
    return float(np.sum(shares**2))


def latex_escape(text: Any) -> str:
    value = str(text)
    replacements = {
        "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
        "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.dpi": 120,
            "savefig.dpi": 220,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def make_figures(
    figure_root: Path,
    branch: pd.DataFrame,
    candidates: pd.DataFrame,
    holdout: pd.DataFrame,
    survey_stats: pd.DataFrame,
    course_lookup: pd.DataFrame,
    uncertainty_branch: pd.DataFrame,
) -> list[str]:
    configure_matplotlib()
    figure_root.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    labels = branch["category"].astype(str).tolist()
    x = np.arange(len(labels))
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 9.0), gridspec_kw={"height_ratios": [2.0, 1.0]})
    width = 0.24
    axes[0].bar(x - width, branch["request_2006"], width, label="2006申请", color="#b8c2cc")
    axes[0].bar(x, branch["allocation_baseline"], width, label="比例基线", color="#5b8ff9")
    axes[0].bar(x + width, branch["allocation_main"], width, label="主模型", color="#e8684a")
    axes[0].scatter(x, branch["bottleneck_capacity"], marker="_", s=520, linewidths=2.0, color="#222222", label="人力瓶颈")
    axes[0].scatter(x, branch["minimum_guarantee"], marker="D", s=25, color="#5d7092", label="最低保障")
    axes[0].set_ylabel("书号数")
    axes[0].set_xticks(x, labels, rotation=24, ha="right")
    axes[0].set_title("各分社申请、约束与分配结果")
    axes[0].legend(ncol=5, fontsize=9, loc="upper right")
    axes[0].grid(axis="y", alpha=0.2)

    cand_x = np.arange(len(candidates))
    axes[1].bar(cand_x, candidates["risk_adjusted_value"] / 1e6, color=["#5b8ff9", "#61d9a5", "#e8684a"])
    axes[1].set_xticks(cand_x, candidates["short_name"])
    axes[1].set_ylabel("风险调整销售额代理（百万元）")
    axes[1].set_title("同一评价口径下的候选方案比较")
    for i, value in enumerate(candidates["risk_adjusted_value"] / 1e6):
        axes[1].text(i, value, f"{value:.2f}", ha="center", va="bottom", fontsize=9)
    axes[1].grid(axis="y", alpha=0.2)
    fig.tight_layout()
    path = figure_root / "<SOURCE_FILE_REDACTED>"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    generated.append(path.name)

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2), sharex=True, sharey=True)
    variants = [
        ("predicted_persistence", "持续性预测"),
        ("predicted_trend", "趋势预测"),
        ("predicted_selected", "入选预测"),
    ]
    low = min(float(holdout["actual_sales"].min()), *(float(holdout[col].min()) for col, _ in variants))
    high = max(float(holdout["actual_sales"].max()), *(float(holdout[col].max()) for col, _ in variants))
    for axis, (column, title) in zip(axes, variants):
        axis.scatter(holdout["actual_sales"], holdout[column], s=18, alpha=0.7, color="#5b8ff9")
        axis.plot([low, high], [low, high], "--", color="#555555", linewidth=1)
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_title(title)
        axis.grid(alpha=0.18)
    axes[0].set_ylabel("预测实际销量（册）")
    axes[1].set_xlabel("2005年实际销量（册，对数坐标）")
    fig.suptitle("留出年份的课程级预测检验", y=1.02)
    fig.tight_layout()
    path = figure_root / "<SOURCE_FILE_REDACTED>"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    generated.append(path.name)

    course_category = course_lookup[["course_code", "category"]]
    survey_plot = survey_stats.merge(course_category, on="course_code")
    heat = np.zeros((len(CATEGORY_ORDER), len(YEARS)))
    for i, category in enumerate(CATEGORY_ORDER):
        for j, year in enumerate(YEARS):
            part = survey_plot[(survey_plot["category"] == category) & (survey_plot["year"] == year)]
            denominator = float(part["publisher_denominator"].sum())
            heat[i, j] = float(part["a_publisher_rows"].sum() / denominator) if denominator else np.nan
    fig, axis = plt.subplots(figsize=(8.0, 5.5))
    image = axis.imshow(heat * 100, cmap="YlOrRd", aspect="auto", vmin=0)
    axis.set_xticks(np.arange(len(YEARS)), YEARS)
    axis.set_yticks(np.arange(len(CATEGORY_ORDER)), CATEGORY_ORDER)
    axis.set_xlabel("调查年份")
    axis.set_title("A出版社（P115）课程样本市场份额（%）")
    for i in range(heat.shape[0]):
        for j in range(heat.shape[1]):
            axis.text(j, i, f"{100 * heat[i, j]:.1f}", ha="center", va="center", fontsize=8)
    fig.colorbar(image, ax=axis, label="市场份额（%）")
    fig.tight_layout()
    path = figure_root / "<SOURCE_FILE_REDACTED>"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    generated.append(path.name)

    uncertainty = uncertainty_branch.set_index("category").loc[CATEGORY_ORDER].reset_index()
    main_values = uncertainty["main_allocation"].to_numpy(dtype=float)
    lower_error = main_values - uncertainty["p05"].to_numpy(dtype=float)
    upper_error = uncertainty["p95"].to_numpy(dtype=float) - main_values
    fig, axis = plt.subplots(figsize=(10.5, 4.8))
    axis.errorbar(
        np.arange(len(CATEGORY_ORDER)),
        main_values,
        yerr=np.vstack([lower_error, upper_error]),
        fmt="o",
        capsize=4,
        color="#e8684a",
        ecolor="#5d7092",
        label="主方案及经验残差扰动5%--95%区间",
    )
    axis.set_xticks(np.arange(len(CATEGORY_ORDER)), CATEGORY_ORDER, rotation=24, ha="right")
    axis.set_ylabel("书号数")
    axis.set_title("分社分配的扰动稳健性")
    axis.grid(axis="y", alpha=0.2)
    axis.legend()
    fig.tight_layout()
    path = figure_root / "<SOURCE_FILE_REDACTED>"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    generated.append(path.name)
    return generated


def write_generated_tex(
    generated_root: Path,
    summary: Mapping[str, Any],
    branch: pd.DataFrame,
    course: pd.DataFrame,
    candidates: pd.DataFrame,
    forecast_validation: Mapping[str, Any],
) -> list[str]:
    generated_root.mkdir(parents=True, exist_ok=True)
    selected_variant = str(summary["selected_forecast"])
    holdout = forecast_validation["variants"][selected_variant]["holdout_2005"]
    macros = [
        f"\\newcommand{{\\TotalISBN}}{{{summary['total_isbn']}}}",
        f"\\newcommand{{\\TotalRequest}}{{{summary['request_2006_total']}}}",
        f"\\newcommand{{\\PublishedTotalRequest}}{{{summary['published_total_row_request_2006']}}}",
        f"\\newcommand{{\\MinimumISBN}}{{{summary['minimum_guarantee_total']}}}",
        f"\\newcommand{{\\CapacityMaximum}}{{{summary['capacity_feasible_maximum']}}}",
        f"\\newcommand{{\\ElasticityUsed}}{{{summary['elasticity_used']:.3f}}}",
        f"\\newcommand{{\\HoldoutWAPE}}{{{100 * holdout['wape']:.2f}\\%}}",
        f"\\newcommand{{\\HoldoutSpearman}}{{{holdout['spearman']:.3f}}}",
        f"\\newcommand{{\\MainCentralValue}}{{{summary['main_central_value'] / 1e6:.3f}}}",
        f"\\newcommand{{\\MainRiskValue}}{{{summary['main_risk_adjusted_value'] / 1e6:.3f}}}",
        f"\\newcommand{{\\BaselineGain}}{{{summary['risk_value_gain_vs_baseline_pct']:.2f}\\%}}",
        f"\\newcommand{{\\RiskZ}}{{{summary['risk_z']:.2f}}}",
    ]
    (generated_root / "result_macros.tex").write_text("\n".join(macros) + "\n", encoding="utf-8")

    branch_lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{2006年各分社书号分配（个）}",
        r"\label{tab:branch-allocation}",
        r"\begin{tabular}{lrrrrrr}",
        r"\toprule",
        r"分社 & 申请 & 最低保障 & 人力上限 & 比例基线 & 趋势收益 & 主模型 \\",
        r"\midrule",
    ]
    for row in branch.itertuples(index=False):
        branch_lines.append(
            f"{latex_escape(row.category)} & {row.request_2006} & {row.minimum_guarantee} & "
            f"{row.bottleneck_capacity} & {row.allocation_baseline} & {row.allocation_trend} & {row.allocation_main} \\\\"
        )
    branch_lines.extend(
        [
            r"\midrule",
            f"合计 & {int(branch['request_2006'].sum())} & {int(branch['minimum_guarantee'].sum())} & "
            f"{int(branch['bottleneck_capacity'].sum())} & {int(branch['allocation_baseline'].sum())} & "
            f"{int(branch['allocation_trend'].sum())} & {int(branch['allocation_main'].sum())} \\\\ ",
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
        ]
    )
    (generated_root / "branch_allocation.tex").write_text("\n".join(branch_lines) + "\n", encoding="utf-8")

    candidate_lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{三个候选方案的同口径比较}",
        r"\label{tab:candidate-comparison}",
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"方案 & 留出WAPE & 中心值/百万元 & 风险值/百万元 & HHI \\",
        r"\midrule",
    ]
    for row in candidates.itertuples(index=False):
        candidate_lines.append(
            f"{latex_escape(row.short_name)} & {100 * row.holdout_wape:.2f}\\% & "
            f"{row.central_value / 1e6:.3f} & {row.risk_adjusted_value / 1e6:.3f} & {row.hhi:.4f} \\\\"
        )
    candidate_lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}"])
    (generated_root / "candidate_comparison.tex").write_text("\n".join(candidate_lines) + "\n", encoding="utf-8")

    validation_lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{预测规则的2005年留出检验}",
        r"\label{tab:forecast-validation}",
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"规则 & WAPE & RMSLE & Spearman秩相关 \\",
        r"\midrule",
    ]
    names = {"persistence": "上一年持续", "trend": "稳健趋势", "survey_augmented": "问卷增强"}
    for key in ("persistence", "trend", "survey_augmented"):
        metrics = forecast_validation["variants"][key]["holdout_2005"]
        validation_lines.append(
            f"{names[key]} & {100 * metrics['wape']:.2f}\\% & {metrics['rmsle']:.3f} & {metrics['spearman']:.3f} \\\\"
        )
    validation_lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}"])
    (generated_root / "forecast_validation.tex").write_text("\n".join(validation_lines) + "\n", encoding="utf-8")

    course_lines = [
        r"\begin{longtable}{r >{\raggedright\arraybackslash}p{2.0cm} >{\raggedright\arraybackslash}p{5.2cm} rrrr}",
        r"\caption{课程级书号建议}\label{tab:course-allocation}\\",
        r"\toprule",
        r"代码 & 分社 & 课程 & 申请 & 2005 & 主方案 & 较2005变化 \\",
        r"\midrule",
        r"\endfirsthead",
        r"\toprule",
        r"代码 & 分社 & 课程 & 申请 & 2005 & 主方案 & 较2005变化 \\",
        r"\midrule",
        r"\endhead",
    ]
    for row in course.itertuples(index=False):
        course_lines.append(
            f"{row.course_code} & {latex_escape(row.category)} & {latex_escape(row.course)} & "
            f"{row.request_2006} & {row.isbn_2005} & {row.allocation_main} & {row.change_from_2005} \\\\"
        )
    course_lines.extend([r"\bottomrule", r"\end{longtable}"])
    (generated_root / "course_allocation.tex").write_text("\n".join(course_lines) + "\n", encoding="utf-8")
    return ["result_macros.tex", "branch_allocation.tex", "candidate_comparison.tex", "forecast_validation.tex", "course_allocation.tex"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    input_root = workspace / "input"
    converted = input_root / "converted"
    result_root = workspace / "results"
    figure_root = workspace / "figures"
    paper_generated = workspace / "paper" / "generated"
    result_root.mkdir(parents=True, exist_ok=True)
    figure_root.mkdir(parents=True, exist_ok=True)
    paper_generated.mkdir(parents=True, exist_ok=True)

    sales_path = locate_one(converted, "excel/附件3_*/*.tsv")
    isbn_path = locate_one(converted, "excel/附件4_*/*.tsv")
    hr_path = locate_one(converted, "excel/附件5_*/*Sheet1.tsv")
    survey_directories = sorted((converted / "excel").glob("附件2_*"))
    if len(survey_directories) != 1:
        raise RuntimeError("Could not uniquely locate survey conversion directory")
    conversion_manifest = json.loads((converted / "manifest.json").read_text(encoding="utf-8"))

    sales, sales_audit = load_sales(sales_path)
    isbn, isbn_audit = load_isbn(isbn_path)
    hr, hr_audit = load_hr(hr_path)
    survey, survey_stats, survey_audit = load_surveys(survey_directories[0])
    cross_file = check_cross_file_consistency(sales, isbn)
    panel = build_panel(sales, isbn)

    if not isbn_audit["historical_total_constant"]:
        raise RuntimeError("Historical total ISBN counts are not constant")
    total_isbn = next(iter(isbn_audit["historical_totals"].values()))
    if total_isbn != 500:
        raise RuntimeError(f"Expected data-inferred total 500, got {total_isbn}")

    forecast_validation = tune_forecasts(panel, survey_stats)
    selected_coeff: ForecastCoefficients = forecast_validation["selected_coefficients"]
    trend_coeff = ForecastCoefficients(**forecast_validation["variants"]["trend"]["coefficients"])
    persistence_coeff = ForecastCoefficients(0.0, 0.0, 0.0)
    q_persistence = forecast_productivity(panel, survey_stats, 2006, persistence_coeff)
    q_trend = forecast_productivity(panel, survey_stats, 2006, trend_coeff)
    q_selected = forecast_productivity(panel, survey_stats, 2006, selected_coeff)
    rolling_errors, sigma = rolling_forecast_errors(panel, survey_stats, selected_coeff, isbn)
    q_risk = q_selected * np.exp(-RISK_Z * sigma)
    elasticity = estimate_elasticity(panel)
    eta = float(elasticity["used_elasticity"])

    course = isbn.copy().sort_values("course_code").reset_index(drop=True)
    survey_2005 = survey_stats[survey_stats["year"] == 2005].set_index("course_code")
    course["productivity_2005"] = panel[panel["year"] == 2005].set_index("course_code").loc[course["course_code"], "productivity"].to_numpy()
    course["forecast_persistence"] = q_persistence.loc[course["course_code"]].to_numpy()
    course["forecast_trend"] = q_trend.loc[course["course_code"]].to_numpy()
    course["forecast_selected"] = q_selected.loc[course["course_code"]].to_numpy()
    course["forecast_log_sigma"] = sigma.loc[course["course_code"]].to_numpy()
    course["forecast_risk_adjusted"] = q_risk.loc[course["course_code"]].to_numpy()
    course["market_share_2005"] = survey_2005.loc[course["course_code"], "market_share_smoothed"].to_numpy()
    course["satisfaction_2005"] = survey_2005.loc[course["course_code"], "a_satisfaction_smoothed"].to_numpy()
    course["survey_n_2005"] = survey_2005.loc[course["course_code"], "publisher_denominator"].to_numpy(dtype=int)

    request = course["request_2006"].to_numpy(dtype=int)
    if not bool(np.all(request % 2 == 0)):
        raise RuntimeError("Course-level half guarantees require an explicit rounding policy")
    # The attachment states a half-request guarantee for each branch, not for
    # every course.  Course lower bounds are therefore zero in the main model;
    # the stronger course-by-course half policy is retained as a structural
    # sensitivity scenario.
    lower = np.zeros_like(request)
    categories = course["category"].astype(str).tolist()
    branch_minimum = {
        category: int(request[np.array(categories) == category].sum() // 2) for category in CATEGORY_ORDER
    }
    branch_capacity = make_capacity_maps(hr, isbn, 1.0)
    capacity_feasible_max = sum(branch_capacity.values())
    validate_feasibility(request, lower, categories, branch_minimum, branch_capacity, total_isbn)

    price = course["price"].to_numpy(dtype=float)
    reference_isbn = course["isbn_2005"].to_numpy(dtype=float)
    baseline = proportional_allocation(
        request, lower, categories, branch_minimum, branch_capacity, total_isbn
    )
    trend_allocation = linear_allocation(
        request,
        lower,
        categories,
        branch_minimum,
        branch_capacity,
        total_isbn,
        price * course["forecast_trend"].to_numpy(dtype=float),
    )
    main_allocation = concave_allocation(
        request,
        lower,
        categories,
        branch_minimum,
        branch_capacity,
        total_isbn,
        price * course["forecast_risk_adjusted"].to_numpy(dtype=float),
        reference_isbn,
        eta,
    )

    allocations = {
        "baseline": baseline,
        "trend": trend_allocation,
        "main": main_allocation,
    }
    for key, allocation in allocations.items():
        check = constraint_checks(
            allocation, request, lower, categories, branch_minimum, branch_capacity, total_isbn
        )
        if check["status"] != "pass":
            raise RuntimeError(f"Constraint check failed for {key}: {check}")

    q_central_array = course["forecast_selected"].to_numpy(dtype=float)
    q_risk_array = course["forecast_risk_adjusted"].to_numpy(dtype=float)
    candidate_rows: list[dict[str, Any]] = []
    candidate_specs = [
        ("baseline", "比例基线", "persistence"),
        ("trend", "趋势收益", "trend"),
        ("main", "风险凹收益", forecast_validation["selected"]),
    ]
    course_values: dict[str, dict[str, np.ndarray]] = {}
    for key, short_name, forecast_key in candidate_specs:
        allocation = allocations[key]
        predicted_sales, central_value = allocation_value(
            allocation, q_central_array, price, reference_isbn, eta
        )
        _, risk_value = allocation_value(allocation, q_risk_array, price, reference_isbn, eta)
        course_values[key] = {
            "predicted_sales": predicted_sales,
            "central_value": central_value,
            "risk_value": risk_value,
        }
        candidate_rows.append(
            {
                "candidate": key,
                "short_name": short_name,
                "forecast_rule": forecast_key,
                "holdout_wape": forecast_validation["variants"][forecast_key]["holdout_2005"]["wape"],
                "holdout_spearman": forecast_validation["variants"][forecast_key]["holdout_2005"]["spearman"],
                "central_value": float(central_value.sum()),
                "risk_adjusted_value": float(risk_value.sum()),
                "hhi": hhi(allocation),
                "allocation_changes_from_2005": int(np.abs(allocation - reference_isbn).sum() / 2),
            }
        )
    candidates = pd.DataFrame.from_records(candidate_rows)
    baseline_risk = float(candidates.loc[candidates["candidate"] == "baseline", "risk_adjusted_value"].iloc[0])
    main_risk = float(candidates.loc[candidates["candidate"] == "main", "risk_adjusted_value"].iloc[0])

    course["course_lower_bound"] = lower
    course["allocation_baseline"] = baseline
    course["allocation_trend"] = trend_allocation
    course["allocation_main"] = main_allocation
    course["allocation_above_course_floor"] = main_allocation - lower
    course["change_from_2005"] = main_allocation - reference_isbn.astype(int)
    course["predicted_sales_main"] = course_values["main"]["predicted_sales"]
    course["central_value_main"] = course_values["main"]["central_value"]
    course["risk_value_main"] = course_values["main"]["risk_value"]

    hr_index = hr.assign(category=hr["category"].astype(str)).set_index("category")
    branch_rows: list[dict[str, Any]] = []
    for category in CATEGORY_ORDER:
        mask = np.array(categories) == category
        hr_row = hr_index.loc[category]
        row: dict[str, Any] = {
            "category": category,
            "request_2006": int(request[mask].sum()),
            "minimum_guarantee": int(branch_minimum[category]),
            "planner_capacity": int(hr_row["planner_capacity"]),
            "editor_capacity": int(hr_row["editor_capacity"]),
            "proof_capacity": int(hr_row["proof_capacity"]),
            "bottleneck_capacity": int(hr_row["bottleneck_capacity"]),
            "allocation_baseline": int(baseline[mask].sum()),
            "allocation_trend": int(trend_allocation[mask].sum()),
            "allocation_main": int(main_allocation[mask].sum()),
            "central_value_baseline": float(course_values["baseline"]["central_value"][mask].sum()),
            "central_value_trend": float(course_values["trend"]["central_value"][mask].sum()),
            "central_value_main": float(course_values["main"]["central_value"][mask].sum()),
            "risk_value_main": float(course_values["main"]["risk_value"][mask].sum()),
            "capacity_utilization_main": float(main_allocation[mask].sum() / hr_row["bottleneck_capacity"]),
        }
        stat_part = survey_2005.loc[course.loc[mask, "course_code"]]
        denom = float(stat_part["publisher_denominator"].sum())
        row["market_share_2005"] = float(stat_part["a_publisher_rows"].sum() / denom) if denom else np.nan
        branch_rows.append(row)
    branch = pd.DataFrame.from_records(branch_rows)

    # Historical assignments versus the nominal average human-resource limit.
    capacity_breaches: list[dict[str, Any]] = []
    for year in YEARS:
        totals = isbn.groupby("category")[f"isbn_{year}"].sum()
        for category in CATEGORY_ORDER:
            assigned = int(totals.loc[category])
            capacity = int(hr_index.loc[category, "bottleneck_capacity"])
            if assigned > capacity:
                capacity_breaches.append(
                    {
                        "year": year,
                        "category": category,
                        "assigned": assigned,
                        "capacity": capacity,
                        "excess": assigned - capacity,
                    }
                )

    # Parameter and structural sensitivity.
    sensitivity_rows: list[dict[str, Any]] = []
    eta_grid = sorted(set(round(v, 6) for v in (0.50, eta, 0.75, 1.00)))
    for risk_z in (0.0, RISK_Z, 1.0):
        for eta_scenario in eta_grid:
            for cap_factor in (1.0, 1.1, 1.2):
                caps = make_capacity_maps(hr, isbn, cap_factor)
                q_scenario = q_central_array * np.exp(-risk_z * course["forecast_log_sigma"].to_numpy(dtype=float))
                allocation = concave_allocation(
                    request,
                    lower,
                    categories,
                    branch_minimum,
                    caps,
                    total_isbn,
                    price * q_scenario,
                    reference_isbn,
                    eta_scenario,
                )
                _, central_scenario = allocation_value(allocation, q_central_array, price, reference_isbn, eta)
                branch_totals = {c: int(allocation[np.array(categories) == c].sum()) for c in CATEGORY_ORDER}
                sensitivity_rows.append(
                    {
                        "scenario": "parameter_grid",
                        "risk_z": risk_z,
                        "elasticity": eta_scenario,
                        "capacity_factor": cap_factor,
                        "lower_bound_policy": "branch_half",
                        "reallocated_units_vs_main": int(np.abs(allocation - main_allocation).sum() / 2),
                        "central_value_under_main_metric": float(central_scenario.sum()),
                        **{f"branch_{c}": branch_totals[c] for c in CATEGORY_ORDER},
                    }
                )

    # Structural check: impose the stronger course-by-course half guarantee.
    course_half_lower = request // 2
    course_half = concave_allocation(
        request,
        course_half_lower,
        categories,
        branch_minimum,
        branch_capacity,
        total_isbn,
        price * q_risk_array,
        reference_isbn,
        eta,
    )
    course_half_totals = {c: int(course_half[np.array(categories) == c].sum()) for c in CATEGORY_ORDER}
    sensitivity_rows.append(
        {
            "scenario": "course_half_minimum",
            "risk_z": RISK_Z,
            "elasticity": eta,
            "capacity_factor": 1.0,
            "lower_bound_policy": "course_half",
            "reallocated_units_vs_main": int(np.abs(course_half - main_allocation).sum() / 2),
            "central_value_under_main_metric": float(
                allocation_value(course_half, q_central_array, price, reference_isbn, eta)[1].sum()
            ),
            **{f"branch_{c}": course_half_totals[c] for c in CATEGORY_ORDER},
        }
    )

    # The inconsistent published total rows imply different branch minimums.
    # Test those totals without inventing course-level requests; the detailed
    # rows still supply course upper bounds.
    published_minimum = {
        c: int(isbn_audit["published_branch_totals"][c]["2006_request"] // 2)
        for c in CATEGORY_ORDER
    }
    published_constraint_allocation = concave_allocation(
        request,
        lower,
        categories,
        published_minimum,
        branch_capacity,
        total_isbn,
        price * q_risk_array,
        reference_isbn,
        eta,
    )
    published_constraint_totals = {
        c: int(published_constraint_allocation[np.array(categories) == c].sum()) for c in CATEGORY_ORDER
    }
    sensitivity_rows.append(
        {
            "scenario": "published_total_row_minimums",
            "risk_z": RISK_Z,
            "elasticity": eta,
            "capacity_factor": 1.0,
            "lower_bound_policy": "published_branch_half",
            "reallocated_units_vs_main": int(np.abs(published_constraint_allocation - main_allocation).sum() / 2),
            "central_value_under_main_metric": float(
                allocation_value(published_constraint_allocation, q_central_array, price, reference_isbn, eta)[1].sum()
            ),
            **{f"branch_{c}": published_constraint_totals[c] for c in CATEGORY_ORDER},
        }
    )
    relaxed_caps = {c: int(isbn.loc[isbn["category"] == c, "request_2006"].sum()) for c in CATEGORY_ORDER}
    no_hr_cap = concave_allocation(
        request,
        lower,
        categories,
        branch_minimum,
        relaxed_caps,
        total_isbn,
        price * q_risk_array,
        reference_isbn,
        eta,
    )
    no_hr_totals = {c: int(no_hr_cap[np.array(categories) == c].sum()) for c in CATEGORY_ORDER}
    sensitivity_rows.append(
        {
            "scenario": "no_hr_capacity_cap",
            "risk_z": RISK_Z,
            "elasticity": eta,
            "capacity_factor": np.nan,
            "lower_bound_policy": "branch_half",
            "reallocated_units_vs_main": int(np.abs(no_hr_cap - main_allocation).sum() / 2),
            "central_value_under_main_metric": float(
                allocation_value(no_hr_cap, q_central_array, price, reference_isbn, eta)[1].sum()
            ),
            **{f"branch_{c}": no_hr_totals[c] for c in CATEGORY_ORDER},
        }
    )
    sensitivity = pd.DataFrame.from_records(sensitivity_rows)

    # Empirical residual perturbation with a fixed seed.
    residual_pools: dict[str, np.ndarray] = {}
    for category in CATEGORY_ORDER:
        values = rolling_errors.loc[rolling_errors["category"] == category, "log_error"].to_numpy(dtype=float)
        residual_pools[category] = values - values.mean()
    rng = np.random.default_rng(SEED)
    mc_allocations = np.empty((MC_DRAWS, len(course)), dtype=int)
    for draw in range(MC_DRAWS):
        perturbation = np.array([rng.choice(residual_pools[c]) for c in categories])
        q_draw = q_central_array * np.exp(perturbation)
        mc_allocations[draw] = concave_allocation(
            request,
            lower,
            categories,
            branch_minimum,
            branch_capacity,
            total_isbn,
            price * q_draw,
            reference_isbn,
            eta,
        )
    uncertainty_course_rows: list[dict[str, Any]] = []
    for i, row in course.iterrows():
        values = mc_allocations[:, i]
        uncertainty_course_rows.append(
            {
                "course_code": int(row["course_code"]),
                "category": row["category"],
                "course": row["course"],
                "main_allocation": int(main_allocation[i]),
                "mean": float(values.mean()),
                "p05": float(np.quantile(values, 0.05)),
                "p50": float(np.quantile(values, 0.50)),
                "p95": float(np.quantile(values, 0.95)),
                "probability_exact_main": float(np.mean(values == main_allocation[i])),
            }
        )
    uncertainty_course = pd.DataFrame.from_records(uncertainty_course_rows)
    uncertainty_branch_rows: list[dict[str, Any]] = []
    for category in CATEGORY_ORDER:
        mask = np.array(categories) == category
        values = mc_allocations[:, mask].sum(axis=1)
        uncertainty_branch_rows.append(
            {
                "category": category,
                "main_allocation": int(main_allocation[mask].sum()),
                "mean": float(values.mean()),
                "p05": float(np.quantile(values, 0.05)),
                "p50": float(np.quantile(values, 0.50)),
                "p95": float(np.quantile(values, 0.95)),
            }
        )
    uncertainty_branch = pd.DataFrame.from_records(uncertainty_branch_rows)

    minimum_total = int(sum(branch_minimum.values()))
    maximum_total = int(sum(branch_capacity.values()))
    minimum_allocation = concave_allocation(
        request,
        lower,
        categories,
        branch_minimum,
        branch_capacity,
        minimum_total,
        price * q_risk_array,
        reference_isbn,
        eta,
    )
    lower_check = constraint_checks(
        minimum_allocation, request, lower, categories, branch_minimum, branch_capacity, minimum_total
    )
    max_allocation = concave_allocation(
        request,
        lower,
        categories,
        branch_minimum,
        branch_capacity,
        maximum_total,
        price * q_risk_array,
        reference_isbn,
        eta,
    )
    upper_check = constraint_checks(
        max_allocation, request, lower, categories, branch_minimum, branch_capacity, maximum_total
    )
    rejected_low = rejected_high = False
    try:
        validate_feasibility(request, lower, categories, branch_minimum, branch_capacity, minimum_total - 1)
    except ValueError:
        rejected_low = True
    try:
        validate_feasibility(request, lower, categories, branch_minimum, branch_capacity, maximum_total + 1)
    except ValueError:
        rejected_high = True
    boundary_checks = {
        "feasible_interval": [minimum_total, maximum_total],
        "minimum_endpoint_status": lower_check["status"],
        "maximum_endpoint_status": upper_check["status"],
        "below_minimum_rejected": rejected_low,
        "above_maximum_rejected": rejected_high,
        "status": "pass" if lower_check["status"] == upper_check["status"] == "pass" and rejected_low and rejected_high else "fail",
    }

    # Holdout records for the paper figure.
    holdout_actual = panel[panel["year"] == 2005].sort_values("course_code")
    x_holdout = holdout_actual["isbn"].to_numpy(dtype=float)
    holdout = holdout_actual[["course_code", "actual_sales"]].copy()
    holdout["predicted_persistence"] = forecast_productivity(panel, survey_stats, 2005, persistence_coeff).loc[holdout["course_code"]].to_numpy() * x_holdout
    holdout["predicted_trend"] = forecast_productivity(panel, survey_stats, 2005, trend_coeff).loc[holdout["course_code"]].to_numpy() * x_holdout
    holdout["predicted_selected"] = forecast_productivity(panel, survey_stats, 2005, selected_coeff).loc[holdout["course_code"]].to_numpy() * x_holdout

    constraint_results = {
        key: constraint_checks(value, request, lower, categories, branch_minimum, branch_capacity, total_isbn)
        for key, value in allocations.items()
    }
    summary = {
        "case_id": "2006A",
        "decision_year": 2006,
        "random_seed": SEED,
        "total_isbn": total_isbn,
        "total_isbn_source": "sum of each 2001-2005 allocation column; all five equal",
        "request_2006_total": int(request.sum()),
        "request_2006_total_source": "sum of the 72 detailed course rows",
        "published_total_row_request_2006": int(isbn_audit["published_request_total_2006"]),
        "request_total_discrepancy": int(isbn_audit["request_total_difference"]),
        "minimum_guarantee_total": minimum_total,
        "capacity_feasible_maximum": capacity_feasible_max,
        "selected_model": "risk_adjusted_concave_integer_allocation",
        "selected_forecast": forecast_validation["selected"],
        "selected_forecast_coefficients": selected_coeff.__dict__,
        "elasticity_used": eta,
        "elasticity_raw": elasticity["raw_estimate"],
        "risk_z": RISK_Z,
        "main_central_value": float(candidates.loc[candidates["candidate"] == "main", "central_value"].iloc[0]),
        "main_risk_adjusted_value": main_risk,
        "risk_value_gain_vs_baseline_pct": 100.0 * (main_risk / baseline_risk - 1.0),
        "allocation_by_branch": {row["category"]: int(row["allocation_main"]) for _, row in branch.iterrows()},
        "binding_capacity_branches": branch.loc[branch["allocation_main"] == branch["bottleneck_capacity"], "category"].tolist(),
        "constraint_status": "pass" if all(v["status"] == "pass" for v in constraint_results.values()) else "fail",
        "boundary_status": boundary_checks["status"],
        "historical_capacity_breach_count": len(capacity_breaches),
        "zero_allocation_course_count": int((main_allocation == 0).sum()),
        "top_course_allocations": course.nlargest(10, ["allocation_main", "risk_value_main"])[
            ["course_code", "course", "category", "allocation_main", "request_2006"]
        ].to_dict("records"),
    }

    data_audit = {
        "status": "needs_review" if capacity_breaches or not isbn_audit["published_branch_totals_match"] else "pass",
        "sales": sales_audit,
        "isbn": isbn_audit,
        "legacy_formula_audit": conversion_manifest.get("formula_audit", []),
        "human_resources": hr_audit,
        "survey": survey_audit,
        "cross_file_consistency": cross_file,
        "historical_capacity_breaches": capacity_breaches,
        "derived_checks": {
            "plan_realization_min": float(panel["plan_realization"].min()),
            "plan_realization_median": float(panel["plan_realization"].median()),
            "plan_realization_max": float(panel["plan_realization"].max()),
            "productivity_min": float(panel["productivity"].min()),
            "productivity_median": float(panel["productivity"].median()),
            "productivity_max": float(panel["productivity"].max()),
            "main_branch_level_half_guarantee_sum": minimum_total,
            "stronger_course_level_half_scenario_sum": int((request // 2).sum()),
            "capacity_constrained_maximum": maximum_total,
        },
        "judgment": <LONG_QUOTE_REDACTED>,
    }

    forecast_json = {
        "tuning_years": forecast_validation["tuning_years"],
        "holdout_year": forecast_validation["holdout_year"],
        "selected": forecast_validation["selected"],
        "selected_coefficients": selected_coeff.__dict__,
        "variants": forecast_validation["variants"],
    }

    original_inputs = sorted(
        [p for p in input_root.rglob("*") if p.is_file() and "converted" not in p.parts],
        key=lambda p: str(p),
    )
    input_hashes = {
        "kind": "model_input_hashes",
        "status": "pass",
        "scope": "original problem, attachment archive, and extracted legacy files used by the numerical pipeline",
        "authoritative_freeze_manifest": False,
        "freeze_status": "needs_review",
        "entries": [
            {
                "path": path.relative_to(workspace).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in original_inputs
        ],
    }
    run_metadata = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "matplotlib": matplotlib.__version__,
        "random_seed": SEED,
        "mc_draws": MC_DRAWS,
        "command": "python code/solve.py --workspace .",
        "network_used": False,
        "blind_revision": True,
        "freeze_performed": False,
    }

    course.to_csv(result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g")
    branch.to_csv(result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g")
    candidates.to_csv(result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g")
    sensitivity.to_csv(result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g")
    uncertainty_course.to_csv(result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g")
    uncertainty_branch.to_csv(result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g")
    holdout.to_csv(result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g")
    rolling_errors.to_csv(result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g")
    survey_stats.merge(isbn[["course_code", "course", "category"]], on="course_code").to_csv(
        result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g"
    )
    pd.DataFrame(forecast_validation["trend_grid"]).to_csv(
        result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g"
    )
    pd.DataFrame(forecast_validation["augmented_grid"]).to_csv(
        result_root / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig", float_format="%.10g"
    )
    write_json(result_root / "summary.json", summary)
    write_json(result_root / "data_audit.json", data_audit)
    write_json(result_root / "forecast_validation.json", forecast_json)
    write_json(result_root / "elasticity.json", elasticity)
    write_json(result_root / "constraint_checks.json", constraint_results)
    write_json(result_root / "boundary_checks.json", boundary_checks)
    write_json(result_root / "input_hashes.json", input_hashes)
    write_json(result_root / "run_metadata.json", run_metadata)

    generated_figures = make_figures(
        figure_root, branch, candidates, holdout, survey_stats, isbn, uncertainty_branch
    )
    generated_tex = write_generated_tex(
        paper_generated, summary, branch, course, candidates, forecast_json
    )
    summary["generated_figures"] = generated_figures
    summary["generated_tex"] = generated_tex
    write_json(result_root / "summary.json", summary)

    print(json.dumps(scalar(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
