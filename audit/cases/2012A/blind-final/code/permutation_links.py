"""Small-sample, tie-preserving permutation tests for Q3/Q4 links.

The conventional block is complete and is evaluated in vectorised batches.
Same-compound aroma pairs retain their pairwise missingness pattern.  Each
colour is one predeclared BH family containing both kinds of links.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats


N_PERM_FIRST = 100_000
N_PERM_FINAL = 200_000
BATCH = 500


def bh_adjust(pvalues: np.ndarray) -> np.ndarray:
    values = np.asarray(pvalues, dtype=float)
    adjusted = np.full(values.shape, np.nan, dtype=float)
    finite = np.isfinite(values)
    current = values[finite]
    if current.size == 0:
        return adjusted
    order = np.argsort(current, kind="mergesort")
    ranked = current[order]
    q = ranked * current.size / np.arange(1, current.size + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.minimum(q, 1.0)
    restored = np.empty_like(q)
    restored[order] = q
    adjusted[finite] = restored
    return adjusted


def clopper_pearson(exceedances: int, repetitions: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact binomial interval for the Monte Carlo exceedance probability."""

    if exceedances == 0:
        lower = 0.0
    else:
        lower = float(stats.beta.ppf(alpha / 2, exceedances, repetitions - exceedances + 1))
    if exceedances == repetitions:
        upper = 1.0
    else:
        upper = float(stats.beta.ppf(1 - alpha / 2, exceedances + 1, repetitions - exceedances))
    return lower, upper


def _rank_unit(values: np.ndarray) -> np.ndarray:
    ranks = stats.rankdata(np.asarray(values, dtype=float), method="average")
    ranks -= ranks.mean()
    norm = float(np.sqrt(np.sum(ranks**2)))
    if norm <= 0:
        return np.full(ranks.shape, np.nan)
    return ranks / norm


def _matrix_rank_unit(values: np.ndarray) -> np.ndarray:
    columns = [_rank_unit(values[:, column]) for column in range(values.shape[1])]
    return np.column_stack(columns)


def _seed_for(base_seed: int, label: str) -> int:
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    return int((base_seed + int.from_bytes(digest[:4], "little")) % (2**32 - 1))


def _conventional_rows(
    grape: pd.DataFrame,
    wine: pd.DataFrame,
    color: str,
    seed: int,
) -> list[dict[str, Any]]:
    x = grape.drop(columns="sample_id").to_numpy(dtype=float)
    y = wine.drop(columns="sample_id").to_numpy(dtype=float)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise RuntimeError(f"Conventional {color} matrices unexpectedly contain missing values")
    x_rank = _matrix_rank_unit(x)
    y_rank = _matrix_rank_unit(y)
    observed = x_rank.T @ y_rank
    finite = np.isfinite(observed)
    threshold = np.abs(observed) - 1e-14
    counts_first = np.zeros(observed.shape, dtype=np.int64)
    counts_final = np.zeros(observed.shape, dtype=np.int64)
    rng = np.random.default_rng(seed)
    completed = 0
    while completed < N_PERM_FINAL:
        batch = min(BATCH, N_PERM_FINAL - completed)
        permutations = np.vstack([rng.permutation(len(x)) for _ in range(batch)])
        permuted_y = y_rank[permutations]
        correlations = np.einsum("ni,bnj->bij", x_rank, permuted_y, optimize=True)
        exceed = np.sum(np.abs(correlations) >= threshold[None, :, :], axis=0)
        next_completed = completed + batch
        if completed < N_PERM_FIRST:
            first_take = min(batch, N_PERM_FIRST - completed)
            counts_first += np.sum(
                np.abs(correlations[:first_take]) >= threshold[None, :, :], axis=0
            )
        counts_final += exceed
        completed = next_completed

    rows: list[dict[str, Any]] = []
    for i, grape_name in enumerate(grape.columns[1:]):
        for j, wine_name in enumerate(wine.columns[1:]):
            if not finite[i, j]:
                p_first = p_final = ci_low = ci_high = math.nan
                k_final = 0
            else:
                k_first = int(counts_first[i, j])
                k_final = int(counts_final[i, j])
                p_first = (k_first + 1) / (N_PERM_FIRST + 1)
                p_final = (k_final + 1) / (N_PERM_FINAL + 1)
                ci_low, ci_high = clopper_pearson(k_final, N_PERM_FINAL)
            rows.append(
                {
                    "color": color,
                    "link_type": "conventional_all_pairs",
                    "grape_feature": str(grape_name),
                    "wine_feature": str(wine_name),
                    "n": int(len(x)),
                    "rho": float(observed[i, j]) if finite[i, j] else math.nan,
                    "permutation_exceedances": k_final,
                    "p_perm_100k": p_first,
                    "p_perm_200k": p_final,
                    "p_mc_ci95_low": ci_low,
                    "p_mc_ci95_high": ci_high,
                }
            )
    return rows


def _aroma_rows(
    grape: pd.DataFrame,
    wine: pd.DataFrame,
    color: str,
    seed: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    shared = sorted(set(grape.columns[1:]).intersection(wine.columns[1:]))
    for name in shared:
        x_raw = grape[name].to_numpy(dtype=float)
        y_raw = wine[name].to_numpy(dtype=float)
        mask = np.isfinite(x_raw) & np.isfinite(y_raw)
        x = x_raw[mask]
        y = y_raw[mask]
        if len(x) < 3 or np.nanstd(x) <= 0 or np.nanstd(y) <= 0:
            rows.append(
                {
                    "color": color,
                    "link_type": "same_aroma_compound",
                    "grape_feature": name,
                    "wine_feature": name,
                    "n": int(len(x)),
                    "rho": math.nan,
                    "permutation_exceedances": 0,
                    "p_perm_100k": math.nan,
                    "p_perm_200k": math.nan,
                    "p_mc_ci95_low": math.nan,
                    "p_mc_ci95_high": math.nan,
                }
            )
            continue
        x_rank = _rank_unit(x)
        y_rank = _rank_unit(y)
        observed = float(x_rank @ y_rank)
        threshold = abs(observed) - 1e-14
        rng = np.random.default_rng(_seed_for(seed, f"{color}|{name}"))
        count_first = 0
        count_final = 0
        completed = 0
        while completed < N_PERM_FINAL:
            batch = min(5_000, N_PERM_FINAL - completed)
            permutations = np.vstack([rng.permutation(len(x)) for _ in range(batch)])
            correlations = y_rank[permutations] @ x_rank
            exceed = np.abs(correlations) >= threshold
            if completed < N_PERM_FIRST:
                first_take = min(batch, N_PERM_FIRST - completed)
                count_first += int(np.sum(exceed[:first_take]))
            count_final += int(np.sum(exceed))
            completed += batch
        p_first = (count_first + 1) / (N_PERM_FIRST + 1)
        p_final = (count_final + 1) / (N_PERM_FINAL + 1)
        ci_low, ci_high = clopper_pearson(count_final, N_PERM_FINAL)
        rows.append(
            {
                "color": color,
                "link_type": "same_aroma_compound",
                "grape_feature": name,
                "wine_feature": name,
                "n": int(len(x)),
                "rho": observed,
                "permutation_exceedances": count_final,
                "p_perm_100k": p_first,
                "p_perm_200k": p_final,
                "p_mc_ci95_low": ci_low,
                "p_mc_ci95_high": ci_high,
            }
        )
    return rows


def permutation_link_family(clean_dir: Path, color: str, seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    grape = pd.read_csv(clean_dir / f"grape_conventional_{color}.csv").sort_values("sample_id")
    wine = pd.read_csv(clean_dir / f"wine_conventional_{color}.csv").sort_values("sample_id")
    grape_aroma = pd.read_csv(clean_dir / f"grape_aroma_{color}.csv").sort_values("sample_id")
    wine_aroma = pd.read_csv(clean_dir / f"wine_aroma_{color}.csv").sort_values("sample_id")
    expected = grape.sample_id.to_numpy(dtype=int)
    for frame in [wine, grape_aroma, wine_aroma]:
        if not np.array_equal(frame.sample_id.to_numpy(dtype=int), expected):
            raise RuntimeError(f"Sample alignment failure in Q3 {color} permutation family")

    rows = _conventional_rows(grape, wine, color, seed)
    rows.extend(_aroma_rows(grape_aroma, wine_aroma, color, seed))
    frame = pd.DataFrame(rows)
    frame["q_bh_100k"] = bh_adjust(frame.p_perm_100k.to_numpy(dtype=float))
    frame["q_bh_200k"] = bh_adjust(frame.p_perm_200k.to_numpy(dtype=float))
    frame["significant_100k"] = frame.q_bh_100k < 0.05
    frame["significant_200k"] = frame.q_bh_200k < 0.05
    frame["boundary_stability"] = np.where(
        frame.significant_100k == frame.significant_200k, "pass", "needs_review"
    )
    frame["status"] = np.where(frame.significant_200k, "pass", "fail")
    frame = frame.sort_values(
        ["q_bh_200k", "rho", "grape_feature", "wine_feature"],
        ascending=[True, False, True, True],
        na_position="last",
    ).reset_index(drop=True)
    sig_first = set(frame.index[frame.significant_100k])
    sig_final = set(frame.index[frame.significant_200k])
    stability_status = "pass" if sig_first == sig_final else "needs_review"
    summary = {
        "family_definition": "all conventional grape-wine pairs plus same-name aroma pairs, once per colour",
        "valid_tests": int(frame.p_perm_200k.notna().sum()),
        "permutations_first": N_PERM_FIRST,
        "permutations_final": N_PERM_FINAL,
        "significant_100k": int(frame.significant_100k.sum()),
        "significant_200k": int(frame.significant_200k.sum()),
        "gained_at_200k": int(len(sig_final - sig_first)),
        "lost_at_200k": int(len(sig_first - sig_final)),
        "significant_set_stability_status": stability_status,
        "monte_carlo_interval": "two-sided 95% Clopper-Pearson interval for each exceedance probability",
    }
    return frame, summary
