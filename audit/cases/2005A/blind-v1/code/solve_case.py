"""Blind, reproducible solution pipeline for the 2005A workspace.

The script reads only the tidy data produced by ``build_data.py`` and writes
all numerical results, tables, figures, and LaTeX value macros used by the
paper.  No network access or external reference solution is required.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, TheilSenRegressor


SEED = 2005
BOOTSTRAPS = 5000
CLASS_NAMES = ["I", "II", "III", "IV", "V", "inferior_V"]
CLASS_TO_GRADE = {name: i + 1 for i, name in enumerate(CLASS_NAMES)}
GRADE_TO_CLASS = {value: key for key, value in CLASS_TO_GRADE.items()}

STATION_ENGLISH = {
    "四川攀枝花龙洞": "Panzhihua (mainstream)",
    "重庆朱沱": "Chongqing (mainstream)",
    "湖北宜昌南津关": "Yichang (mainstream)",
    "湖南岳阳城陵矶": "Yueyang (mainstream)",
    "江西九江河西水厂": "Jiujiang (mainstream)",
    "安徽安庆皖河口": "Anqing (mainstream)",
    "江苏南京林山": "Nanjing (mainstream)",
    "四川乐山岷江大桥": "Leshan / Minjiang",
    "四川宜宾凉姜沟": "Yibin / Minjiang",
    "四川泸州沱江二桥": "Luzhou / Tuojiang",
    "湖北丹江口胡家岭": "Danjiangkou Reservoir",
    "湖南长沙新港": "Changsha / Xiangjiang",
    "湖南岳阳岳阳楼": "Yueyang / Dongting",
    "湖北武汉宗关": "Wuhan / Hanjiang",
    "江西南昌滁槎": "Nanchang / Ganjiang",
    "江西九江蛤蟆石": "Jiujiang / Poyang",
    "江苏扬州三江营": "Yangzhou / Jiajiang",
}

MAINSTREAM_MAP = {
    "四川攀枝花": "四川攀枝花龙洞",
    "重庆朱沱": "重庆朱沱",
    "湖北宜昌": "湖北宜昌南津关",
    "湖南岳阳": "湖南岳阳城陵矶",
    "江西九江": "江西九江河西水厂",
    "安徽安庆": "安徽安庆皖河口",
    "江苏南京": "江苏南京林山",
}

SEGMENT_NAMES_ZH = [
    "攀枝花—重庆",
    "重庆—宜昌",
    "宜昌—岳阳",
    "岳阳—九江",
    "九江—安庆",
    "安庆—南京",
]
SEGMENT_NAMES_EN = [
    "Panzhihua-Chongqing",
    "Chongqing-Yichang",
    "Yichang-Yueyang",
    "Yueyang-Jiujiang",
    "Jiujiang-Anqing",
    "Anqing-Nanjing",
]


def write_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def project_simplex(vector: np.ndarray, total: float = 100.0) -> np.ndarray:
    """Euclidean projection of a vector onto the nonnegative simplex."""
    vector = np.asarray(vector, dtype=float)
    sorted_values = np.sort(vector)[::-1]
    cumulative = np.cumsum(sorted_values)
    candidates = np.nonzero(
        sorted_values * np.arange(1, len(vector) + 1) > cumulative - total
    )[0]
    rho = int(candidates[-1])
    theta = (cumulative[rho] - total) / (rho + 1)
    return np.maximum(vector - theta, 0.0)


def ols_fit_predict(x: np.ndarray, y: np.ndarray, future_x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    design = np.column_stack([np.ones(len(x)), x])
    future_design = np.column_stack([np.ones(len(future_x)), future_x])
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    fitted = design @ coefficients
    prediction = future_design @ coefficients
    return fitted, prediction, coefficients


def grade_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["grade_ph"] = np.where(result["ph"].between(6, 9, inclusive="both"), 1, 6)
    result["grade_do"] = np.select(
        [
            result["do_mg_l"] >= 7.5,
            result["do_mg_l"] >= 6,
            result["do_mg_l"] >= 5,
            result["do_mg_l"] >= 3,
            result["do_mg_l"] >= 2,
        ],
        [1, 2, 3, 4, 5],
        default=6,
    ).astype(int)
    result["grade_codmn"] = np.select(
        [
            result["codmn_mg_l"] <= 2,
            result["codmn_mg_l"] <= 4,
            result["codmn_mg_l"] <= 6,
            result["codmn_mg_l"] <= 10,
            result["codmn_mg_l"] <= 15,
        ],
        [1, 2, 3, 4, 5],
        default=6,
    ).astype(int)
    result["grade_nh3n"] = np.select(
        [
            result["nh3n_mg_l"] <= 0.15,
            result["nh3n_mg_l"] <= 0.5,
            result["nh3n_mg_l"] <= 1.0,
            result["nh3n_mg_l"] <= 1.5,
            result["nh3n_mg_l"] <= 2.0,
        ],
        [1, 2, 3, 4, 5],
        default=6,
    ).astype(int)
    grade_columns = ["grade_ph", "grade_do", "grade_codmn", "grade_nh3n"]
    result["calculated_grade"] = result[grade_columns].max(axis=1)
    result["calculated_class"] = result["calculated_grade"].map(GRADE_TO_CLASS)

    # Supporting continuous index, normalized to the class-III drinking-water
    # boundary.  The regulatory category above remains the primary result.
    result["subindex_do"] = 5.0 / result["do_mg_l"]
    result["subindex_codmn"] = result["codmn_mg_l"] / 6.0
    result["subindex_nh3n"] = result["nh3n_mg_l"] / 1.0
    result["subindex_ph"] = np.where(
        result["ph"].between(6, 9, inclusive="both"),
        1.0,
        1.0 + np.maximum(6.0 - result["ph"], result["ph"] - 9.0) / 3.0,
    )
    subindex_columns = ["subindex_ph", "subindex_do", "subindex_codmn", "subindex_nh3n"]
    average = result[subindex_columns].mean(axis=1)
    maximum = result[subindex_columns].max(axis=1)
    result["nemerow_index"] = np.sqrt((average**2 + maximum**2) / 2.0)

    driver_labels = {
        "grade_ph": "pH",
        "grade_do": "DO",
        "grade_codmn": "CODMn",
        "grade_nh3n": "NH3-N",
    }
    result["calculated_drivers"] = result.apply(
        lambda row: ",".join(
            driver_labels[col]
            for col in grade_columns
            if row[col] == row["calculated_grade"] and row["calculated_grade"] >= 4
        ),
        axis=1,
    )
    return result


def plot_q1_station(station_summary: pd.DataFrame, figures_dir: Path) -> None:
    ordered = station_summary.sort_values("mean_grade", ascending=True).copy()
    labels = [STATION_ENGLISH[name] for name in ordered["station"]]
    colors = ["#2c7bb6", "#74add1", "#abd9e9", "#fdae61", "#f46d43", "#a50026"]
    fig, ax = plt.subplots(figsize=(11, 8.5))
    left = np.zeros(len(ordered))
    for water_class, color in zip(CLASS_NAMES, colors, strict=True):
        values = ordered[f"pct_{water_class}"].to_numpy()
        ax.barh(labels, values, left=left, color=color, label=water_class.replace("_", " "))
        left += values
    ax.set_xlabel("Share of 28 monthly observations (%)")
    ax.set_xlim(0, 100)
    ax.set_title("Water-quality category composition by monitoring station")
    ax.legend(ncol=3, loc="lower right", frameon=False)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220)
    plt.close(fig)


def plot_q1_monthly(monthly_summary: pd.DataFrame, figures_dir: Path) -> None:
    dates = pd.to_datetime(monthly_summary["date"])
    fig, ax = plt.subplots(figsize=(10.5, 4.8))
    ax.plot(dates, monthly_summary["drinkable_pct"], marker="o", ms=3, label="I-III")
    ax.plot(dates, monthly_summary["iv_v_pct"], marker="s", ms=3, label="IV-V")
    ax.plot(dates, monthly_summary["inferior_pct"], marker="^", ms=3, label="Inferior V")
    ax.axhline(20, color="black", linestyle="--", linewidth=1, label="20% reference")
    ax.set_ylabel("Station share (%)")
    ax.set_title("Monthly category shares across 17 stations")
    ax.grid(alpha=0.25)
    ax.legend(ncol=4, frameon=False)
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220)
    plt.close(fig)


def solve_q1(monthly: pd.DataFrame, results_dir: Path, figures_dir: Path) -> dict:
    graded = grade_indicators(monthly)
    graded["reported_grade"] = graded["reported_class"].map(CLASS_TO_GRADE)
    agreement = graded["reported_grade"].eq(graded["calculated_grade"])

    counts = graded["calculated_class"].value_counts().reindex(CLASS_NAMES, fill_value=0)
    overall = pd.DataFrame(
        {
            "water_class": CLASS_NAMES,
            "count": counts.to_numpy(),
            "percentage": counts.to_numpy() / len(graded) * 100,
        }
    )

    records = []
    for (station, section), group in graded.groupby(["station", "section"], sort=False):
        record = {
            "station": station,
            "section": section,
            "months": len(group),
            "mean_grade": group["calculated_grade"].mean(),
            "mean_nemerow": group["nemerow_index"].mean(),
            "drinkable_pct": (group["calculated_grade"] <= 3).mean() * 100,
            "iv_v_pct": group["calculated_grade"].isin([4, 5]).mean() * 100,
            "inferior_pct": (group["calculated_grade"] == 6).mean() * 100,
            "worst_grade": int(group["calculated_grade"].max()),
        }
        for water_class in CLASS_NAMES:
            record[f"pct_{water_class}"] = (
                group["calculated_class"].eq(water_class).mean() * 100
            )
        records.append(record)
    station_summary = pd.DataFrame(records).sort_values(
        ["mean_grade", "inferior_pct", "mean_nemerow"], ascending=False
    )

    monthly_rows = []
    for date, group in graded.groupby("date"):
        monthly_rows.append(
            {
                "date": date,
                "mean_grade": group["calculated_grade"].mean(),
                "drinkable_pct": (group["calculated_grade"] <= 3).mean() * 100,
                "iv_v_pct": group["calculated_grade"].isin([4, 5]).mean() * 100,
                "inferior_pct": (group["calculated_grade"] == 6).mean() * 100,
            }
        )
    monthly_summary = pd.DataFrame(monthly_rows)

    group_rows = []
    for group_name, group in graded.groupby(
        np.where(graded["is_mainstream"], "mainstream", "tributary_lake")
    ):
        group_rows.append(
            {
                "group": group_name,
                "observations": len(group),
                "mean_grade": group["calculated_grade"].mean(),
                "drinkable_pct": (group["calculated_grade"] <= 3).mean() * 100,
                "iv_v_pct": group["calculated_grade"].isin([4, 5]).mean() * 100,
                "inferior_pct": (group["calculated_grade"] == 6).mean() * 100,
            }
        )
    group_summary = pd.DataFrame(group_rows)

    driver_counts = (
        graded.loc[graded["calculated_grade"] >= 4, "calculated_drivers"]
        .str.get_dummies(sep=",")
        .sum()
        .sort_values(ascending=False)
        .rename_axis("indicator")
        .reset_index(name="poor_quality_month_count")
    )

    graded.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    overall.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    station_summary.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    monthly_summary.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    group_summary.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    driver_counts.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    plot_q1_station(station_summary, figures_dir)
    plot_q1_monthly(monthly_summary, figures_dir)

    return {
        "observations": int(len(graded)),
        "classification_agreement_count": int(agreement.sum()),
        "classification_agreement_pct": float(agreement.mean() * 100),
        "overall_drinkable_pct": float((graded["calculated_grade"] <= 3).mean() * 100),
        "overall_iv_v_pct": float(graded["calculated_grade"].isin([4, 5]).mean() * 100),
        "overall_inferior_pct": float((graded["calculated_grade"] == 6).mean() * 100),
        "worst_stations": station_summary.head(4)[
            ["station", "mean_grade", "drinkable_pct", "iv_v_pct", "inferior_pct"]
        ].to_dict(orient="records"),
        "group_summary": group_summary.to_dict(orient="records"),
        "primary_driver_counts": driver_counts.to_dict(orient="records"),
    }


def compute_segment_loads(
    monthly: pd.DataFrame, hydrology: pd.DataFrame, degradation_k_day: float
) -> pd.DataFrame:
    months = sorted(hydrology["date"].unique())
    hydrology = hydrology.sort_values(["date", "distance_km"]).copy()
    records = []
    for date in months:
        hydro_month = hydrology[hydrology["date"] == date].sort_values("distance_km")
        water_month = monthly[monthly["date"] == date].set_index("station")
        hydro_rows = hydro_month.to_dict(orient="records")
        for i in range(1, len(hydro_rows)):
            upstream = hydro_rows[i - 1]
            downstream = hydro_rows[i]
            upstream_station = MAINSTREAM_MAP[upstream["station_short"]]
            downstream_station = MAINSTREAM_MAP[downstream["station_short"]]
            distance_km = downstream["distance_km"] - upstream["distance_km"]
            travel_seconds = distance_km * 1000 * 0.5 * (
                1.0 / upstream["velocity_m_s"] + 1.0 / downstream["velocity_m_s"]
            )
            travel_days = travel_seconds / 86400.0
            attenuation = math.exp(-degradation_k_day * travel_days)
            for pollutant, concentration_column in [
                ("CODMn", "codmn_mg_l"),
                ("NH3-N", "nh3n_mg_l"),
            ]:
                upstream_concentration = float(water_month.loc[upstream_station, concentration_column])
                downstream_concentration = float(water_month.loc[downstream_station, concentration_column])
                upstream_load_g_s = upstream["flow_m3_s"] * upstream_concentration
                downstream_load_g_s = downstream["flow_m3_s"] * downstream_concentration
                arriving_load_g_s = upstream_load_g_s * attenuation
                incremental_g_s = downstream_load_g_s - arriving_load_g_s
                records.append(
                    {
                        "date": date,
                        "degradation_k_day": degradation_k_day,
                        "segment_index": i,
                        "segment_zh": SEGMENT_NAMES_ZH[i - 1],
                        "segment_en": SEGMENT_NAMES_EN[i - 1],
                        "upstream_station": upstream_station,
                        "downstream_station": downstream_station,
                        "pollutant": pollutant,
                        "distance_km": distance_km,
                        "travel_days": travel_days,
                        "attenuation": attenuation,
                        "upstream_concentration_mg_l": upstream_concentration,
                        "downstream_concentration_mg_l": downstream_concentration,
                        "upstream_load_g_s": upstream_load_g_s,
                        "arriving_upstream_load_g_s": arriving_load_g_s,
                        "downstream_load_g_s": downstream_load_g_s,
                        "signed_increment_t_day": incremental_g_s * 0.0864,
                        "positive_increment_t_day": max(incremental_g_s, 0.0) * 0.0864,
                        "concentration_change_mg_l": downstream_concentration
                        - upstream_concentration,
                    }
                )
    return pd.DataFrame(records)


def plot_q2_sources(summary: pd.DataFrame, figures_dir: Path) -> None:
    selected = summary[summary["degradation_k_day"] == 0.2]
    fig, axes = plt.subplots(2, 1, figsize=(10.5, 8.2), sharex=True)
    for ax, pollutant, color in zip(axes, ["CODMn", "NH3-N"], ["#4575b4", "#d73027"], strict=True):
        subset = selected[selected["pollutant"] == pollutant].sort_values("segment_index")
        ax.bar(subset["segment_en"], subset["mean_positive_t_day"], color=color)
        ax.set_ylabel("Estimated local load (t/day)")
        ax.set_title(pollutant)
        ax.grid(axis="y", alpha=0.25)
    axes[-1].tick_params(axis="x", rotation=25)
    fig.suptitle("Decay-corrected incremental pollutant loads by mainstream reach (k=0.2/day)")
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220)
    plt.close(fig)


def solve_q2(
    monthly: pd.DataFrame,
    hydrology: pd.DataFrame,
    results_dir: Path,
    figures_dir: Path,
    rng: np.random.Generator,
) -> dict:
    all_loads = pd.concat(
        [compute_segment_loads(monthly, hydrology, k) for k in [0.0, 0.1, 0.2, 0.5]],
        ignore_index=True,
    )
    summary = (
        all_loads.groupby(
            ["degradation_k_day", "segment_index", "segment_zh", "segment_en", "pollutant"],
            as_index=False,
        )
        .agg(
            mean_positive_t_day=("positive_increment_t_day", "mean"),
            median_positive_t_day=("positive_increment_t_day", "median"),
            mean_signed_t_day=("signed_increment_t_day", "mean"),
            total_positive_t=("positive_increment_t_day", "sum"),
            negative_months=("signed_increment_t_day", lambda x: int((x < 0).sum())),
            mean_travel_days=("travel_days", "mean"),
            mean_attenuation=("attenuation", "mean"),
        )
    )
    summary["source_share_pct"] = summary.groupby(
        ["degradation_k_day", "pollutant"]
    )["total_positive_t"].transform(lambda x: x / x.sum() * 100)
    summary["rank"] = summary.groupby(["degradation_k_day", "pollutant"])[
        "mean_positive_t_day"
    ].rank(method="min", ascending=False).astype(int)

    selected = all_loads[all_loads["degradation_k_day"] == 0.2]
    bootstrap_rows = []
    months = np.array(sorted(selected["date"].unique()))
    for pollutant in ["CODMn", "NH3-N"]:
        pollutant_frame = selected[selected["pollutant"] == pollutant]
        pivot = pollutant_frame.pivot(
            index="date", columns="segment_index", values="positive_increment_t_day"
        ).reindex(months)
        top_counts = np.zeros(len(pivot.columns), dtype=int)
        for _ in range(BOOTSTRAPS):
            sample_indices = rng.integers(0, len(months), size=len(months))
            means = pivot.to_numpy()[sample_indices].mean(axis=0)
            top_counts[int(np.argmax(means))] += 1
        for segment_index, top_count in zip(pivot.columns, top_counts, strict=True):
            bootstrap_rows.append(
                {
                    "pollutant": pollutant,
                    "segment_index": int(segment_index),
                    "segment_zh": SEGMENT_NAMES_ZH[int(segment_index) - 1],
                    "top_rank_frequency_pct": top_count / BOOTSTRAPS * 100,
                }
            )
    bootstrap = pd.DataFrame(bootstrap_rows)

    # Identity check in native g/s before the t/day conversion.
    balance_error = (
        all_loads["downstream_load_g_s"]
        - all_loads["arriving_upstream_load_g_s"]
        - all_loads["signed_increment_t_day"] / 0.0864
    ).abs()

    all_loads.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    summary.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    bootstrap.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    plot_q2_sources(summary, figures_dir)

    primary = summary[summary["degradation_k_day"] == 0.2].merge(
        bootstrap, on=["pollutant", "segment_index", "segment_zh"], how="left"
    )
    top_sources: dict[str, list[dict]] = {}
    for pollutant in ["CODMn", "NH3-N"]:
        top_sources[pollutant] = (
            primary[primary["pollutant"] == pollutant]
            .sort_values("mean_positive_t_day", ascending=False)
            .head(3)[
                [
                    "segment_zh",
                    "mean_positive_t_day",
                    "source_share_pct",
                    "negative_months",
                    "top_rank_frequency_pct",
                ]
            ]
            .to_dict(orient="records")
        )
    rank_by_k = (
        summary.sort_values(["degradation_k_day", "pollutant", "rank"])
        .groupby(["degradation_k_day", "pollutant"], as_index=False)
        .first()[["degradation_k_day", "pollutant", "segment_zh", "mean_positive_t_day"]]
    )
    return {
        "degradation_k_day": 0.2,
        "top_sources": top_sources,
        "top_segment_by_k": rank_by_k.to_dict(orient="records"),
        "mass_balance_max_abs_error_g_s": float(balance_error.max()),
        "bootstrap_resamples": BOOTSTRAPS,
    }


def composition_cv(years: np.ndarray, composition: np.ndarray, pressure: np.ndarray) -> pd.DataFrame:
    rows = []
    for i in range(5, len(years)):
        actual = composition[i]
        predictions = {"persistence_baseline": composition[i - 1]}

        direct = []
        for component in range(composition.shape[1]):
            model = LinearRegression().fit(
                (years[:i] - years[0]).reshape(-1, 1), composition[:i, component]
            )
            direct.append(model.predict([[years[i] - years[0]]])[0])
        predictions["linear_simplex"] = project_simplex(np.array(direct))

        epsilon = 0.5
        alr = np.log((composition[:i, 1:] + epsilon) / (composition[:i, :1] + epsilon))
        pressure_model = LinearRegression().fit(pressure[:i].reshape(-1, 1), alr)
        z = pressure_model.predict([[pressure[i]]])[0]
        ratios = np.exp(z)
        predictions["pressure_alr"] = (
            np.r_[1.0, ratios] / (1.0 + ratios.sum()) * 100
        )

        for model_name, prediction in predictions.items():
            rows.append(
                {
                    "task": "water_quality_composition",
                    "model": model_name,
                    "validation_year": int(years[i]),
                    "mae_pct_point": float(np.abs(prediction - actual).mean()),
                    "rmse_pct_point": float(np.sqrt(np.mean((prediction - actual) ** 2))),
                }
            )
    return pd.DataFrame(rows)


def scalar_cv(years: np.ndarray, values: np.ndarray, task: str) -> pd.DataFrame:
    rows = []
    for i in range(5, len(years)):
        training_x = (years[:i] - years[0]).reshape(-1, 1)
        target_x = [[years[i] - years[0]]]
        predictions = {
            "last_value_baseline": values[i - 1],
            "expanding_mean": values[:i].mean(),
            "linear_ols": LinearRegression().fit(training_x, values[:i]).predict(target_x)[0],
            "theil_sen": TheilSenRegressor(random_state=SEED)
            .fit(training_x, values[:i])
            .predict(target_x)[0],
        }
        for model_name, prediction in predictions.items():
            error = float(prediction - values[i])
            rows.append(
                {
                    "task": task,
                    "model": model_name,
                    "validation_year": int(years[i]),
                    "absolute_error": abs(error),
                    "squared_error": error**2,
                }
            )
    return pd.DataFrame(rows)


def bootstrap_linear_composition(
    t: np.ndarray,
    composition: np.ndarray,
    future_t: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    fitted_components = []
    coefficient_components = []
    for component in range(composition.shape[1]):
        fitted, _, coefficients = ols_fit_predict(t, composition[:, component], future_t)
        fitted_components.append(fitted)
        coefficient_components.append(coefficients)
    fitted_matrix = np.column_stack(fitted_components)
    residuals = composition - fitted_matrix
    residuals -= residuals.mean(axis=0, keepdims=True)
    design = np.column_stack([np.ones(len(t)), t])
    future_design = np.column_stack([np.ones(len(future_t)), future_t])
    draws = np.empty((BOOTSTRAPS, len(future_t), composition.shape[1]))
    for b in range(BOOTSTRAPS):
        sampled_residuals = residuals[rng.integers(0, len(t), size=len(t))]
        synthetic = fitted_matrix + sampled_residuals
        coefficients = np.linalg.lstsq(design, synthetic, rcond=None)[0]
        predicted = future_design @ coefficients
        predictive_noise = residuals[rng.integers(0, len(t), size=len(future_t))]
        predicted += predictive_noise
        draws[b] = np.vstack([project_simplex(row) for row in predicted])
    return np.quantile(draws, [0.1, 0.9], axis=0), draws


def bootstrap_scalar_linear(
    t: np.ndarray,
    values: np.ndarray,
    future_t: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    fitted, _, _ = ols_fit_predict(t, values, future_t)
    residuals = values - fitted
    residuals -= residuals.mean()
    design = np.column_stack([np.ones(len(t)), t])
    future_design = np.column_stack([np.ones(len(future_t)), future_t])
    draws = np.empty((BOOTSTRAPS, len(future_t)))
    for b in range(BOOTSTRAPS):
        synthetic = fitted + residuals[rng.integers(0, len(t), size=len(t))]
        coefficients = np.linalg.lstsq(design, synthetic, rcond=None)[0]
        prediction = future_design @ coefficients
        prediction += residuals[rng.integers(0, len(t), size=len(future_t))]
        draws[b] = np.maximum(prediction, 0.0)
    return np.quantile(draws, [0.1, 0.9], axis=0), draws


def plot_q3_quality(
    history: pd.DataFrame, forecast: pd.DataFrame, figures_dir: Path
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(10.3, 9), sharex=True)
    components = [
        ("good_pct", "Good (I-III)", "#1a9850"),
        ("iv_v_pct", "IV-V", "#f46d43"),
        ("inferior_pct", "Inferior V", "#a50026"),
    ]
    for ax, (column, label, color) in zip(axes, components, strict=True):
        ax.plot(history["year"], history[column], "o-", color=color, label="observed")
        ax.plot(forecast["year"], forecast[column], "o--", color=color, label="forecast")
        ax.fill_between(
            forecast["year"],
            forecast[f"{column}_p10"],
            forecast[f"{column}_p90"],
            color=color,
            alpha=0.18,
            label="80% bootstrap PI",
        )
        ax.axvline(2004.5, color="black", linewidth=1, linestyle=":")
        ax.set_ylabel("Share (%)")
        ax.set_title(label)
        ax.grid(alpha=0.25)
    axes[0].legend(ncol=3, frameon=False)
    axes[-1].set_xlabel("Year")
    fig.suptitle("Mainstream water-quality composition: history and trend forecast")
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220)
    plt.close(fig)


def plot_q3_wastewater(
    annual: pd.DataFrame, forecast: pd.DataFrame, figures_dir: Path
) -> None:
    fig, ax = plt.subplots(figsize=(9.8, 4.8))
    ax.plot(annual["year"], annual["wastewater_1e8_t"], "o-", label="observed")
    ax.plot(forecast["year"], forecast["wastewater_1e8_t"], "o--", label="OLS trend")
    ax.fill_between(
        forecast["year"],
        forecast["wastewater_1e8_t_p10"],
        forecast["wastewater_1e8_t_p90"],
        alpha=0.2,
        label="80% bootstrap PI",
    )
    ax.axvline(2004.5, color="black", linewidth=1, linestyle=":")
    ax.set_ylabel("Wastewater discharge (10^8 t/year)")
    ax.set_xlabel("Year")
    ax.set_title("Wastewater-discharge trend forecast")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220)
    plt.close(fig)


def solve_q3(
    annual_wq: pd.DataFrame,
    annual_totals: pd.DataFrame,
    results_dir: Path,
    figures_dir: Path,
    rng: np.random.Generator,
) -> tuple[dict, pd.DataFrame, np.ndarray, np.ndarray, pd.DataFrame]:
    mainstream = annual_wq[
        (annual_wq["period"] == "hydrological_year")
        & (annual_wq["scope"] == "mainstream")
    ].sort_values("year")
    history = pd.DataFrame(
        {
            "year": mainstream["year"].astype(int),
            "good_pct": mainstream[["share_I_pct", "share_II_pct", "share_III_pct"]].sum(axis=1),
            "iv_v_pct": mainstream[["share_IV_pct", "share_V_pct"]].sum(axis=1),
            "inferior_pct": mainstream["share_inferior_V_pct"],
        }
    ).reset_index(drop=True)
    composition = history[["good_pct", "iv_v_pct", "inferior_pct"]].to_numpy(float)
    composition = composition / composition.sum(axis=1, keepdims=True) * 100
    history[["good_pct", "iv_v_pct", "inferior_pct"]] = composition

    annual = annual_totals.sort_values("year").reset_index(drop=True)
    years = annual["year"].to_numpy(int)
    pressure = annual["wastewater_1e8_t"].to_numpy() / annual["river_flow_1e8_m3"].to_numpy() * 100

    comp_cv = composition_cv(years, composition, pressure)
    wastewater_cv = scalar_cv(years, annual["wastewater_1e8_t"].to_numpy(float), "wastewater")
    flow_cv = scalar_cv(years, annual["river_flow_1e8_m3"].to_numpy(float), "river_flow")
    comp_aggregate = (
        comp_cv.groupby(["task", "model"], as_index=False)
        .agg(mae=("mae_pct_point", "mean"), rmse=("rmse_pct_point", "mean"))
    )
    scalar_aggregate = (
        pd.concat([wastewater_cv, flow_cv], ignore_index=True)
        .groupby(["task", "model"], as_index=False)
        .agg(mae=("absolute_error", "mean"), rmse=("squared_error", lambda x: np.sqrt(x.mean())))
    )
    model_cv = pd.concat([comp_aggregate, scalar_aggregate], ignore_index=True)

    t = years - years[0]
    future_years = np.arange(2005, 2015)
    future_t = future_years - years[0]
    central_components = []
    component_slopes = []
    for component in range(composition.shape[1]):
        _, prediction, coefficients = ols_fit_predict(
            t, composition[:, component], future_t
        )
        central_components.append(prediction)
        component_slopes.append(coefficients[1])
    raw_forecast = np.column_stack(central_components)
    central_composition = np.vstack([project_simplex(row) for row in raw_forecast])
    comp_quantiles, comp_draws = bootstrap_linear_composition(
        t, composition, future_t, rng
    )

    wastewater_values = annual["wastewater_1e8_t"].to_numpy(float)
    _, wastewater_prediction, wastewater_coefficients = ols_fit_predict(
        t, wastewater_values, future_t
    )
    wastewater_quantiles, wastewater_draws = bootstrap_scalar_linear(
        t, wastewater_values, future_t, rng
    )
    flow_values = annual["river_flow_1e8_m3"].to_numpy(float)
    flow_central = float(flow_values.mean())

    forecast = pd.DataFrame(
        {
            "year": future_years,
            "good_pct": central_composition[:, 0],
            "iv_v_pct": central_composition[:, 1],
            "inferior_pct": central_composition[:, 2],
            "good_pct_p10": comp_quantiles[0, :, 0],
            "good_pct_p90": comp_quantiles[1, :, 0],
            "iv_v_pct_p10": comp_quantiles[0, :, 1],
            "iv_v_pct_p90": comp_quantiles[1, :, 1],
            "inferior_pct_p10": comp_quantiles[0, :, 2],
            "inferior_pct_p90": comp_quantiles[1, :, 2],
            "wastewater_1e8_t": wastewater_prediction,
            "wastewater_1e8_t_p10": wastewater_quantiles[0],
            "wastewater_1e8_t_p90": wastewater_quantiles[1],
            "river_flow_1e8_m3": flow_central,
            "pollution_pressure_pct": wastewater_prediction / flow_central * 100,
        }
    )
    history.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    forecast.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    model_cv.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    comp_cv.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    pd.concat([wastewater_cv, flow_cv], ignore_index=True).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig"
    )
    plot_q3_quality(history, forecast, figures_dir)
    plot_q3_wastewater(annual, forecast, figures_dir)

    composition_row = model_cv[
        (model_cv["task"] == "water_quality_composition")
        & (model_cv["model"] == "linear_simplex")
    ].iloc[0]
    wastewater_row = model_cv[
        (model_cv["task"] == "wastewater") & (model_cv["model"] == "linear_ols")
    ].iloc[0]
    flow_row = model_cv[
        (model_cv["task"] == "river_flow") & (model_cv["model"] == "expanding_mean")
    ].iloc[0]
    summary = {
        "selected_quality_model": "linear_simplex",
        "selected_wastewater_model": "linear_ols",
        "selected_flow_model": "historical_mean",
        "rolling_cv": {
            "quality_mae_pct_point": float(composition_row["mae"]),
            "wastewater_mae_1e8_t": float(wastewater_row["mae"]),
            "flow_mae_1e8_m3": float(flow_row["mae"]),
        },
        "annual_component_trends_pct_point": {
            "good": float(component_slopes[0]),
            "IV_V": float(component_slopes[1]),
            "inferior_V": float(component_slopes[2]),
        },
        "wastewater_trend_1e8_t_per_year": float(wastewater_coefficients[1]),
        "flow_central_1e8_m3": flow_central,
        "forecast_2005": forecast.iloc[0].to_dict(),
        "forecast_2014": forecast.iloc[-1].to_dict(),
        "bootstrap_resamples": BOOTSTRAPS,
    }
    return summary, forecast, wastewater_draws, flow_values, history


def find_safe_pressure_cap(history: pd.DataFrame, annual: pd.DataFrame) -> tuple[float, int]:
    frame = history.merge(
        annual[["year", "wastewater_1e8_t", "river_flow_1e8_m3"]], on="year"
    )
    frame["pressure_fraction"] = frame["wastewater_1e8_t"] / frame["river_flow_1e8_m3"]
    frame = frame.sort_values("pressure_fraction")
    safe_cap = 0.0
    safe_year = int(frame.iloc[0]["year"])
    for threshold in frame["pressure_fraction"].unique():
        subset = frame[frame["pressure_fraction"] <= threshold + 1e-15]
        meets = (subset["iv_v_pct"] <= 20 + 1e-12).all() and (
            subset["inferior_pct"] <= 1e-12
        ).all()
        if not meets:
            break
        safe_cap = float(threshold)
        safe_year = int(
            frame.loc[np.isclose(frame["pressure_fraction"], threshold), "year"].iloc[0]
        )
    return safe_cap, safe_year


def plot_q4_treatment(treatment: pd.DataFrame, figures_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(
        treatment["year"],
        treatment["treatment_equivalent_1e8_t"],
        color="#4575b4",
        label="required effective treatment",
    )
    ax.errorbar(
        treatment["year"],
        treatment["treatment_equivalent_1e8_t"],
        yerr=np.vstack(
            [
                treatment["treatment_equivalent_1e8_t"]
                - treatment["treatment_equivalent_1e8_t_p10"],
                treatment["treatment_equivalent_1e8_t_p90"]
                - treatment["treatment_equivalent_1e8_t"],
            ]
        ),
        fmt="none",
        ecolor="black",
        capsize=3,
        label="80% scenario interval",
    )
    ax.set_xlabel("Year")
    ax.set_ylabel("Treatment volume (10^8 t/year)")
    ax.set_title("Annual treatment requirement under the historical safe-pressure envelope")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220)
    plt.close(fig)


def solve_q4(
    history: pd.DataFrame,
    annual_totals: pd.DataFrame,
    forecast: pd.DataFrame,
    wastewater_draws: np.ndarray,
    flow_values: np.ndarray,
    results_dir: Path,
    figures_dir: Path,
    rng: np.random.Generator,
) -> dict:
    annual = annual_totals.sort_values("year").reset_index(drop=True)
    safe_cap, safe_year = find_safe_pressure_cap(history, annual)
    central_wastewater = forecast["wastewater_1e8_t"].to_numpy(float)
    central_flow = forecast["river_flow_1e8_m3"].to_numpy(float)
    assimilative_equivalent = safe_cap * central_flow
    treatment_equivalent = np.maximum(central_wastewater - assimilative_equivalent, 0.0)

    flow_draws = rng.choice(flow_values, size=wastewater_draws.shape, replace=True)
    treatment_draws = np.maximum(wastewater_draws - safe_cap * flow_draws, 0.0)
    treatment_quantiles = np.quantile(treatment_draws, [0.1, 0.9], axis=0)
    treatment = pd.DataFrame(
        {
            "year": forecast["year"].astype(int),
            "forecast_wastewater_1e8_t": central_wastewater,
            "forecast_flow_1e8_m3": central_flow,
            "safe_pressure_cap_pct": safe_cap * 100,
            "allowable_untreated_equivalent_1e8_t": assimilative_equivalent,
            "treatment_equivalent_1e8_t": treatment_equivalent,
            "treatment_equivalent_1e8_t_p10": treatment_quantiles[0],
            "treatment_equivalent_1e8_t_p90": treatment_quantiles[1],
            "treated_share_pct": treatment_equivalent / central_wastewater * 100,
            "treatment_volume_at_80pct_removal_1e8_t": treatment_equivalent / 0.8,
        }
    )

    sensitivity_rows = []
    for cap_pct in [2.0, safe_cap * 100, 2.4]:
        for efficiency in [0.6, 0.8, 1.0]:
            required = np.maximum(
                central_wastewater - cap_pct / 100 * central_flow, 0.0
            ) / efficiency
            for year, value in zip(forecast["year"], required, strict=True):
                sensitivity_rows.append(
                    {
                        "year": int(year),
                        "pressure_cap_pct": cap_pct,
                        "pollutant_removal_efficiency": efficiency,
                        "required_treatment_1e8_t": value,
                    }
                )
    sensitivity = pd.DataFrame(sensitivity_rows)
    treatment.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    sensitivity.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    plot_q4_treatment(treatment, figures_dir)

    effective_after = central_wastewater - treatment_equivalent
    pressure_after = effective_after / central_flow
    return {
        "safe_pressure_cap_pct": safe_cap * 100,
        "safe_cap_anchor_year": safe_year,
        "treatment_2005_1e8_t": float(treatment.iloc[0]["treatment_equivalent_1e8_t"]),
        "treatment_2014_1e8_t": float(treatment.iloc[-1]["treatment_equivalent_1e8_t"]),
        "treated_share_2005_pct": float(treatment.iloc[0]["treated_share_pct"]),
        "treated_share_2014_pct": float(treatment.iloc[-1]["treated_share_pct"]),
        "max_post_treatment_pressure_pct": float(pressure_after.max() * 100),
        "interpretation": "pollutant-equivalent volume at 100% removal; divide by actual removal efficiency",
    }


def latex_escape(value: object) -> str:
    text = str(value)
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "_": r"\_",
        "#": r"\#",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def write_latex_table(path: Path, headers: list[str], rows: list[list[object]], alignment: str) -> None:
    lines = [rf"\begin{{tabular}}{{{alignment}}}", r"\toprule"]
    # Headers are controlled LaTeX fragments supplied by this script; body
    # values originate in data and are escaped.
    lines.append(" & ".join(headers) + r" \\")
    lines.append(r"\midrule")
    for row in rows:
        lines.append(" & ".join(latex_escape(value) for value in row) + r" \\")
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_paper_artifacts(
    paper_dir: Path,
    results_dir: Path,
    q1: dict,
    q2: dict,
    q3: dict,
    q4: dict,
) -> None:
    q1_groups = {row["group"]: row for row in q1["group_summary"]}
    cod_top = q2["top_sources"]["CODMn"][0]
    nh_top = q2["top_sources"]["NH3-N"][0]
    macros = {
        "OverallDrinkablePct": f"{q1['overall_drinkable_pct']:.2f}",
        "OverallIVVPct": f"{q1['overall_iv_v_pct']:.2f}",
        "OverallInferiorPct": f"{q1['overall_inferior_pct']:.2f}",
        "ClassificationAgreementPct": f"{q1['classification_agreement_pct']:.2f}",
        "WorstStation": q1["worst_stations"][0]["station"],
        "WorstStationMeanGrade": f"{q1['worst_stations'][0]['mean_grade']:.2f}",
        "MainstreamDrinkablePct": f"{q1_groups['mainstream']['drinkable_pct']:.2f}",
        "TributaryDrinkablePct": f"{q1_groups['tributary_lake']['drinkable_pct']:.2f}",
        "QTwoTopSegment": cod_top["segment_zh"],
        "QTwoCODLoad": f"{cod_top['mean_positive_t_day']:.1f}",
        "QTwoCODShare": f"{cod_top['source_share_pct']:.2f}",
        "QTwoCODBootstrap": f"{cod_top['top_rank_frequency_pct']:.2f}",
        "QTwoNHLoad": f"{nh_top['mean_positive_t_day']:.1f}",
        "QTwoNHShare": f"{nh_top['source_share_pct']:.2f}",
        "QTwoNHBootstrap": f"{nh_top['top_rank_frequency_pct']:.2f}",
        "QualityCVMAE": f"{q3['rolling_cv']['quality_mae_pct_point']:.3f}",
        "WastewaterCVMAE": f"{q3['rolling_cv']['wastewater_mae_1e8_t']:.3f}",
        "WastewaterTrend": f"{q3['wastewater_trend_1e8_t_per_year']:.2f}",
        "ForecastWastewaterFirst": f"{q3['forecast_2005']['wastewater_1e8_t']:.1f}",
        "ForecastWastewaterLast": f"{q3['forecast_2014']['wastewater_1e8_t']:.1f}",
        "ForecastIVVFirst": f"{q3['forecast_2005']['iv_v_pct']:.1f}",
        "ForecastIVVLast": f"{q3['forecast_2014']['iv_v_pct']:.1f}",
        "ForecastInferiorFirst": f"{q3['forecast_2005']['inferior_pct']:.1f}",
        "ForecastInferiorLast": f"{q3['forecast_2014']['inferior_pct']:.1f}",
        "SafePressureCap": f"{q4['safe_pressure_cap_pct']:.3f}",
        "TreatmentFirst": f"{q4['treatment_2005_1e8_t']:.1f}",
        "TreatmentLast": f"{q4['treatment_2014_1e8_t']:.1f}",
        "TreatedShareFirst": f"{q4['treated_share_2005_pct']:.1f}",
        "TreatedShareLast": f"{q4['treated_share_2014_pct']:.1f}",
    }
    macro_lines = [rf"\newcommand{{\{name}}}{{{value}}}" for name, value in macros.items()]
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "generated-values.tex").write_text(
        "\n".join(macro_lines) + "\n", encoding="utf-8"
    )

    q1_table = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    q1_rows = [
        [
            row.station,
            f"{row.mean_grade:.2f}",
            f"{row.drinkable_pct:.1f}",
            f"{row.iv_v_pct:.1f}",
            f"{row.inferior_pct:.1f}",
        ]
        for row in q1_table.itertuples(index=False)
    ]
    write_latex_table(
        paper_dir / "generated" / "q1_station_table.tex",
        ["断面", "平均等级", "I--III/\\%", "IV--V/\\%", "劣V/\\%"],
        q1_rows,
        "lrrrr",
    )

    q2_table = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    q2_table = q2_table[np.isclose(q2_table["degradation_k_day"], 0.2)]
    q2_pivot = q2_table.pivot(index="segment_zh", columns="pollutant", values=["mean_positive_t_day", "source_share_pct"])
    q2_rows = []
    for segment in SEGMENT_NAMES_ZH:
        q2_rows.append(
            [
                segment,
                f"{q2_pivot.loc[segment, ('mean_positive_t_day', 'CODMn')]:.1f}",
                f"{q2_pivot.loc[segment, ('source_share_pct', 'CODMn')]:.1f}",
                f"{q2_pivot.loc[segment, ('mean_positive_t_day', 'NH3-N')]:.1f}",
                f"{q2_pivot.loc[segment, ('source_share_pct', 'NH3-N')]:.1f}",
            ]
        )
    write_latex_table(
        paper_dir / "generated" / "q2_segment_table.tex",
        ["区间", "CODMn/(t$\\cdot$d$^{-1}$)", "占比/\\%", "NH$_3$-N/(t$\\cdot$d$^{-1}$)", "占比/\\%"],
        q2_rows,
        "lrrrr",
    )

    q3_table = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    q3_rows = [
        [
            int(row.year),
            f"{row.wastewater_1e8_t:.1f}",
            f"{row.good_pct:.1f}",
            f"{row.iv_v_pct:.1f}",
            f"{row.inferior_pct:.1f}",
        ]
        for row in q3_table.itertuples(index=False)
    ]
    write_latex_table(
        paper_dir / "generated" / "q3_forecast_table.tex",
        ["年份", "废水/亿t", "I--III/\\%", "IV--V/\\%", "劣V/\\%"],
        q3_rows,
        "rrrrr",
    )

    q4_table = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    q4_rows = [
        [
            int(row.year),
            f"{row.forecast_wastewater_1e8_t:.1f}",
            f"{row.treatment_equivalent_1e8_t:.1f}",
            f"[{row.treatment_equivalent_1e8_t_p10:.1f},{row.treatment_equivalent_1e8_t_p90:.1f}]",
            f"{row.treated_share_pct:.1f}",
            f"{row.treatment_volume_at_80pct_removal_1e8_t:.1f}",
        ]
        for row in q4_table.itertuples(index=False)
    ]
    write_latex_table(
        paper_dir / "generated" / "q4_treatment_table.tex",
        ["年份", "预测废水/亿t", "等效处理/亿t", "80\\%区间", "处理占比/\\%", "效率80\\%时/亿t"],
        q4_rows,
        "rrrrrr",
    )

    write_json(paper_dir / "generated-paper-values.json", macros)


def build_verification(
    q1: dict,
    q2: dict,
    q3: dict,
    q4: dict,
    results_dir: Path,
) -> dict:
    q2_summary = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    monotone_k = True
    for (_, _), group in q2_summary.groupby(["segment_zh", "pollutant"]):
        ordered = group.sort_values("degradation_k_day")["mean_positive_t_day"].to_numpy()
        monotone_k &= bool(np.all(np.diff(ordered) >= -1e-9))
    forecast = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    closure_error = (
        forecast[["good_pct", "iv_v_pct", "inferior_pct"]].sum(axis=1) - 100
    ).abs().max()
    treatment = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    tests = {
        "q1_all_476_rows_reproduced": "pass" if q1["observations"] == 476 else "fail",
        "q1_threshold_class_matches_reported": "pass"
        if math.isclose(q1["classification_agreement_pct"], 100.0)
        else "fail",
        "q2_mass_balance_identity": "pass"
        if q2["mass_balance_max_abs_error_g_s"] < 1e-8
        else "fail",
        "q2_load_non_decreasing_with_k": "pass" if monotone_k else "fail",
        "q3_forecast_composition_closure": "pass" if closure_error < 1e-8 else "fail",
        "q4_treatment_not_above_discharge_at_full_efficiency": "pass"
        if (treatment["treatment_equivalent_1e8_t"] <= treatment["forecast_wastewater_1e8_t"] + 1e-12).all()
        else "fail",
        "q4_internal_pressure_cap_satisfied": "pass"
        if q4["max_post_treatment_pressure_pct"] <= q4["safe_pressure_cap_pct"] + 1e-10
        else "fail",
        "external_guarantee_of_no_inferior_V": "needs_review",
        "source_table_anomalies_and_coverage_break": "needs_review",
        "mathematical_correctness_beyond_internal_checks": "needs_review",
    }
    overall = "fail" if "fail" in tests.values() else "needs_review" if "needs_review" in tests.values() else "pass"
    return {
        "overall": overall,
        "tests": tests,
        "numeric_diagnostics": {
            "q2_mass_balance_max_abs_error_g_s": q2["mass_balance_max_abs_error_g_s"],
            "q3_composition_closure_max_abs_error_pct_point": float(closure_error),
            "q4_max_post_treatment_pressure_pct": q4["max_post_treatment_pressure_pct"],
        },
        "random_seed": SEED,
        "bootstrap_resamples": BOOTSTRAPS,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--figures-dir", type=Path, required=True)
    parser.add_argument("--paper-dir", type=Path, required=True)
    args = parser.parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=True)
    args.figures_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)

    monthly = pd.read_csv(args.data_dir / "<SOURCE_FILE_REDACTED>")
    hydrology = pd.read_csv(args.data_dir / "<SOURCE_FILE_REDACTED>")
    annual_wq = pd.read_csv(args.data_dir / "<SOURCE_FILE_REDACTED>")
    annual_totals = pd.read_csv(args.data_dir / "<SOURCE_FILE_REDACTED>")

    q1 = solve_q1(monthly, args.results_dir, args.figures_dir)
    q2 = solve_q2(monthly, hydrology, args.results_dir, args.figures_dir, rng)
    q3, forecast, wastewater_draws, flow_values, history = solve_q3(
        annual_wq, annual_totals, args.results_dir, args.figures_dir, rng
    )
    q4 = solve_q4(
        history,
        annual_totals,
        forecast,
        wastewater_draws,
        flow_values,
        args.results_dir,
        args.figures_dir,
        rng,
    )
    key_results = {"q1": q1, "q2": q2, "q3": q3, "q4": q4}
    write_json(args.results_dir / "key_results.json", key_results)
    generate_paper_artifacts(args.paper_dir, args.results_dir, q1, q2, q3, q4)
    verification = build_verification(q1, q2, q3, q4, args.results_dir)
    write_json(args.results_dir / "verification.json", verification)
    print(json.dumps({"key_results": key_results, "verification": verification}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
