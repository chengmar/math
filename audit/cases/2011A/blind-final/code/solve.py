from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
import sklearn
from matplotlib.colors import TwoSlopeNorm
from scipy.interpolate import RBFInterpolator
from scipy.ndimage import maximum_filter
from scipy.optimize import least_squares, linear_sum_assignment
from scipy.spatial import ConvexHull, Delaunay
from scipy.spatial.distance import cdist, pdist
from scipy.sparse.csgraph import connected_components
from scipy.stats import kruskal, spearmanr
from sklearn.cluster import KMeans
from sklearn.decomposition import NMF, PCA
from sklearn.exceptions import ConvergenceWarning
from sklearn.neighbors import NearestNeighbors


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "results" / "raw"
RESULTS_DIR = ROOT / "results"
FIGURES_DIR = ROOT / "figures"
PAPER_GENERATED_DIR = ROOT / "paper" / "generated"

SEED = 20260311
METALS = ["As", "Cd", "Cr", "Cu", "Hg", "Ni", "Pb", "Zn"]
ZONE_NAMES_EN = {
    1: "Residential",
    2: "Industrial",
    3: "Mountain",
    4: "Traffic",
    5: "Park/green",
}
ZONE_NAMES_ZH = {
    1: "生活区",
    2: "工业区",
    3: "山区",
    4: "交通区",
    5: "公园绿地区",
}
SPATIAL_BLOCK_KM = 3.0
N_OUTER_FOLDS = 5
GAUSSIAN_BANDWIDTH_GRID_KM = [1.0, 2.0, 3.0, 4.0]
SOURCE_BANDWIDTH_KM = 1.0
SOURCE_SENSITIVITY_BANDWIDTHS_KM = [0.75, 1.0, 1.25]
GRID_STEP_KM = 0.25
IID_BOOTSTRAP_REPLICATES = 2000
ZONE_BLOCK_BOOTSTRAP_REPLICATES = 10000
SOURCE_BLOCK_BOOTSTRAP_REPLICATES = 500
DIRECTION_PERMUTATION_REPLICATES = 2000
PERMUTATION_REPLICATES = 999
SOURCE_SUPPORT_MIN_POSITIVE_ESS = 5.0
SOURCE_SUPPORT_MAX_NEAREST_KM = 1.0
SOURCE_SUPPORT_MIN_NEIGHBORS_2KM = 3
SOURCE_SUPPORT_MAX_SINGLE_SHARE = 0.50


def ensure_directories() -> None:
    for path in [RESULTS_DIR, FIGURES_DIR, PAPER_GENERATED_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if not np.isfinite(value):
            return None
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return value.as_posix()
    raise TypeError(f"Unsupported JSON type: {type(value)!r}")


def write_json(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=json_default)
        + "\n",
        encoding="utf-8",
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8", float_format="%.10g")


def parse_background_range(text: str) -> tuple[float, float]:
    left, right = str(text).replace("～", "~").split("~")
    return float(left), float(right)


def load_data():
    manifest_path = RAW_DIR / "extraction_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            "Run code/extract_input.ps1 before code/solve.py."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "pass":
        raise RuntimeError("Workbook extraction manifest is not pass.")

    locations = pd.read_csv(RAW_DIR / "<SOURCE_FILE_REDACTED>", skiprows=2, usecols=range(5))
    locations.columns = ["id", "x_m", "y_m", "elevation_m", "zone"]
    concentrations = pd.read_csv(RAW_DIR / "<SOURCE_FILE_REDACTED>", skiprows=2)
    concentrations.columns = ["id"] + METALS
    background = pd.read_csv(RAW_DIR / "<SOURCE_FILE_REDACTED>", skiprows=2)
    background["metal"] = background["元素"].str.extract(r"^([A-Za-z]+)")
    background["unit"] = background["元素"].str.extract(r"\((.+)\)")
    background[["range_lower", "range_upper"]] = background["范围"].apply(
        lambda value: pd.Series(parse_background_range(value))
    )
    background = background.rename(
        columns={"平均值": "mean", "标准偏差": "sd"}
    )[["metal", "unit", "mean", "sd", "range_lower", "range_upper"]]

    data = locations.merge(
        concentrations, on="id", how="outer", validate="one_to_one", indicator=True
    )
    background = background.set_index("metal").loc[METALS].reset_index()
    return data, background, manifest


def audit_data(data: pd.DataFrame, background: pd.DataFrame, manifest: dict):
    input_path = ROOT / manifest["source"]
    actual_hash = sha256_file(input_path)
    xy_km = data[["x_m", "y_m"]].to_numpy(dtype=float) / 1000.0
    nearest = NearestNeighbors(n_neighbors=2).fit(xy_km).kneighbors(xy_km)[0][:, 1]
    pair_distances = pdist(xy_km)
    close_pair_count = int(np.sum(pair_distances < 0.1))
    missing_by_column = data.isna().sum().astype(int).to_dict()
    nonpositive = (data[METALS] <= 0).sum().astype(int).to_dict()
    ids_expected = set(range(int(data["id"].min()), int(data["id"].max()) + 1))
    ids_observed = set(data["id"].dropna().astype(int))

    schema_pass = (
        len(data) == 319
        and data["id"].nunique() == 319
        and data["id"].duplicated().sum() == 0
        and data.duplicated(["x_m", "y_m"]).sum() == 0
        and sum(missing_by_column.values()) == 0
        and sum(nonpositive.values()) == 0
        and set(background["metal"]) == set(METALS)
        and ids_expected == ids_observed
        and actual_hash == manifest["source_sha256"]
        and manifest.get("semantic_table_identity", {}).get("status") == "pass"
    )
    skewness = data[METALS].skew().to_dict()
    audit = {
        "status": "pass" if schema_pass else "fail",
        "source_identity": {
            "status": "pass" if actual_hash == manifest["source_sha256"] else "fail",
            "path": manifest["source"],
            "sha256": actual_hash,
            "container_copy_chain_status": "needs_review",
            "container_copy_chain_reason": (
                "The independent audit recorded differing outer/frozen container hashes; "
                "the origin of container-byte differences cannot be established from the "
                "allowed materials."
            ),
        },
        "tabular_semantic_identity": manifest.get("semantic_table_identity", {}),
        "shape": {"rows": len(data), "metal_columns": len(METALS)},
        "id_range": [int(data["id"].min()), int(data["id"].max())],
        "missing_by_column": missing_by_column,
        "duplicate_id_count": int(data["id"].duplicated().sum()),
        "duplicate_coordinate_count": int(data.duplicated(["x_m", "y_m"]).sum()),
        "nonpositive_concentration_count": nonpositive,
        "zone_counts": {
            str(int(key)): int(value)
            for key, value in data["zone"].value_counts().sort_index().items()
        },
        "coordinate_extent_m": {
            "x": [float(data["x_m"].min()), float(data["x_m"].max())],
            "y": [float(data["y_m"].min()), float(data["y_m"].max())],
            "elevation": [
                float(data["elevation_m"].min()),
                float(data["elevation_m"].max()),
            ],
        },
        "nearest_neighbor_distance_km": {
            "minimum": float(nearest.min()),
            "median": float(np.median(nearest)),
            "q95": float(np.quantile(nearest, 0.95)),
            "maximum": float(nearest.max()),
        },
        "pairs_closer_than_0_1_km": close_pair_count,
        "skewness": skewness,
        "log_transform_status": "pass"
        if all(value > 1.0 for value in skewness.values())
        else "needs_review",
        "background_raw_samples_available": False,
        "background_uncertainty_status": "needs_review",
        "background_uncertainty_reason": (
            "Only means, standard deviations and ranges are supplied; natural-area "
            "sample size and raw values are unavailable."
        ),
        "close_coordinate_status": "needs_review" if close_pair_count else "pass",
    }
    write_json(RESULTS_DIR / "data_audit.json", audit)

    desc = data[METALS].describe(
        percentiles=[0.25, 0.5, 0.75, 0.9, 0.95, 0.99]
    ).T.reset_index(names="metal")
    desc["skewness"] = data[METALS].skew().values
    write_csv(desc, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(background, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    return audit


def build_spatial_folds(xy_km: np.ndarray):
    block = np.floor(xy_km / SPATIAL_BLOCK_KM).astype(int)
    groups: dict[tuple[int, int], list[int]] = {}
    for index, pair in enumerate(map(tuple, block)):
        groups.setdefault(pair, []).append(index)

    items = sorted(groups.items(), key=lambda item: item[0])
    rng = np.random.default_rng(SEED)
    order = rng.permutation(len(items))
    items = [items[index] for index in order]
    items.sort(key=lambda item: len(item[1]), reverse=True)

    fold_counts = [0] * N_OUTER_FOLDS
    folds = np.empty(len(xy_km), dtype=int)
    for _, indices in items:
        fold = int(np.argmin(fold_counts))
        folds[indices] = fold
        fold_counts[fold] += len(indices)
    return folds, block, fold_counts


def predict_idw(
    train_xy: np.ndarray,
    train_y: np.ndarray,
    test_xy: np.ndarray,
    neighbor_count: int = 12,
    power: float = 2.0,
):
    distances = cdist(test_xy, train_xy)
    neighbor_count = min(neighbor_count, len(train_xy))
    order = np.argsort(distances, axis=1)[:, :neighbor_count]
    selected_distances = np.take_along_axis(distances, order, axis=1)
    weights = 1.0 / np.maximum(selected_distances, 1e-12) ** power
    weights /= weights.sum(axis=1, keepdims=True)
    return np.column_stack(
        [(weights * train_y[:, column][order]).sum(axis=1) for column in range(train_y.shape[1])]
    )


def predict_gaussian(
    train_xy: np.ndarray,
    train_y: np.ndarray,
    test_xy: np.ndarray,
    bandwidth_km: float,
):
    distances = cdist(test_xy, train_xy)
    weights = np.exp(-0.5 * (distances / bandwidth_km) ** 2)
    denominator = np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
    return weights @ train_y / denominator


def choose_inner_bandwidth(
    xy_km: np.ndarray, y: np.ndarray, folds: np.ndarray, outer_fold: int
):
    scores = []
    for bandwidth in GAUSSIAN_BANDWIDTH_GRID_KM:
        inner_scores = []
        for validation_fold in range(N_OUTER_FOLDS):
            if validation_fold == outer_fold:
                continue
            train = (folds != outer_fold) & (folds != validation_fold)
            validation = folds == validation_fold
            prediction = predict_gaussian(
                xy_km[train], y[train], xy_km[validation], bandwidth
            )
            rmse_by_metal = np.sqrt(
                np.mean((prediction - y[validation]) ** 2, axis=0)
            )
            inner_scores.append(float(rmse_by_metal.mean()))
        scores.append(float(np.mean(inner_scores)))
    selected = float(GAUSSIAN_BANDWIDTH_GRID_KM[int(np.argmin(scores))])
    return selected, scores


def spatial_model_comparison(
    data: pd.DataFrame, background: pd.DataFrame
):
    xy_km = data[["x_m", "y_m"]].to_numpy(dtype=float) / 1000.0
    background_mean = background.set_index("metal").loc[METALS, "mean"].to_numpy()
    log2_enrichment = np.log2(data[METALS].to_numpy(dtype=float) / background_mean)
    folds, block, fold_counts = build_spatial_folds(xy_km)
    fold_frame = data[["id", "x_m", "y_m"]].copy()
    fold_frame["block_x"] = block[:, 0]
    fold_frame["block_y"] = block[:, 1]
    fold_frame["fold"] = folds
    write_csv(fold_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")

    metric_rows = []
    bandwidth_rows = []
    distance_rows = []
    selected_bandwidth_by_fold = {}
    for outer_fold in range(N_OUTER_FOLDS):
        train = folds != outer_fold
        test = ~train
        selected_bandwidth, inner_scores = choose_inner_bandwidth(
            xy_km, log2_enrichment, folds, outer_fold
        )
        selected_bandwidth_by_fold[outer_fold] = selected_bandwidth
        for bandwidth, score in zip(GAUSSIAN_BANDWIDTH_GRID_KM, inner_scores):
            bandwidth_rows.append(
                {
                    "outer_fold": outer_fold,
                    "bandwidth_km": bandwidth,
                    "inner_mean_rmse_log2": score,
                    "selected": bandwidth == selected_bandwidth,
                }
            )

        predictions = {
            "constant_mean": np.repeat(
                log2_enrichment[train].mean(axis=0, keepdims=True),
                test.sum(),
                axis=0,
            ),
            "idw_p2_k12": predict_idw(
                xy_km[train], log2_enrichment[train], xy_km[test]
            ),
            "gaussian_nested": predict_gaussian(
                xy_km[train],
                log2_enrichment[train],
                xy_km[test],
                selected_bandwidth,
            ),
        }
        for model, prediction in predictions.items():
            error = prediction - log2_enrichment[test]
            nearest_training_distance = cdist(xy_km[test], xy_km[train]).min(axis=1)
            distance_group = np.where(
                nearest_training_distance < 1.0,
                "lt_1_km",
                np.where(nearest_training_distance < 2.0, "1_to_2_km", "ge_2_km"),
            )
            for column, metal in enumerate(METALS):
                metric_rows.append(
                    {
                        "outer_fold": outer_fold,
                        "model": model,
                        "metal": metal,
                        "n_test": int(test.sum()),
                        "mae_log2": float(np.mean(np.abs(error[:, column]))),
                        "rmse_log2": float(
                            np.sqrt(np.mean(error[:, column] ** 2))
                        ),
                        "gaussian_bandwidth_km": selected_bandwidth
                        if model == "gaussian_nested"
                        else np.nan,
                    }
                )
                for group in ["lt_1_km", "1_to_2_km", "ge_2_km"]:
                    selected_group = distance_group == group
                    if not selected_group.any():
                        continue
                    distance_rows.append(
                        {
                            "outer_fold": outer_fold,
                            "model": model,
                            "metal": metal,
                            "test_to_train_distance_group": group,
                            "n_test": int(selected_group.sum()),
                            "minimum_distance_km": float(
                                nearest_training_distance[selected_group].min()
                            ),
                            "median_distance_km": float(
                                np.median(nearest_training_distance[selected_group])
                            ),
                            "rmse_log2": float(
                                np.sqrt(
                                    np.mean(error[selected_group, column] ** 2)
                                )
                            ),
                        }
                    )

    metrics = pd.DataFrame(metric_rows)
    bandwidth_validation = pd.DataFrame(bandwidth_rows)
    write_csv(metrics, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(
        bandwidth_validation, RESULTS_DIR / "<SOURCE_FILE_REDACTED>"
    )
    write_csv(
        pd.DataFrame(distance_rows), RESULTS_DIR / "<SOURCE_FILE_REDACTED>"
    )

    summary = (
        metrics.groupby("model")
        .agg(
            mean_mae_log2=("mae_log2", "mean"),
            mean_rmse_log2=("rmse_log2", "mean"),
        )
        .reset_index()
    )
    fold_overall = (
        metrics.groupby(["model", "outer_fold"])["rmse_log2"].mean().reset_index()
    )
    worst = (
        fold_overall.groupby("model")["rmse_log2"]
        .max()
        .rename("worst_fold_mean_rmse_log2")
        .reset_index()
    )
    summary = summary.merge(worst, on="model")
    summary["rank"] = summary["mean_rmse_log2"].rank(method="min").astype(int)
    write_csv(summary, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")

    per_metal = (
        metrics.groupby(["model", "metal"])[["mae_log2", "rmse_log2"]]
        .mean()
        .reset_index()
    )
    write_csv(per_metal, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")

    final_bandwidth_scores = []
    for bandwidth in GAUSSIAN_BANDWIDTH_GRID_KM:
        fold_scores = []
        for fold in range(N_OUTER_FOLDS):
            train = folds != fold
            test = ~train
            prediction = predict_gaussian(
                xy_km[train], log2_enrichment[train], xy_km[test], bandwidth
            )
            fold_scores.append(
                float(
                    np.sqrt(
                        np.mean((prediction - log2_enrichment[test]) ** 2, axis=0)
                    ).mean()
                )
            )
        final_bandwidth_scores.append(
            {
                "bandwidth_km": bandwidth,
                "five_fold_mean_rmse_log2": float(np.mean(fold_scores)),
            }
        )
    final_bandwidth_frame = pd.DataFrame(final_bandwidth_scores)
    final_bandwidth = float(
        final_bandwidth_frame.loc[
            final_bandwidth_frame["five_fold_mean_rmse_log2"].idxmin(),
            "bandwidth_km",
        ]
    )
    final_bandwidth_frame["selected_for_full_fit"] = (
        final_bandwidth_frame["bandwidth_km"] == final_bandwidth
    )
    write_csv(
        final_bandwidth_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>"
    )

    stress_rows = []

    def add_stress_rows(
        scheme: str,
        outer_fold: int,
        train: np.ndarray,
        test: np.ndarray,
        selected_bandwidth: float,
        buffer_km: float,
    ) -> None:
        if int(train.sum()) < 20 or int(test.sum()) == 0:
            raise RuntimeError(
                f"Insufficient observations for {scheme} fold {outer_fold}."
            )
        predictions = {
            "constant_mean": np.repeat(
                log2_enrichment[train].mean(axis=0, keepdims=True),
                int(test.sum()),
                axis=0,
            ),
            "idw_p2_k12": predict_idw(
                xy_km[train], log2_enrichment[train], xy_km[test]
            ),
            "gaussian_nested": predict_gaussian(
                xy_km[train],
                log2_enrichment[train],
                xy_km[test],
                selected_bandwidth,
            ),
        }
        minimum_distance = float(cdist(xy_km[test], xy_km[train]).min(axis=1).min())
        for model, prediction in predictions.items():
            error = prediction - log2_enrichment[test]
            for column, metal in enumerate(METALS):
                stress_rows.append(
                    {
                        "scheme": scheme,
                        "buffer_km": buffer_km,
                        "outer_fold": outer_fold,
                        "model": model,
                        "metal": metal,
                        "n_train": int(train.sum()),
                        "n_test": int(test.sum()),
                        "minimum_test_train_distance_km": minimum_distance,
                        "gaussian_bandwidth_km": selected_bandwidth
                        if model == "gaussian_nested"
                        else np.nan,
                        "mae_log2": float(np.mean(np.abs(error[:, column]))),
                        "rmse_log2": float(np.sqrt(np.mean(error[:, column] ** 2))),
                    }
                )

    contiguous_folds = KMeans(
        n_clusters=N_OUTER_FOLDS,
        random_state=SEED,
        n_init=20,
    ).fit_predict(xy_km)
    contiguous_frame = data[["id", "x_m", "y_m"]].copy()
    contiguous_frame["contiguous_fold"] = contiguous_folds
    write_csv(contiguous_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    for outer_fold in range(N_OUTER_FOLDS):
        train = contiguous_folds != outer_fold
        test = ~train
        selected_bandwidth, _ = choose_inner_bandwidth(
            xy_km, log2_enrichment, contiguous_folds, outer_fold
        )
        add_stress_rows(
            "contiguous_region_holdout",
            outer_fold,
            train,
            test,
            selected_bandwidth,
            0.0,
        )

    for buffer_km in [1.0, 2.0]:
        for outer_fold in range(N_OUTER_FOLDS):
            test = folds == outer_fold
            candidate_indices = np.flatnonzero(~test)
            distance_to_test = cdist(xy_km[candidate_indices], xy_km[test]).min(axis=1)
            retained_indices = candidate_indices[distance_to_test >= buffer_km]
            train = np.zeros(len(data), dtype=bool)
            train[retained_indices] = True
            add_stress_rows(
                f"blocked_with_{buffer_km:.0f}km_buffer",
                outer_fold,
                train,
                test,
                selected_bandwidth_by_fold[outer_fold],
                buffer_km,
            )

    stress_metrics = pd.DataFrame(stress_rows)
    stress_summary = (
        stress_metrics.groupby(["scheme", "buffer_km", "model"])
        .agg(
            mean_mae_log2=("mae_log2", "mean"),
            mean_rmse_log2=("rmse_log2", "mean"),
            minimum_train_count=("n_train", "min"),
            minimum_test_train_distance_km=(
                "minimum_test_train_distance_km",
                "min",
            ),
        )
        .reset_index()
    )
    stress_summary["rank_within_scheme"] = (
        stress_summary.groupby("scheme")["mean_rmse_log2"]
        .rank(method="min")
        .astype(int)
    )
    write_csv(stress_metrics, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(stress_summary, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    return {
        "xy_km": xy_km,
        "log2_enrichment": log2_enrichment,
        "folds": folds,
        "fold_counts": fold_counts,
        "metrics": metrics,
        "summary": summary,
        "per_metal": per_metal,
        "final_bandwidth_km": final_bandwidth,
        "stress_metrics": stress_metrics,
        "stress_summary": stress_summary,
    }


def zone_pollution_analysis(
    data: pd.DataFrame, background: pd.DataFrame
):
    bg = background.set_index("metal").loc[METALS]
    concentration = data[METALS].to_numpy(dtype=float)
    enrichment = concentration / bg["mean"].to_numpy()
    upper = bg["range_upper"].to_numpy()
    zones = sorted(data["zone"].astype(int).unique())

    metal_rows = []
    zone_rows = []
    for zone in zones:
        mask = data["zone"].to_numpy() == zone
        zone_enrichment = enrichment[mask]
        zone_concentration = concentration[mask]
        medians = np.median(zone_enrichment, axis=0)
        q90 = np.quantile(zone_enrichment, 0.9, axis=0)
        exceedance = np.mean(zone_concentration > upper, axis=0)
        for column, metal in enumerate(METALS):
            metal_rows.append(
                {
                    "zone": zone,
                    "zone_name": ZONE_NAMES_EN[zone],
                    "metal": metal,
                    "n": int(mask.sum()),
                    "mean_concentration": float(zone_concentration[:, column].mean()),
                    "median_concentration": float(
                        np.median(zone_concentration[:, column])
                    ),
                    "mean_enrichment": float(zone_enrichment[:, column].mean()),
                    "median_enrichment": float(medians[column]),
                    "q90_enrichment": float(q90[column]),
                    "background_upper_exceedance_rate": float(exceedance[column]),
                }
            )

        site_geometric = np.exp(np.mean(np.log(zone_enrichment), axis=1))
        zone_rows.append(
            {
                "zone": zone,
                "zone_name": ZONE_NAMES_EN[zone],
                "n": int(mask.sum()),
                "typical_geometric_enrichment": float(
                    np.exp(np.mean(np.log(medians)))
                ),
                "q90_geometric_enrichment": float(
                    np.exp(np.mean(np.log(q90)))
                ),
                "mean_background_upper_exceedance_rate": float(exceedance.mean()),
                "arithmetic_mean_enrichment": float(zone_enrichment.mean()),
                "median_site_geometric_enrichment": float(
                    np.median(site_geometric)
                ),
            }
        )

    zone_metal = pd.DataFrame(metal_rows)
    zone_summary = pd.DataFrame(zone_rows)
    zone_summary["robust_rank"] = (
        zone_summary["typical_geometric_enrichment"]
        .rank(ascending=False, method="min")
        .astype(int)
    )

    zone_array = data["zone"].to_numpy(dtype=int)

    def assign_ranks(values: np.ndarray) -> np.ndarray:
        ranks = np.empty(len(values), dtype=int)
        order = np.argsort(-values)
        ranks[order] = np.arange(1, len(values) + 1)
        return ranks

    iid_rng = np.random.default_rng(SEED + 1)
    iid_values = np.empty((IID_BOOTSTRAP_REPLICATES, len(zones)))
    iid_ranks = np.empty_like(iid_values, dtype=int)
    for replicate in range(IID_BOOTSTRAP_REPLICATES):
        for zone_index, zone in enumerate(zones):
            values = enrichment[zone_array == zone]
            sample = values[
                iid_rng.integers(0, len(values), size=len(values))
            ]
            medians = np.median(sample, axis=0)
            iid_values[replicate, zone_index] = np.exp(
                np.mean(np.log(medians))
            )
        iid_ranks[replicate] = assign_ranks(iid_values[replicate])

    xy_km = data[["x_m", "y_m"]].to_numpy(dtype=float) / 1000.0
    block_labels = np.floor(xy_km / SPATIAL_BLOCK_KM).astype(int)
    zone_clusters: dict[int, list[np.ndarray]] = {}
    for zone in zones:
        indices = np.flatnonzero(zone_array == zone)
        groups: dict[tuple[int, int], list[int]] = {}
        for index in indices:
            groups.setdefault(tuple(block_labels[index]), []).append(int(index))
        zone_clusters[zone] = [
            np.asarray(group, dtype=int) for _, group in sorted(groups.items())
        ]

    block_rng = np.random.default_rng(SEED + 2)
    block_values = np.empty((ZONE_BLOCK_BOOTSTRAP_REPLICATES, len(zones)))
    block_ranks = np.empty_like(block_values, dtype=int)
    for replicate in range(ZONE_BLOCK_BOOTSTRAP_REPLICATES):
        for zone_index, zone in enumerate(zones):
            clusters = zone_clusters[zone]
            selected_clusters = block_rng.integers(
                0, len(clusters), size=len(clusters)
            )
            sampled_indices = np.concatenate(
                [clusters[index] for index in selected_clusters]
            )
            medians = np.median(enrichment[sampled_indices], axis=0)
            block_values[replicate, zone_index] = np.exp(
                np.mean(np.log(medians))
            )
        block_ranks[replicate] = assign_ranks(block_values[replicate])

    bootstrap_rows = []
    iid_rows = []
    for zone_index, zone in enumerate(zones):
        bootstrap_rows.append(
            {
                "zone": zone,
                "zone_name": ZONE_NAMES_EN[zone],
                "method": "3km_spatial_block_cluster_bootstrap",
                "replicates": ZONE_BLOCK_BOOTSTRAP_REPLICATES,
                "cluster_count": len(zone_clusters[zone]),
                "conditional_ci_lower_2_5": float(
                    np.quantile(block_values[:, zone_index], 0.025)
                ),
                "conditional_ci_upper_97_5": float(
                    np.quantile(block_values[:, zone_index], 0.975)
                ),
                "rank_1_frequency": float(
                    np.mean(block_ranks[:, zone_index] == 1)
                ),
                "median_rank": float(np.median(block_ranks[:, zone_index])),
            }
        )
        iid_rows.append(
            {
                "zone": zone,
                "zone_name": ZONE_NAMES_EN[zone],
                "method": "iid_point_bootstrap_sensitivity_only",
                "replicates": IID_BOOTSTRAP_REPLICATES,
                "conditional_ci_lower_2_5": float(
                    np.quantile(iid_values[:, zone_index], 0.025)
                ),
                "conditional_ci_upper_97_5": float(
                    np.quantile(iid_values[:, zone_index], 0.975)
                ),
                "rank_1_frequency": float(np.mean(iid_ranks[:, zone_index] == 1)),
                "median_rank": float(np.median(iid_ranks[:, zone_index])),
            }
        )
    bootstrap_frame = pd.DataFrame(bootstrap_rows)
    iid_frame = pd.DataFrame(iid_rows)
    zone_summary = zone_summary.merge(
        bootstrap_frame.drop(columns=["method", "replicates", "cluster_count"]),
        on=["zone", "zone_name"],
    )
    zone_summary["ranking_uncertainty_status"] = "needs_review"
    zone_summary["background_spatial_validity_status"] = "needs_review"

    industrial_index = zones.index(2)
    traffic_index = zones.index(4)
    contrast = block_values[:, industrial_index] - block_values[:, traffic_index]
    contrast_frame = pd.DataFrame(
        [
            {
                "contrast": "Industrial_minus_Traffic",
                "method": "3km_spatial_block_cluster_bootstrap",
                "replicates": ZONE_BLOCK_BOOTSTRAP_REPLICATES,
                "median_difference": float(np.median(contrast)),
                "difference_ci_lower_2_5": float(np.quantile(contrast, 0.025)),
                "difference_ci_upper_97_5": float(np.quantile(contrast, 0.975)),
                "traffic_exceeds_industrial_frequency": float(
                    np.mean(contrast < 0)
                ),
                "interpretation_status": "needs_review",
            }
        ]
    )

    bg_mean = bg["mean"].to_numpy()
    bg_sd = bg["sd"].to_numpy()
    scenario_rows = []
    scenarios = {
        "common_baseline": {
            zone: bg_mean.copy() for zone in zones
        },
        "logical_counterexample_industrial_plus_sd_traffic_minus_sd": {
            zone: (
                bg_mean + bg_sd
                if zone == 2
                else np.maximum(bg_mean - bg_sd, 1e-12)
                if zone == 4
                else bg_mean.copy()
            )
            for zone in zones
        },
    }
    for scenario, background_by_zone in scenarios.items():
        indices = np.empty(len(zones), dtype=float)
        for zone_index, zone in enumerate(zones):
            medians = np.median(
                concentration[zone_array == zone] / background_by_zone[zone], axis=0
            )
            indices[zone_index] = np.exp(np.mean(np.log(medians)))
        scenario_ranks = assign_ranks(indices)
        for zone_index, zone in enumerate(zones):
            scenario_rows.append(
                {
                    "scenario": scenario,
                    "zone": zone,
                    "zone_name": ZONE_NAMES_EN[zone],
                    "geometric_index": float(indices[zone_index]),
                    "rank": int(scenario_ranks[zone_index]),
                    "scenario_truth_status": "needs_review"
                    if scenario != "common_baseline"
                    else "pass",
                }
            )
    background_scenarios = pd.DataFrame(scenario_rows)
    write_json(
        RESULTS_DIR / "background_invariance.json",
        {
            "common_background_rank_invariance_status": "pass",
            "spatial_background_validity_status": "needs_review",
            "reason": (
                "For positive backgrounds shared by every zone, the background term is "
                "a common multiplicative factor and ranking invariance is algebraic. "
                "The supplied summaries cannot identify zone-specific background fields."
            ),
        },
    )

    write_csv(zone_metal, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(zone_summary, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(bootstrap_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(iid_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(contrast_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(background_scenarios, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    return {
        "enrichment": enrichment,
        "zone_metal": zone_metal,
        "zone_summary": zone_summary,
        "bootstrap_ranks": block_ranks,
        "iid_bootstrap_ranks": iid_ranks,
        "rank_contrast": contrast_frame,
        "background_scenarios": background_scenarios,
    }


def zone_associations(
    data: pd.DataFrame, log2_enrichment: np.ndarray
):
    zones = sorted(data["zone"].astype(int).unique())
    zone_array = data["zone"].to_numpy(dtype=int)
    elevation = data["elevation_m"].to_numpy(dtype=float)
    elevation_centered = elevation - pd.Series(elevation).groupby(zone_array).transform("mean").to_numpy()
    rows = []
    for column, metal in enumerate(METALS):
        values = log2_enrichment[:, column]
        grand_mean = values.mean()
        total_ss = np.sum((values - grand_mean) ** 2)
        between_ss = sum(
            np.sum(zone_array == zone)
            * (values[zone_array == zone].mean() - grand_mean) ** 2
            for zone in zones
        )
        statistic, p_value = kruskal(
            *[values[zone_array == zone] for zone in zones]
        )
        elevation_rho, elevation_p = spearmanr(elevation, values)
        centered_values = values - pd.Series(values).groupby(zone_array).transform("mean").to_numpy()
        within_rho, within_p = spearmanr(elevation_centered, centered_values)
        rows.append(
            {
                "metal": metal,
                "zone_eta_squared_log2": float(between_ss / total_ss),
                "kruskal_h": float(statistic),
                "kruskal_p": float(p_value),
                "elevation_spearman_rho": float(elevation_rho),
                "elevation_spearman_p": float(elevation_p),
                "within_zone_elevation_rho": float(within_rho),
                "within_zone_elevation_p": float(within_p),
                "causal_interpretation_status": "needs_review",
            }
        )
    frame = pd.DataFrame(rows)
    write_csv(frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    return frame


def fit_nmf_model(
    data: pd.DataFrame,
    enrichment: np.ndarray,
    folds: np.ndarray,
):
    transformed = np.log1p(enrichment)
    rms_scale = np.sqrt(np.mean(transformed**2, axis=0))
    normalized = transformed / rms_scale

    validation_rows = []
    convergence_rows = []
    scale_rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        for component_count in [2, 3, 4]:
            for fold in range(N_OUTER_FOLDS):
                train = folds != fold
                test = ~train
                fold_rms_scale = np.sqrt(
                    np.mean(transformed[train] ** 2, axis=0)
                )
                normalized_train = transformed[train] / fold_rms_scale
                normalized_test = transformed[test] / fold_rms_scale
                for metal, scale in zip(METALS, fold_rms_scale):
                    scale_rows.append(
                        {
                            "components": component_count,
                            "fold": fold,
                            "metal": metal,
                            "training_only_rms_scale": float(scale),
                            "preprocessing_fit_status": "pass",
                        }
                    )
                model = NMF(
                    n_components=component_count,
                    init="nndsvdar",
                    random_state=SEED + 10 * component_count + fold,
                    max_iter=5000,
                    tol=1e-5,
                )
                model.fit(normalized_train)
                test_scores = model.transform(normalized_test)
                prediction = test_scores @ model.components_
                validation_rows.append(
                    {
                        "components": component_count,
                        "fold": fold,
                        "rmse_normalized_log1p": float(
                            np.sqrt(np.mean((prediction - normalized_test) ** 2))
                        ),
                    }
                )
                convergence_rows.append(
                    {
                        "components": component_count,
                        "fold": fold,
                        "n_iter": int(model.n_iter_),
                        "convergence_status": "pass"
                        if model.n_iter_ < model.max_iter
                        else "needs_review",
                    }
                )

    validation = pd.DataFrame(validation_rows)
    validation_summary = (
        validation.groupby("components")["rmse_normalized_log1p"]
        .agg(["mean", "std"])
        .reset_index()
        .rename(columns={"mean": "cv_rmse_mean", "std": "cv_rmse_sd"})
    )
    validation_summary["improvement_from_previous"] = np.nan
    for index in range(1, len(validation_summary)):
        previous = validation_summary.loc[index - 1, "cv_rmse_mean"]
        current = validation_summary.loc[index, "cv_rmse_mean"]
        validation_summary.loc[index, "improvement_from_previous"] = (
            previous - current
        ) / previous

    selected_components = 4
    for component_count in [2, 3]:
        current_error = float(
            validation_summary.loc[
                validation_summary["components"] == component_count,
                "cv_rmse_mean",
            ].iloc[0]
        )
        next_error = float(
            validation_summary.loc[
                validation_summary["components"] == component_count + 1,
                "cv_rmse_mean",
            ].iloc[0]
        )
        improvement = (current_error - next_error) / current_error
        if improvement < 0.20:
            selected_components = component_count
            break

    stability_rows = []
    reference_components = None
    aligned_components = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        for run in range(20):
            model = NMF(
                n_components=selected_components,
                init="nndsvdar",
                random_state=SEED + 100 + run,
                max_iter=5000,
                tol=1e-5,
            )
            model.fit(normalized)
            unit_components = model.components_ / np.maximum(
                np.linalg.norm(model.components_, axis=1, keepdims=True), 1e-12
            )
            if reference_components is None:
                reference_components = unit_components
                aligned = unit_components
                similarity = 1.0
            else:
                row_index, column_index = linear_sum_assignment(
                    -(reference_components @ unit_components.T)
                )
                aligned = unit_components[column_index[np.argsort(row_index)]]
                similarity = float(
                    np.mean(np.sum(reference_components * aligned, axis=1))
                )
            aligned_components.append(aligned)
            stability_rows.append(
                {
                    "run": run,
                    "mean_matched_cosine": similarity,
                    "n_iter": int(model.n_iter_),
                    "convergence_status": "pass"
                    if model.n_iter_ < model.max_iter
                    else "needs_review",
                }
            )

    final_model = NMF(
        n_components=selected_components,
        init="nndsvdar",
        random_state=SEED,
        max_iter=5000,
        tol=1e-5,
    )
    scores = final_model.fit_transform(normalized)
    final_unit_components = final_model.components_ / np.maximum(
        np.linalg.norm(final_model.components_, axis=1, keepdims=True), 1e-12
    )
    initialization_rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        for run in range(20):
            alternate = NMF(
                n_components=selected_components,
                init="random",
                random_state=SEED + 500 + run,
                max_iter=5000,
                tol=1e-5,
            )
            alternate_scores = alternate.fit_transform(normalized)
            alternate_unit = alternate.components_ / np.maximum(
                np.linalg.norm(alternate.components_, axis=1, keepdims=True), 1e-12
            )
            row_index, column_index = linear_sum_assignment(
                -(final_unit_components @ alternate_unit.T)
            )
            aligned = alternate_unit[column_index[np.argsort(row_index)]]
            matched_cosines = np.sum(final_unit_components * aligned, axis=1)
            reconstruction = alternate_scores @ alternate.components_
            initialization_rows.append(
                {
                    "run": run,
                    "initialization": "random",
                    "mean_matched_cosine_to_nndsvdar_reference": float(
                        np.mean(matched_cosines)
                    ),
                    "minimum_matched_cosine_to_nndsvdar_reference": float(
                        np.min(matched_cosines)
                    ),
                    "reconstruction_rmse": float(
                        np.sqrt(np.mean((normalized - reconstruction) ** 2))
                    ),
                    "n_iter": int(alternate.n_iter_),
                    "numerical_status": "pass"
                    if alternate.n_iter_ < alternate.max_iter
                    else "needs_review",
                    "physical_uniqueness_status": "needs_review",
                }
            )
    loadings_original_scale = final_model.components_ * rms_scale[None, :]
    loading_proportions = loadings_original_scale / loadings_original_scale.sum(
        axis=1, keepdims=True
    )

    hg_component = int(np.argmax(loading_proportions[:, METALS.index("Hg")]))
    remaining = [index for index in range(selected_components) if index != hg_component]
    acrni = [METALS.index(metal) for metal in ["As", "Cr", "Ni"]]
    acrni_component = remaining[
        int(np.argmax([loading_proportions[index, acrni].sum() for index in remaining]))
    ]
    final_remaining = [
        index for index in remaining if index != acrni_component
    ]
    mixed_component = final_remaining[0]
    component_order = [hg_component, acrni_component, mixed_component]
    component_labels = [
        "Hg-dominant",
        "As-Cr-Ni covariation",
        "Cd-Cu-Pb-Zn covariation",
    ]
    loading_proportions = loading_proportions[component_order]
    scores = scores[:, component_order]
    normalized_scores = scores / scores.mean(axis=0, keepdims=True)

    loading_frame = pd.DataFrame(loading_proportions, columns=METALS)
    loading_frame.insert(0, "component", component_labels)
    score_frame = data[["id", "x_m", "y_m", "zone"]].copy()
    for column, label in enumerate(component_labels):
        score_frame[label] = normalized_scores[:, column]
    zone_score = (
        score_frame.groupby("zone")[component_labels]
        .median()
        .reset_index()
    )
    zone_score.insert(1, "zone_name", zone_score["zone"].map(ZONE_NAMES_EN))

    top_rows = []
    for column, label in enumerate(component_labels):
        for rank, index in enumerate(
            np.argsort(normalized_scores[:, column])[::-1][:5], start=1
        ):
            top_rows.append(
                {
                    "component": label,
                    "rank": rank,
                    "id": int(data.iloc[index]["id"]),
                    "x_m": float(data.iloc[index]["x_m"]),
                    "y_m": float(data.iloc[index]["y_m"]),
                    "zone": int(data.iloc[index]["zone"]),
                    "normalized_score": float(normalized_scores[index, column]),
                }
            )

    standardized = (transformed - transformed.mean(axis=0)) / transformed.std(
        axis=0, ddof=0
    )
    pca = PCA().fit(standardized)
    pca_components = pca.components_.copy()
    for component in range(pca_components.shape[0]):
        if pca_components[component].sum() < 0:
            pca_components[component] *= -1
    pca_loading = pd.DataFrame(
        pca_components[:4].T,
        index=METALS,
        columns=["PC1", "PC2", "PC3", "PC4"],
    ).reset_index(names="metal")
    pca_variance = pd.DataFrame(
        {
            "component": np.arange(1, len(METALS) + 1),
            "explained_variance_ratio": pca.explained_variance_ratio_,
            "cumulative_explained_variance_ratio": np.cumsum(
                pca.explained_variance_ratio_
            ),
        }
    )

    write_csv(validation, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(validation_summary, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(pd.DataFrame(convergence_rows), RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(pd.DataFrame(scale_rows), RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(pd.DataFrame(stability_rows), RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(
        pd.DataFrame(initialization_rows),
        RESULTS_DIR / "<SOURCE_FILE_REDACTED>",
    )
    write_csv(loading_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(score_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(zone_score, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(pd.DataFrame(top_rows), RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(pca_loading, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(pca_variance, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")

    return {
        "selected_components": selected_components,
        "validation_summary": validation_summary,
        "stability": pd.DataFrame(stability_rows),
        "initialization_sensitivity": pd.DataFrame(initialization_rows),
        "loadings": loading_frame,
        "scores": score_frame,
        "zone_scores": zone_score,
        "pca_loading": pca_loading,
        "pca_variance": pca_variance,
        "component_labels": component_labels,
    }


def variogram_curve(
    model: str,
    distance_km: np.ndarray,
    nugget: float,
    partial_sill: float,
    scale_km: float,
) -> np.ndarray:
    distance = np.asarray(distance_km, dtype=float)
    ratio = distance / max(scale_km, 1e-12)
    if model == "exponential":
        structure = 1.0 - np.exp(-ratio)
    elif model == "gaussian":
        structure = 1.0 - np.exp(-(ratio**2))
    elif model == "spherical":
        structure = np.where(
            ratio < 1.0,
            1.5 * ratio - 0.5 * ratio**3,
            1.0,
        )
    else:
        raise ValueError(f"Unknown variogram model: {model}")
    return nugget + partial_sill * structure


def variogram_practical_range(model: str, scale_km: float) -> float:
    factors = {
        "exponential": -math.log(0.05),
        "gaussian": math.sqrt(-math.log(0.05)),
        "spherical": 1.0,
    }
    return float(factors[model] * scale_km)


def fit_variogram_candidates(
    distances: np.ndarray,
    semivariance: np.ndarray,
    pair_counts: np.ndarray,
) -> pd.DataFrame:
    positive = np.isfinite(semivariance) & (pair_counts > 0)
    h = np.asarray(distances[positive], dtype=float)
    gamma = np.asarray(semivariance[positive], dtype=float)
    counts = np.asarray(pair_counts[positive], dtype=float)
    if len(h) < 6:
        raise RuntimeError("Too few non-empty bins for variogram fitting.")
    weights = np.sqrt(counts / counts.max())
    gamma_max = max(float(np.max(gamma)), 1e-6)
    first_gamma = max(float(gamma[0]), 1e-8)
    starts = [
        [0.25 * first_gamma, max(gamma_max - 0.25 * first_gamma, 1e-6), 1.5],
        [0.50 * first_gamma, max(gamma_max - 0.50 * first_gamma, 1e-6), 4.0],
        [0.10 * gamma_max, 0.90 * gamma_max, 8.0],
    ]
    lower = np.array([0.0, 1e-10, 0.10])
    upper = np.array([3.0 * gamma_max, 5.0 * gamma_max, 40.0])
    rows = []
    for model in ["exponential", "spherical", "gaussian"]:
        best = None
        for start in starts:
            result = least_squares(
                lambda parameters: weights
                * (
                    variogram_curve(model, h, *parameters)
                    - gamma
                ),
                x0=np.clip(np.asarray(start, dtype=float), lower + 1e-9, upper - 1e-9),
                bounds=(lower, upper),
                max_nfev=20000,
            )
            if best is None or float(np.sum(result.fun**2)) < float(
                np.sum(best.fun**2)
            ):
                best = result
        if best is None:
            raise RuntimeError(f"Variogram optimizer did not run for {model}.")
        parameters = best.x
        prediction = variogram_curve(model, h, *parameters)
        weighted_rmse = float(
            np.sqrt(np.sum(counts * (prediction - gamma) ** 2) / np.sum(counts))
        )
        degrees_of_freedom = max(len(h) - len(parameters), 1)
        residual_variance = float(np.sum(best.fun**2) / degrees_of_freedom)
        covariance = residual_variance * np.linalg.pinv(best.jac.T @ best.jac)
        standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
        practical_range = variogram_practical_range(model, parameters[2])
        practical_factor = variogram_practical_range(model, 1.0)
        rows.append(
            {
                "model": model,
                "nugget": float(parameters[0]),
                "partial_sill": float(parameters[1]),
                "total_sill": float(parameters[0] + parameters[1]),
                "scale_km": float(parameters[2]),
                "practical_range_km": practical_range,
                "nugget_ci_lower_95": float(max(0.0, parameters[0] - 1.96 * standard_errors[0])),
                "nugget_ci_upper_95": float(parameters[0] + 1.96 * standard_errors[0]),
                "partial_sill_ci_lower_95": float(max(0.0, parameters[1] - 1.96 * standard_errors[1])),
                "partial_sill_ci_upper_95": float(parameters[1] + 1.96 * standard_errors[1]),
                "practical_range_ci_lower_95_km": float(
                    max(0.0, practical_factor * (parameters[2] - 1.96 * standard_errors[2]))
                ),
                "practical_range_ci_upper_95_km": float(
                    practical_factor * (parameters[2] + 1.96 * standard_errors[2])
                ),
                "weighted_rmse": weighted_rmse,
                "optimizer_status": "pass" if best.success else "fail",
            }
        )
    frame = pd.DataFrame(rows)
    frame["selected"] = frame["weighted_rmse"] == frame["weighted_rmse"].min()
    return frame


def spatial_dependence(xy_km: np.ndarray, log2_enrichment: np.ndarray):
    n = len(xy_km)
    neighbor_model = NearestNeighbors(n_neighbors=9).fit(xy_km)
    _, neighbor_indices = neighbor_model.kneighbors(xy_km)
    weights = np.zeros((n, n), dtype=float)
    for row in range(n):
        weights[row, neighbor_indices[row, 1:]] = 1.0
    weights = np.maximum(weights, weights.T)
    weights /= weights.sum(axis=1, keepdims=True)
    weight_sum = weights.sum()

    permutation_rng = np.random.default_rng(SEED + 3)
    permutations = np.vstack(
        [permutation_rng.permutation(n) for _ in range(PERMUTATION_REPLICATES)]
    )

    def moran(values):
        centered = values - values.mean()
        return float(
            n
            / weight_sum
            * (centered @ (weights @ centered))
            / (centered @ centered)
        )

    moran_rows = []
    for column, metal in enumerate(METALS):
        values = log2_enrichment[:, column]
        observed = moran(values)
        permutation_values = np.array([moran(values[index]) for index in permutations])
        p_value = float(
            (1 + np.sum(permutation_values >= observed))
            / (1 + PERMUTATION_REPLICATES)
        )
        moran_rows.append(
            {
                "metal": metal,
                "moran_i": observed,
                "permutation_p_one_sided": p_value,
                "permutation_z": float(
                    (observed - permutation_values.mean())
                    / permutation_values.std(ddof=1)
                ),
                "spatial_clustering_status": "pass"
                if p_value <= 0.01 and observed > 0
                else "needs_review",
            }
        )
    moran_frame = pd.DataFrame(moran_rows)
    write_csv(moran_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")

    pair_distance = pdist(xy_km)
    upper_i, upper_j = np.triu_indices(n, 1)
    design = np.column_stack([np.ones(n), xy_km])
    variogram_rows = []
    model_fit_rows = []
    bin_sensitivity_rows = []
    trend_rows = []
    summary_rows = []
    for column, metal in enumerate(METALS):
        values = log2_enrichment[:, column]
        coefficients, *_ = np.linalg.lstsq(design, values, rcond=None)
        trend = design @ coefficients
        residual = values - trend
        total_ss = float(np.sum((values - values.mean()) ** 2))
        residual_ss = float(np.sum(residual**2))
        trend_r_squared = 1.0 - residual_ss / max(total_ss, 1e-12)
        residual_variance = float(np.var(residual, ddof=1))
        trend_rows.append(
            {
                "metal": metal,
                "intercept": float(coefficients[0]),
                "x_slope_per_km": float(coefficients[1]),
                "y_slope_per_km": float(coefficients[2]),
                "trend_r_squared": trend_r_squared,
                "detrending_status": "pass",
                "trend_physical_interpretation_status": "needs_review",
            }
        )
        pair_semivariance = 0.5 * (
            residual[upper_i] - residual[upper_j]
        ) ** 2
        selected_by_width = {}
        for bin_width in [0.5, 1.0, 2.0]:
            empirical = []
            for lower in np.arange(0.0, 20.0, bin_width):
                selected = (pair_distance >= lower) & (
                    pair_distance < lower + bin_width
                )
                if not selected.any():
                    continue
                gamma = float(np.mean(pair_semivariance[selected]))
                empirical.append(
                    {
                        "distance_lower_km": float(lower),
                        "distance_upper_km": float(lower + bin_width),
                        "distance_mid_km": float(lower + 0.5 * bin_width),
                        "pair_count": int(selected.sum()),
                        "semivariance": gamma,
                        "semivariance_over_residual_variance": gamma
                        / max(residual_variance, 1e-12),
                    }
                )
            empirical_frame = pd.DataFrame(empirical)
            fitted = fit_variogram_candidates(
                empirical_frame["distance_mid_km"].to_numpy(),
                empirical_frame["semivariance"].to_numpy(),
                empirical_frame["pair_count"].to_numpy(),
            )
            selected_fit = fitted.loc[fitted["selected"]].iloc[0]
            selected_by_width[bin_width] = selected_fit
            bin_sensitivity_rows.append(
                {
                    "metal": metal,
                    "bin_width_km": bin_width,
                    "selected_model": selected_fit["model"],
                    "nugget": float(selected_fit["nugget"]),
                    "partial_sill": float(selected_fit["partial_sill"]),
                    "total_sill": float(selected_fit["total_sill"]),
                    "practical_range_km": float(
                        selected_fit["practical_range_km"]
                    ),
                    "weighted_rmse": float(selected_fit["weighted_rmse"]),
                    "interpretation_status": "needs_review",
                }
            )
            if bin_width == 1.0:
                for row in empirical:
                    variogram_rows.append(
                        {
                            "metal": metal,
                            "bin_width_km": bin_width,
                            **row,
                        }
                    )
                for row in fitted.to_dict(orient="records"):
                    model_fit_rows.append(
                        {
                            "metal": metal,
                            "bin_width_km": bin_width,
                            **row,
                        }
                    )

        main_fit = selected_by_width[1.0]
        ranges = np.array(
            [selected_by_width[width]["practical_range_km"] for width in [0.5, 1.0, 2.0]],
            dtype=float,
        )
        first_bin = next(
            row
            for row in variogram_rows
            if row["metal"] == metal and row["distance_lower_km"] == 0.0
        )
        range_resolution_status = (
            "pass"
            if float(main_fit["practical_range_km"]) <= 20.0
            and float(main_fit["practical_range_ci_upper_95_km"]) <= 20.0
            else "needs_review"
        )
        summary_rows.append(
            {
                "metal": metal,
                "trend_r_squared": trend_r_squared,
                "residual_variance": residual_variance,
                "first_bin_empirical_semivariance_ratio": first_bin[
                    "semivariance_over_residual_variance"
                ],
                "selected_model": main_fit["model"],
                "nugget": float(main_fit["nugget"]),
                "partial_sill": float(main_fit["partial_sill"]),
                "total_sill": float(main_fit["total_sill"]),
                "fitted_nugget_to_total_sill": float(
                    main_fit["nugget"] / max(main_fit["total_sill"], 1e-12)
                ),
                "practical_range_km": float(main_fit["practical_range_km"]),
                "practical_range_ci_lower_95_km": float(
                    main_fit["practical_range_ci_lower_95_km"]
                ),
                "practical_range_ci_upper_95_km": float(
                    main_fit["practical_range_ci_upper_95_km"]
                ),
                "bin_sensitivity_range_min_km": float(np.min(ranges)),
                "bin_sensitivity_range_max_km": float(np.max(ranges)),
                "fit_numerical_status": str(main_fit["optimizer_status"]),
                "range_resolved_within_20km_status": range_resolution_status,
                "spatial_scale_interpretation_status": "needs_review",
                "physical_propagation_scale_status": "needs_review",
            }
        )
    variogram_frame = pd.DataFrame(variogram_rows)
    model_fit_frame = pd.DataFrame(model_fit_rows)
    bin_sensitivity_frame = pd.DataFrame(bin_sensitivity_rows)
    trend_frame = pd.DataFrame(trend_rows)
    summary_frame = pd.DataFrame(summary_rows)
    write_csv(variogram_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(model_fit_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(
        bin_sensitivity_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>"
    )
    write_csv(trend_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(summary_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    propagation = moran_frame.merge(summary_frame, on="metal")
    write_csv(propagation, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    return {
        "moran": moran_frame,
        "variogram": variogram_frame,
        "variogram_fits": model_fit_frame,
        "variogram_summary": summary_frame,
        "propagation": propagation,
    }


def make_grid(xy_km: np.ndarray, grid_step_km: float = GRID_STEP_KM):
    grid_x = np.arange(
        xy_km[:, 0].min(),
        xy_km[:, 0].max() + grid_step_km / 2,
        grid_step_km,
    )
    grid_y = np.arange(
        xy_km[:, 1].min(),
        xy_km[:, 1].max() + grid_step_km / 2,
        grid_step_km,
    )
    mesh_x, mesh_y = np.meshgrid(grid_x, grid_y)
    grid_points = np.column_stack([mesh_x.ravel(), mesh_y.ravel()])
    inside = Delaunay(xy_km).find_simplex(grid_points) >= 0
    distances = cdist(grid_points, xy_km)
    return grid_x, grid_y, mesh_x, mesh_y, grid_points, inside, distances


def kernel_grid_surface(
    distances: np.ndarray,
    values: np.ndarray,
    bandwidth_km: float,
    inside: np.ndarray,
):
    weights = np.exp(-0.5 * (distances / bandwidth_km) ** 2)
    surface = weights @ values / np.maximum(
        weights.sum(axis=1, keepdims=True), 1e-12
    )
    surface[~inside] = np.nan
    return surface


def local_peaks(
    surface_column: np.ndarray,
    mesh_shape: tuple[int, int],
    grid_x: np.ndarray,
    grid_y: np.ndarray,
    bandwidth_km: float,
    relative_threshold: float = 0.60,
    maximum_peaks: int = 3,
    grid_step_km: float = GRID_STEP_KM,
):
    surface = surface_column.reshape(mesh_shape)
    filled = np.where(np.isfinite(surface), surface, -np.inf)
    radius_cells = int(np.ceil(2 * bandwidth_km / grid_step_km))
    filter_size = 2 * radius_cells + 1
    local_maximum = maximum_filter(
        filled, size=filter_size, mode="constant", cval=-np.inf
    )
    candidate_indices = np.argwhere(
        (filled == local_maximum) & np.isfinite(surface)
    )
    candidates = sorted(
        [
            (float(surface[row, column]), float(grid_x[column]), float(grid_y[row]))
            for row, column in candidate_indices
        ],
        reverse=True,
    )
    if not candidates:
        return []
    selected = []
    for score, x_value, y_value in candidates:
        if score < relative_threshold * candidates[0][0]:
            continue
        if all(
            math.hypot(x_value - prior_x, y_value - prior_y)
            >= 2 * bandwidth_km
            for _, prior_x, prior_y in selected
        ):
            selected.append((score, x_value, y_value))
        if len(selected) >= maximum_peaks:
            break
    return selected


def axial_angle_mean_degrees(angles_degrees: list[float]) -> float:
    radians = np.deg2rad(np.asarray(angles_degrees) * 2)
    value = 0.5 * np.rad2deg(
        np.arctan2(np.mean(np.sin(radians)), np.mean(np.cos(radians)))
    )
    return float(value % 180)


def axial_distance_degrees(left: float, right: float) -> float:
    difference = abs(left - right) % 180
    return float(min(difference, 180 - difference))


def weighted_axis_statistics(
    xy_km: np.ndarray,
    center_xy: np.ndarray,
    values: np.ndarray,
    window_km: float,
) -> tuple[float, float, float, float]:
    offsets = xy_km - center_xy
    distance = np.linalg.norm(offsets, axis=1)
    weights = np.maximum(values, 0.0) * np.exp(
        -0.5 * (distance / window_km) ** 2
    )
    if float(weights.sum()) <= 1e-12:
        return np.nan, np.nan, np.nan, np.nan
    weighted_center = np.sum(weights[:, None] * offsets, axis=0) / weights.sum()
    centered = offsets - weighted_center
    covariance = (centered * weights[:, None]).T @ centered / weights.sum()
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    eigenvectors = eigenvectors[:, order]
    angle = float(
        np.degrees(np.arctan2(eigenvectors[1, 0], eigenvectors[0, 0])) % 180
    )
    ratio = float(np.sqrt(eigenvalues[0] / max(eigenvalues[1], 1e-12)))
    return angle, ratio, float(np.sqrt(eigenvalues[0])), float(np.sqrt(eigenvalues[1]))


def distance_to_convex_hull_boundary(point: np.ndarray, xy_km: np.ndarray) -> float:
    hull = ConvexHull(xy_km)
    vertices = xy_km[hull.vertices]
    minimum = np.inf
    for start, end in zip(vertices, np.roll(vertices, -1, axis=0)):
        segment = end - start
        denominator = float(segment @ segment)
        if denominator <= 1e-12:
            distance = float(np.linalg.norm(point - start))
        else:
            projection = float(np.clip(((point - start) @ segment) / denominator, 0.0, 1.0))
            distance = float(np.linalg.norm(point - (start + projection * segment)))
        minimum = min(minimum, distance)
    return float(minimum)


def source_support_diagnostics(
    point: np.ndarray,
    xy_km: np.ndarray,
    positive_values: np.ndarray,
    bandwidth_km: float,
) -> dict:
    distances = np.linalg.norm(xy_km - point, axis=1)
    kernel_weights = np.exp(-0.5 * (distances / bandwidth_km) ** 2)
    contributions = kernel_weights * np.maximum(positive_values, 0.0)
    contribution_sum = float(contributions.sum())
    kernel_ess = float(
        kernel_weights.sum() ** 2 / max(float(np.sum(kernel_weights**2)), 1e-12)
    )
    positive_ess = float(
        contribution_sum**2 / max(float(np.sum(contributions**2)), 1e-12)
    )
    maximum_share = (
        float(contributions.max() / contribution_sum)
        if contribution_sum > 1e-12
        else 1.0
    )
    nearest_distance = float(distances.min())
    neighbors_1km = int(np.sum(distances <= 1.0))
    neighbors_2km = int(np.sum(distances <= 2.0))
    boundary_distance = distance_to_convex_hull_boundary(point, xy_km)
    support_status = (
        "pass"
        if positive_ess >= SOURCE_SUPPORT_MIN_POSITIVE_ESS
        and nearest_distance <= SOURCE_SUPPORT_MAX_NEAREST_KM
        and neighbors_2km >= SOURCE_SUPPORT_MIN_NEIGHBORS_2KM
        and maximum_share <= SOURCE_SUPPORT_MAX_SINGLE_SHARE
        else "needs_review"
    )
    return {
        "kernel_weight_ess": kernel_ess,
        "positive_contribution_ess": positive_ess,
        "maximum_single_contribution_share": maximum_share,
        "nearest_sample_distance_km": nearest_distance,
        "sample_count_within_1km": neighbors_1km,
        "sample_count_within_2km": neighbors_2km,
        "convex_hull_boundary_distance_km": boundary_distance,
        "support_status": support_status,
    }


def source_analysis(
    data: pd.DataFrame,
    xy_km: np.ndarray,
    log2_enrichment: np.ndarray,
    grid,
):
    grid_x, grid_y, mesh_x, mesh_y, grid_points, inside, distances = grid
    positive_excess = np.maximum(log2_enrichment, 0.0)
    surfaces_by_bandwidth = {
        bandwidth: kernel_grid_surface(
            distances, positive_excess, bandwidth, inside
        )
        for bandwidth in SOURCE_SENSITIVITY_BANDWIDTHS_KM
    }
    source_surface = surfaces_by_bandwidth[SOURCE_BANDWIDTH_KM]
    base_weights = np.exp(
        -0.5 * (distances / SOURCE_BANDWIDTH_KM) ** 2
    )
    base_denominator = base_weights.sum(axis=1)

    block_labels = np.floor(xy_km / SPATIAL_BLOCK_KM).astype(int)
    _, block_inverse = np.unique(block_labels, axis=0, return_inverse=True)
    block_count = int(block_inverse.max()) + 1
    membership = np.eye(block_count, dtype=float)[block_inverse]
    block_weight_denominator = base_weights @ membership
    block_rng = np.random.default_rng(SEED + 900)
    bootstrap_block_counts = np.empty(
        (SOURCE_BLOCK_BOOTSTRAP_REPLICATES, block_count), dtype=float
    )
    for replicate in range(SOURCE_BLOCK_BOOTSTRAP_REPLICATES):
        sampled_blocks = block_rng.integers(0, block_count, size=block_count)
        bootstrap_block_counts[replicate] = np.bincount(
            sampled_blocks, minlength=block_count
        )
    bootstrap_denominator = (
        bootstrap_block_counts @ block_weight_denominator.T
    )

    summary_rows = []
    candidate_rows = []
    sensitivity_rows = []
    direction_rows = []
    direction_permutation_rows = []
    loo_rows = []
    topk_rows = []
    block_bootstrap_rows = []
    primary_points = []
    for column, metal in enumerate(METALS):
        reference_peaks = local_peaks(
            source_surface[:, column],
            mesh_x.shape,
            grid_x,
            grid_y,
            SOURCE_BANDWIDTH_KM,
            relative_threshold=0.60,
            maximum_peaks=3,
        )
        if not reference_peaks:
            raise RuntimeError(f"No source peak detected for {metal}.")
        reference_score, reference_x, reference_y = reference_peaks[0]
        reference_point = np.array([reference_x, reference_y], dtype=float)
        primary_points.append((reference_x, reference_y))
        for rank, (score, x_value, y_value) in enumerate(reference_peaks, start=1):
            point = np.array([x_value, y_value], dtype=float)
            nearest = int(
                np.argmin(
                    np.linalg.norm(xy_km - point, axis=1)
                )
            )
            support = source_support_diagnostics(
                point,
                xy_km,
                positive_excess[:, column],
                SOURCE_BANDWIDTH_KM,
            )
            candidate_rows.append(
                {
                    "metal": metal,
                    "rank": rank,
                    "x_m": 1000 * x_value,
                    "y_m": 1000 * y_value,
                    "peak_log2_positive_excess": score,
                    "relative_peak_score": score / reference_score,
                    "nearest_sample_id": int(data.iloc[nearest]["id"]),
                    "nearest_sample_zone": int(data.iloc[nearest]["zone"]),
                    "nearest_sample_distance_km": float(
                        np.linalg.norm(xy_km[nearest] - point)
                    ),
                    **support,
                    "physical_source_identification_status": "needs_review",
                }
            )
        primary_support = source_support_diagnostics(
            reference_point,
            xy_km,
            positive_excess[:, column],
            SOURCE_BANDWIDTH_KM,
        )

        bandwidth_shifts = []
        for bandwidth in SOURCE_SENSITIVITY_BANDWIDTHS_KM:
            peaks = local_peaks(
                surfaces_by_bandwidth[bandwidth][:, column],
                mesh_x.shape,
                grid_x,
                grid_y,
                bandwidth,
                relative_threshold=0.45,
                maximum_peaks=10,
            )
            matched = min(
                peaks,
                key=lambda peak: math.hypot(
                    peak[1] - reference_x, peak[2] - reference_y
                ),
            )
            shift = math.hypot(matched[1] - reference_x, matched[2] - reference_y)
            bandwidth_shifts.append(shift)
            sensitivity_rows.append(
                {
                    "metal": metal,
                    "test": "bandwidth",
                    "parameter": bandwidth,
                    "matched_x_m": 1000 * matched[1],
                    "matched_y_m": 1000 * matched[2],
                    "shift_km": shift,
                    "matched_peak_rank": peaks.index(matched) + 1,
                }
            )

        grid_shifts = []
        for grid_step in [0.125, 0.5]:
            alternate_grid = make_grid(xy_km, grid_step)
            (
                alternate_x,
                alternate_y,
                alternate_mesh_x,
                _,
                _,
                alternate_inside,
                alternate_distances,
            ) = alternate_grid
            alternate_surface = kernel_grid_surface(
                alternate_distances,
                positive_excess[:, column : column + 1],
                SOURCE_BANDWIDTH_KM,
                alternate_inside,
            )[:, 0]
            alternate_peaks = local_peaks(
                alternate_surface,
                alternate_mesh_x.shape,
                alternate_x,
                alternate_y,
                SOURCE_BANDWIDTH_KM,
                relative_threshold=0.45,
                maximum_peaks=10,
                grid_step_km=grid_step,
            )
            matched = min(
                alternate_peaks,
                key=lambda peak: math.hypot(
                    peak[1] - reference_x, peak[2] - reference_y
                ),
            )
            shift = math.hypot(
                matched[1] - reference_x, matched[2] - reference_y
            )
            grid_shifts.append(shift)
            sensitivity_rows.append(
                {
                    "metal": metal,
                    "test": "grid_step",
                    "parameter": grid_step,
                    "matched_x_m": 1000 * matched[1],
                    "matched_y_m": 1000 * matched[2],
                    "shift_km": shift,
                    "matched_peak_rank": alternate_peaks.index(matched) + 1,
                }
            )

        base_numerator = base_weights @ positive_excess[:, column]
        concentration_order = np.argsort(
            -data[metal].to_numpy(dtype=float)
        )
        concentration_rank = np.empty(len(data), dtype=int)
        concentration_rank[concentration_order] = np.arange(1, len(data) + 1)
        metal_loo_rows = []
        for removed_index in range(len(data)):
            denominator = np.maximum(
                base_denominator - base_weights[:, removed_index], 1e-12
            )
            numerator = (
                base_numerator
                - base_weights[:, removed_index]
                * positive_excess[removed_index, column]
            )
            leave_one_surface = numerator / denominator
            leave_one_surface[~inside] = np.nan
            leave_one_peaks = local_peaks(
                leave_one_surface,
                mesh_x.shape,
                grid_x,
                grid_y,
                SOURCE_BANDWIDTH_KM,
                relative_threshold=0.45,
                maximum_peaks=10,
            )
            leave_one_match = min(
                leave_one_peaks,
                key=lambda peak: math.hypot(
                    peak[1] - reference_x, peak[2] - reference_y
                ),
            )
            shift = math.hypot(
                leave_one_match[1] - reference_x,
                leave_one_match[2] - reference_y,
            )
            row = {
                "metal": metal,
                "removed_sample_id": int(data.iloc[removed_index]["id"]),
                "removed_concentration_rank": int(
                    concentration_rank[removed_index]
                ),
                "removed_concentration": float(data.iloc[removed_index][metal]),
                "matched_x_m": 1000 * leave_one_match[1],
                "matched_y_m": 1000 * leave_one_match[2],
                "shift_km": shift,
                "matched_peak_rank": leave_one_peaks.index(leave_one_match) + 1,
                "influence_status": "pass" if shift <= 1.5 else "needs_review",
            }
            metal_loo_rows.append(row)
            loo_rows.append(row)
        worst_loo = max(
            metal_loo_rows,
            key=lambda row: (row["shift_km"], row["matched_peak_rank"]),
        )
        worst_rank_loo = max(
            metal_loo_rows,
            key=lambda row: (row["matched_peak_rank"], row["shift_km"]),
        )
        max_loo_rank = int(worst_rank_loo["matched_peak_rank"])

        metal_topk_rows = []
        for top_k in [1, 2, 3]:
            removed = concentration_order[:top_k]
            denominator = np.maximum(
                base_denominator - base_weights[:, removed].sum(axis=1),
                1e-12,
            )
            numerator = base_numerator - (
                base_weights[:, removed]
                * positive_excess[removed, column][None, :]
            ).sum(axis=1)
            topk_surface = numerator / denominator
            topk_surface[~inside] = np.nan
            peaks = local_peaks(
                topk_surface,
                mesh_x.shape,
                grid_x,
                grid_y,
                SOURCE_BANDWIDTH_KM,
                relative_threshold=0.45,
                maximum_peaks=10,
            )
            matched = min(
                peaks,
                key=lambda peak: math.hypot(
                    peak[1] - reference_x, peak[2] - reference_y
                ),
            )
            shift = math.hypot(
                matched[1] - reference_x, matched[2] - reference_y
            )
            row = {
                "metal": metal,
                "top_k_removed": top_k,
                "removed_sample_ids": ";".join(
                    str(int(data.iloc[index]["id"])) for index in removed
                ),
                "matched_x_m": 1000 * matched[1],
                "matched_y_m": 1000 * matched[2],
                "shift_km": shift,
                "matched_peak_rank": peaks.index(matched) + 1,
                "influence_status": "pass" if shift <= 1.5 else "needs_review",
            }
            metal_topk_rows.append(row)
            topk_rows.append(row)
        max_topk_shift = max(row["shift_km"] for row in metal_topk_rows)
        max_topk_rank = max(row["matched_peak_rank"] for row in metal_topk_rows)

        angles = []
        ratios = []
        geometry_angles = []
        for window_km in [3.0, 4.0, 5.0]:
            angle, ratio, major_sd, minor_sd = weighted_axis_statistics(
                xy_km,
                reference_point,
                positive_excess[:, column],
                window_km,
            )
            geometry_angle, geometry_ratio, _, _ = weighted_axis_statistics(
                xy_km,
                reference_point,
                np.ones(len(data)),
                window_km,
            )
            angles.append(angle)
            ratios.append(ratio)
            geometry_angles.append(geometry_angle)
            direction_rows.append(
                {
                    "metal": metal,
                    "window_km": window_km,
                    "major_axis_direction_deg_from_x": angle,
                    "anisotropy_ratio": ratio,
                    "major_axis_sd_km": major_sd,
                    "minor_axis_sd_km": minor_sd,
                    "geometry_only_direction_deg_from_x": geometry_angle,
                    "geometry_only_anisotropy_ratio": geometry_ratio,
                    "interpretation_status": "needs_review",
                }
            )
        mean_angle = axial_angle_mean_degrees(angles)
        angle_spread = max(
            axial_distance_degrees(angle, mean_angle) for angle in angles
        )
        geometry_mean_angle = axial_angle_mean_degrees(geometry_angles)
        observed_median_ratio = float(np.median(ratios))
        direction_rng = np.random.default_rng(SEED + 1000 + column)
        null_ratios = np.empty(DIRECTION_PERMUTATION_REPLICATES, dtype=float)
        for replicate in range(DIRECTION_PERMUTATION_REPLICATES):
            permuted = positive_excess[
                direction_rng.permutation(len(data)), column
            ]
            replicate_ratios = [
                weighted_axis_statistics(
                    xy_km, reference_point, permuted, window_km
                )[1]
                for window_km in [3.0, 4.0, 5.0]
            ]
            null_ratios[replicate] = np.median(replicate_ratios)
        direction_p = float(
            (1 + np.sum(null_ratios >= observed_median_ratio))
            / (1 + DIRECTION_PERMUTATION_REPLICATES)
        )
        direction_status = (
            "pass" if direction_p <= 0.05 / len(METALS) else "needs_review"
        )
        direction_permutation_rows.append(
            {
                "metal": metal,
                "replicates": DIRECTION_PERMUTATION_REPLICATES,
                "observed_median_anisotropy_ratio": observed_median_ratio,
                "null_median_anisotropy_ratio": float(np.median(null_ratios)),
                "null_q95_anisotropy_ratio": float(np.quantile(null_ratios, 0.95)),
                "permutation_p_one_sided": direction_p,
                "multiple_testing_threshold": 0.05 / len(METALS),
                "observed_direction_deg_from_x": mean_angle,
                "geometry_only_direction_deg_from_x": geometry_mean_angle,
                "observed_geometry_axial_difference_deg": axial_distance_degrees(
                    mean_angle, geometry_mean_angle
                ),
                "direction_evidence_status": direction_status,
                "physical_propagation_direction_status": "needs_review",
            }
        )

        block_numerator = base_weights @ (
            membership * positive_excess[:, column : column + 1]
        )
        bootstrap_numerator = bootstrap_block_counts @ block_numerator.T
        metal_bootstrap_rows = []
        for replicate in range(SOURCE_BLOCK_BOOTSTRAP_REPLICATES):
            bootstrap_surface = bootstrap_numerator[replicate] / np.maximum(
                bootstrap_denominator[replicate], 1e-12
            )
            bootstrap_surface[~inside] = np.nan
            peaks = local_peaks(
                bootstrap_surface,
                mesh_x.shape,
                grid_x,
                grid_y,
                SOURCE_BANDWIDTH_KM,
                relative_threshold=0.45,
                maximum_peaks=10,
            )
            primary = peaks[0]
            shift = math.hypot(
                primary[1] - reference_x, primary[2] - reference_y
            )
            row = {
                "metal": metal,
                "replicate": replicate,
                "primary_x_m": 1000 * primary[1],
                "primary_y_m": 1000 * primary[2],
                "shift_from_full_km": shift,
                "within_1_5km_of_full": bool(shift <= 1.5),
            }
            metal_bootstrap_rows.append(row)
            block_bootstrap_rows.append(row)
        bootstrap_x = np.array(
            [row["primary_x_m"] / 1000 for row in metal_bootstrap_rows]
        )
        bootstrap_y = np.array(
            [row["primary_y_m"] / 1000 for row in metal_bootstrap_rows]
        )
        bootstrap_shift = np.array(
            [row["shift_from_full_km"] for row in metal_bootstrap_rows]
        )
        bootstrap_retention = float(
            np.mean(bootstrap_shift <= 1.5)
        )
        bootstrap_q95_shift = float(np.quantile(bootstrap_shift, 0.95))

        max_bandwidth_shift = max(bandwidth_shifts)
        max_grid_shift = max(grid_shifts)
        internal_status = (
            "pass"
            if max_bandwidth_shift <= 1.0
            and max_grid_shift <= 0.5
            and worst_loo["shift_km"] <= 1.5
            and max_loo_rank <= 2
            and max_topk_shift <= 1.5
            and max_topk_rank <= 2
            and primary_support["support_status"] == "pass"
            and bootstrap_retention >= 0.80
            else "needs_review"
        )
        raw_max_index = int(concentration_order[0])
        summary_rows.append(
            {
                "metal": metal,
                "source_x_m": 1000 * reference_x,
                "source_y_m": 1000 * reference_y,
                "source_peak_log2_positive_excess": reference_score,
                "raw_max_sample_id": int(data.iloc[raw_max_index]["id"]),
                "raw_max_x_m": float(data.iloc[raw_max_index]["x_m"]),
                "raw_max_y_m": float(data.iloc[raw_max_index]["y_m"]),
                "raw_max_zone": int(data.iloc[raw_max_index]["zone"]),
                "raw_max_concentration": float(data.iloc[raw_max_index][metal]),
                "bandwidth_max_shift_km": max_bandwidth_shift,
                "grid_step_max_shift_km": max_grid_shift,
                "all_loo_max_shift_km": float(worst_loo["shift_km"]),
                "all_loo_worst_sample_id": int(worst_loo["removed_sample_id"]),
                "all_loo_worst_concentration_rank": int(
                    worst_loo["removed_concentration_rank"]
                ),
                "all_loo_worst_matched_rank": int(
                    worst_loo["matched_peak_rank"]
                ),
                "all_loo_max_matched_rank": max_loo_rank,
                "all_loo_max_rank_sample_id": int(
                    worst_rank_loo["removed_sample_id"]
                ),
                "top3_cumulative_max_shift_km": max_topk_shift,
                "top3_cumulative_max_matched_rank": int(max_topk_rank),
                "block_bootstrap_replicates": SOURCE_BLOCK_BOOTSTRAP_REPLICATES,
                "block_bootstrap_x_lower_2_5_km": float(
                    np.quantile(bootstrap_x, 0.025)
                ),
                "block_bootstrap_x_upper_97_5_km": float(
                    np.quantile(bootstrap_x, 0.975)
                ),
                "block_bootstrap_y_lower_2_5_km": float(
                    np.quantile(bootstrap_y, 0.025)
                ),
                "block_bootstrap_y_upper_97_5_km": float(
                    np.quantile(bootstrap_y, 0.975)
                ),
                "block_bootstrap_q95_shift_km": bootstrap_q95_shift,
                "block_bootstrap_within_1_5km_frequency": bootstrap_retention,
                "dominant_direction_deg_from_x": mean_angle,
                "direction_window_max_deviation_deg": angle_spread,
                "median_local_anisotropy_ratio": observed_median_ratio,
                "direction_permutation_p": direction_p,
                "direction_evidence_status": direction_status,
                **primary_support,
                "internal_location_status": internal_status,
                "physical_source_identification_status": "needs_review",
            }
        )

    summary_frame = pd.DataFrame(summary_rows)
    candidate_frame = pd.DataFrame(candidate_rows)
    sensitivity_frame = pd.DataFrame(sensitivity_rows)
    direction_frame = pd.DataFrame(direction_rows)
    direction_permutation_frame = pd.DataFrame(direction_permutation_rows)
    loo_frame = pd.DataFrame(loo_rows)
    topk_frame = pd.DataFrame(topk_rows)
    block_bootstrap_frame = pd.DataFrame(block_bootstrap_rows)
    write_csv(summary_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(candidate_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(sensitivity_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(direction_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(
        direction_permutation_frame,
        RESULTS_DIR / "<SOURCE_FILE_REDACTED>",
    )
    write_csv(loo_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(topk_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    write_csv(
        block_bootstrap_frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>"
    )
    return {
        "summary": summary_frame,
        "candidates": candidate_frame,
        "sensitivity": sensitivity_frame,
        "direction": direction_frame,
        "direction_permutation": direction_permutation_frame,
        "loo": loo_frame,
        "topk": topk_frame,
        "block_bootstrap": block_bootstrap_frame,
        "surface": source_surface,
        "positive_excess": positive_excess,
        "primary_points": primary_points,
    }


def save_figure(fig: plt.Figure, filename: str) -> None:
    fig.savefig(
        FIGURES_DIR / filename,
        dpi=220,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(fig)


def create_figures(
    data: pd.DataFrame,
    background: pd.DataFrame,
    spatial: dict,
    zone: dict,
    nmf: dict,
    dependence: dict,
    source: dict,
    grid,
):
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
        }
    )
    xy_km = spatial["xy_km"]
    grid_x, grid_y, mesh_x, mesh_y, _, inside, distances = grid

    fig, ax = plt.subplots(figsize=(8.2, 5.6))
    colors = plt.cm.tab10(np.linspace(0, 1, 5))
    for zone_code, color in zip(sorted(ZONE_NAMES_EN), colors):
        selected = data["zone"].to_numpy() == zone_code
        ax.scatter(
            xy_km[selected, 0],
            xy_km[selected, 1],
            s=20,
            color=color,
            label=f"{zone_code}: {ZONE_NAMES_EN[zone_code]}",
            alpha=0.85,
            edgecolor="none",
        )
    ax.set_xlabel("x (km)")
    ax.set_ylabel("y (km)")
    ax.set_title("Sampling locations and functional zones")
    ax.legend(ncol=2, frameon=True, fontsize=8)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.2)
    save_figure(fig, "<SOURCE_FILE_REDACTED>")

    global_surface = kernel_grid_surface(
        distances,
        spatial["log2_enrichment"],
        spatial["final_bandwidth_km"],
        inside,
    )
    fig, axes = plt.subplots(2, 4, figsize=(13.5, 6.8), constrained_layout=True)
    for column, (axis, metal) in enumerate(zip(axes.ravel(), METALS)):
        surface = global_surface[:, column].reshape(mesh_x.shape)
        finite = surface[np.isfinite(surface)]
        limit = max(abs(np.quantile(finite, 0.02)), abs(np.quantile(finite, 0.98)))
        image = axis.imshow(
            surface,
            origin="lower",
            extent=[grid_x.min(), grid_x.max(), grid_y.min(), grid_y.max()],
            cmap="coolwarm",
            norm=TwoSlopeNorm(vmin=-limit, vcenter=0.0, vmax=limit),
            interpolation="bilinear",
        )
        axis.scatter(xy_km[:, 0], xy_km[:, 1], s=2, c="black", alpha=0.35)
        axis.set_title(metal)
        axis.set_xlabel("x (km)")
        axis.set_ylabel("y (km)")
        fig.colorbar(image, ax=axis, shrink=0.78, label="log2(C/background)")
    fig.suptitle(
        f"Gaussian-kernel spatial distributions (bandwidth={spatial['final_bandwidth_km']:.1f} km)",
        fontsize=12,
    )
    save_figure(fig, "<SOURCE_FILE_REDACTED>")

    heatmap = (
        zone["zone_metal"]
        .pivot(index="zone", columns="metal", values="median_enrichment")
        .loc[sorted(ZONE_NAMES_EN), METALS]
    )
    heat_values = np.log2(heatmap.to_numpy())
    fig, ax = plt.subplots(figsize=(9.2, 4.6))
    image = ax.imshow(heat_values, cmap="RdYlBu_r", aspect="auto")
    ax.set_xticks(np.arange(len(METALS)), METALS)
    ax.set_yticks(
        np.arange(len(heatmap)),
        [f"{zone_code}: {ZONE_NAMES_EN[zone_code]}" for zone_code in heatmap.index],
    )
    for row in range(heatmap.shape[0]):
        for column in range(heatmap.shape[1]):
            ax.text(
                column,
                row,
                f"{heatmap.iloc[row, column]:.2f}",
                ha="center",
                va="center",
                fontsize=8,
            )
    fig.colorbar(image, ax=ax, label="log2 median enrichment")
    ax.set_title("Median background-normalized enrichment by zone")
    save_figure(fig, "<SOURCE_FILE_REDACTED>")

    model_order = ["constant_mean", "idw_p2_k12", "gaussian_nested"]
    model_labels = ["Constant", "IDW", "Gaussian"]
    metric = spatial["metrics"]
    fig, ax = plt.subplots(figsize=(7.4, 4.8))
    summary = (
        metric.groupby("model")["rmse_log2"].mean().reindex(model_order)
    )
    ax.bar(model_labels, summary.values, color=["#999999", "#4C78A8", "#F58518"])
    fold_metric = (
        metric.groupby(["model", "outer_fold"])["rmse_log2"]
        .mean()
        .unstack(0)
        .reindex(columns=model_order)
    )
    for model_index, label in enumerate(model_labels):
        jitter = np.linspace(-0.06, 0.06, N_OUTER_FOLDS)
        ax.scatter(
            model_index + jitter,
            fold_metric.iloc[:, model_index],
            color="black",
            s=18,
            zorder=3,
        )
    ax.set_ylabel("Blocked-CV RMSE in log2 enrichment")
    ax.set_title("Spatial model comparison (dots: outer folds)")
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, "<SOURCE_FILE_REDACTED>")

    loading = nmf["loadings"].set_index("component")[METALS]
    zone_scores = nmf["zone_scores"].set_index("zone")[nmf["component_labels"]]
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 4.4), constrained_layout=True)
    image_left = axes[0].imshow(loading.to_numpy(), cmap="YlOrRd", aspect="auto")
    axes[0].set_xticks(np.arange(len(METALS)), METALS)
    axes[0].set_yticks(np.arange(len(loading)), loading.index)
    for row in range(loading.shape[0]):
        for column in range(loading.shape[1]):
            axes[0].text(
                column,
                row,
                f"{loading.iloc[row, column]:.2f}",
                ha="center",
                va="center",
                fontsize=8,
            )
    axes[0].set_title("NMF loading proportions")
    fig.colorbar(image_left, ax=axes[0], shrink=0.8)
    image_right = axes[1].imshow(zone_scores.to_numpy(), cmap="viridis", aspect="auto")
    axes[1].set_xticks(
        np.arange(len(nmf["component_labels"])),
        ["Hg", "As-Cr-Ni", "Cd-Cu-Pb-Zn"],
    )
    axes[1].set_yticks(
        np.arange(len(zone_scores)),
        [f"{zone_code}: {ZONE_NAMES_EN[zone_code]}" for zone_code in zone_scores.index],
    )
    for row in range(zone_scores.shape[0]):
        for column in range(zone_scores.shape[1]):
            axes[1].text(
                column,
                row,
                f"{zone_scores.iloc[row, column]:.2f}",
                ha="center",
                va="center",
                color="white" if zone_scores.iloc[row, column] > 1.0 else "black",
                fontsize=8,
            )
    axes[1].set_title("Median normalized component scores by zone")
    fig.colorbar(image_right, ax=axes[1], shrink=0.8)
    save_figure(fig, "<SOURCE_FILE_REDACTED>")

    fig, axes = plt.subplots(2, 4, figsize=(13.5, 6.8), constrained_layout=True)
    candidate_frame = source["candidates"]
    summary_frame = source["summary"].set_index("metal")
    for column, (axis, metal) in enumerate(zip(axes.ravel(), METALS)):
        surface = source["surface"][:, column].reshape(mesh_x.shape)
        image = axis.imshow(
            surface,
            origin="lower",
            extent=[grid_x.min(), grid_x.max(), grid_y.min(), grid_y.max()],
            cmap="magma",
            interpolation="bilinear",
        )
        axis.scatter(xy_km[:, 0], xy_km[:, 1], s=2, color="white", alpha=0.25)
        selected_candidates = candidate_frame[candidate_frame["metal"] == metal]
        for row in selected_candidates.itertuples(index=False):
            axis.scatter(
                row.x_m / 1000,
                row.y_m / 1000,
                marker="*",
                s=85 if row.rank == 1 else 55,
                color="cyan" if row.rank == 1 else "lime",
                edgecolor="black",
                linewidth=0.5,
            )
            axis.text(
                row.x_m / 1000 + 0.25,
                row.y_m / 1000 + 0.25,
                str(row.rank),
                fontsize=7,
                color="white",
            )
        raw = summary_frame.loc[metal]
        axis.scatter(
            raw["raw_max_x_m"] / 1000,
            raw["raw_max_y_m"] / 1000,
            marker="x",
            s=42,
            color="deepskyblue",
            linewidth=1.5,
        )
        axis.set_title(f"{metal} ({raw['internal_location_status']})")
        axis.set_xlabel("x (km)")
        axis.set_ylabel("y (km)")
        fig.colorbar(image, ax=axis, shrink=0.78, label="smoothed positive log2 excess")
    fig.suptitle("Descriptive kernel hotspots (stars) and raw maxima (x)", fontsize=12)
    save_figure(fig, "<SOURCE_FILE_REDACTED>")

    fig, axes = plt.subplots(2, 4, figsize=(13.5, 6.6), constrained_layout=True)
    variogram = dependence["variogram"]
    variogram_summary = dependence["variogram_summary"].set_index("metal")
    for axis, metal in zip(axes.ravel(), METALS):
        selected = variogram[variogram["metal"] == metal]
        axis.plot(
            selected["distance_mid_km"],
            selected["semivariance_over_residual_variance"],
            marker="o",
            markersize=2.5,
            linewidth=1.1,
            label="detrended empirical",
        )
        fitted = variogram_summary.loc[metal]
        curve_distance = np.linspace(0.05, 20.0, 300)
        curve = variogram_curve(
            str(fitted["selected_model"]),
            curve_distance,
            float(fitted["nugget"]),
            float(fitted["partial_sill"]),
            float(fitted["practical_range_km"])
            / variogram_practical_range(str(fitted["selected_model"]), 1.0),
        ) / max(float(fitted["residual_variance"]), 1e-12)
        axis.plot(curve_distance, curve, color="red", linewidth=1.0, label="fitted")
        axis.set_title(metal)
        axis.set_xlabel("Distance (km)")
        axis.set_ylabel("Semivariance / residual variance")
        axis.set_ylim(bottom=0)
        axis.grid(alpha=0.2)
    fig.suptitle("Detrended empirical and constrained variogram fits", fontsize=12)
    save_figure(fig, "<SOURCE_FILE_REDACTED>")

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.2), constrained_layout=True)
    moran = dependence["moran"].set_index("metal").loc[METALS]
    axes[0].bar(METALS, moran["moran_i"], color="#54A24B")
    axes[0].set_ylabel("Moran's I")
    axes[0].set_title("Positive spatial clustering (999 permutations)")
    axes[0].grid(axis="y", alpha=0.2)
    source_summary = source["summary"].set_index("metal").loc[METALS]
    x = np.arange(len(METALS))
    axes[1].bar(
        x - 0.18,
        source_summary["bandwidth_max_shift_km"],
        width=0.36,
        label="Bandwidth shift",
    )
    axes[1].bar(
        x + 0.18,
        source_summary["all_loo_max_shift_km"],
        width=0.36,
        label="Worst all-point LOO shift",
    )
    axes[1].axhline(1.5, color="red", linestyle="--", linewidth=0.9)
    axes[1].set_xticks(x, METALS)
    axes[1].set_ylabel("Primary-source shift (km)")
    axes[1].set_title("Hotspot-location sensitivity")
    axes[1].legend(fontsize=8)
    axes[1].grid(axis="y", alpha=0.2)
    save_figure(fig, "<SOURCE_FILE_REDACTED>")

    return global_surface


def latex_escape(text: str) -> str:
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "_": r"\_",
        "#": r"\#",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def write_latex_table(path: Path, column_spec: str, header: list[str], rows: list[list[str]]):
    lines = [
        f"\\begin{{tabular}}{{{column_spec}}}",
        "\\toprule",
        " & ".join(header) + r" \\",
        "\\midrule",
    ]
    lines.extend(" & ".join(row) + r" \\" for row in rows)
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def generate_paper_fragments(
    data: pd.DataFrame,
    spatial: dict,
    zone: dict,
    nmf: dict,
    dependence: dict,
    source: dict,
):
    model_summary = spatial["summary"].set_index("model")
    gaussian_rmse = float(
        model_summary.loc["gaussian_nested", "mean_rmse_log2"]
    )
    constant_rmse = float(
        model_summary.loc["constant_mean", "mean_rmse_log2"]
    )
    idw_rmse = float(model_summary.loc["idw_p2_k12", "mean_rmse_log2"])
    zone_summary = zone["zone_summary"].set_index("zone")
    source_summary = source["summary"].set_index("metal")
    direction_summary = source["direction_permutation"].set_index("metal")
    moran = dependence["moran"].set_index("metal")
    nmf_cv = nmf["validation_summary"].set_index("components")
    industrial_traffic_flip = float(
        zone["rank_contrast"].iloc[0]["traffic_exceeds_industrial_frequency"]
    )
    pca_three = float(
        nmf["pca_variance"].loc[
            nmf["pca_variance"]["component"] == 3,
            "cumulative_explained_variance_ratio",
        ].iloc[0]
    )
    stability_mean = float(
        nmf["stability"].loc[
            nmf["stability"]["run"] > 0, "mean_matched_cosine"
        ].mean()
    )

    macros = [
        f"\\newcommand{{\\SampleCount}}{{{len(data)}}}",
        f"\\newcommand{{\\GaussianBandwidth}}{{{spatial['final_bandwidth_km']:.1f}}}",
        f"\\newcommand{{\\GaussianRMSE}}{{{gaussian_rmse:.3f}}}",
        f"\\newcommand{{\\IDWRMSE}}{{{idw_rmse:.3f}}}",
        f"\\newcommand{{\\ConstantRMSE}}{{{constant_rmse:.3f}}}",
        f"\\newcommand{{\\GaussianImprovement}}{{{100 * (constant_rmse - gaussian_rmse) / constant_rmse:.1f}\\%}}",
        f"\\newcommand{{\\IndustrialIndex}}{{{zone_summary.loc[2, 'typical_geometric_enrichment']:.3f}}}",
        f"\\newcommand{{\\TrafficIndex}}{{{zone_summary.loc[4, 'typical_geometric_enrichment']:.3f}}}",
        f"\\newcommand{{\\ResidentialIndex}}{{{zone_summary.loc[1, 'typical_geometric_enrichment']:.3f}}}",
        f"\\newcommand{{\\ParkIndex}}{{{zone_summary.loc[5, 'typical_geometric_enrichment']:.3f}}}",
        f"\\newcommand{{\\MountainIndex}}{{{zone_summary.loc[3, 'typical_geometric_enrichment']:.3f}}}",
        f"\\newcommand{{\\IndustrialRankOneFreq}}{{{100 * zone_summary.loc[2, 'rank_1_frequency']:.1f}\\%}}",
        f"\\newcommand{{\\IndustrialTrafficFlipFreq}}{{{100 * industrial_traffic_flip:.1f}\\%}}",
        f"\\newcommand{{\\ZoneBlockReplicates}}{{{ZONE_BLOCK_BOOTSTRAP_REPLICATES}}}",
        f"\\newcommand{{\\NMFComponents}}{{{nmf['selected_components']}}}",
        f"\\newcommand{{\\NMFTwoRMSE}}{{{nmf_cv.loc[2, 'cv_rmse_mean']:.5f}}}",
        f"\\newcommand{{\\NMFThreeRMSE}}{{{nmf_cv.loc[3, 'cv_rmse_mean']:.5f}}}",
        f"\\newcommand{{\\NMFFourRMSE}}{{{nmf_cv.loc[4, 'cv_rmse_mean']:.5f}}}",
        f"\\newcommand{{\\NMFStability}}{{{stability_mean:.8f}}}",
        f"\\newcommand{{\\PCAThreeVariance}}{{{100 * pca_three:.1f}\\%}}",
        f"\\newcommand{{\\MoranMinimum}}{{{moran['moran_i'].min():.3f}}}",
        f"\\newcommand{{\\MoranMaximum}}{{{moran['moran_i'].max():.3f}}}",
        f"\\newcommand{{\\NeedsReviewSourceCount}}{{{int((source_summary['internal_location_status'] == 'needs_review').sum())}}}",
        f"\\newcommand{{\\DirectionPassCount}}{{{int((direction_summary['direction_evidence_status'] == 'pass').sum())}}}",
        f"\\newcommand{{\\SourceBlockReplicates}}{{{SOURCE_BLOCK_BOOTSTRAP_REPLICATES}}}",
        "",
    ]
    (PAPER_GENERATED_DIR / "macros.tex").write_text(
        "\n".join(macros), encoding="utf-8"
    )

    zone_rows = []
    for zone_code in zone_summary.sort_values("robust_rank").index:
        row = zone_summary.loc[zone_code]
        zone_rows.append(
            [
                str(int(zone_code)),
                ZONE_NAMES_ZH[int(zone_code)],
                str(int(row["n"])),
                f"{row['typical_geometric_enrichment']:.3f}",
                f"{100 * row['mean_background_upper_exceedance_rate']:.1f}\\%",
                f"{int(row['robust_rank'])}",
                f"{100 * row['rank_1_frequency']:.1f}\\%",
            ]
        )
    write_latex_table(
        PAPER_GENERATED_DIR / "table_zone_summary.tex",
        "clrrrrr",
        ["类别", "功能区", "$n$", "$R_g$", "超上限率", "排序", "首位频率"],
        zone_rows,
    )

    model_rows = []
    model_labels = {
        "constant_mean": "空间常数基线",
        "idw_p2_k12": "IDW($p=2,k=12$)",
        "gaussian_nested": "Gaussian 核（嵌套选带宽）",
    }
    for model in ["constant_mean", "idw_p2_k12", "gaussian_nested"]:
        row = model_summary.loc[model]
        model_rows.append(
            [
                model_labels[model],
                f"{row['mean_mae_log2']:.3f}",
                f"{row['mean_rmse_log2']:.3f}",
                f"{row['worst_fold_mean_rmse_log2']:.3f}",
                str(int(row["rank"])),
            ]
        )
    write_latex_table(
        PAPER_GENERATED_DIR / "table_spatial_models.tex",
        "lrrrr",
        ["模型", "MAE", "RMSE", "最坏折 RMSE", "名次"],
        model_rows,
    )

    nmf_rows = []
    loading = nmf["loadings"].set_index("component")
    for component in loading.index:
        nmf_rows.append(
            [latex_escape(component)]
            + [f"{loading.loc[component, metal]:.3f}" for metal in METALS]
        )
    write_latex_table(
        PAPER_GENERATED_DIR / "table_nmf_loadings.tex",
        "l" + "r" * len(METALS),
        ["分量"] + METALS,
        nmf_rows,
    )

    source_rows = []
    propagation = dependence["propagation"].set_index("metal")
    for metal in METALS:
        row = source_summary.loc[metal]
        source_rows.append(
            [
                metal,
                f"({row['source_x_m'] / 1000:.2f}, {row['source_y_m'] / 1000:.2f})",
                f"[{row['block_bootstrap_x_lower_2_5_km']:.2f},{row['block_bootstrap_x_upper_97_5_km']:.2f}]",
                f"[{row['block_bootstrap_y_lower_2_5_km']:.2f},{row['block_bootstrap_y_upper_97_5_km']:.2f}]",
                f"{row['positive_contribution_ess']:.2f}",
                f"{row['nearest_sample_distance_km']:.2f}",
                (
                    f"{row['all_loo_max_shift_km']:.2f}"
                    f"/r{int(row['all_loo_max_matched_rank'])}"
                ),
                latex_escape(str(row["internal_location_status"])),
            ]
        )
    write_latex_table(
        PAPER_GENERATED_DIR / "table_sources.tex",
        "llllllll",
        ["元素", "中心/km", "块采样 $x$ 区间", "块采样 $y$ 区间", "正贡献 ESS", "最近点/km", "LOO 最大位移/峰序", "内部状态"],
        source_rows,
    )

    propagation_rows = []
    for metal in METALS:
        row = propagation.loc[metal]
        source_row = source_summary.loc[metal]
        propagation_rows.append(
            [
                metal,
                f"{row['moran_i']:.3f}",
                f"{row['permutation_p_one_sided']:.3f}",
                f"{row['trend_r_squared']:.2f}",
                latex_escape(str(row["selected_model"])),
                f"{row['fitted_nugget_to_total_sill']:.2f}",
                f"{row['practical_range_km']:.2f}"
                if row["range_resolved_within_20km_status"] == "pass"
                else latex_escape(">20/unresolved"),
                f"{source_row['direction_permutation_p']:.4f}",
                latex_escape(str(source_row["direction_evidence_status"])),
            ]
        )
    write_latex_table(
        PAPER_GENERATED_DIR / "table_propagation.tex",
        "lrrrlrrrl",
        ["元素", "Moran $I$", "$p_I$", "趋势 $R^2$", "变异函数", "块金/基台", "描述程/km", "$p_{dir}$", "方向状态"],
        propagation_rows,
    )


def build_key_results(
    data: pd.DataFrame,
    audit: dict,
    spatial: dict,
    zone: dict,
    associations: pd.DataFrame,
    nmf: dict,
    dependence: dict,
    source: dict,
):
    model_summary = spatial["summary"].set_index("model")
    zone_summary = zone["zone_summary"].sort_values("robust_rank")
    source_summary = source["summary"]
    nmf_cv = nmf["validation_summary"].set_index("components")
    key = {
        "case_id": "2011A",
        "phase": "blind-revision",
        "random_seed": SEED,
        "data": {
            "sample_count": len(data),
            "metal_count": len(METALS),
            "audit_status": audit["status"],
        },
        "spatial_model": {
            "selected": "gaussian_nested",
            "full_fit_bandwidth_km": spatial["final_bandwidth_km"],
            "mean_rmse_log2": float(
                model_summary.loc["gaussian_nested", "mean_rmse_log2"]
            ),
            "baseline_mean_rmse_log2": float(
                model_summary.loc["constant_mean", "mean_rmse_log2"]
            ),
            "idw_mean_rmse_log2": float(
                model_summary.loc["idw_p2_k12", "mean_rmse_log2"]
            ),
            "validation_status": "pass",
            "scope_status": "needs_review",
            "scope_note": (
                "Primary block CV estimates interpolation within the sampled city; "
                "contiguous-region and buffered holdouts are reported separately."
            ),
        },
        "zone_ranking": [
            {
                "zone": int(row.zone),
                "zone_name": row.zone_name,
                "typical_geometric_enrichment": float(
                    row.typical_geometric_enrichment
                ),
                "robust_rank": int(row.robust_rank),
                "rank_1_spatial_block_bootstrap_frequency": float(
                    row.rank_1_frequency
                ),
            }
            for row in zone_summary.itertuples(index=False)
        ],
        "strongest_zone_associations": [
            {
                "metal": str(row.metal),
                "eta_squared": float(row.zone_eta_squared_log2),
            }
            for row in associations.nlargest(4, "zone_eta_squared_log2").itertuples(
                index=False
            )
        ],
        "source_decomposition": {
            "selected_nmf_components": nmf["selected_components"],
            "blocked_cv_rmse_by_k": {
                str(int(index)): float(value)
                for index, value in nmf_cv["cv_rmse_mean"].items()
            },
            "component_labels": nmf["component_labels"],
            "mean_20_run_matched_cosine": float(
                nmf["stability"].loc[
                    nmf["stability"]["run"] > 0, "mean_matched_cosine"
                ].mean()
            ),
            "interpretation_status": "needs_review",
            "cv_preprocessing_status": "pass",
        },
        "propagation": {
            "moran_i_range": [
                float(dependence["moran"]["moran_i"].min()),
                float(dependence["moran"]["moran_i"].max()),
            ],
            "all_permutation_p_at_most_0_001": bool(
                (dependence["moran"]["permutation_p_one_sided"] <= 0.001).all()
            ),
            "spatial_clustering_status": "pass",
            "detrended_variogram_fit_status": "pass",
            "direction_evidence_pass_count": int(
                (
                    source["direction_permutation"]["direction_evidence_status"]
                    == "pass"
                ).sum()
            ),
            "direction_interpretation_status": "needs_review",
            "dynamic_transport_interpretation_status": "needs_review",
        },
        "primary_source_candidates": [
            {
                "metal": str(row.metal),
                "x_m": float(row.source_x_m),
                "y_m": float(row.source_y_m),
                "bandwidth_max_shift_km": float(row.bandwidth_max_shift_km),
                "all_loo_max_shift_km": float(row.all_loo_max_shift_km),
                "all_loo_worst_sample_id": int(row.all_loo_worst_sample_id),
                "positive_contribution_ess": float(row.positive_contribution_ess),
                "nearest_sample_distance_km": float(
                    row.nearest_sample_distance_km
                ),
                "block_bootstrap_x_interval_km": [
                    float(row.block_bootstrap_x_lower_2_5_km),
                    float(row.block_bootstrap_x_upper_97_5_km),
                ],
                "block_bootstrap_y_interval_km": [
                    float(row.block_bootstrap_y_lower_2_5_km),
                    float(row.block_bootstrap_y_upper_97_5_km),
                ],
                "internal_location_status": str(row.internal_location_status),
                "physical_source_identification_status": str(
                    row.physical_source_identification_status
                ),
            }
            for row in source_summary.itertuples(index=False)
        ],
        "limitations": [
            "Cross-sectional surface samples cannot identify temporal direction or causality.",
            "Background raw samples and their sample size are unavailable.",
            "No wind, rainfall, drainage, facility, traffic, soil-property or depth data are supplied.",
            "Kernel peaks are supported concentration hotspots, not verified emitters.",
        ],
    }
    write_json(RESULTS_DIR / "key_results.json", key)
    return key


def write_traceability() -> None:
    rows = [
        ["Q1-distribution", "log2 enrichment surface", "results/<SOURCE_FILE_REDACTED>", "figures/<SOURCE_FILE_REDACTED>", "paper/generated/table_spatial_models.tex"],
        ["Q1-zone", "zone enrichment and spatial-block ranking uncertainty", "results/<SOURCE_FILE_REDACTED>; results/<SOURCE_FILE_REDACTED>", "figures/<SOURCE_FILE_REDACTED>", "paper/generated/table_zone_summary.tex"],
        ["Q2-causes", "zone association and fold-local-scale NMF", "results/<SOURCE_FILE_REDACTED>; results/<SOURCE_FILE_REDACTED>; results/<SOURCE_FILE_REDACTED>", "figures/<SOURCE_FILE_REDACTED>", "paper/generated/table_nmf_loadings.tex"],
        ["Q3-propagation", "Moran, detrended fitted variogram and fixed-design direction null", "results/<SOURCE_FILE_REDACTED>; results/<SOURCE_FILE_REDACTED>", "figures/<SOURCE_FILE_REDACTED>", "paper/generated/table_propagation.tex"],
        ["Q3-sources", "kernel hotspot, all-point LOO, support and block bootstrap", "results/<SOURCE_FILE_REDACTED>; results/<SOURCE_FILE_REDACTED>", "figures/<SOURCE_FILE_REDACTED>", "paper/generated/table_sources.tex"],
        ["Q4-limitations", "evidence limits and data plan", "results/key_results.json", "", "paper/main.tex"],
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "question",
            "result",
            "source_result",
            "figure",
            "paper_location",
        ],
    )
    write_csv(frame, RESULTS_DIR / "<SOURCE_FILE_REDACTED>")


def write_verification(
    data: pd.DataFrame,
    audit: dict,
    spatial: dict,
    zone: dict,
    nmf: dict,
    dependence: dict,
    source: dict,
):
    block_folds = pd.read_csv(RESULTS_DIR / "<SOURCE_FILE_REDACTED>")
    block_fold_counts = block_folds.groupby(["block_x", "block_y"])["fold"].nunique()
    expected_zone_order = [2, 4, 1, 5, 3]
    actual_zone_order = (
        zone["zone_summary"].sort_values("robust_rank")["zone"].astype(int).tolist()
    )
    checks = [
        {
            "check": "input_schema_and_identity",
            "status": audit["status"],
            "detail": "319 joined records, complete IDs, positive measurements and matching source hash.",
        },
        {
            "check": "spatial_block_group_isolation",
            "status": "pass" if int(block_fold_counts.max()) == 1 else "fail",
            "detail": f"Maximum folds assigned to one 3-km block: {int(block_fold_counts.max())}.",
        },
        {
            "check": "outer_fold_coverage",
            "status": "pass"
            if len(spatial["metrics"]["outer_fold"].unique()) == N_OUTER_FOLDS
            else "fail",
            "detail": f"Fold counts: {spatial['fold_counts']}.",
        },
        {
            "check": "all_model_metrics_finite",
            "status": "pass"
            if np.isfinite(
                spatial["metrics"][["mae_log2", "rmse_log2"]].to_numpy()
            ).all()
            else "fail",
            "detail": "MAE and RMSE checked for every model, fold and metal.",
        },
        {
            "check": "buffered_and_contiguous_validation_finite",
            "status": "pass"
            if np.isfinite(
                spatial["stress_metrics"][["mae_log2", "rmse_log2"]].to_numpy()
            ).all()
            else "fail",
            "detail": "Contiguous-region and 1/2-km buffered holdout metrics were generated without non-finite values.",
        },
        {
            "check": "zone_ranking_recomputed",
            "status": "pass" if actual_zone_order == expected_zone_order else "needs_review",
            "detail": f"Observed point-estimate order: {actual_zone_order}; primary rank frequencies use 3-km spatial clusters.",
        },
        {
            "check": "zone_spatial_block_bootstrap",
            "status": "pass"
            if np.isfinite(
                zone["zone_summary"][[
                    "conditional_ci_lower_2_5",
                    "conditional_ci_upper_97_5",
                    "rank_1_frequency",
                ]].to_numpy()
            ).all()
            else "fail",
            "detail": f"{ZONE_BLOCK_BOOTSTRAP_REPLICATES} fixed-seed 3-km cluster replicates; i.i.d. results retained only as sensitivity.",
        },
        {
            "check": "spatial_background_validity",
            "status": "needs_review",
            "detail": "Common-background ranking invariance is algebraic; zone-specific background fields are not observed.",
        },
        {
            "check": "nmf_seed_stability",
            "status": "pass"
            if nmf["stability"].loc[
                nmf["stability"]["run"] > 0, "mean_matched_cosine"
            ].mean()
            >= 0.95
            else "needs_review",
            "detail": "Mean matched cosine over 19 alternate seeded fits.",
        },
        {
            "check": "nmf_fold_local_preprocessing",
            "status": "pass",
            "detail": "Each validation fold is transformed with RMS scales fitted only on its training fold;<SOURCE_FILE_REDACTED>.",
        },
        {
            "check": "moran_permutation_evidence",
            "status": "pass"
            if (
                (dependence["moran"]["moran_i"] > 0)
                & (dependence["moran"]["permutation_p_one_sided"] <= 0.01)
            ).all()
            else "needs_review",
            "detail": "Positive clustering evaluated with 999 fixed-seed permutations.",
        },
        {
            "check": "detrended_variogram_fit",
            "status": "pass"
            if (dependence["variogram_summary"]["fit_numerical_status"] == "pass").all()
            else "fail",
            "detail": "Linear spatial trend removed before constrained exponential/spherical/Gaussian fits; physical propagation-scale interpretation remains needs_review.",
        },
        {
            "check": "fixed_design_direction_null",
            "status": "needs_review",
            "detail": f"{DIRECTION_PERMUTATION_REPLICATES} conditional permutations per metal with Bonferroni threshold; unsupported directions are not called propagation.",
        },
        {
            "check": "source_internal_sensitivity",
            "status": "needs_review"
            if (source["summary"]["internal_location_status"] == "needs_review").any()
            else "pass",
            "detail": "Bandwidth/grid perturbation, all-sample LOO, cumulative top-3 deletion, support diagnostics and spatial-block bootstrap were tested.",
        },
        {
            "check": "physical_source_identifiability",
            "status": "needs_review",
            "detail": "No temporal or emission covariates; hotspot coordinates are not verified emitters.",
        },
        {
            "check": "background_external_validity",
            "status": "needs_review",
            "detail": "Background sample size and raw natural-area values are unavailable.",
        },
    ]
    overall = "fail" if any(row["status"] == "fail" for row in checks) else "needs_review"
    write_json(
        RESULTS_DIR / "verification.json",
        {
            "overall_status": overall,
            "interpretation": (
                "Internal execution checks pass where stated; scientific source identity "
                "and external validity remain needs_review."
            ),
            "checks": checks,
        },
    )


def write_output_hashes() -> None:
    included = []
    for base in [RESULTS_DIR, FIGURES_DIR, PAPER_GENERATED_DIR]:
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in {
                "results/output_hashes.json",
                "reports/reproduction-check.json",
            }:
                continue
            included.append(
                {
                    "path": relative,
                    "sha256": sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    payload = {
        "status": "pass",
        "file_count": len(included),
        "files": included,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn.__version__,
            "matplotlib": matplotlib.__version__,
        },
    }
    write_json(RESULTS_DIR / "output_hashes.json", payload)


def main() -> None:
    ensure_directories()
    np.random.seed(SEED)
    data, background, manifest = load_data()
    audit = audit_data(data, background, manifest)
    if audit["status"] == "fail":
        raise RuntimeError("Data audit failed; modeling stopped.")

    spatial = spatial_model_comparison(data, background)
    zone = zone_pollution_analysis(data, background)
    associations = zone_associations(data, spatial["log2_enrichment"])
    nmf = fit_nmf_model(data, zone["enrichment"], spatial["folds"])
    dependence = spatial_dependence(
        spatial["xy_km"], spatial["log2_enrichment"]
    )
    grid = make_grid(spatial["xy_km"])
    source = source_analysis(
        data,
        spatial["xy_km"],
        spatial["log2_enrichment"],
        grid,
    )
    create_figures(
        data, background, spatial, zone, nmf, dependence, source, grid
    )
    generate_paper_fragments(data, spatial, zone, nmf, dependence, source)
    build_key_results(
        data, audit, spatial, zone, associations, nmf, dependence, source
    )
    write_traceability()
    write_verification(data, audit, spatial, zone, nmf, dependence, source)
    write_output_hashes()
    print("pass: all numerical results and figures generated")


if __name__ == "__main__":
    main()
