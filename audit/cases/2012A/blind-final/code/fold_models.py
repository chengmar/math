"""Fold-isolated Q3/Q4 modelling for the blind revision.

Every data-driven feature decision is fitted on the current training indices.
Aroma concentrations are transformed as the dimensionless ratio
``log1p(x / x_ref)`` where ``x_ref`` is the training-fold median positive
concentration.  Therefore an equivalent unit conversion changes both numerator
and reference and leaves the model matrix invariant.
"""

from __future__ import annotations

import math
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cross_decomposition import PLSRegression
from sklearn.decomposition import PCA
from sklearn.linear_model import Ridge

from permutation_links import clopper_pearson, permutation_link_family


SEED = 20240824
PERMUTATION_SEEDS = [1, 42, 20240824, 20240825]
N_MODEL_PERM = 499
OUTER_REPEATS = 4
OUTER_FOLDS = 5
INNER_FOLDS = 4
DETECTION_THRESHOLD = 0.50
RIDGE_GRID = [1.0, 100.0]
PLS_GRID = [1, 2]


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_ready(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def balanced_folds(indices: np.ndarray, folds: int, offset: int) -> list[np.ndarray]:
    ordered = np.sort(np.asarray(indices, dtype=int))
    if folds < 2 or folds > len(ordered):
        raise ValueError(f"Invalid fold count {folds} for {len(ordered)} observations")
    rolled = np.roll(ordered, -(offset % len(ordered)))
    return [np.sort(part.astype(int)) for part in np.array_split(rolled, folds) if len(part)]


def status_rollup(statuses: Iterable[str]) -> str:
    values = list(statuses)
    if any(value == "fail" for value in values):
        return "fail"
    if any(value == "needs_review" for value in values):
        return "needs_review"
    return "pass"


@dataclass
class RawColorData:
    color: str
    sample_ids: np.ndarray
    parts: dict[str, np.ndarray]
    names: dict[str, list[str]]
    aroma_factor: float = 1.0


SCOPE_PARTS: dict[str, tuple[str, ...]] = {
    "grape": ("grape_conv", "grape_aroma"),
    "wine": ("wine_conv", "wine_aroma"),
    "combined": ("grape_conv", "grape_aroma", "wine_conv", "wine_aroma"),
    "grape_conventional": ("grape_conv",),
    "grape_aroma": ("grape_aroma",),
    "wine_conventional": ("wine_conv",),
    "wine_aroma": ("wine_aroma",),
}


def load_raw_color(clean_dir: Path, color: str, aroma_factor: float = 1.0) -> RawColorData:
    frames = {
        "grape_conv": pd.read_csv(clean_dir / f"grape_conventional_{color}.csv").sort_values("sample_id"),
        "wine_conv": pd.read_csv(clean_dir / f"wine_conventional_{color}.csv").sort_values("sample_id"),
        "grape_aroma": pd.read_csv(clean_dir / f"grape_aroma_{color}.csv").sort_values("sample_id"),
        "wine_aroma": pd.read_csv(clean_dir / f"wine_aroma_{color}.csv").sort_values("sample_id"),
    }
    sample_ids = frames["grape_conv"].sample_id.to_numpy(dtype=int)
    for name, frame in frames.items():
        if not np.array_equal(frame.sample_id.to_numpy(dtype=int), sample_ids):
            raise RuntimeError(f"Sample alignment failure for {color}/{name}")
    parts: dict[str, np.ndarray] = {}
    names: dict[str, list[str]] = {}
    for part, frame in frames.items():
        values = frame.drop(columns="sample_id").to_numpy(dtype=float)
        if part.endswith("aroma"):
            values = values * float(aroma_factor)
        parts[part] = values
        names[part] = [f"{part}::{column}" for column in frame.columns[1:]]
    return RawColorData(
        color=color,
        sample_ids=sample_ids,
        parts=parts,
        names=names,
        aroma_factor=float(aroma_factor),
    )


@dataclass
class PartTransformer:
    kind: str
    keep: np.ndarray
    median: np.ndarray
    low: np.ndarray
    high: np.ndarray
    reference: np.ndarray
    center: np.ndarray
    scale: np.ndarray
    mode: str

    @classmethod
    def fit(
        cls,
        values: np.ndarray,
        kind: str,
        mode: str = "zero",
        winsor: bool = False,
    ) -> "PartTransformer":
        values = np.asarray(values, dtype=float)
        if values.ndim != 2:
            raise ValueError("Expected a two-dimensional feature part")
        finite = np.isfinite(values)
        detection = finite.mean(axis=0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            minimum = np.nanmin(values, axis=0)
            maximum = np.nanmax(values, axis=0)
        variable = np.isfinite(minimum) & np.isfinite(maximum) & ((maximum - minimum) > 1e-12)
        keep = variable if kind == "conv" else (variable & (detection >= DETECTION_THRESHOLD))
        if not np.any(keep):
            raise RuntimeError(f"Training fold retained no {kind} features")
        selected = values[:, keep].copy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            median = np.nanmedian(selected, axis=0)
        median = np.where(np.isfinite(median), median, 0.0)
        low = np.full(selected.shape[1], -np.inf)
        high = np.full(selected.shape[1], np.inf)
        reference = np.ones(selected.shape[1], dtype=float)
        if kind == "conv":
            selected = np.where(np.isfinite(selected), selected, median)
            if winsor:
                low = np.quantile(selected, 0.05, axis=0)
                high = np.quantile(selected, 0.95, axis=0)
                selected = np.clip(selected, low, high)
        else:
            for column in range(selected.shape[1]):
                positive = selected[np.isfinite(selected[:, column]) & (selected[:, column] > 0), column]
                reference[column] = float(np.median(positive)) if positive.size else 1.0
                if not np.isfinite(reference[column]) or reference[column] <= 0:
                    reference[column] = 1.0
                replacement = 0.0 if mode == "zero" else (float(np.min(positive)) / 2 if positive.size else 0.0)
                selected[~np.isfinite(selected[:, column]), column] = replacement
            if np.any(selected < 0):
                raise RuntimeError("Aroma concentrations must be nonnegative after parsing")
            selected = np.log1p(selected / reference)
        center = selected.mean(axis=0)
        scale = selected.std(axis=0, ddof=0)
        scale = np.where(scale > 1e-12, scale, 1.0)
        return cls(kind, keep, median, low, high, reference, center, scale, mode)

    def transform(self, values: np.ndarray) -> np.ndarray:
        selected = np.asarray(values, dtype=float)[:, self.keep].copy()
        if self.kind == "conv":
            selected = np.where(np.isfinite(selected), selected, self.median)
            selected = np.clip(selected, self.low, self.high)
        else:
            for column in range(selected.shape[1]):
                positive_reference = self.reference[column]
                replacement = 0.0
                if self.mode == "half_min":
                    # The training half-minimum equals the smallest positive
                    # value implied by the fitted reference only when retained
                    # explicitly; use zero here only for impossible all-missing
                    # test cells.  A fitted half-minimum is stored as median for
                    # aroma transformers below.
                    replacement = self.median[column]
                selected[~np.isfinite(selected[:, column]), column] = replacement
            if np.any(selected < 0):
                raise RuntimeError("Negative aroma value encountered during transform")
            selected = np.log1p(selected / self.reference)
        return (selected - self.center) / self.scale


@dataclass
class ScopeTransformer:
    scope: str
    part_names: tuple[str, ...]
    transformers: dict[str, PartTransformer]
    feature_names: list[str]

    @classmethod
    def fit(
        cls,
        raw: RawColorData,
        scope: str,
        train: np.ndarray,
        aroma_mode: str = "zero",
        winsor: bool = False,
    ) -> "ScopeTransformer":
        part_names = SCOPE_PARTS[scope]
        transformers: dict[str, PartTransformer] = {}
        names: list[str] = []
        for part in part_names:
            kind = "aroma" if part.endswith("aroma") else "conv"
            transformer = PartTransformer.fit(
                raw.parts[part][train], kind, mode=aroma_mode, winsor=winsor and kind == "conv"
            )
            # Store the actual training-fold half-minimum for application.
            if kind == "aroma" and aroma_mode == "half_min":
                selected = raw.parts[part][train][:, transformer.keep]
                replacements = []
                for column in range(selected.shape[1]):
                    positive = selected[np.isfinite(selected[:, column]) & (selected[:, column] > 0), column]
                    replacements.append(float(np.min(positive)) / 2 if positive.size else 0.0)
                transformer.median = np.asarray(replacements, dtype=float)
            transformers[part] = transformer
            names.extend(
                name for name, keep in zip(raw.names[part], transformer.keep, strict=True) if keep
            )
        return cls(scope=scope, part_names=part_names, transformers=transformers, feature_names=names)

    def transform(self, raw: RawColorData, indices: np.ndarray) -> np.ndarray:
        matrices = [self.transformers[part].transform(raw.parts[part][indices]) for part in self.part_names]
        return np.column_stack(matrices)


@dataclass
class FoldCache:
    repeat: int
    fold: int
    train: np.ndarray
    test: np.ndarray
    outer_x: dict[str, tuple[np.ndarray, np.ndarray, list[str]]]
    inner: list[dict[str, Any]]


def _record_fit(
    records: list[dict[str, Any]] | None,
    raw: RawColorData,
    task: str,
    repeat: int,
    fold: str,
    scope: str,
    train: np.ndarray,
    apply: np.ndarray,
) -> None:
    if records is None:
        return
    fitted = raw.sample_ids[train].astype(int).tolist()
    applied = raw.sample_ids[apply].astype(int).tolist()
    overlap = sorted(set(fitted).intersection(applied))
    records.append(
        {
            "task": task,
            "color": raw.color,
            "repeat": repeat,
            "fold": fold,
            "scope": scope,
            "fitted_on_sample_ids": fitted,
            "applied_to_sample_ids": applied,
            "overlap": overlap,
            "status": "pass" if not overlap else "fail",
        }
    )


def build_cache(
    raw: RawColorData,
    scopes: Iterable[str],
    task: str,
    records: list[dict[str, Any]] | None = None,
    aroma_mode: str = "zero",
    winsor: bool = False,
) -> list[FoldCache]:
    indices = np.arange(len(raw.sample_ids), dtype=int)
    caches: list[FoldCache] = []
    unique_scopes = list(dict.fromkeys(scopes))
    for repeat in range(OUTER_REPEATS):
        outer_tests = balanced_folds(indices, OUTER_FOLDS, offset=repeat)
        for fold, test in enumerate(outer_tests):
            train = np.setdiff1d(indices, test, assume_unique=True)
            outer_x: dict[str, tuple[np.ndarray, np.ndarray, list[str]]] = {}
            for scope in unique_scopes:
                transformer = ScopeTransformer.fit(raw, scope, train, aroma_mode, winsor)
                outer_x[scope] = (
                    transformer.transform(raw, train),
                    transformer.transform(raw, test),
                    transformer.feature_names,
                )
                _record_fit(records, raw, task, repeat, f"outer-{fold}", scope, train, test)
            inner_payload: list[dict[str, Any]] = []
            inner_valids = balanced_folds(train, min(INNER_FOLDS, len(train)), offset=repeat + fold)
            for inner_fold, valid in enumerate(inner_valids):
                inner_train = np.setdiff1d(train, valid, assume_unique=True)
                transformed: dict[str, tuple[np.ndarray, np.ndarray, list[str]]] = {}
                for scope in unique_scopes:
                    transformer = ScopeTransformer.fit(raw, scope, inner_train, aroma_mode, winsor)
                    transformed[scope] = (
                        transformer.transform(raw, inner_train),
                        transformer.transform(raw, valid),
                        transformer.feature_names,
                    )
                    _record_fit(
                        records,
                        raw,
                        task,
                        repeat,
                        f"outer-{fold}/inner-{inner_fold}",
                        scope,
                        inner_train,
                        valid,
                    )
                inner_payload.append({"train": inner_train, "valid": valid, "x": transformed})
            caches.append(FoldCache(repeat, fold, train, test, outer_x, inner_payload))
    return caches


def single_metrics(y: np.ndarray, prediction: np.ndarray, baseline: np.ndarray) -> dict[str, float]:
    y = np.asarray(y, dtype=float).ravel()
    prediction = np.asarray(prediction, dtype=float).ravel()
    baseline = np.asarray(baseline, dtype=float).ravel()
    sse = float(np.sum((y - prediction) ** 2))
    baseline_sse = float(np.sum((y - baseline) ** 2))
    tss = float(np.sum((y - y.mean()) ** 2))
    rho = stats.spearmanr(y, prediction)
    return {
        "rmse": float(np.sqrt(np.mean((y - prediction) ** 2))),
        "mae": float(np.mean(np.abs(y - prediction))),
        "baseline_rmse": float(np.sqrt(np.mean((y - baseline) ** 2))),
        "q2_full_mean_tss": 1 - sse / tss if tss > 0 else math.nan,
        "q2_vs_training_mean": 1 - sse / baseline_sse if baseline_sse > 0 else math.nan,
        "relative_rmse_improvement": 1 - math.sqrt(sse / baseline_sse) if baseline_sse > 0 else math.nan,
        "spearman_rho": float(rho.statistic),
        "spearman_p_asymptotic_descriptive": float(rho.pvalue),
    }


def _fit_candidate(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    family: str,
    parameter: float | int,
) -> np.ndarray:
    if family == "ridge":
        model = Ridge(alpha=float(parameter))
        model.fit(x_train, y_train)
        return np.asarray(model.predict(x_test), dtype=float).ravel()
    y_mean = float(np.mean(y_train))
    y_scale = float(np.std(y_train, ddof=0))
    if y_scale <= 1e-12:
        return np.full(len(x_test), y_mean)
    components = max(1, min(int(parameter), len(y_train) - 1, x_train.shape[1]))
    model = PLSRegression(n_components=components, scale=False, max_iter=1_000)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(x_train, (y_train - y_mean) / y_scale)
        prediction = model.predict(x_test).ravel()
    return prediction * y_scale + y_mean


def _candidate_grid(scopes: Iterable[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for scope in scopes:
        for alpha in RIDGE_GRID:
            candidates.append(
                {
                    "scope": scope,
                    "family": "ridge",
                    "parameter": alpha,
                    "name": f"{scope}|ridge|{alpha:g}",
                    "complexity": (0 if scope != "combined" else 1, 0, -alpha),
                }
            )
        for component in PLS_GRID:
            candidates.append(
                {
                    "scope": scope,
                    "family": "pls",
                    "parameter": component,
                    "name": f"{scope}|pls|{component}",
                    "complexity": (0 if scope != "combined" else 1, 1, component),
                }
            )
    return candidates


def _one_se_choice(errors: dict[str, np.ndarray], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    mse = {name: float(np.mean(values**2)) for name, values in errors.items()}
    best_name = min(mse, key=lambda name: (mse[name], name))
    best_sq = errors[best_name] ** 2
    threshold = mse[best_name] + float(np.std(best_sq, ddof=1) / math.sqrt(len(best_sq)))
    eligible = [candidate for candidate in candidates if mse[candidate["name"]] <= threshold + 1e-12]
    return min(eligible, key=lambda item: (item["complexity"], mse[item["name"]], item["name"]))


def evaluate_q4(
    y: np.ndarray,
    cache: list[FoldCache],
    diagnostic_groups: bool = False,
) -> dict[str, Any]:
    """Repeated nested CV with scope, family and parameter selected inside."""

    y = np.asarray(y, dtype=float).ravel()
    main_scopes = ["grape", "wine", "combined"]
    candidates = _candidate_grid(main_scopes)
    prediction = np.full((OUTER_REPEATS, len(y)), np.nan)
    raw_prediction = np.full_like(prediction, np.nan)
    baseline = np.full_like(prediction, np.nan)
    selected_names: list[str] = []
    fixed_keys = [(scope, family) for scope in main_scopes for family in ["ridge", "pls"]]
    if diagnostic_groups:
        fixed_keys.extend(
            (scope, "ridge")
            for scope in ["grape_conventional", "grape_aroma", "wine_conventional", "wine_aroma"]
        )
    fixed_prediction = {
        key: np.full((OUTER_REPEATS, len(y)), np.nan) for key in fixed_keys
    }
    inner_choice_rows: list[dict[str, Any]] = []

    for fold_cache in cache:
        inner_residuals: dict[str, np.ndarray] = {
            candidate["name"]: np.full(len(fold_cache.train), np.nan) for candidate in candidates
        }
        local_position = {int(index): pos for pos, index in enumerate(fold_cache.train)}
        diagnostic_candidates = _candidate_grid([key[0] for key in fixed_keys if key[0] not in main_scopes])
        all_candidates = candidates + diagnostic_candidates
        # Remove duplicates caused by the group list construction.
        all_candidates = list({item["name"]: item for item in all_candidates}.values())
        diagnostic_residuals: dict[str, np.ndarray] = {
            item["name"]: np.full(len(fold_cache.train), np.nan) for item in all_candidates
        }
        for inner in fold_cache.inner:
            for candidate in all_candidates:
                scope = candidate["scope"]
                if scope not in inner["x"]:
                    continue
                x_train, x_valid, _ = inner["x"][scope]
                current = _fit_candidate(
                    x_train,
                    y[inner["train"]],
                    x_valid,
                    candidate["family"],
                    candidate["parameter"],
                )
                positions = [local_position[int(index)] for index in inner["valid"]]
                diagnostic_residuals[candidate["name"]][positions] = y[inner["valid"]] - current
        for candidate in candidates:
            inner_residuals[candidate["name"]] = diagnostic_residuals[candidate["name"]]
        if any(np.isnan(values).any() for values in inner_residuals.values()):
            raise RuntimeError("Incomplete inner predictions in Q4")
        selected = _one_se_choice(inner_residuals, candidates)
        selected_names.append(selected["name"])
        x_train, x_test, _ = fold_cache.outer_x[selected["scope"]]
        current_raw = _fit_candidate(
            x_train,
            y[fold_cache.train],
            x_test,
            selected["family"],
            selected["parameter"],
        )
        current = np.clip(current_raw, 0.0, 100.0)
        raw_prediction[fold_cache.repeat, fold_cache.test] = current_raw
        prediction[fold_cache.repeat, fold_cache.test] = current
        baseline[fold_cache.repeat, fold_cache.test] = float(np.mean(y[fold_cache.train]))
        inner_choice_rows.append(
            {
                "repeat": fold_cache.repeat,
                "fold": fold_cache.fold,
                "selected": selected["name"],
                "inner_rmse": float(np.sqrt(np.mean(inner_residuals[selected["name"]] ** 2))),
            }
        )

        if diagnostic_groups:
            for scope, family in fixed_keys:
                subset = [
                    item for item in all_candidates if item["scope"] == scope and item["family"] == family
                ]
                subset_errors = {item["name"]: diagnostic_residuals[item["name"]] for item in subset}
                chosen = _one_se_choice(subset_errors, subset)
                x_train, x_test, _ = fold_cache.outer_x[scope]
                fixed_prediction[(scope, family)][fold_cache.repeat, fold_cache.test] = np.clip(
                    _fit_candidate(
                        x_train,
                        y[fold_cache.train],
                        x_test,
                        family,
                        chosen["parameter"],
                    ),
                    0.0,
                    100.0,
                )

    if np.isnan(prediction).any() or np.isnan(baseline).any():
        raise RuntimeError("Incomplete outer predictions in Q4")
    repeated_y = np.tile(y, OUTER_REPEATS)
    metrics = single_metrics(repeated_y, prediction.ravel(), baseline.ravel())
    repeat_metrics = [
        {"repeat": repeat, **single_metrics(y, prediction[repeat], baseline[repeat])}
        for repeat in range(OUTER_REPEATS)
    ]
    result: dict[str, Any] = {
        "prediction": prediction,
        "raw_prediction": raw_prediction,
        "baseline": baseline,
        "metrics": metrics,
        "repeat_metrics": repeat_metrics,
        "selected": selected_names,
        "selected_counts": dict(Counter(selected_names)),
        "support_violations_before_clip": int(np.sum((raw_prediction < 0) | (raw_prediction > 100))),
        "support_violations_after_clip": int(np.sum((prediction < 0) | (prediction > 100))),
        "inner_choices": inner_choice_rows,
    }
    if diagnostic_groups:
        fixed_metrics: dict[str, Any] = {}
        for (scope, family), current_prediction in fixed_prediction.items():
            fixed_metrics[f"{scope}|{family}"] = {
                **single_metrics(repeated_y, current_prediction.ravel(), baseline.ravel()),
                "prediction": current_prediction,
            }
        result["fixed_candidates"] = fixed_metrics
    return result


def _model_permutation(
    evaluator: Any,
    observed_statistic: float,
    y: np.ndarray,
) -> dict[str, Any]:
    counts = [N_MODEL_PERM // len(PERMUTATION_SEEDS)] * len(PERMUTATION_SEEDS)
    for index in range(N_MODEL_PERM % len(PERMUTATION_SEEDS)):
        counts[index] += 1
    null_values: list[float] = []
    seed_rows: list[dict[str, Any]] = []
    total_exceed = 0
    for seed, repetitions in zip(PERMUTATION_SEEDS, counts, strict=True):
        rng = np.random.default_rng(seed)
        values: list[float] = []
        for _ in range(repetitions):
            permuted = y[rng.permutation(len(y))]
            result = evaluator(permuted)
            values.append(float(result["metrics"]["q2_vs_training_mean"]))
        exceed = int(np.sum(np.asarray(values) >= observed_statistic - 1e-14))
        total_exceed += exceed
        null_values.extend(values)
        p_seed = (exceed + 1) / (repetitions + 1)
        low, high = clopper_pearson(exceed, repetitions)
        seed_rows.append(
            {
                "seed": seed,
                "permutations": repetitions,
                "exceedances": exceed,
                "p_plus_one": p_seed,
                "mc_ci95": [low, high],
                "threshold_status": "pass" if high < 0.05 else "fail" if low >= 0.05 else "needs_review",
            }
        )
    p_value = (total_exceed + 1) / (N_MODEL_PERM + 1)
    low, high = clopper_pearson(total_exceed, N_MODEL_PERM)
    return {
        "permutations": N_MODEL_PERM,
        "observed_q2_vs_training_mean": observed_statistic,
        "exceedances": total_exceed,
        "p_plus_one": p_value,
        "mc_ci95": [low, high],
        "seed_grid": seed_rows,
        "null_quantiles": [float(value) for value in np.quantile(null_values, [0.025, 0.5, 0.975])],
        "selection_aware": True,
    }


def _paired_rmse_bootstrap(
    y: np.ndarray,
    first: np.ndarray,
    second: np.ndarray,
    seed: int,
    repetitions: int = 2_000,
) -> dict[str, Any]:
    y_rep = np.tile(np.asarray(y, dtype=float), first.shape[0])
    a = first.ravel()
    b = second.ravel()
    observed = float(np.sqrt(np.mean((y_rep - a) ** 2)) - np.sqrt(np.mean((y_rep - b) ** 2)))
    rng = np.random.default_rng(seed)
    values = np.empty(repetitions, dtype=float)
    n = len(y_rep)
    for iteration in range(repetitions):
        chosen = rng.integers(0, n, n)
        values[iteration] = (
            np.sqrt(np.mean((y_rep[chosen] - a[chosen]) ** 2))
            - np.sqrt(np.mean((y_rep[chosen] - b[chosen]) ** 2))
        )
    return {
        "rmse_first_minus_second": observed,
        "bootstrap_ci95": [float(value) for value in np.quantile(values, [0.025, 0.975])],
        "bootstrap_note": "paired resampling of repeated outer-fold residuals; model-selection uncertainty is additionally shown by repeat-wise metrics",
    }


def analyze_q4_revised(
    clean_dir: Path,
    results_dir: Path,
    figures_dir: Path,
    tasting: pd.DataFrame,
    trusted_panels: dict[str, int],
    fold_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    comparison_rows: list[dict[str, Any]] = []
    sensitivity_rows: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    color_results: dict[str, Any] = {}
    unit_results: dict[str, Any] = {}
    plot_payload: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    all_scopes = list(SCOPE_PARTS)
    for color in ["red", "white"]:
        raw = load_raw_color(clean_dir, color)
        panel = trusted_panels[color]
        quality = (
            tasting[(tasting.color == color) & (tasting.panel == panel)]
            .groupby("sample_id")
            .total.mean()
            .reindex(raw.sample_ids)
            .to_numpy(dtype=float)
        )
        cache = build_cache(raw, all_scopes, "q4", fold_records)
        observed = evaluate_q4(quality, cache, diagnostic_groups=True)
        permutation = _model_permutation(
            lambda current: evaluate_q4(current, cache, diagnostic_groups=False),
            observed["metrics"]["q2_vs_training_mean"],
            quality,
        )
        seed_thresholds = [row["threshold_status"] for row in permutation["seed_grid"]]
        metrics = observed["metrics"]
        if (
            metrics["q2_vs_training_mean"] > 0
            and metrics["relative_rmse_improvement"] >= 0.10
            and permutation["mc_ci95"][1] < 0.05
            and metrics["spearman_rho"] >= 0.50
            and all(item == "pass" for item in seed_thresholds)
            and observed["support_violations_after_clip"] == 0
        ):
            evaluation_status = "pass"
        elif metrics["q2_vs_training_mean"] <= 0 or metrics["relative_rmse_improvement"] <= 0:
            evaluation_status = "fail"
        else:
            evaluation_status = "needs_review"

        fixed = observed["fixed_candidates"]
        for key, item in fixed.items():
            scope, family = key.split("|")
            row = {"color": color, "scope": scope, "family": family}
            row.update({name: value for name, value in item.items() if name != "prediction"})
            row["baseline_gain_status"] = "pass" if item["q2_vs_training_mean"] > 0 else "fail"
            comparison_rows.append(row)
            if scope not in {"grape", "wine", "combined"}:
                group_rows.append(row)
        comparison_rows.append(
            {
                "color": color,
                "scope": "selected_inside_outer_fold",
                "family": "scope+family+parameter",
                **metrics,
                "status": evaluation_status,
            }
        )
        grape_best_key = min(
            ["grape|ridge", "grape|pls"], key=lambda key: fixed[key]["rmse"]
        )
        combined_best_key = min(
            ["combined|ridge", "combined|pls"], key=lambda key: fixed[key]["rmse"]
        )
        wine_increment = _paired_rmse_bootstrap(
            quality,
            fixed[grape_best_key]["prediction"],
            fixed[combined_best_key]["prediction"],
            SEED + (1 if color == "red" else 2),
        )
        # Positive first-minus-second means adding the wine block reduced RMSE.
        increment_ci = wine_increment["bootstrap_ci95"]
        wine_increment["paired_descriptive_status"] = (
            "pass" if increment_ci[0] > 0 else "fail" if increment_ci[1] < 0 else "needs_review"
        )
        wine_increment["status"] = (
            "fail"
            if increment_ci[1] < 0
            else "needs_review"
        )
        wine_increment["status_reason"] = (
            "The best fixed grape and combined candidates were identified after comparing outer errors; "
            "the fully selection-aware scope/family pipeline is the primary inference, so a positive paired interval alone is not promoted to pass."
        )
        wine_increment["grape_candidate"] = grape_best_key
        wine_increment["combined_candidate"] = combined_best_key

        for scenario, mode, winsor in [
            ("aroma_half_min", "half_min", False),
            ("conventional_winsor_5_95", "zero", True),
        ]:
            sensitivity_cache = build_cache(
                raw, all_scopes, f"q4_{scenario}", None, aroma_mode=mode, winsor=winsor
            )
            current = evaluate_q4(quality, sensitivity_cache, diagnostic_groups=False)
            sensitivity_rows.append(
                {
                    "color": color,
                    "scenario": scenario,
                    **current["metrics"],
                    "status": (
                        "pass"
                        if np.sign(current["metrics"]["q2_vs_training_mean"])
                        == np.sign(metrics["q2_vs_training_mean"])
                        else "needs_review"
                    ),
                }
            )
        alternative_panel = 3 - panel
        alternative_quality = (
            tasting[(tasting.color == color) & (tasting.panel == alternative_panel)]
            .groupby("sample_id")
            .total.mean()
            .reindex(raw.sample_ids)
            .to_numpy(dtype=float)
        )
        alternative = evaluate_q4(alternative_quality, cache, diagnostic_groups=False)
        sensitivity_rows.append(
            {
                "color": color,
                "scenario": f"alternative_panel_{alternative_panel}_target",
                **alternative["metrics"],
                "status": (
                    "pass"
                    if np.sign(alternative["metrics"]["q2_vs_training_mean"])
                    == np.sign(metrics["q2_vs_training_mean"])
                    else "needs_review"
                ),
            }
        )

        unit_checks: dict[str, Any] = {}
        for factor in [0.001, 1000.0]:
            scaled_raw = load_raw_color(clean_dir, color, aroma_factor=factor)
            scaled_cache = build_cache(scaled_raw, all_scopes, f"q4_unit_{factor:g}", None)
            scaled = evaluate_q4(quality, scaled_cache, diagnostic_groups=False)
            max_difference = float(np.max(np.abs(scaled["prediction"] - observed["prediction"])))
            selections_equal = scaled["selected"] == observed["selected"]
            q2_difference = float(
                scaled["metrics"]["q2_vs_training_mean"] - metrics["q2_vs_training_mean"]
            )
            unit_checks[f"factor_{factor:g}"] = {
                "max_abs_prediction_difference": max_difference,
                "selection_identical": selections_equal,
                "q2_difference": q2_difference,
                "status": (
                    "pass"
                    if max_difference <= 1e-9
                    and abs(q2_difference) <= 1e-9
                    and selections_equal
                    else "fail"
                ),
            }
        unit_results[color] = unit_checks

        full_transformer = ScopeTransformer.fit(raw, "combined", np.arange(len(raw.sample_ids)))
        full_x = full_transformer.transform(raw, np.arange(len(raw.sample_ids)))
        design_rank = int(np.linalg.matrix_rank(full_x - full_x.mean(axis=0)))
        nullity = int(full_x.shape[1] - design_rank)

        for repeat in range(OUTER_REPEATS):
            for index, sample_id in enumerate(raw.sample_ids):
                prediction_rows.append(
                    {
                        "color": color,
                        "repeat": repeat,
                        "sample_id": int(sample_id),
                        "observed_quality": float(quality[index]),
                        "predicted_quality": float(observed["prediction"][repeat, index]),
                        "training_mean_baseline": float(observed["baseline"][repeat, index]),
                    }
                )
        for row in observed["inner_choices"]:
            selection_rows.append({"color": color, **row})
        plot_payload[color] = (np.tile(quality, OUTER_REPEATS), observed["prediction"].ravel())
        color_results[color] = {
            "evaluation_status": evaluation_status,
            "trusted_panel": panel,
            "metrics": metrics,
            "repeat_metrics": observed["repeat_metrics"],
            "selected_counts": observed["selected_counts"],
            "model_selection_frequency": {
                key: value / len(observed["selected"]) for key, value in observed["selected_counts"].items()
            },
            "permutation": permutation,
            "support_violations_before_clip": observed["support_violations_before_clip"],
            "support_violations_after_clip": observed["support_violations_after_clip"],
            "best_grape_candidate": grape_best_key,
            "best_combined_candidate": combined_best_key,
            "wine_block_increment": wine_increment,
            "alternative_panel": alternative_panel,
            "alternative_panel_metrics": alternative["metrics"],
            "design_features": int(full_x.shape[1]),
            "centered_design_rank": design_rank,
            "design_nullity": nullity,
            "individual_effect_identifiability_status": "needs_review" if nullity > 0 else "pass",
            "individual_effect_policy": "No single-variable coefficient is interpreted as an identifiable effect; block-level out-of-fold comparisons are primary.",
        }

    pd.DataFrame(comparison_rows).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )
    pd.DataFrame(group_rows).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )
    pd.DataFrame(sensitivity_rows).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )
    pd.DataFrame(prediction_rows).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )
    pd.DataFrame(selection_rows).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )
    # Keep the required legacy filename, but make its non-identifiability explicit.
    pd.DataFrame(
        [
            {
                "color": color,
                "feature": "individual coefficients intentionally not ranked",
                "effect_status": "needs_review",
                "reason": color_results[color]["individual_effect_policy"],
                "centered_rank": color_results[color]["centered_design_rank"],
                "features": color_results[color]["design_features"],
                "nullity": color_results[color]["design_nullity"],
            }
            for color in ["red", "white"]
        ]
    ).to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.3), constrained_layout=True)
    for axis, color in zip(axes, ["red", "white"], strict=True):
        actual, predicted = plot_payload[color]
        axis.scatter(actual, predicted, alpha=0.55, s=28, color="#7f1d1d" if color == "red" else "#b58900")
        low = float(min(actual.min(), predicted.min()) - 1)
        high = float(max(actual.max(), predicted.max()) + 1)
        axis.plot([low, high], [low, high], "--", color="0.35", linewidth=1)
        axis.set(title=color.title(), xlabel="Observed quality", ylabel="Repeated nested-CV prediction", xlim=(low, high), ylim=(low, high))
        axis.grid(alpha=0.2)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220, metadata={"Software": "CUMCM blind revision"})
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)

    comparison = pd.DataFrame(comparison_rows)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), constrained_layout=True)
    for axis, color in zip(axes, ["red", "white"], strict=True):
        data = comparison[(comparison.color == color) & comparison.scope.isin(["grape", "wine", "combined"])]
        labels = [f"{row.scope}\n{row.family}" for row in data.itertuples()]
        axis.bar(labels, data.rmse, color="#2563eb")
        axis.tick_params(axis="x", labelrotation=35, labelsize=7)
        axis.set(title=color.title(), ylabel="Repeated nested-CV RMSE")
        axis.grid(axis="y", alpha=0.2)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220, metadata={"Software": "CUMCM blind revision"})
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)

    overall = status_rollup(item["evaluation_status"] for item in color_results.values())
    summary = {
        "overall_evaluation_status": overall,
        "sensory_replacement_status": "pass" if overall == "pass" else "fail",
        "decision_rule": <LONG_QUOTE_REDACTED>,
        "resampling": {
            "outer": f"{OUTER_REPEATS} deterministic balanced repeats x {OUTER_FOLDS} folds",
            "inner": f"{INNER_FOLDS} deterministic folds refitted within each outer training set",
            "selection": "one-standard-error rule jointly over grape/wine/combined scope, ridge/PLS family, and fixed candidate parameter grids",
        },
        "colors": color_results,
    }
    return summary, unit_results


@dataclass
class ResponseTransformer:
    conv_center: np.ndarray
    conv_scale: np.ndarray
    conv_nonnegative: np.ndarray
    conv_lstar: np.ndarray
    aroma_reference: np.ndarray
    aroma_center: np.ndarray
    aroma_scale: np.ndarray

    @classmethod
    def fit(cls, raw: RawColorData, train: np.ndarray) -> "ResponseTransformer":
        conv = raw.parts["wine_conv"][train]
        if not np.isfinite(conv).all():
            raise RuntimeError("Wine conventional response contains missing values")
        conv_center = conv.mean(axis=0)
        conv_scale = conv.std(axis=0, ddof=0)
        conv_scale = np.where(conv_scale > 1e-12, conv_scale, 1.0)
        conv_nonnegative = np.min(conv, axis=0) >= 0
        conv_lstar = np.array(["|L*" in name for name in raw.names["wine_conv"]], dtype=bool)

        aroma = raw.parts["wine_aroma"][train]
        aroma_reference = np.ones(aroma.shape[1], dtype=float)
        filled = np.nan_to_num(aroma, nan=0.0)
        if np.any(filled < 0):
            raise RuntimeError("Wine aroma response contains a negative concentration")
        for column in range(aroma.shape[1]):
            positive = aroma[np.isfinite(aroma[:, column]) & (aroma[:, column] > 0), column]
            aroma_reference[column] = (
                float(np.median(positive)) if positive.size else float(raw.aroma_factor)
            )
            if aroma_reference[column] <= 0 or not np.isfinite(aroma_reference[column]):
                aroma_reference[column] = float(raw.aroma_factor)
        transformed = np.log1p(filled / aroma_reference)
        aroma_center = transformed.mean(axis=0)
        aroma_scale = transformed.std(axis=0, ddof=0)
        aroma_scale = np.where(aroma_scale > 1e-12, aroma_scale, 1.0)
        return cls(
            conv_center,
            conv_scale,
            conv_nonnegative,
            conv_lstar,
            aroma_reference,
            aroma_center,
            aroma_scale,
        )

    def transform(self, raw: RawColorData, indices: np.ndarray) -> np.ndarray:
        conv = raw.parts["wine_conv"][indices]
        aroma = np.nan_to_num(raw.parts["wine_aroma"][indices], nan=0.0)
        transformed_aroma = np.log1p(aroma / self.aroma_reference)
        return np.column_stack(
            [
                (conv - self.conv_center) / self.conv_scale,
                (transformed_aroma - self.aroma_center) / self.aroma_scale,
            ]
        )

    def constrain(self, prediction: np.ndarray) -> tuple[np.ndarray, int, int]:
        prediction = np.asarray(prediction, dtype=float).copy()
        before = 0
        conv_count = len(self.conv_center)
        conv = prediction[:, :conv_count]
        lower = (0.0 - self.conv_center) / self.conv_scale
        upper = (100.0 - self.conv_center) / self.conv_scale
        for column in range(conv.shape[1]):
            if self.conv_nonnegative[column]:
                before += int(np.sum(conv[:, column] < lower[column]))
                conv[:, column] = np.maximum(conv[:, column], lower[column])
            if self.conv_lstar[column]:
                before += int(np.sum(conv[:, column] > upper[column]))
                conv[:, column] = np.minimum(conv[:, column], upper[column])
        aroma = prediction[:, conv_count:]
        aroma_lower = (0.0 - self.aroma_center) / self.aroma_scale
        before += int(np.sum(aroma < aroma_lower[None, :]))
        aroma[:] = np.maximum(aroma, aroma_lower[None, :])
        after = 0
        return prediction, before, after


def _fit_pls_multi(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    components: int,
) -> np.ndarray:
    components = max(1, min(int(components), len(x_train) - 1, x_train.shape[1]))
    model = PLSRegression(n_components=components, scale=False, max_iter=1_000)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(x_train, y_train)
        prediction = model.predict(x_test)
    return np.asarray(prediction, dtype=float)


def evaluate_q3(
    raw: RawColorData,
    cache: list[FoldCache],
    response_order: np.ndarray | None = None,
    collect_response_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Repeated nested PLS for a fixed, predeclared wine response set."""

    if response_order is None:
        response_order = np.arange(len(raw.sample_ids), dtype=int)
    response_order = np.asarray(response_order, dtype=int)
    # Permutation is represented by a row-reordered response-only RawColorData.
    response_raw = RawColorData(
        color=raw.color,
        sample_ids=raw.sample_ids,
        parts={
            **raw.parts,
            "wine_conv": raw.parts["wine_conv"][response_order],
            "wine_aroma": raw.parts["wine_aroma"][response_order],
        },
        names=raw.names,
        aroma_factor=raw.aroma_factor,
    )
    response_features = raw.parts["wine_conv"].shape[1] + raw.parts["wine_aroma"].shape[1]
    actual = np.full((OUTER_REPEATS, len(raw.sample_ids), response_features), np.nan)
    prediction = np.full_like(actual, np.nan)
    raw_prediction = np.full_like(actual, np.nan)
    baseline = np.zeros_like(actual)
    components_selected: list[int] = []
    violations_before = 0
    violations_after = 0

    for fold_cache in cache:
        component_errors = {component: [] for component in PLS_GRID}
        for inner_index, inner in enumerate(fold_cache.inner):
            response_transformer = ResponseTransformer.fit(response_raw, inner["train"])
            y_train = response_transformer.transform(response_raw, inner["train"])
            y_valid = response_transformer.transform(response_raw, inner["valid"])
            x_train, x_valid, _ = inner["x"]["grape"]
            for component in PLS_GRID:
                current = _fit_pls_multi(x_train, y_train, x_valid, component)
                current, _, _ = response_transformer.constrain(current)
                component_errors[component].extend(np.mean((y_valid - current) ** 2, axis=1).tolist())
            if collect_response_records is not None:
                _record_fit(
                    collect_response_records,
                    raw,
                    "q3_response",
                    fold_cache.repeat,
                    f"outer-{fold_cache.fold}/inner-{inner_index}",
                    "fixed_wine_response_set",
                    inner["train"],
                    inner["valid"],
                )
        means = {component: float(np.mean(values)) for component, values in component_errors.items()}
        best = min(means, key=lambda component: (means[component], component))
        # One-standard-error preference for the smaller component count.
        best_values = np.asarray(component_errors[best], dtype=float)
        threshold = means[best] + float(np.std(best_values, ddof=1) / math.sqrt(len(best_values)))
        eligible = [component for component in PLS_GRID if means[component] <= threshold + 1e-12]
        selected = min(eligible)
        components_selected.append(selected)

        transformer = ResponseTransformer.fit(response_raw, fold_cache.train)
        y_train = transformer.transform(response_raw, fold_cache.train)
        y_test = transformer.transform(response_raw, fold_cache.test)
        x_train, x_test, _ = fold_cache.outer_x["grape"]
        current_raw = _fit_pls_multi(x_train, y_train, x_test, selected)
        current, before, after = transformer.constrain(current_raw)
        actual[fold_cache.repeat, fold_cache.test] = y_test
        raw_prediction[fold_cache.repeat, fold_cache.test] = current_raw
        prediction[fold_cache.repeat, fold_cache.test] = current
        violations_before += before
        violations_after += after
        if collect_response_records is not None:
            _record_fit(
                collect_response_records,
                raw,
                "q3_response",
                fold_cache.repeat,
                f"outer-{fold_cache.fold}",
                "fixed_wine_response_set",
                fold_cache.train,
                fold_cache.test,
            )

    if np.isnan(actual).any() or np.isnan(prediction).any():
        raise RuntimeError("Incomplete Q3 repeated outer predictions")
    residual_sse = float(np.sum((actual - prediction) ** 2))
    baseline_sse = float(np.sum((actual - baseline) ** 2))
    q2 = 1 - residual_sse / baseline_sse if baseline_sse > 0 else math.nan
    repeat_metrics: list[dict[str, Any]] = []
    for repeat in range(OUTER_REPEATS):
        repeat_residual = float(np.sum((actual[repeat] - prediction[repeat]) ** 2))
        repeat_baseline = float(np.sum(actual[repeat] ** 2))
        repeat_metrics.append(
            {
                "repeat": repeat,
                "q2_vs_training_mean": 1 - repeat_residual / repeat_baseline,
                "normalized_rmse": float(np.sqrt(np.mean((actual[repeat] - prediction[repeat]) ** 2))),
                "baseline_normalized_rmse": float(np.sqrt(np.mean(actual[repeat] ** 2)),),
            }
        )
    return {
        "actual": actual,
        "prediction": prediction,
        "raw_prediction": raw_prediction,
        "metrics": {
            "q2_vs_training_mean": q2,
            "normalized_rmse": float(np.sqrt(np.mean((actual - prediction) ** 2))),
            "baseline_normalized_rmse": float(np.sqrt(np.mean(actual**2))),
        },
        "repeat_metrics": repeat_metrics,
        "selected_components": components_selected,
        "selected_component_counts": dict(Counter(components_selected)),
        "support_violations_before_constraint": violations_before,
        "support_violations_after_constraint": violations_after,
    }


def analyze_q3_revised(
    clean_dir: Path,
    results_dir: Path,
    figures_dir: Path,
    fold_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    all_links: list[pd.DataFrame] = []
    prediction_rows: list[dict[str, Any]] = []
    color_results: dict[str, Any] = {}
    unit_results: dict[str, Any] = {}
    plot_payload: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for color_index, color in enumerate(["red", "white"]):
        raw = load_raw_color(clean_dir, color)
        cache = build_cache(raw, ["grape"], "q3", fold_records)
        observed = evaluate_q3(raw, cache, collect_response_records=fold_records)
        permutation = _model_permutation(
            lambda order_as_values: evaluate_q3(
                raw,
                cache,
                response_order=np.asarray(order_as_values, dtype=int),
            ),
            observed["metrics"]["q2_vs_training_mean"],
            np.arange(len(raw.sample_ids), dtype=float),
        )

        links, link_summary = permutation_link_family(clean_dir, color, SEED + 300 + color_index)
        all_links.append(links)
        significant = links[links.significant_200k]
        q2 = observed["metrics"]["q2_vs_training_mean"]
        seed_thresholds = [row["threshold_status"] for row in permutation["seed_grid"]]
        if (
            q2 > 0
            and permutation["mc_ci95"][1] < 0.05
            and all(item == "pass" for item in seed_thresholds)
            and all(item["q2_vs_training_mean"] > 0 for item in observed["repeat_metrics"])
            and observed["support_violations_after_constraint"] == 0
        ):
            mapping_status = "pass"
        elif q2 <= 0:
            mapping_status = "fail"
        else:
            mapping_status = "needs_review"
        overall_status = "needs_review" if len(significant) > 0 and mapping_status != "pass" else mapping_status

        unit_checks: dict[str, Any] = {}
        for factor in [0.001, 1000.0]:
            scaled_raw = load_raw_color(clean_dir, color, aroma_factor=factor)
            scaled_cache = build_cache(scaled_raw, ["grape"], f"q3_unit_{factor:g}", None)
            scaled = evaluate_q3(scaled_raw, scaled_cache)
            difference = float(np.max(np.abs(scaled["prediction"] - observed["prediction"])))
            actual_difference = float(np.max(np.abs(scaled["actual"] - observed["actual"])))
            selection_equal = scaled["selected_components"] == observed["selected_components"]
            q2_difference = float(
                scaled["metrics"]["q2_vs_training_mean"] - q2
            )
            unit_checks[f"factor_{factor:g}"] = {
                "max_abs_prediction_difference": difference,
                "max_abs_transformed_response_difference": actual_difference,
                "selection_identical": selection_equal,
                "q2_difference": q2_difference,
                "status": (
                    "pass"
                    if difference <= 1e-9
                    and actual_difference <= 1e-9
                    and abs(q2_difference) <= 1e-9
                    and selection_equal
                    else "fail"
                ),
            }
        unit_results[color] = unit_checks

        # A common PCA basis is fitted only for visualisation after validation.
        actual_rows = observed["actual"].reshape(-1, observed["actual"].shape[-1])
        predicted_rows = observed["prediction"].reshape(-1, observed["prediction"].shape[-1])
        pca = PCA(n_components=1).fit(actual_rows)
        actual_pc = pca.transform(actual_rows).ravel()
        predicted_pc = pca.transform(predicted_rows).ravel()
        plot_payload[color] = (actual_pc, predicted_pc)
        row_position = 0
        for repeat in range(OUTER_REPEATS):
            for sample_id in raw.sample_ids:
                prediction_rows.append(
                    {
                        "color": color,
                        "repeat": repeat,
                        "sample_id": int(sample_id),
                        "observed_wine_block_pc1": float(actual_pc[row_position]),
                        "predicted_wine_block_pc1": float(predicted_pc[row_position]),
                    }
                )
                row_position += 1
        color_results[color] = {
            "status": overall_status,
            "mapping_status": mapping_status,
            "samples": int(len(raw.sample_ids)),
            "grape_task_features_before_fold_filter": int(
                raw.parts["grape_conv"].shape[1] + raw.parts["grape_aroma"].shape[1]
            ),
            "wine_response_features_fixed": int(
                raw.parts["wine_conv"].shape[1] + raw.parts["wine_aroma"].shape[1]
            ),
            "metrics": observed["metrics"],
            "repeat_metrics": observed["repeat_metrics"],
            "selected_component_counts": observed["selected_component_counts"],
            "permutation": permutation,
            "support_violations_before_constraint": observed["support_violations_before_constraint"],
            "support_violations_after_constraint": observed["support_violations_after_constraint"],
            "multiple_testing": link_summary,
            "significant_links": int(len(significant)),
            "significant_same_aroma_links": int(
                np.sum(significant.link_type == "same_aroma_compound")
            ),
            "top_links": significant.head(8).to_dict(orient="records") if len(significant) else links.head(8).to_dict(orient="records"),
        }

    links_frame = pd.concat(all_links, ignore_index=True)
    links_frame.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")
    links_frame.groupby("color", group_keys=False).head(20).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )
    pd.DataFrame(prediction_rows).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )

    fig, axes = plt.subplots(1, 2, figsize=(9.5, 4.2), constrained_layout=True)
    for axis, color in zip(axes, ["red", "white"], strict=True):
        actual, predicted = plot_payload[color]
        axis.scatter(actual, predicted, alpha=0.55, s=26, color="#7f1d1d" if color == "red" else "#b58900")
        low = float(min(actual.min(), predicted.min()))
        high = float(max(actual.max(), predicted.max()))
        axis.plot([low, high], [low, high], "--", color="0.4", linewidth=1)
        axis.set(title=color.title(), xlabel="Observed wine-block PC1", ylabel="Repeated-CV predicted PC1")
        axis.grid(alpha=0.2)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220, metadata={"Software": "CUMCM blind revision"})
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)

    summary = {
        "overall_status": status_rollup(item["status"] for item in color_results.values()),
        "multiple_testing": "one colour-wise family; tie-preserving label permutation; BH once at 100k and 200k checkpoints",
        "response_definition": "all attachment wine conventional and aroma columns fixed before modelling; non-detections are zero under the primary assumption",
        "colors": color_results,
    }
    return summary, unit_results
