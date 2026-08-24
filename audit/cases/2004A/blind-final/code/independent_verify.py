from __future__ import annotations

import argparse
import heapq
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


AUDIENCE_PER_ZONE = 10_000
AUDIENCE_TOTAL = 200_000
SMALL_CAPACITY = 2_000
LARGE_CAPACITY = 4_000
CAPTURE_RATE = 0.50

TRAVEL = ["bus_ns", "bus_ew", "taxi", "car", "metro_east", "metro_west"]
FOOD = ["chinese_food", "western_food", "mall_food"]
SPEND_MIDPOINTS = np.array([50, 150, 250, 350, 450, 550], dtype=float)

# Deliberately duplicated rather than imported from solve.py: the route and
# flow results below are reconstructed by a standalone implementation.
RINGS: dict[str, list[tuple[str, tuple[float, float]]]] = {
    "A": [
        ("A1", (374, 221)), ("A2", (417, 221)), ("A3", (458, 221)),
        ("A4", (499, 221)), ("A5", (510, 260)), ("A6", (510, 294)),
        ("A7", (460, 306)), ("A8", (418, 306)), ("A9", (375, 306)),
        ("A10", (365, 260)),
    ],
    "B": [
        ("B1", (132, 221)), ("B2", (181, 214)), ("B3", (229, 221)),
        ("B4", (235, 286)), ("B5", (183, 295)), ("B6", (130, 286)),
    ],
    "C": [
        ("C1", (139, 131)), ("C2", (197, 131)),
        ("C3", (197, 168)), ("C4", (139, 168)),
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
ZONE_INDEX = {name: index for index, name in enumerate(ZONE_ORDER)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone verification for the blind revision.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    return parser.parse_args()


def json_dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_data(workspace: Path) -> pd.DataFrame:
    frames = []
    for wave, filename in enumerate(["<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>"], start=1):
        frame = pd.read_csv(workspace / "work" / "staging" / filename, encoding="utf-8-sig")
        frame["survey"] = wave
        frames.append(frame)
    data = pd.concat(frames, ignore_index=True)
    data["travel"] = np.array(TRAVEL, dtype=object)[
        np.argmax(data[TRAVEL].to_numpy(dtype=int), axis=1)
    ]
    data["food"] = np.array(FOOD, dtype=object)[
        np.argmax(data[FOOD].to_numpy(dtype=int), axis=1)
    ]
    data["spend_midpoint"] = SPEND_MIDPOINTS[
        data["spend_group"].to_numpy(dtype=int) - 1
    ]
    return data


def adjacency(ring: list[tuple[str, tuple[float, float]]]) -> dict[str, list[tuple[str, float]]]:
    names = [name for name, _ in ring]
    coordinates = dict(ring)
    graph = {name: [] for name in names}
    for index, name in enumerate(names):
        following = names[(index + 1) % len(names)]
        weight = math.dist(coordinates[name], coordinates[following])
        graph[name].append((following, weight))
        graph[following].append((name, weight))
    return graph


def all_shortest_paths(
    ring: list[tuple[str, tuple[float, float]]], source: str, target: str
) -> list[tuple[str, ...]]:
    """Dijkstra plus predecessor backtracking, independent of solve.py's cycle enumeration."""
    if source == target:
        return [(source,)]
    graph = adjacency(ring)
    distances = {node: math.inf for node in graph}
    predecessors: dict[str, list[str]] = {node: [] for node in graph}
    distances[source] = 0.0
    queue: list[tuple[float, str]] = [(0.0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > distances[node] + 1e-12:
            continue
        for following, weight in graph[node]:
            candidate = distance + weight
            if candidate < distances[following] - 1e-10:
                distances[following] = candidate
                predecessors[following] = [node]
                heapq.heappush(queue, (candidate, following))
            elif math.isclose(candidate, distances[following], rel_tol=0, abs_tol=1e-10):
                if node not in predecessors[following]:
                    predecessors[following].append(node)

    def backtrack(node: str) -> list[tuple[str, ...]]:
        if node == source:
            return [(source,)]
        paths: list[tuple[str, ...]] = []
        for previous in sorted(predecessors[node]):
            for prefix in backtrack(previous):
                paths.append((*prefix, node))
        return paths

    paths = backtrack(target)
    if not paths:
        raise RuntimeError(f"No path from {source} to {target}.")
    return paths


def path_options(
    ring: list[tuple[str, tuple[float, float]]], source: str, category: str
) -> list[tuple[tuple[str, ...], float]]:
    merged: dict[tuple[str, ...], float] = {}
    coordinates = dict(ring)
    for destination_name, destination_weight in CATEGORY_DESTINATIONS[category]:
        destination = DESTINATIONS[destination_name]
        distances = {name: math.dist(point, destination) for name, point in ring}
        minimum = min(distances.values())
        anchors = sorted(
            name for name, distance in distances.items()
            if math.isclose(distance, minimum, rel_tol=0, abs_tol=1e-10)
        )
        for anchor in anchors:
            paths = all_shortest_paths(ring, source, anchor)
            weight = destination_weight / len(anchors) / len(paths)
            for path in paths:
                merged[path] = merged.get(path, 0.0) + weight
    total = sum(merged.values())
    return [(path, weight / total) for path, weight in sorted(merged.items())]


def standalone_recompute(data: pd.DataFrame) -> dict[str, np.ndarray]:
    travel_codes = pd.Categorical(data["travel"], categories=TRAVEL).codes
    food_codes = pd.Categorical(data["food"], categories=FOOD).codes
    spend = data["spend_midpoint"].to_numpy(dtype=float)
    n = len(data)
    travel_probability = np.bincount(travel_codes, minlength=len(TRAVEL)) / n
    food_probability = np.bincount(food_codes, minlength=len(FOOD)) / n
    joint_counts = np.zeros((len(TRAVEL), len(FOOD)), dtype=float)
    joint_spend = np.zeros_like(joint_counts)
    np.add.at(joint_counts, (travel_codes, food_codes), 1.0)
    np.add.at(joint_spend, (travel_codes, food_codes), spend)

    travel_flow = np.zeros(len(ZONE_ORDER), dtype=float)
    food_flow = np.zeros(len(ZONE_ORDER), dtype=float)
    transaction_share = np.zeros(len(ZONE_ORDER), dtype=float)
    revenue_potential = np.zeros(len(ZONE_ORDER), dtype=float)

    for ring in RINGS.values():
        for source, _ in ring:
            travel_options = {category: path_options(ring, source, category) for category in TRAVEL}
            food_options = {category: path_options(ring, source, category) for category in FOOD}

            for ti, category in enumerate(TRAVEL):
                for path, weight in travel_options[category]:
                    for zone in path:
                        travel_flow[ZONE_INDEX[zone]] += AUDIENCE_PER_ZONE * travel_probability[ti] * weight
            for fi, category in enumerate(FOOD):
                for path, weight in food_options[category]:
                    for zone in path:
                        food_flow[ZONE_INDEX[zone]] += AUDIENCE_PER_ZONE * food_probability[fi] * weight

            for ti, travel_category in enumerate(TRAVEL):
                for fi, food_category in enumerate(FOOD):
                    record_probability = joint_counts[ti, fi] / n
                    spend_mass = joint_spend[ti, fi] / n
                    for travel_path, travel_weight in travel_options[travel_category]:
                        for food_path, food_weight in food_options[food_category]:
                            exposure = np.zeros(len(ZONE_ORDER), dtype=float)
                            for zone in travel_path:
                                exposure[ZONE_INDEX[zone]] += 1.0
                            for zone in food_path:
                                exposure[ZONE_INDEX[zone]] += 1.0
                            choice = exposure / exposure.sum()
                            path_weight = travel_weight * food_weight
                            transaction_share += record_probability * path_weight * choice / 20.0
                            revenue_potential += AUDIENCE_PER_ZONE * spend_mass * path_weight * choice

    footfall = travel_flow + food_flow
    return {
        "travel_flow": travel_flow,
        "food_flow": food_flow,
        "footfall": footfall,
        "footfall_share": footfall / footfall.sum(),
        "transaction_share": transaction_share / transaction_share.sum(),
        "revenue_potential": revenue_potential,
        "revenue_share": revenue_potential / revenue_potential.sum(),
    }


def reference_optimize(demand: float) -> tuple[int, int, float]:
    max_small = max(1, math.ceil(demand / SMALL_CAPACITY))
    max_large = max(1, math.ceil(demand / LARGE_CAPACITY))
    candidates: list[tuple[tuple[float, float, int], int, int, float]] = []
    for small in range(max_small + 1):
        for large in range(max_large + 1):
            if small + large == 0:
                continue
            capacity = SMALL_CAPACITY * small + LARGE_CAPACITY * large
            if capacity + 1e-9 < demand:
                continue
            candidates.append(((small + 1.7 * large, capacity - demand, small + large), small, large, capacity))
    if not candidates:
        raise RuntimeError("Reference optimizer found no feasible plan.")
    _, small, large, capacity = min(candidates)
    return small, large, capacity


def load_solution_module(workspace: Path):
    path = workspace / "code" / "solve.py"
    specification = importlib.util.spec_from_file_location("blind_revision_solve", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("Unable to load solve.py for boundary checks.")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def main() -> int:
    workspace = parse_args().workspace.resolve()
    data = load_data(workspace)
    flow = pd.read_csv(workspace / "results" / "<SOURCE_FILE_REDACTED>")
    allocation = pd.read_csv(workspace / "results" / "<SOURCE_FILE_REDACTED>")
    scenarios = pd.read_csv(workspace / "results" / "<SOURCE_FILE_REDACTED>")
    summary = json.loads((workspace / "results" / "summary.json").read_text(encoding="utf-8"))
    independent = standalone_recompute(data)
    solution_module = load_solution_module(workspace)

    checks: list[dict[str, str]] = []

    def add(item: str, condition: bool, evidence: str) -> None:
        checks.append({"item": item, "status": "pass" if bool(condition) else "fail", "evidence": evidence})

    add("sample_rows", len(data) == 10_600, f"rows={len(data)}")
    add("zone_order", flow["zone"].tolist() == ZONE_ORDER, f"rows={len(flow)}")

    for column in ["travel_flow", "food_flow", "footfall", "footfall_share", "transaction_share", "revenue_share"]:
        maximum_error = float(np.max(np.abs(flow[column].to_numpy(dtype=float) - independent[column])))
        tolerance = 1e-7 if not column.endswith("share") else 1e-12
        add(f"standalone_{column}", maximum_error <= tolerance, f"maximum_error={maximum_error:.3e}")
    revenue_error = float(
        np.max(
            np.abs(
                flow["full_capture_revenue_potential_yuan"].to_numpy(dtype=float)
                - independent["revenue_potential"]
            )
        )
    )
    add("standalone_revenue_potential", revenue_error <= 1e-6, f"maximum_error={revenue_error:.3e}")

    share_errors = {
        column: abs(float(flow[column].sum()) - 1.0)
        for column in ["footfall_share", "transaction_share", "revenue_share"]
    }
    add("share_conservation", max(share_errors.values()) <= 1e-12, json.dumps(share_errors, sort_keys=True))

    planning_expected = np.ceil(
        AUDIENCE_TOTAL * CAPTURE_RATE * flow["transaction_ci95_high"].to_numpy(dtype=float)
    )
    planning_error = float(
        np.max(np.abs(planning_expected - allocation["planning_checkout_demand"].to_numpy(dtype=float)))
    )
    add("planning_demand_formula", planning_error <= 1e-9, f"maximum_error={planning_error:.3e}")

    capacity_expected = (
        SMALL_CAPACITY * allocation["small_ms"].to_numpy(dtype=int)
        + LARGE_CAPACITY * allocation["large_ms"].to_numpy(dtype=int)
    )
    capacity_error = float(
        np.max(np.abs(capacity_expected - allocation["service_capacity"].to_numpy(dtype=float)))
    )
    add("capacity_formula", capacity_error <= 1e-9, f"maximum_error={capacity_error:.3e}")
    minimum_slack = float(
        np.min(allocation["service_capacity"] - allocation["planning_checkout_demand"])
    )
    add("capacity_constraints", minimum_slack >= 0, f"minimum_slack={minimum_slack:.6f}")

    plan_matches = True
    for row in allocation.itertuples(index=False):
        expected = reference_optimize(float(row.planning_checkout_demand))
        actual = (int(row.small_ms), int(row.large_ms), float(row.service_capacity))
        plan_matches = plan_matches and actual == expected
    add("allocation_global_enumeration", plan_matches, f"zones_checked={len(allocation)}")

    boundary_demands = [0, 1, 1_999, 2_000, 2_001, 3_999, 4_000, 4_001, 20_000, 200_000]
    boundary_matches = all(
        solution_module.optimize_zone(value, SMALL_CAPACITY, LARGE_CAPACITY)
        == reference_optimize(value)
        for value in boundary_demands
    )
    add("optimizer_boundary_and_extreme_cases", boundary_matches, f"demands={boundary_demands}")
    add(
        "zero_demand_minimum_presence_policy",
        solution_module.optimize_zone(0, SMALL_CAPACITY, LARGE_CAPACITY) == (1, 0, 2_000),
        "expected=(1, 0, 2000)",
    )
    invalid_rejected = False
    try:
        solution_module.optimize_zone(-1, SMALL_CAPACITY, LARGE_CAPACITY)
    except ValueError:
        invalid_rejected = True
    add("negative_demand_rejected", invalid_rejected, "demand=-1")
    invalid_capacity_rejected = False
    try:
        solution_module.optimize_zone(1, 0, LARGE_CAPACITY)
    except ValueError:
        invalid_capacity_rejected = True
    add("nonpositive_capacity_rejected", invalid_capacity_rejected, "small_capacity=0")

    profit_expected = (
        0.25 * allocation["captured_revenue_yuan"]
        - allocation["daily_operating_cost_yuan"]
    )
    profit_error = float(np.max(np.abs(profit_expected - allocation["scenario_profit_yuan"])))
    add("scenario_profit_formula", profit_error <= 1e-6, f"maximum_error={profit_error:.3e}")
    add(
        "sensitivity_service_constraints",
        bool((scenarios["service_status"] == "pass").all()),
        f"scenarios={len(scenarios)}",
    )

    totals_match = (
        int(allocation["small_ms"].sum()) == int(summary["allocation"]["small_ms_total"])
        and int(allocation["large_ms"].sum()) == int(summary["allocation"]["large_ms_total"])
        and math.isclose(
            float(allocation["service_capacity"].sum()),
            float(summary["allocation"]["service_capacity_total"]),
            rel_tol=0,
            abs_tol=1e-6,
        )
        and math.isclose(
            float(flow["footfall"].sum()),
            float(summary["flow"]["commercial_area_passages"]),
            rel_tol=0,
            abs_tol=1e-6,
        )
    )
    add("summary_totals", totals_match, f"small={int(allocation['small_ms'].sum())}; large={int(allocation['large_ms'].sum())}")

    ci_valid = bool(
        (
            (flow[["footfall_ci95_low", "transaction_ci95_low", "revenue_ci95_low"]] >= 0).all().all()
            and (flow[["footfall_ci95_high", "transaction_ci95_high", "revenue_ci95_high"]] <= 1).all().all()
            and (flow["footfall_ci95_low"] <= flow["footfall_ci95_high"]).all()
            and (flow["transaction_ci95_low"] <= flow["transaction_ci95_high"]).all()
            and (flow["revenue_ci95_low"] <= flow["revenue_ci95_high"]).all()
        )
    )
    add("uncertainty_interval_bounds", ci_valid, "all marginal intervals lie in [0,1]")

    overall = "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    report = {
        "status": overall,
        "scope": (
            "使用独立 Dijkstra/前驱回溯从提取后的三波问卷与重复编码的图 2 几何重算人流、"
            "交易和消费额，并独立枚举配置与边界案例；不声称替代外部数学正确性审阅。"
        ),
        "checks": checks,
        "mathematical_correctness_status": "needs_review",
        "external_validation_status": "needs_review",
    }
    output = workspace / "results" / "independent-verification.json"
    json_dump(output, report)
    print(f"[{overall}] Independent verification completed.")
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
