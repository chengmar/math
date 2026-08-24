#!/usr/bin/env python3
"""Deterministic boundary and counterexample tests for the integer allocator."""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np

import solve


SEED = 2006


def objective(
    allocation: np.ndarray,
    value: np.ndarray,
    reference: np.ndarray,
    elasticity: float,
) -> float:
    positive = allocation > 0
    terms = np.zeros_like(value, dtype=float)
    terms[positive] = (
        value[positive]
        * reference[positive]
        * (allocation[positive] / reference[positive]) ** elasticity
    )
    return float(terms.sum())


def brute_force_optimum(
    request: np.ndarray,
    categories: list[str],
    branch_minimum: dict[str, int],
    branch_capacity: dict[str, int],
    budget: int,
    value: np.ndarray,
    reference: np.ndarray,
    elasticity: float,
) -> tuple[float, list[list[int]]]:
    best = -np.inf
    optimizers: list[list[int]] = []
    for candidate_tuple in itertools.product(*(range(int(bound) + 1) for bound in request)):
        if sum(candidate_tuple) != budget:
            continue
        totals = {category: 0 for category in branch_minimum}
        for amount, category in zip(candidate_tuple, categories):
            totals[category] += int(amount)
        if any(
            totals[category] < branch_minimum[category]
            or totals[category] > branch_capacity[category]
            for category in branch_minimum
        ):
            continue
        candidate = np.asarray(candidate_tuple, dtype=int)
        score = objective(candidate, value, reference, elasticity)
        if score > best + 1e-10:
            best = score
            optimizers = [candidate.tolist()]
        elif abs(score - best) <= 1e-10:
            optimizers.append(candidate.tolist())
    if not optimizers:
        raise RuntimeError("Brute-force fixture unexpectedly has no feasible allocation")
    return best, optimizers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    checks: dict[str, dict[str, Any]] = {}

    def record(name: str, condition: bool, evidence: Any) -> None:
        checks[name] = {
            "status": "pass" if bool(condition) else "fail",
            "evidence": evidence,
        }

    zero = solve.concave_allocation(
        np.array([2, 3]),
        np.zeros(2, dtype=int),
        ["A", "B"],
        {"A": 0, "B": 0},
        {"A": 2, "B": 3},
        0,
        np.array([1.0, 1.0]),
        np.ones(2),
        0.5,
    )
    record("zero_budget", bool(np.array_equal(zero, np.zeros(2, dtype=int))), zero.tolist())

    dominant = solve.concave_allocation(
        np.array([5, 5]),
        np.zeros(2, dtype=int),
        ["A", "A"],
        {"A": 0},
        {"A": 10},
        5,
        np.array([1_000_000.0, 1.0]),
        np.ones(2),
        0.5,
    )
    record("single_course_dominance", bool(dominant.tolist() == [5, 0]), dominant.tolist())

    toy_request = np.array([3, 2, 3, 2])
    toy_categories = ["A", "A", "B", "B"]
    toy_minimum = {"A": 2, "B": 1}
    toy_capacity = {"A": 4, "B": 3}
    toy_value = np.array([8.0, 5.0, 6.0, 4.0])
    toy_reference = np.ones(4)
    toy = solve.concave_allocation(
        toy_request,
        np.zeros(4, dtype=int),
        toy_categories,
        toy_minimum,
        toy_capacity,
        5,
        toy_value,
        toy_reference,
        0.5,
    )
    best, optimizers = brute_force_optimum(
        toy_request,
        toy_categories,
        toy_minimum,
        toy_capacity,
        5,
        toy_value,
        toy_reference,
        0.5,
    )
    toy_score = objective(toy, toy_value, toy_reference, 0.5)
    record(
        "brute_force_fixture",
        abs(toy_score - best) <= 1e-10,
        {"allocation": toy.tolist(), "score": toy_score, "best": best, "optimizers": optimizers},
    )

    scaled = solve.concave_allocation(
        toy_request,
        np.zeros(4, dtype=int),
        toy_categories,
        toy_minimum,
        toy_capacity,
        5,
        17.0 * toy_value,
        toy_reference,
        0.5,
    )
    record("positive_value_scale_invariance", bool(np.array_equal(toy, scaled)), scaled.tolist())

    tie_first = solve.concave_allocation(
        np.array([3, 3, 3]),
        np.zeros(3, dtype=int),
        ["A", "A", "A"],
        {"A": 0},
        {"A": 9},
        4,
        np.ones(3),
        np.ones(3),
        0.5,
    )
    tie_second = solve.concave_allocation(
        np.array([3, 3, 3]),
        np.zeros(3, dtype=int),
        ["A", "A", "A"],
        {"A": 0},
        {"A": 9},
        4,
        np.ones(3),
        np.ones(3),
        0.5,
    )
    record("deterministic_tie_break", bool(np.array_equal(tie_first, tie_second)), tie_first.tolist())

    rejected: dict[str, bool] = {}
    invalid_cases = {
        "below_minimum": ({"A": 2}, {"A": 3}, 1),
        "above_maximum": ({"A": 0}, {"A": 3}, 4),
        "minimum_above_capacity": ({"A": 4}, {"A": 3}, 3),
    }
    for name, (minimum, capacity, budget) in invalid_cases.items():
        try:
            solve.validate_feasibility(
                np.array([3]), np.array([0]), ["A"], minimum, capacity, budget
            )
        except ValueError:
            rejected[name] = True
        else:
            rejected[name] = False
    record("infeasible_cases_rejected", all(rejected.values()), rejected)

    rng = np.random.default_rng(SEED)
    random_failures: list[dict[str, Any]] = []
    random_cases = 100
    for case_id in range(random_cases):
        request = rng.integers(0, 4, size=4, dtype=int)
        categories = ["A", "A", "B", "B"]
        request_by_branch = {
            "A": int(request[:2].sum()),
            "B": int(request[2:].sum()),
        }
        capacity = {
            category: int(rng.integers(0, request_by_branch[category] + 1))
            for category in ("A", "B")
        }
        minimum = {
            category: int(rng.integers(0, capacity[category] + 1))
            for category in ("A", "B")
        }
        budget = int(rng.integers(sum(minimum.values()), sum(capacity.values()) + 1))
        value = rng.uniform(0.1, 20.0, size=4)
        reference = rng.integers(1, 5, size=4).astype(float)
        elasticity = float(rng.choice([0.25, 0.5, 0.75, 1.0]))
        allocation = solve.concave_allocation(
            request,
            np.zeros(4, dtype=int),
            categories,
            minimum,
            capacity,
            budget,
            value,
            reference,
            elasticity,
        )
        observed = objective(allocation, value, reference, elasticity)
        optimum, _ = brute_force_optimum(
            request,
            categories,
            minimum,
            capacity,
            budget,
            value,
            reference,
            elasticity,
        )
        if abs(observed - optimum) > 1e-9:
            random_failures.append(
                {
                    "case_id": case_id,
                    "allocation": allocation.tolist(),
                    "observed": observed,
                    "optimum": optimum,
                }
            )
    record(
        "random_small_instance_global_optimality",
        not random_failures,
        {"cases": random_cases, "failures": random_failures},
    )

    forecast_path = workspace / "results" / "forecast_validation.json"
    forecast = json.loads(forecast_path.read_text(encoding="utf-8"))
    tuning_order = sorted(
        forecast["variants"],
        key=lambda name: (
            forecast["variants"][name]["tuning"]["wape"],
            sum(
                abs(float(value)) > 1e-12
                for value in forecast["variants"][name]["coefficients"].values()
            ),
            forecast["variants"][name]["tuning"]["rmsle"],
            name,
        ),
    )
    no_holdout_selection = (
        forecast.get("holdout_used_for_selection") is False
        and forecast.get("selection_order") == tuning_order
        and forecast.get("selected") == tuning_order[0]
    )
    record(
        "holdout_isolation_contract",
        no_holdout_selection,
        {
            "selected": forecast.get("selected"),
            "tuning_order": tuning_order,
            "holdout_used_for_selection": forecast.get("holdout_used_for_selection"),
        },
    )

    overall = "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    report = {
        "status": overall,
        "seed": SEED,
        "scope": "optimizer boundaries, infeasibility, scale/tie behavior, small-instance optimality, and holdout isolation",
        "mathematical_truth_claim": "needs_review",
        "checks": checks,
    }
    output = workspace / "results" / "model_stress_tests.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
