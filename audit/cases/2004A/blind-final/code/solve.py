from __future__ import annotations

import argparse
import json
import math
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from scipy.sparse.csgraph import dijkstra
from scipy.stats import chi2_contingency, spearmanr


SEED = 2004
AUDIENCE_PER_ZONE = 10_000
AUDIENCE_TOTAL = 200_000
BASE_CAPTURE_RATE = 0.50
SMALL_CAPACITY = 2_000
LARGE_CAPACITY = 4_000
SMALL_RELATIVE_COST = 1.0
LARGE_RELATIVE_COST = 1.7
SMALL_DAILY_COST_YUAN = 30_000
LARGE_DAILY_COST_YUAN = 50_000
GROSS_MARGIN = 0.25
MAP_JITTER_SD_PX = 4.0

TRAVEL = ["bus_ns", "bus_ew", "taxi", "car", "metro_east", "metro_west"]
FOOD = ["chinese_food", "western_food", "mall_food"]
EXPECTED_COLUMNS = [
    "no",
    "gender",
    "age_group",
    *TRAVEL,
    *FOOD,
    "spend_group",
]

TRAVEL_LABELS = {
    "bus_ns": "南北向公交",
    "bus_ew": "东西向公交",
    "taxi": "出租车",
    "car": "私车",
    "metro_east": "地铁东",
    "metro_west": "地铁西",
}
FOOD_LABELS = {
    "chinese_food": "中餐",
    "western_food": "西餐",
    "mall_food": "商场餐饮",
}
DEST_LABELS = {
    "bus_west": "公交西",
    "bus_east": "公交东",
    "bus_south": "公交南",
    "taxi": "出租",
    "car": "私车",
    "metro_east": "地铁东",
    "metro_west": "地铁西",
    "chinese_food": "中餐",
    "western_food": "西餐",
    "mall_food": "商场",
}

# Pixel coordinates digitized from Figure 2.  The order is clockwise on each venue ring.
RINGS: dict[str, list[tuple[str, tuple[float, float]]]] = {
    "A": [
        ("A1", (374, 221)),
        ("A2", (417, 221)),
        ("A3", (458, 221)),
        ("A4", (499, 221)),
        ("A5", (510, 260)),
        ("A6", (510, 294)),
        ("A7", (460, 306)),
        ("A8", (418, 306)),
        ("A9", (375, 306)),
        ("A10", (365, 260)),
    ],
    "B": [
        ("B1", (132, 221)),
        ("B2", (181, 214)),
        ("B3", (229, 221)),
        ("B4", (235, 286)),
        ("B5", (183, 295)),
        ("B6", (130, 286)),
    ],
    "C": [
        ("C1", (139, 131)),
        ("C2", (197, 131)),
        ("C3", (197, 168)),
        ("C4", (139, 168)),
    ],
}

DESTINATIONS: dict[str, tuple[float, float]] = {
    "bus_west": (12, 30),
    "bus_east": (76, 30),
    "car": (12, 132),
    "taxi": (552, 62),
    "bus_south": (523, 372),
    "metro_east": (632, 374),
    "metro_west": (15, 374),
    "chinese_food": (503, 167),
    "western_food": (319, 377),
    "mall_food": (103, 377),
}

CATEGORY_DESTINATIONS: dict[str, list[tuple[str, float]]] = {
    "bus_ns": [("bus_west", 0.5), ("bus_east", 0.5)],
    "bus_ew": [("bus_south", 1.0)],
    "taxi": [("taxi", 1.0)],
    "car": [("car", 1.0)],
    "metro_east": [("metro_east", 1.0)],
    "metro_west": [("metro_west", 1.0)],
    "chinese_food": [("chinese_food", 1.0)],
    "western_food": [("western_food", 1.0)],
    "mall_food": [("mall_food", 1.0)],
}

ZONE_ORDER = [name for ring in RINGS.values() for name, _ in ring]
ZONE_INDEX = {name: i for i, name in enumerate(ZONE_ORDER)}
SPEND_MIDPOINTS = np.array([50, 150, 250, 350, 450, 550], dtype=float)


@dataclass
class RouteMatrices:
    travel_visits: np.ndarray
    food_visits: np.ndarray
    transaction_choice: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Blind solution pipeline for CUMCM 2004 A.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--bootstrap", type=int, default=500)
    return parser.parse_args()


def status(condition: bool) -> str:
    return "pass" if bool(condition) else "fail"


def json_dump(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def wilson_interval(count: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    p = count / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return center - half, center + half


def cramers_v(table: pd.DataFrame) -> tuple[float, float, float, int]:
    chi2, p_value, dof, _ = chi2_contingency(table)
    n = table.to_numpy().sum()
    denominator = n * min(table.shape[0] - 1, table.shape[1] - 1)
    value = math.sqrt(chi2 / denominator) if denominator > 0 else 0.0
    return float(value), float(p_value), float(chi2), int(dof)


def load_and_audit(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    frames: list[pd.DataFrame] = []
    audit_rows: list[dict[str, object]] = []
    for survey, filename in enumerate(["<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>"], start=1):
        path = data_dir / filename
        frame = pd.read_csv(path, encoding="utf-8-sig")
        schema_ok = list(frame.columns) == EXPECTED_COLUMNS
        frame["survey"] = survey
        frames.append(frame)
        audit_rows.append(
            {
                "survey": survey,
                "file": filename,
                "rows": int(len(frame)),
                "schema_status": status(schema_ok),
                "unique_id_status": status(frame["no"].nunique() == len(frame)),
            }
        )

    data = pd.concat(frames, ignore_index=True)
    travel_sum = data[TRAVEL].sum(axis=1)
    food_sum = data[FOOD].sum(axis=1)
    checks = {
        "schema_status": status(all(row["schema_status"] == "pass" for row in audit_rows)),
        "missing_status": status(int(data.isna().sum().sum()) == 0),
        "travel_one_hot_status": status(bool((travel_sum == 1).all())),
        "food_one_hot_status": status(bool((food_sum == 1).all())),
        "gender_domain_status": status(set(data["gender"]) == {"男", "女"}),
        "age_domain_status": status(set(data["age_group"]) == {1, 2, 3, 4}),
        "spend_domain_status": status(set(data["spend_group"]) == {1, 2, 3, 4, 5, 6}),
    }
    overall = all(value == "pass" for value in checks.values()) and all(
        row["unique_id_status"] == "pass" for row in audit_rows
    )
    audit: dict[str, object] = {
        "status": status(overall),
        "rows": int(len(data)),
        "columns": int(len(EXPECTED_COLUMNS)),
        "missing_cells": int(data.isna().sum().sum()),
        "surveys": audit_rows,
        "checks": checks,
    }
    if not overall:
        raise ValueError("Input audit failed; see data-audit output.")

    data["travel"] = np.array(TRAVEL, dtype=object)[np.argmax(data[TRAVEL].to_numpy(), axis=1)]
    data["food"] = np.array(FOOD, dtype=object)[np.argmax(data[FOOD].to_numpy(), axis=1)]
    data["spend_midpoint"] = SPEND_MIDPOINTS[data["spend_group"].to_numpy(dtype=int) - 1]
    return data, audit


def build_marginals(data: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    specifications: list[tuple[str, Iterable[object]]] = [
        ("gender", ["男", "女"]),
        ("age_group", [1, 2, 3, 4]),
        ("travel", TRAVEL),
        ("food", FOOD),
        ("spend_group", [1, 2, 3, 4, 5, 6]),
    ]
    marginal_rows: list[dict[str, object]] = []
    stability_rows: list[dict[str, object]] = []
    for variable, levels in specifications:
        for wave in ["pooled", 1, 2, 3]:
            subset = data if wave == "pooled" else data[data["survey"] == wave]
            total = len(subset)
            counts = subset[variable].value_counts()
            for level in levels:
                count = int(counts.get(level, 0))
                low, high = wilson_interval(count, total)
                marginal_rows.append(
                    {
                        "variable": variable,
                        "level": str(level),
                        "survey": str(wave),
                        "count": count,
                        "total": total,
                        "proportion": count / total,
                        "ci95_low": low,
                        "ci95_high": high,
                    }
                )
        table = pd.crosstab(data[variable], data["survey"])
        effect, p_value, chi2, dof = cramers_v(table)
        stability_rows.append(
            {
                "variable": variable,
                "chi_square": chi2,
                "dof": dof,
                "p_value": p_value,
                "cramers_v": effect,
                "stability_status": "pass" if effect < 0.10 else "needs_review",
            }
        )

    pattern_rows: list[dict[str, object]] = []
    for variable in ["survey", "gender", "age_group", "travel", "food"]:
        grouped = data.groupby(variable, observed=False)["spend_midpoint"]
        for level, values in grouped:
            n = int(values.count())
            mean = float(values.mean())
            std = float(values.std(ddof=1))
            half = 1.959963984540054 * std / math.sqrt(n)
            pattern_rows.append(
                {
                    "variable": variable,
                    "level": str(level),
                    "count": n,
                    "mean_spend_yuan": mean,
                    "std_spend_yuan": std,
                    "mean_ci95_low": mean - half,
                    "mean_ci95_high": mean + half,
                }
            )
    return pd.DataFrame(marginal_rows), pd.DataFrame(stability_rows), pd.DataFrame(pattern_rows)


def perturbed_geometry(
    rng: np.random.Generator | None, jitter_sd: float
) -> tuple[dict[str, list[tuple[str, tuple[float, float]]]], dict[str, tuple[float, float]]]:
    rings: dict[str, list[tuple[str, tuple[float, float]]]] = {}
    for venue, ring in RINGS.items():
        rings[venue] = []
        for name, point in ring:
            shift = np.zeros(2) if rng is None or jitter_sd == 0 else rng.normal(0, jitter_sd, 2)
            rings[venue].append((name, tuple(np.asarray(point, dtype=float) + shift)))
    destinations: dict[str, tuple[float, float]] = {}
    for name, point in DESTINATIONS.items():
        shift = np.zeros(2) if rng is None or jitter_sd == 0 else rng.normal(0, jitter_sd, 2)
        destinations[name] = tuple(np.asarray(point, dtype=float) + shift)
    return rings, destinations


def merge_path_options(options: list[tuple[tuple[str, ...], float]]) -> list[tuple[tuple[str, ...], float]]:
    merged: dict[tuple[str, ...], float] = {}
    for path, probability in options:
        merged[path] = merged.get(path, 0.0) + probability
    total = sum(merged.values())
    return [(path, probability / total) for path, probability in merged.items()]


def nearest_anchors(
    ring: list[tuple[str, tuple[float, float]]], destination: tuple[float, float]
) -> list[tuple[str, float]]:
    distances = np.array([math.dist(point, destination) for _, point in ring])
    minimum = float(distances.min())
    candidates = np.flatnonzero(np.isclose(distances, minimum, rtol=0, atol=1e-10))
    return [(ring[int(i)][0], 1 / len(candidates)) for i in candidates]


def cycle_path_options(
    ring: list[tuple[str, tuple[float, float]]], source: str, target: str, model: str
) -> list[tuple[tuple[str, ...], float]]:
    if model == "gate_only":
        return [((source,), 1.0)]
    names = [name for name, _ in ring]
    coordinates = dict(ring)
    n = len(names)
    source_index = names.index(source)
    target_index = names.index(target)

    paths: list[tuple[tuple[str, ...], float]] = []
    lengths: list[float] = []
    for direction in [1, -1]:
        current = source_index
        path = [names[current]]
        length = 0.0
        while current != target_index:
            following = (current + direction) % n
            if model == "unweighted":
                length += 1.0
            else:
                length += math.dist(coordinates[names[current]], coordinates[names[following]])
            current = following
            path.append(names[current])
        paths.append((tuple(path), 1.0))
        lengths.append(length)
    if math.isclose(lengths[0], lengths[1], rel_tol=0, abs_tol=1e-10):
        return [(paths[0][0], 0.5), (paths[1][0], 0.5)]
    return [paths[int(np.argmin(lengths))]]


def category_path_options(
    ring: list[tuple[str, tuple[float, float]]],
    source: str,
    category: str,
    destinations: dict[str, tuple[float, float]],
    model: str,
) -> list[tuple[tuple[str, ...], float]]:
    if model == "gate_only":
        return [((source,), 1.0)]
    options: list[tuple[tuple[str, ...], float]] = []
    for destination_name, destination_weight in CATEGORY_DESTINATIONS[category]:
        for anchor, anchor_weight in nearest_anchors(ring, destinations[destination_name]):
            for path, path_weight in cycle_path_options(ring, source, anchor, model):
                options.append((path, destination_weight * anchor_weight * path_weight))
    return merge_path_options(options)


def build_route_matrices(
    model: str,
    rng: np.random.Generator | None = None,
    jitter_sd: float = 0.0,
) -> RouteMatrices:
    rings, destinations = perturbed_geometry(rng, jitter_sd)
    travel_visits = np.zeros((len(TRAVEL), len(ZONE_ORDER)), dtype=float)
    food_visits = np.zeros((len(FOOD), len(ZONE_ORDER)), dtype=float)
    transaction_choice = np.zeros((len(TRAVEL), len(FOOD), len(ZONE_ORDER)), dtype=float)

    for ring in rings.values():
        for source, _ in ring:
            travel_options = {
                category: category_path_options(ring, source, category, destinations, model)
                for category in TRAVEL
            }
            food_options = {
                category: category_path_options(ring, source, category, destinations, model)
                for category in FOOD
            }
            for ti, category in enumerate(TRAVEL):
                for path, probability in travel_options[category]:
                    for zone in path:
                        travel_visits[ti, ZONE_INDEX[zone]] += probability
            for fi, category in enumerate(FOOD):
                for path, probability in food_options[category]:
                    for zone in path:
                        food_visits[fi, ZONE_INDEX[zone]] += probability
            for ti, travel_category in enumerate(TRAVEL):
                for fi, food_category in enumerate(FOOD):
                    for travel_path, travel_weight in travel_options[travel_category]:
                        for food_path, food_weight in food_options[food_category]:
                            exposure = np.zeros(len(ZONE_ORDER), dtype=float)
                            for zone in travel_path:
                                exposure[ZONE_INDEX[zone]] += 1
                            for zone in food_path:
                                exposure[ZONE_INDEX[zone]] += 1
                            exposure /= exposure.sum()
                            transaction_choice[ti, fi] += travel_weight * food_weight * exposure

    return RouteMatrices(travel_visits, food_visits, transaction_choice)


def empirical_parameters(
    travel_codes: np.ndarray, food_codes: np.ndarray, spend: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    n = len(travel_codes)
    p_travel = np.bincount(travel_codes, minlength=len(TRAVEL)) / n
    p_food = np.bincount(food_codes, minlength=len(FOOD)) / n
    joint_counts = np.zeros((len(TRAVEL), len(FOOD)), dtype=float)
    joint_spend = np.zeros((len(TRAVEL), len(FOOD)), dtype=float)
    np.add.at(joint_counts, (travel_codes, food_codes), 1)
    np.add.at(joint_spend, (travel_codes, food_codes), spend)
    return p_travel, p_food, joint_counts / n, joint_spend / n, float(spend.mean())


def compute_metrics(
    matrices: RouteMatrices,
    parameters: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float],
) -> dict[str, np.ndarray | float]:
    p_travel, p_food, joint, joint_spend, mean_spend = parameters
    travel_flow = AUDIENCE_PER_ZONE * (p_travel @ matrices.travel_visits)
    food_flow = AUDIENCE_PER_ZONE * (p_food @ matrices.food_visits)
    footfall = travel_flow + food_flow
    transaction_share = np.einsum("tf,tfz->z", joint, matrices.transaction_choice) / 20.0
    revenue_per_attendee = np.einsum("tf,tfz->z", joint_spend, matrices.transaction_choice) / 20.0
    revenue_potential = AUDIENCE_TOTAL * revenue_per_attendee
    return {
        "travel_flow": travel_flow,
        "food_flow": food_flow,
        "footfall": footfall,
        "footfall_share": footfall / footfall.sum(),
        "transaction_share": transaction_share / transaction_share.sum(),
        "revenue_potential": revenue_potential,
        "revenue_share": revenue_potential / revenue_potential.sum(),
        "mean_spend": mean_spend,
    }


def route_anchor_table() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for venue, ring in RINGS.items():
        for destination_name, destination in DESTINATIONS.items():
            for anchor, weight in nearest_anchors(ring, destination):
                point = dict(ring)[anchor]
                rows.append(
                    {
                        "venue": venue,
                        "destination": destination_name,
                        "destination_label": DEST_LABELS[destination_name],
                        "anchor_zone": anchor,
                        "anchor_weight": weight,
                        "straight_distance_px": math.dist(point, destination),
                    }
                )
    return pd.DataFrame(rows)


def validate_cycle_distances() -> tuple[str, float]:
    maximum_error = 0.0
    for ring in RINGS.values():
        names = [name for name, _ in ring]
        coordinates = dict(ring)
        n = len(names)
        adjacency = np.full((n, n), np.inf)
        np.fill_diagonal(adjacency, 0.0)
        for i in range(n):
            j = (i + 1) % n
            weight = math.dist(coordinates[names[i]], coordinates[names[j]])
            adjacency[i, j] = adjacency[j, i] = weight
        independent = dijkstra(adjacency, directed=False)
        for i, source in enumerate(names):
            for j, target in enumerate(names):
                options = []
                for direction in [1, -1]:
                    current = i
                    length = 0.0
                    while current != j:
                        following = (current + direction) % n
                        length += adjacency[current, following]
                        current = following
                    options.append(length)
                maximum_error = max(maximum_error, abs(min(options) - independent[i, j]))
    return status(maximum_error < 1e-9), maximum_error


def bootstrap_selected_model(
    data: pd.DataFrame, replicates: int, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    travel_codes_all = pd.Categorical(data["travel"], categories=TRAVEL).codes
    food_codes_all = pd.Categorical(data["food"], categories=FOOD).codes
    spend_all = data["spend_midpoint"].to_numpy(dtype=float)
    wave_indices = [np.flatnonzero(data["survey"].to_numpy() == wave) for wave in [1, 2, 3]]
    foot = np.zeros((replicates, len(ZONE_ORDER)))
    transaction = np.zeros_like(foot)
    revenue = np.zeros_like(foot)
    top_five = np.zeros_like(foot)
    for replicate in range(replicates):
        sample = np.concatenate([rng.choice(index, size=len(index), replace=True) for index in wave_indices])
        parameters = empirical_parameters(
            travel_codes_all[sample], food_codes_all[sample], spend_all[sample]
        )
        matrices = build_route_matrices("weighted", rng=rng, jitter_sd=MAP_JITTER_SD_PX)
        metrics = compute_metrics(matrices, parameters)
        foot[replicate] = metrics["footfall_share"]
        transaction[replicate] = metrics["transaction_share"]
        revenue[replicate] = metrics["revenue_share"]
        top_five[replicate, np.argsort(foot[replicate])[-5:]] = 1
    return foot, transaction, revenue, top_five.mean(axis=0)


def optimize_zone(demand: float, small_capacity: float, large_capacity: float) -> tuple[int, int, float]:
    if not math.isfinite(demand) or demand < 0:
        raise ValueError("Demand must be finite and nonnegative.")
    if not math.isfinite(small_capacity) or not math.isfinite(large_capacity):
        raise ValueError("Capacities must be finite.")
    if small_capacity <= 0 or large_capacity <= 0:
        raise ValueError("Capacities must be positive.")

    # The V1 implementation used a fixed 0--20 enumeration bound.  That is
    # sufficient for the nominal case but silently makes larger stress cases
    # infeasible.  Bounds derived from demand preserve exhaustive enumeration
    # while covering every finite nonnegative input.
    max_small = max(1, math.ceil(demand / small_capacity))
    max_large = max(1, math.ceil(demand / large_capacity))
    best: tuple[tuple[float, float, int], int, int, float] | None = None
    for small in range(0, max_small + 1):
        for large in range(0, max_large + 1):
            if small + large == 0:
                continue
            capacity = small * small_capacity + large * large_capacity
            if capacity + 1e-9 < demand:
                continue
            objective = (
                small * SMALL_RELATIVE_COST + large * LARGE_RELATIVE_COST,
                capacity - demand,
                small + large,
            )
            if best is None or objective < best[0]:
                best = (objective, small, large, capacity)
    if best is None:
        raise RuntimeError("Allocation enumeration bound is insufficient.")
    return best[1], best[2], best[3]


def build_allocation(
    transaction_share: np.ndarray,
    transaction_upper: np.ndarray,
    revenue_potential: np.ndarray,
    capture_rate: float = BASE_CAPTURE_RATE,
    capacity_factor: float = 1.0,
) -> pd.DataFrame:
    if not 0 <= capture_rate <= 1:
        raise ValueError("Capture rate must lie in [0, 1].")
    if not math.isfinite(capacity_factor) or capacity_factor <= 0:
        raise ValueError("Capacity factor must be finite and positive.")
    rows: list[dict[str, object]] = []
    for i, zone in enumerate(ZONE_ORDER):
        baseline_demand = AUDIENCE_TOTAL * capture_rate * transaction_share[i]
        planning_demand = math.ceil(AUDIENCE_TOTAL * capture_rate * transaction_upper[i])
        small, large, capacity = optimize_zone(
            planning_demand,
            SMALL_CAPACITY * capacity_factor,
            LARGE_CAPACITY * capacity_factor,
        )
        operating_cost = small * SMALL_DAILY_COST_YUAN + large * LARGE_DAILY_COST_YUAN
        captured_revenue = capture_rate * revenue_potential[i]
        scenario_profit = GROSS_MARGIN * captured_revenue - operating_cost
        break_even_capture = operating_cost / (GROSS_MARGIN * revenue_potential[i])
        rows.append(
            {
                "zone": zone,
                "baseline_checkout_demand": baseline_demand,
                "planning_checkout_demand": planning_demand,
                "small_ms": small,
                "large_ms": large,
                "service_capacity": capacity,
                "capacity_utilization_baseline": baseline_demand / capacity,
                "full_capture_revenue_potential_yuan": revenue_potential[i],
                "captured_revenue_yuan": captured_revenue,
                "daily_operating_cost_yuan": operating_cost,
                "scenario_profit_yuan": scenario_profit,
                "break_even_capture_rate": break_even_capture,
                "service_status": status(capacity >= planning_demand),
                "profit_status": status(scenario_profit > 0),
            }
        )
    return pd.DataFrame(rows)


def allocation_scenarios(
    transaction_share: np.ndarray,
    transaction_upper: np.ndarray,
    revenue_potential: np.ndarray,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for capture_rate in [0.30, 0.50, 0.70]:
        for capacity_factor in [0.80, 1.00, 1.20]:
            allocation = build_allocation(
                transaction_share,
                transaction_upper,
                revenue_potential,
                capture_rate,
                capacity_factor,
            )
            rows.append(
                {
                    "capture_rate": capture_rate,
                    "capacity_factor": capacity_factor,
                    "small_ms_total": int(allocation["small_ms"].sum()),
                    "large_ms_total": int(allocation["large_ms"].sum()),
                    "capacity_total": float(allocation["service_capacity"].sum()),
                    "scenario_profit_total_yuan": float(allocation["scenario_profit_yuan"].sum()),
                    "min_baseline_utilization": float(allocation["capacity_utilization_baseline"].min()),
                    "max_baseline_utilization": float(allocation["capacity_utilization_baseline"].max()),
                    "service_status": status(bool((allocation["service_status"] == "pass").all())),
                    "profit_status": status(bool((allocation["profit_status"] == "pass").all())),
                }
            )
    return pd.DataFrame(rows)


def configure_plotting() -> None:
    plt.rcParams.update(
        {
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.dpi": 120,
            "savefig.dpi": 220,
        }
    )


def save_survey_figure(marginals: pd.DataFrame, output: Path) -> None:
    configure_plotting()
    panels = [
        ("travel", TRAVEL, TRAVEL_LABELS, "出行方式"),
        ("food", FOOD, FOOD_LABELS, "餐饮方式"),
        ("spend_group", [str(i) for i in range(1, 7)], {str(i): f"第{i}档" for i in range(1, 7)}, "非餐饮消费额"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(14, 5.2))
    pooled = marginals[marginals["survey"] == "pooled"]
    for axis, (variable, levels, labels, title) in zip(axes, panels):
        level_strings = [str(level) for level in levels]
        subset = pooled[pooled["variable"] == variable].set_index("level").loc[level_strings]
        values = 100 * subset["proportion"].to_numpy()
        errors = np.vstack(
            [
                values - 100 * subset["ci95_low"].to_numpy(),
                100 * subset["ci95_high"].to_numpy() - values,
            ]
        )
        positions = np.arange(len(level_strings))
        axis.barh(positions, values, xerr=errors, color="#3b82b6", alpha=0.86, capsize=2)
        axis.set_yticks(positions, [labels[level] for level in level_strings])
        axis.invert_yaxis()
        axis.set_xlabel("比例（%）")
        axis.set_title(title)
        axis.grid(axis="x", alpha=0.25)
        for y, value in zip(positions, values):
            axis.text(value + 0.8, y, f"{value:.1f}", va="center", fontsize=8)
    fig.suptitle("三次调查合并后的观众行为分布（误差线为 95% Wilson 区间）")
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_route_figure(anchors: pd.DataFrame, output: Path) -> None:
    configure_plotting()
    fig, axis = plt.subplots(figsize=(12, 7))
    colors = {"A": "#e78b2f", "B": "#d65f5f", "C": "#5b8fd1"}
    for venue, ring in RINGS.items():
        points = np.array([point for _, point in ring] + [ring[0][1]])
        axis.plot(points[:, 0], points[:, 1], color=colors[venue], linewidth=2.2)
        for name, point in ring:
            axis.scatter(*point, s=42, color=colors[venue], zorder=4)
            axis.text(point[0], point[1] - 8, name, ha="center", va="bottom", fontsize=8)
    for destination_name, point in DESTINATIONS.items():
        is_food = destination_name in FOOD
        marker = "s" if is_food else "^"
        color = "#b33b3b" if is_food else "#2b6cb0"
        axis.scatter(*point, marker=marker, s=80, color=color, zorder=5)
        axis.text(point[0] + 7, point[1], DEST_LABELS[destination_name], va="center", fontsize=8)
    for _, row in anchors.iterrows():
        destination = DESTINATIONS[str(row["destination"])]
        zone_point = dict(RINGS[str(row["venue"])])[str(row["anchor_zone"])]
        axis.plot(
            [destination[0], zone_point[0]],
            [destination[1], zone_point[1]],
            linestyle=":",
            linewidth=0.45,
            color="#777777",
            alpha=0.42,
        )
    axis.set_xlim(0, 660)
    axis.set_ylim(400, 0)
    axis.set_aspect("equal")
    axis.set_xlabel("图 2 横向像素坐标")
    axis.set_ylabel("图 2 纵向像素坐标")
    axis.set_title("场馆环路、外部设施与最近接入商区的数字化示意")
    axis.grid(alpha=0.15)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def zone_colors() -> list[str]:
    return ["#e78b2f" if zone.startswith("A") else "#d65f5f" if zone.startswith("B") else "#5b8fd1" for zone in ZONE_ORDER]


def save_flow_figure(flow: pd.DataFrame, output: Path) -> None:
    configure_plotting()
    fig, axis = plt.subplots(figsize=(13, 5.4))
    x = np.arange(len(flow))
    values = 100 * flow["footfall_share"].to_numpy()
    low = 100 * flow["footfall_ci95_low"].to_numpy()
    high = 100 * flow["footfall_ci95_high"].to_numpy()
    axis.bar(x, values, color=zone_colors(), alpha=0.88)
    axis.errorbar(x, values, yerr=np.vstack([values - low, high - values]), fmt="none", color="black", capsize=2, linewidth=0.8)
    axis.axhline(5.0, color="#333333", linestyle="--", linewidth=1.2, label="仅计本出口的均匀基线 5%")
    axis.set_xticks(x, flow["zone"], rotation=45)
    axis.set_ylabel("商区过流占比（%）")
    axis.set_title("加权环路最短路的人流分布")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_allocation_figure(flow: pd.DataFrame, allocation: pd.DataFrame, output: Path) -> None:
    configure_plotting()
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    x = np.arange(len(flow))
    axes[0].plot(x, 100 * flow["transaction_share"], marker="o", label="潜在交易份额")
    axes[0].plot(x, 100 * flow["revenue_share"], marker="s", label="消费额加权份额")
    axes[0].set_ylabel("份额（%）")
    axes[0].set_title("同质网点下的交易选择与消费额潜力")
    axes[0].grid(alpha=0.25)
    axes[0].legend()

    demand = allocation["planning_checkout_demand"].to_numpy() / 1000
    capacity = allocation["service_capacity"].to_numpy() / 1000
    axes[1].bar(x - 0.2, demand, width=0.4, color="#7a9cc6", label="规划需求（千次）")
    axes[1].bar(x + 0.2, capacity, width=0.4, color="#df9b49", label="服务能力（千次）")
    for i, row in allocation.iterrows():
        axes[1].text(
            i,
            max(demand[i], capacity[i]) + 0.25,
            f"{int(row['small_ms'])}S+{int(row['large_ms'])}L",
            ha="center",
            fontsize=7,
            rotation=45,
        )
    axes[1].set_ylabel("结账服务量（千次/日）")
    axes[1].set_xticks(x, flow["zone"], rotation=45)
    axes[1].grid(axis="y", alpha=0.25)
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def save_sensitivity_figure(
    selected: np.ndarray,
    unweighted: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    output: Path,
) -> None:
    configure_plotting()
    fig, axis = plt.subplots(figsize=(13, 5.4))
    x = np.arange(len(ZONE_ORDER))
    axis.fill_between(x, 100 * lower, 100 * upper, color="#9ecae1", alpha=0.45, label="抽样 + 地图扰动 95% 区间")
    axis.plot(x, 100 * selected, marker="o", color="#1f5a85", label="像素加权环路")
    axis.plot(x, 100 * unweighted, marker="x", linestyle="--", color="#b24d3e", label="等边环路")
    axis.set_xticks(x, ZONE_ORDER, rotation=45)
    axis.set_ylabel("人流份额（%）")
    axis.set_title("结构与输入扰动的敏感性")
    axis.grid(alpha=0.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def latex_escape(value: object) -> str:
    return str(value).replace("_", r"\_").replace("%", r"\%")


def write_generated_tables(
    table_dir: Path,
    marginals: pd.DataFrame,
    flow: pd.DataFrame,
    allocation: pd.DataFrame,
) -> None:
    table_dir.mkdir(parents=True, exist_ok=True)
    pooled = marginals[marginals["survey"] == "pooled"].copy()
    key_levels = [("travel", level, TRAVEL_LABELS[level]) for level in TRAVEL] + [
        ("food", level, FOOD_LABELS[level]) for level in FOOD
    ]
    lines = [
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"类别 & 样本数 & 比例（\%） & 95\%区间（\%） \\",
        r"\midrule",
    ]
    for variable, level, label in key_levels:
        row = pooled[(pooled["variable"] == variable) & (pooled["level"] == str(level))].iloc[0]
        lines.append(
            f"{label} & {int(row['count'])} & {100*row['proportion']:.2f} & "
            f"[{100*row['ci95_low']:.2f}, {100*row['ci95_high']:.2f}] " + r"\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}"]
    (table_dir / "survey-key.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")

    lines = [
        r"\begin{longtable}{lrrrrrr}",
        r"\toprule",
        r"商区 & 过流（万人次） & 份额（\%） & 95\%区间（\%） & 交易份额（\%） & 小型 & 大型 \\",
        r"\midrule",
        r"\endfirsthead",
        r"\toprule",
        r"商区 & 过流（万人次） & 份额（\%） & 95\%区间（\%） & 交易份额（\%） & 小型 & 大型 \\",
        r"\midrule",
        r"\endhead",
    ]
    merged = flow.merge(allocation[["zone", "small_ms", "large_ms"]], on="zone")
    for _, row in merged.iterrows():
        lines.append(
            f"{row['zone']} & {row['footfall']/10000:.3f} & {100*row['footfall_share']:.3f} & "
            f"[{100*row['footfall_ci95_low']:.3f}, {100*row['footfall_ci95_high']:.3f}] & "
            f"{100*row['transaction_share']:.3f} & {int(row['small_ms'])} & {int(row['large_ms'])} " + r"\\"
        )
    lines += [r"\bottomrule", r"\end{longtable}"]
    (table_dir / "flow-allocation.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")

    markdown_lines = [
        "| 商区 | 过流（万人次） | 份额（%） | 95%区间（%） | 交易份额（%） | 小型 | 大型 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in merged.iterrows():
        markdown_lines.append(
            f"| {row['zone']} | {row['footfall']/10000:.3f} | {100*row['footfall_share']:.3f} | "
            f"[{100*row['footfall_ci95_low']:.3f}, {100*row['footfall_ci95_high']:.3f}] | "
            f"{100*row['transaction_share']:.3f} | {int(row['small_ms'])} | {int(row['large_ms'])} |"
        )
    (table_dir / "flow-allocation.md").write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.bootstrap < 1:
        raise ValueError("Bootstrap replicate count must be positive.")
    output = args.output_dir.resolve()
    figures = args.figure_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    table_dir = output / "tables"

    data, audit = load_and_audit(args.data_dir.resolve())
    json_dump(output / "data-audit.json", audit)
    marginals, stability, spending_patterns = build_marginals(data)
    marginals.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")
    stability.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")
    spending_patterns.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    travel_codes = pd.Categorical(data["travel"], categories=TRAVEL).codes
    food_codes = pd.Categorical(data["food"], categories=FOOD).codes
    spend = data["spend_midpoint"].to_numpy(dtype=float)
    parameters = empirical_parameters(travel_codes, food_codes, spend)

    models = {
        "gate_only": build_route_matrices("gate_only"),
        "unweighted": build_route_matrices("unweighted"),
        "weighted": build_route_matrices("weighted"),
    }
    model_metrics = {name: compute_metrics(matrix, parameters) for name, matrix in models.items()}
    selected = model_metrics["weighted"]

    anchors = route_anchor_table()
    anchors.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    bootstrap_foot, bootstrap_transaction, bootstrap_revenue, top_five = bootstrap_selected_model(
        data, args.bootstrap, args.seed
    )
    foot_low, foot_high = np.quantile(bootstrap_foot, [0.025, 0.975], axis=0)
    transaction_low, transaction_high = np.quantile(bootstrap_transaction, [0.025, 0.975], axis=0)
    revenue_low, revenue_high = np.quantile(bootstrap_revenue, [0.025, 0.975], axis=0)

    flow = pd.DataFrame(
        {
            "zone": ZONE_ORDER,
            "travel_flow": selected["travel_flow"],
            "food_flow": selected["food_flow"],
            "footfall": selected["footfall"],
            "footfall_share": selected["footfall_share"],
            "footfall_ci95_low": foot_low,
            "footfall_ci95_high": foot_high,
            "transaction_share": selected["transaction_share"],
            "transaction_ci95_low": transaction_low,
            "transaction_ci95_high": transaction_high,
            "revenue_share": selected["revenue_share"],
            "revenue_ci95_low": revenue_low,
            "revenue_ci95_high": revenue_high,
            "full_capture_revenue_potential_yuan": selected["revenue_potential"],
            "top_five_frequency": top_five,
        }
    )
    flow.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    comparison_rows: list[dict[str, object]] = []
    match_scores = {"gate_only": 1, "unweighted": 2, "weighted": 3}
    for name, metrics in model_metrics.items():
        if float(np.std(metrics["footfall_share"])) < 1e-12:
            correlation = float("nan")
        else:
            correlation = spearmanr(
                selected["footfall_share"], metrics["footfall_share"]
            ).statistic
        comparison_rows.append(
            {
                "candidate": name,
                "selection_status": "pass" if name == "weighted" else "fail",
                "assumption_match_score": match_scores[name],
                "flow_cv": float(np.std(metrics["footfall_share"]) / np.mean(metrics["footfall_share"])),
                "l1_distance_from_selected": float(np.abs(metrics["footfall_share"] - selected["footfall_share"]).sum()),
                "max_abs_difference_percentage_points": float(
                    100 * np.abs(metrics["footfall_share"] - selected["footfall_share"]).max()
                ),
                "spearman_with_selected": None if np.isnan(correlation) else float(correlation),
            }
        )
    comparison = pd.DataFrame(comparison_rows)
    comparison.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    wave_rows: list[dict[str, object]] = []
    for wave in [1, 2, 3]:
        subset = data[data["survey"] == wave]
        wave_parameters = empirical_parameters(
            pd.Categorical(subset["travel"], categories=TRAVEL).codes,
            pd.Categorical(subset["food"], categories=FOOD).codes,
            subset["spend_midpoint"].to_numpy(dtype=float),
        )
        wave_metrics = compute_metrics(models["weighted"], wave_parameters)
        wave_rows.append(
            {
                "survey": wave,
                "max_footfall_deviation_percentage_points": float(
                    100 * np.abs(wave_metrics["footfall_share"] - selected["footfall_share"]).max()
                ),
                "max_transaction_deviation_percentage_points": float(
                    100 * np.abs(wave_metrics["transaction_share"] - selected["transaction_share"]).max()
                ),
                "max_revenue_deviation_percentage_points": float(
                    100 * np.abs(wave_metrics["revenue_share"] - selected["revenue_share"]).max()
                ),
            }
        )
    wave_sensitivity = pd.DataFrame(wave_rows)
    wave_sensitivity.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    allocation = build_allocation(
        selected["transaction_share"], transaction_high, selected["revenue_potential"]
    )
    allocation.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")
    scenarios = allocation_scenarios(
        selected["transaction_share"], transaction_high, selected["revenue_potential"]
    )
    scenarios.to_csv(output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    spend_sensitivity_rows = []
    for top_midpoint in [550, 650, 800]:
        midpoints = SPEND_MIDPOINTS.copy()
        midpoints[-1] = top_midpoint
        alternative_spend = midpoints[data["spend_group"].to_numpy(dtype=int) - 1]
        alternative_parameters = empirical_parameters(travel_codes, food_codes, alternative_spend)
        alternative_metrics = compute_metrics(models["weighted"], alternative_parameters)
        spend_sensitivity_rows.append(
            {
                "top_bin_midpoint_yuan": top_midpoint,
                "mean_spend_yuan": float(alternative_spend.mean()),
                "full_capture_revenue_total_yuan": float(alternative_metrics["revenue_potential"].sum()),
                "max_revenue_share_change_percentage_points": float(
                    100 * np.abs(alternative_metrics["revenue_share"] - selected["revenue_share"]).max()
                ),
            }
        )
    pd.DataFrame(spend_sensitivity_rows).to_csv(
        output / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )

    dijkstra_status, dijkstra_error = validate_cycle_distances()
    share_errors = {
        "footfall_share": abs(float(np.sum(selected["footfall_share"])) - 1),
        "transaction_share": abs(float(np.sum(selected["transaction_share"])) - 1),
        "revenue_share": abs(float(np.sum(selected["revenue_share"])) - 1),
    }
    service_all_pass = bool((allocation["service_status"] == "pass").all())
    profit_all_pass = bool((allocation["profit_status"] == "pass").all())
    capacity_share = allocation["service_capacity"].to_numpy() / allocation["service_capacity"].sum()
    demand_share = allocation["planning_checkout_demand"].to_numpy() / allocation[
        "planning_checkout_demand"
    ].sum()
    balance_max_deviation_pp = float(100 * np.max(np.abs(capacity_share - demand_share)))
    validation = {
        "data_audit_status": audit["status"],
        "independent_dijkstra_status": dijkstra_status,
        "independent_dijkstra_max_abs_error": dijkstra_error,
        "share_conservation_status": status(max(share_errors.values()) < 1e-12),
        "share_conservation_errors": share_errors,
        "wave_stability_status": "pass"
        if float(stability["cramers_v"].max()) < 0.10
        else "needs_review",
        "allocation_service_status": status(service_all_pass),
        "allocation_balance_status": status(balance_max_deviation_pp <= 2.0),
        "allocation_balance_max_deviation_percentage_points": balance_max_deviation_pp,
        "profitability_scenario_status": status(profit_all_pass),
        "mathematical_correctness_status": "needs_review",
        "paper_format_status": "needs_review",
    }
    json_dump(output / "validation.json", validation)

    flow_rank = flow.sort_values("footfall_share", ascending=False)
    pooled_marginals = marginals[marginals["survey"] == "pooled"].set_index(["variable", "level"])
    summary = {
        "status": "pass",
        "seed": args.seed,
        "bootstrap_replicates": args.bootstrap,
        "sample_rows": int(len(data)),
        "survey_rows": data.groupby("survey").size().astype(int).to_dict(),
        "behavior": {
            "bus_total_proportion": float(
                pooled_marginals.loc[("travel", "bus_ns"), "proportion"]
                + pooled_marginals.loc[("travel", "bus_ew"), "proportion"]
            ),
            "metro_total_proportion": float(
                pooled_marginals.loc[("travel", "metro_east"), "proportion"]
                + pooled_marginals.loc[("travel", "metro_west"), "proportion"]
            ),
            "taxi_proportion": float(pooled_marginals.loc[("travel", "taxi"), "proportion"]),
            "car_proportion": float(pooled_marginals.loc[("travel", "car"), "proportion"]),
            "western_food_proportion": float(
                pooled_marginals.loc[("food", "western_food"), "proportion"]
            ),
            "chinese_food_proportion": float(
                pooled_marginals.loc[("food", "chinese_food"), "proportion"]
            ),
            "mall_food_proportion": float(
                pooled_marginals.loc[("food", "mall_food"), "proportion"]
            ),
            "mean_spend_yuan": float(selected["mean_spend"]),
            "top_spend_bin_proportion": float(
                pooled_marginals.loc[("spend_group", "6"), "proportion"]
            ),
            "max_wave_cramers_v": float(stability["cramers_v"].max()),
        },
        "flow": {
            "commercial_area_passages": float(selected["footfall"].sum()),
            "highest_zone": str(flow_rank.iloc[0]["zone"]),
            "highest_share": float(flow_rank.iloc[0]["footfall_share"]),
            "lowest_zone": str(flow_rank.iloc[-1]["zone"]),
            "lowest_share": float(flow_rank.iloc[-1]["footfall_share"]),
            "top_five_zones": flow_rank.head(5)["zone"].tolist(),
            "max_combined_ci_width_percentage_points": float(
                100 * np.max(foot_high - foot_low)
            ),
            "max_leave_one_wave_deviation_percentage_points": float(
                wave_sensitivity["max_footfall_deviation_percentage_points"].max()
            ),
            "unweighted_max_difference_percentage_points": float(
                comparison.loc[
                    comparison["candidate"] == "unweighted",
                    "max_abs_difference_percentage_points",
                ].iloc[0]
            ),
        },
        "allocation": {
            "capture_rate": BASE_CAPTURE_RATE,
            "small_capacity": SMALL_CAPACITY,
            "large_capacity": LARGE_CAPACITY,
            "small_ms_total": int(allocation["small_ms"].sum()),
            "large_ms_total": int(allocation["large_ms"].sum()),
            "service_capacity_total": float(allocation["service_capacity"].sum()),
            "baseline_checkout_demand_total": float(
                allocation["baseline_checkout_demand"].sum()
            ),
            "scenario_profit_total_yuan": float(allocation["scenario_profit_yuan"].sum()),
            "minimum_scenario_profit_yuan": float(allocation["scenario_profit_yuan"].min()),
            "maximum_break_even_capture_rate": float(allocation["break_even_capture_rate"].max()),
            "balance_max_deviation_percentage_points": balance_max_deviation_pp,
        },
        "validation": validation,
    }
    json_dump(output / "summary.json", summary)

    key_value_lines = [
        f"\\newcommand{{\\SampleN}}{{{len(data):,}}}",
        f"\\newcommand{{\\BusPct}}{{{100*summary['behavior']['bus_total_proportion']:.2f}\\%}}",
        f"\\newcommand{{\\MetroPct}}{{{100*summary['behavior']['metro_total_proportion']:.2f}\\%}}",
        f"\\newcommand{{\\TaxiPct}}{{{100*summary['behavior']['taxi_proportion']:.2f}\\%}}",
        f"\\newcommand{{\\CarPct}}{{{100*summary['behavior']['car_proportion']:.2f}\\%}}",
        f"\\newcommand{{\\WesternFoodPct}}{{{100*summary['behavior']['western_food_proportion']:.2f}\\%}}",
        f"\\newcommand{{\\MeanSpend}}{{{summary['behavior']['mean_spend_yuan']:.2f}}}",
        f"\\newcommand{{\\TotalPassagesWan}}{{{summary['flow']['commercial_area_passages']/10000:.2f}}}",
        f"\\newcommand{{\\HighestZone}}{{{summary['flow']['highest_zone']}}}",
        f"\\newcommand{{\\HighestShare}}{{{100*summary['flow']['highest_share']:.3f}\\%}}",
        f"\\newcommand{{\\LowestZone}}{{{summary['flow']['lowest_zone']}}}",
        f"\\newcommand{{\\LowestShare}}{{{100*summary['flow']['lowest_share']:.3f}\\%}}",
        f"\\newcommand{{\\SmallTotal}}{{{summary['allocation']['small_ms_total']}}}",
        f"\\newcommand{{\\LargeTotal}}{{{summary['allocation']['large_ms_total']}}}",
        f"\\newcommand{{\\CapacityWan}}{{{summary['allocation']['service_capacity_total']/10000:.2f}}}",
        f"\\newcommand{{\\ScenarioProfitWan}}{{{summary['allocation']['scenario_profit_total_yuan']/10000:.2f}}}",
        f"\\newcommand{{\\MaxCombinedWidth}}{{{summary['flow']['max_combined_ci_width_percentage_points']:.3f}}}",
        f"\\newcommand{{\\MaxWaveDeviation}}{{{summary['flow']['max_leave_one_wave_deviation_percentage_points']:.3f}}}",
        f"\\newcommand{{\\UnweightedDifference}}{{{summary['flow']['unweighted_max_difference_percentage_points']:.3f}}}",
        f"\\newcommand{{\\BalanceDeviation}}{{{summary['allocation']['balance_max_deviation_percentage_points']:.3f}}}",
    ]
    table_dir.mkdir(parents=True, exist_ok=True)
    (table_dir / "key-values.tex").write_text(
        "\n".join(key_value_lines) + "\n", encoding="utf-8"
    )

    environment = {
        "status": "pass",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "matplotlib": matplotlib.__version__,
    }
    json_dump(output / "environment.json", environment)

    save_survey_figure(marginals, figures / "<SOURCE_FILE_REDACTED>")
    save_route_figure(anchors, figures / "<SOURCE_FILE_REDACTED>")
    save_flow_figure(flow, figures / "<SOURCE_FILE_REDACTED>")
    save_allocation_figure(flow, allocation, figures / "<SOURCE_FILE_REDACTED>")
    save_sensitivity_figure(
        selected["footfall_share"],
        model_metrics["unweighted"]["footfall_share"],
        foot_low,
        foot_high,
        figures / "<SOURCE_FILE_REDACTED>",
    )
    write_generated_tables(table_dir, marginals, flow, allocation)
    print("[pass] Blind-solution computation completed.")


if __name__ == "__main__":
    main()
