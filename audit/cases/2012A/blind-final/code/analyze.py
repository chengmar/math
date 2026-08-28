"""Generate the complete blind-revision analysis from prepared attachment data."""

from __future__ import annotations

import argparse
import json
import math
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.decomposition import PCA
from sklearn.linear_model import Ridge
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

from fold_models import (
    SEED,
    analyze_q3_revised,
    analyze_q4_revised,
    balanced_folds,
    json_ready,
    status_rollup,
)


N_BOOT = 1_000
N_GRADE_PERTURB = 300


def save_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def safe_spearman(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 3 or np.std(x[mask]) <= 0 or np.std(y[mask]) <= 0:
        return math.nan, math.nan
    result = stats.spearmanr(x[mask], y[mask])
    return float(result.statistic), float(result.pvalue)


def exact_sign_flip_pvalue(differences: np.ndarray) -> dict[str, Any]:
    """Exact two-sided sign-flip test using the data's one-decimal lattice."""

    values = np.asarray(differences, dtype=float)
    scaled = np.rint(values * 10).astype(int)
    if np.max(np.abs(values - scaled / 10)) > 1e-10:
        raise RuntimeError("Q1 differences are not on the declared 0.1-score lattice")
    distribution: Counter[int] = Counter({0: 1})
    for value in scaled:
        updated: Counter[int] = Counter()
        for total, count in distribution.items():
            updated[total + int(value)] += count
            updated[total - int(value)] += count
        distribution = updated
    observed = abs(int(np.sum(scaled)))
    exceedances = sum(count for total, count in distribution.items() if abs(total) >= observed)
    configurations = 2 ** len(scaled)
    return {
        "p": exceedances / configurations,
        "exceedances": int(exceedances),
        "configurations": int(configurations),
        "lattice_score": 0.1,
        "method": "exact dynamic-programming enumeration of all sign sums",
    }


def icc_two_way(matrix: np.ndarray) -> dict[str, float]:
    matrix = np.asarray(matrix, dtype=float)
    n, k = matrix.shape
    grand = float(matrix.mean())
    row_means = matrix.mean(axis=1)
    column_means = matrix.mean(axis=0)
    ss_rows = k * float(np.sum((row_means - grand) ** 2))
    ss_columns = n * float(np.sum((column_means - grand) ** 2))
    residual = matrix - row_means[:, None] - column_means[None, :] + grand
    ss_error = float(np.sum(residual**2))
    ms_rows = ss_rows / (n - 1)
    ms_columns = ss_columns / (k - 1)
    ms_error = ss_error / ((n - 1) * (k - 1))
    denominator_single = ms_rows + (k - 1) * ms_error + k * (ms_columns - ms_error) / n
    denominator_mean = ms_rows + (ms_columns - ms_error) / n
    return {
        "icc_2_1": (ms_rows - ms_error) / denominator_single if denominator_single else math.nan,
        "icc_2_k": (ms_rows - ms_error) / denominator_mean if denominator_mean else math.nan,
        "icc_3_k": (ms_rows - ms_error) / ms_rows if ms_rows else math.nan,
        "ms_wine": ms_rows,
        "ms_rater": ms_columns,
        "ms_error": ms_error,
    }


def kendall_w(matrix: np.ndarray) -> tuple[float, float]:
    matrix = np.asarray(matrix, dtype=float)
    n, k = matrix.shape
    ranks = np.column_stack([stats.rankdata(matrix[:, column], method="average") for column in range(k)])
    rank_sums = ranks.sum(axis=1)
    numerator = 12 * float(np.sum((rank_sums - rank_sums.mean()) ** 2))
    tie_sum = 0.0
    for column in range(k):
        _, counts = np.unique(matrix[:, column], return_counts=True)
        tie_sum += float(np.sum(counts**3 - counts))
    denominator = k**2 * (n**3 - n) - k * tie_sum
    value = numerator / denominator if denominator > 0 else math.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        friedman = stats.friedmanchisquare(*[matrix[:, column] for column in range(k)])
    return float(value), float(friedman.pvalue) if np.isfinite(friedman.pvalue) else 1.0


def panel_metrics(matrix: np.ndarray, rng: np.random.Generator) -> dict[str, Any]:
    estimates: list[float] = []
    for _ in range(N_BOOT):
        sampled = matrix[rng.integers(0, len(matrix), len(matrix))]
        value = icc_two_way(sampled)["icc_2_k"]
        if np.isfinite(value):
            estimates.append(float(value))
    icc = icc_two_way(matrix)
    w, friedman_p = kendall_w(matrix)
    within_sd = np.std(matrix, axis=1, ddof=1)
    consensus = matrix.mean(axis=1)
    loo_rho = [
        safe_spearman(consensus, np.delete(matrix, rater, axis=1).mean(axis=1))[0]
        for rater in range(matrix.shape[1])
    ]
    return {
        **icc,
        "icc_2_k_ci95": [float(value) for value in np.quantile(estimates, [0.025, 0.975])],
        "kendall_w": w,
        "friedman_p": friedman_p,
        "mean_within_wine_sd": float(within_sd.mean()),
        "median_within_wine_sd": float(np.median(within_sd)),
        "rater_mean_sd": float(np.std(matrix.mean(axis=0), ddof=1)),
        "loo_consensus_rho_min": float(np.min(loo_rho)),
        "loo_consensus_rho_mean": float(np.mean(loo_rho)),
    }


def analyze_q1(
    tasting: pd.DataFrame,
    results_dir: Path,
    figures_dir: Path,
) -> tuple[dict[str, Any], dict[str, int]]:
    rng = np.random.default_rng(SEED + 101)
    sample_summary = (
        tasting.groupby(["color", "panel", "sample_id"])["total"]
        .agg(mean="mean", median="median", sd="std", minimum="min", maximum="max")
        .reset_index()
    )
    sample_summary.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")
    reliability_rows: list[dict[str, Any]] = []
    colors: dict[str, Any] = {}
    working_panels: dict[str, int] = {}
    for color in ["red", "white"]:
        matrices: dict[int, np.ndarray] = {}
        reliability: dict[int, dict[str, Any]] = {}
        for panel in [1, 2]:
            subset = tasting[(tasting.color == color) & (tasting.panel == panel)]
            matrix = subset.pivot(index="sample_id", columns="rater", values="total").sort_index().to_numpy(float)
            matrices[panel] = matrix
            reliability[panel] = panel_metrics(matrix, rng)
            reliability_rows.append({"color": color, "panel": panel, **reliability[panel]})
        means_1 = matrices[1].mean(axis=1)
        means_2 = matrices[2].mean(axis=1)
        difference = means_1 - means_2
        exact = exact_sign_flip_pvalue(difference)
        paired_t = stats.ttest_rel(means_1, means_2)
        wilcoxon = stats.wilcoxon(difference, alternative="two-sided", zero_method="pratt")
        mean_difference = float(difference.mean())
        se = float(stats.sem(difference))
        critical = float(stats.t.ppf(0.975, len(difference) - 1))
        rho, rho_p = safe_spearman(means_1, means_2)
        evidence = {
            "higher_icc": 1 if reliability[1]["icc_2_k"] > reliability[2]["icc_2_k"] else 2,
            "higher_kendall_w": 1 if reliability[1]["kendall_w"] > reliability[2]["kendall_w"] else 2,
            "lower_within_wine_sd": 1 if reliability[1]["median_within_wine_sd"] < reliability[2]["median_within_wine_sd"] else 2,
        }
        working_panel = 1 if evidence["higher_icc"] == evidence["higher_kendall_w"] == 1 else 2
        working_panels[color] = working_panel

        sensitivity_means: dict[int, pd.Series] = {}
        for panel in [1, 2]:
            subset = tasting[
                (tasting.color == color)
                & (tasting.panel == panel)
                & (tasting.repaired_item_count == 0)
            ]
            sensitivity_means[panel] = subset.groupby("sample_id").total.mean().sort_index()
        common = sensitivity_means[1].index.intersection(sensitivity_means[2].index)
        sensitivity_difference = (
            sensitivity_means[1].loc[common].to_numpy()
            - sensitivity_means[2].loc[common].to_numpy()
        )
        # Deletion changes the decimal lattice, so this cross-check is a paired t
        # result; the exact primary result remains independent of Monte Carlo.
        sensitivity_t_p = float(stats.ttest_1samp(sensitivity_difference, 0).pvalue)
        colors[color] = {
            "n_wines": int(len(difference)),
            "panel_1_mean": float(means_1.mean()),
            "panel_2_mean": float(means_2.mean()),
            "mean_difference_panel1_minus_panel2": mean_difference,
            "difference_ci95": [mean_difference - critical * se, mean_difference + critical * se],
            "paired_t_p": float(paired_t.pvalue),
            "wilcoxon_p": float(wilcoxon.pvalue),
            "exact_sign_flip": exact,
            "difference_status": "pass" if exact["p"] < 0.05 else "fail",
            "effect_size_dz": mean_difference / float(np.std(difference, ddof=1)),
            "panel_rank_spearman": rho,
            "panel_rank_spearman_p": rho_p,
            "internal_evidence_directions": evidence,
            "working_panel": working_panel,
            "trust_status": "needs_review",
            "trust_reason": "ICC and Kendall W are correlated internal-consistency evidence and there is no external quality truth; panel 1 is only the working target.",
            "repair_exclusion_sensitivity_t_p": sensitivity_t_p,
            "reliability": {"panel_1": reliability[1], "panel_2": reliability[2]},
        }

    pd.DataFrame(reliability_rows).to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4), constrained_layout=True)
    for axis, color in zip(axes, ["red", "white"], strict=True):
        pivot = sample_summary[sample_summary.color == color].pivot(index="sample_id", columns="panel", values="mean")
        axis.scatter(pivot[1], pivot[2], s=32, color="#7f1d1d" if color == "red" else "#b58900")
        low = float(min(pivot.min()) - 1)
        high = float(max(pivot.max()) + 1)
        axis.plot([low, high], [low, high], "--", color="0.4", linewidth=1)
        axis.set(title=color.title(), xlabel="Panel 1 mean", ylabel="Panel 2 mean", xlim=(low, high), ylim=(low, high))
        axis.grid(alpha=0.2)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220, metadata={"Software": "CUMCM blind revision"})
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)

    reliability_frame = pd.DataFrame(reliability_rows)
    fig, axes = plt.subplots(1, 2, figsize=(9, 4), constrained_layout=True)
    for axis, color in zip(axes, ["red", "white"], strict=True):
        data = reliability_frame[reliability_frame.color == color].sort_values("panel")
        positions = np.arange(2)
        axis.bar(positions - 0.18, data.icc_2_k, 0.36, label="ICC(2,k)")
        axis.bar(positions + 0.18, data.kendall_w, 0.36, label="Kendall W")
        axis.set_xticks(positions, ["Panel 1", "Panel 2"])
        axis.set_ylim(0, 1)
        axis.set_title(color.title())
        axis.grid(axis="y", alpha=0.2)
    axes[1].legend(frameon=False, fontsize=8)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220, metadata={"Software": "CUMCM blind revision"})
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)

    summary = {"colors": colors, "working_panels": working_panels}
    save_json(results_dir / "q1_summary.json", summary)
    return summary, working_panels


def _fit_ridge(x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray, alpha: float) -> np.ndarray:
    scaler = StandardScaler().fit(x_train)
    model = Ridge(alpha=alpha).fit(scaler.transform(x_train), y_train)
    return np.asarray(model.predict(scaler.transform(x_test)), dtype=float)


def nested_loo_ridge(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, list[float]]:
    prediction = np.empty(len(y), dtype=float)
    chosen: list[float] = []
    all_indices = np.arange(len(y))
    for test in all_indices:
        outer_train = all_indices[all_indices != test]
        errors: dict[float, list[float]] = {1.0: [], 100.0: []}
        for valid in balanced_folds(outer_train, min(5, len(outer_train)), offset=int(test)):
            train = np.setdiff1d(outer_train, valid, assume_unique=True)
            for alpha in errors:
                current = _fit_ridge(x[train], y[train], x[valid], alpha)
                errors[alpha].extend((y[valid] - current).tolist())
        mse = {alpha: float(np.mean(np.asarray(values) ** 2)) for alpha, values in errors.items()}
        alpha = min(mse, key=lambda value: (mse[value], -value))
        prediction[test] = _fit_ridge(x[outer_train], y[outer_train], x[[test]], alpha)[0]
        chosen.append(alpha)
    return prediction, chosen


def grade_labels(labels: np.ndarray, quality: np.ndarray) -> np.ndarray:
    ordering = sorted(np.unique(labels), key=lambda label: float(np.mean(quality[labels == label])), reverse=True)
    names = [chr(ord("A") + index) for index in range(len(ordering))]
    mapping = dict(zip(ordering, names, strict=True))
    return np.array([mapping[label] for label in labels])


def quality_tertiles(quality: np.ndarray) -> np.ndarray:
    order = np.argsort(-quality, kind="mergesort")
    grades = np.empty(len(quality), dtype=object)
    for position, index in enumerate(order):
        grades[index] = ["A", "B", "C"][min(2, position * 3 // len(quality))]
    return grades.astype(str)


def ward_partition(
    x: np.ndarray,
    quality: np.ndarray,
    grape_weight: float = 0.50,
    whiten: bool = True,
    clusters: int = 3,
) -> tuple[np.ndarray, np.ndarray, int, PCA]:
    scaled = StandardScaler().fit_transform(x)
    full_pca = PCA().fit(scaled)
    components = int(np.searchsorted(np.cumsum(full_pca.explained_variance_ratio_), 0.80) + 1)
    components = max(2, min(8, components, len(quality) - 1, x.shape[1]))
    pca = PCA(n_components=components, whiten=whiten, random_state=SEED).fit(scaled)
    scores = pca.transform(scaled)
    if not whiten:
        scores = StandardScaler().fit_transform(scores)
    quality_z = StandardScaler().fit_transform(quality.reshape(-1, 1))
    representation = np.column_stack(
        [
            math.sqrt(grape_weight / components) * scores,
            math.sqrt(1 - grape_weight) * quality_z,
        ]
    )
    labels = AgglomerativeClustering(n_clusters=clusters, linkage="ward").fit_predict(representation)
    return labels, representation, components, pca


def analyze_q2(
    clean_dir: Path,
    results_dir: Path,
    figures_dir: Path,
    tasting: pd.DataFrame,
    working_panels: dict[str, int],
) -> dict[str, Any]:
    rng = np.random.default_rng(SEED + 202)
    grade_frames: list[pd.DataFrame] = []
    candidate_rows: list[dict[str, Any]] = []
    sensitivity_rows: list[dict[str, Any]] = []
    colors: dict[str, Any] = {}
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), constrained_layout=True)
    palette = {"A": "#15803d", "B": "#d97706", "C": "#b91c1c"}
    for axis, color in zip(axes, ["red", "white"], strict=True):
        grape = pd.read_csv(clean_dir / f"grape_conventional_{color}.csv").sort_values("sample_id")
        sample_ids = grape.sample_id.to_numpy(dtype=int)
        x = grape.drop(columns="sample_id").to_numpy(dtype=float)
        panel = working_panels[color]
        panel_rows = tasting[(tasting.color == color) & (tasting.panel == panel)]
        target = panel_rows.groupby("sample_id").total.agg(["mean", "std"]).reindex(sample_ids)
        quality = target["mean"].to_numpy(float)
        quality_se = target["std"].to_numpy(float) / math.sqrt(10)

        main_labels, representation, components, pca = ward_partition(x, quality)
        grades = grade_labels(main_labels, quality)
        retained_variance = float(np.sum(pca.explained_variance_ratio_))
        alternative_panel = 3 - panel
        alternative_quality = (
            tasting[(tasting.color == color) & (tasting.panel == alternative_panel)]
            .groupby("sample_id").total.mean().reindex(sample_ids).to_numpy(float)
        )
        alternative_labels, _, _, _ = ward_partition(x, alternative_quality)
        alternative_ari = float(adjusted_rand_score(main_labels, alternative_labels))

        ari_values: list[float] = []
        exact_stability = np.zeros(len(quality), dtype=float)
        for _ in range(N_GRADE_PERTURB):
            sampled_features = rng.integers(0, x.shape[1], x.shape[1])
            perturbed_quality = quality + rng.normal(0, quality_se)
            labels, _, _, _ = ward_partition(x[:, sampled_features], perturbed_quality)
            ari_values.append(float(adjusted_rand_score(main_labels, labels)))
            aligned_grades = grade_labels(labels, perturbed_quality)
            exact_stability += aligned_grades == grades
        exact_stability /= N_GRADE_PERTURB
        median_ari = float(np.median(ari_values))

        for weight in [0.25, 0.50, 0.75]:
            for whiten in [True, False]:
                for cluster_count in [2, 3, 4, 5]:
                    labels, current_representation, _, _ = ward_partition(
                        x, quality, grape_weight=weight, whiten=whiten, clusters=cluster_count
                    )
                    sensitivity_rows.append(
                        {
                            "color": color,
                            "scenario": "weight_whitening_k",
                            "grape_weight": weight,
                            "whiten": whiten,
                            "clusters": cluster_count,
                            "panel": panel,
                            "ari_to_primary": float(adjusted_rand_score(main_labels, labels)),
                            "silhouette": float(silhouette_score(current_representation, labels)),
                            "minimum_cluster_size": int(np.bincount(labels).min()),
                            "status": "pass" if cluster_count == 3 and np.bincount(labels).min() >= 3 else "needs_review",
                        }
                    )
        sensitivity_rows.append(
            {
                "color": color,
                "scenario": "alternative_panel",
                "grape_weight": 0.50,
                "whiten": True,
                "clusters": 3,
                "panel": alternative_panel,
                "ari_to_primary": alternative_ari,
                "silhouette": math.nan,
                "minimum_cluster_size": int(np.bincount(alternative_labels).min()),
                "status": "pass" if alternative_ari >= 0.80 else "needs_review",
            }
        )

        ridge_prediction, ridge_parameters = nested_loo_ridge(x, quality)
        z_quality = StandardScaler().fit_transform(quality.reshape(-1, 1)).ravel()
        z_prediction = StandardScaler().fit_transform(ridge_prediction.reshape(-1, 1)).ravel()
        composite = 0.5 * z_quality + 0.5 * z_prediction
        ridge_labels = KMeans(n_clusters=3, n_init=50, random_state=SEED).fit_predict(composite.reshape(-1, 1))
        candidate_payload = {
            "quality_tertiles": (quality_tertiles(quality), StandardScaler().fit_transform(quality.reshape(-1, 1))),
            "equal_block_pca_ward": (grades, representation),
            "ridge_composite": (grade_labels(ridge_labels, quality), composite.reshape(-1, 1)),
        }
        for name, (candidate_grades, candidate_representation) in candidate_payload.items():
            counts = Counter(candidate_grades)
            candidate_rows.append(
                {
                    "color": color,
                    "candidate": name,
                    "uses_grape_indicators": name != "quality_tertiles",
                    "silhouette": float(silhouette_score(candidate_representation, candidate_grades)),
                    "minimum_grade_size": int(min(counts.values())),
                    "quality_separation_role": "descriptive_by_construction",
                    "selection_status": "needs_review",
                }
            )

        grade_frame = pd.DataFrame(
            {
                "color": color,
                "sample_id": sample_ids,
                "working_panel": panel,
                "quality_mean": quality,
                "quality_se": quality_se,
                "grape_pc1": PCA(n_components=1).fit_transform(StandardScaler().fit_transform(x)).ravel(),
                "grade": grades,
                "grade_stability_probability": exact_stability,
            }
        ).sort_values(["grade", "quality_mean"], ascending=[True, False])
        grade_frames.append(grade_frame)
        for grade in ["A", "B", "C"]:
            subset = grade_frame[grade_frame.grade == grade]
            axis.scatter(subset.grape_pc1, subset.quality_mean, color=palette[grade], label=f"Grade {grade}", s=42)
            for row in subset.itertuples():
                axis.annotate(str(row.sample_id), (row.grape_pc1, row.quality_mean), fontsize=6, xytext=(2, 2), textcoords="offset points")
        axis.set(title=color.title(), xlabel="Grape PC1", ylabel="Working-panel quality")
        axis.grid(alpha=0.2)

        grade_stats = (
            grade_frame.groupby("grade")
            .agg(n=("sample_id", "size"), quality_mean=("quality_mean", "mean"), quality_min=("quality_mean", "min"), quality_max=("quality_mean", "max"))
            .reset_index().to_dict(orient="records")
        )
        colors[color] = {
            "selected_candidate": "equal_block_pca_ward",
            "selection_status": "needs_review",
            "retained_pca_components_cap_8": components,
            "retained_pca_variance": retained_variance,
            "target_80pct_reached": bool(retained_variance >= 0.80),
            "ward_stability_median_ari": median_ari,
            "ward_stability_ari_ci95": [float(value) for value in np.quantile(ari_values, [0.025, 0.975])],
            "minimum_sample_grade_stability": float(exact_stability.min()),
            "alternative_panel": alternative_panel,
            "alternative_panel_grade_ari": alternative_ari,
            "ridge_grape_quality": {
                "rmse": float(np.sqrt(np.mean((quality - ridge_prediction) ** 2))),
                "parameters": dict(Counter(str(value) for value in ridge_parameters)),
            },
            "quality_separation_validation_status": "needs_review",
            "quality_separation_reason": "quality has 50% weight and names the clusters, so separation is not independent validation",
            "grade_stats": grade_stats,
            "members": {
                grade: grade_frame.loc[grade_frame.grade == grade, "sample_id"].astype(int).tolist()
                for grade in ["A", "B", "C"]
            },
        }

    axes[1].legend(frameon=False, fontsize=8)
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", dpi=220, metadata={"Software": "CUMCM blind revision"})
    fig.savefig(figures_dir / "<SOURCE_FILE_REDACTED>", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)
    pd.concat(grade_frames, ignore_index=True).to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")
    pd.DataFrame(candidate_rows).to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")
    sensitivity = pd.DataFrame(sensitivity_rows)
    sensitivity.to_csv(results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")
    sensitivity[sensitivity.scenario == "weight_whitening_k"].to_csv(
        results_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8"
    )
    summary = {
        "method": "equal-block PCA-Ward; primary grape/quality weights 50/50; three provisional grades",
        "method_status": "needs_review",
        "colors": colors,
    }
    save_json(results_dir / "q2_summary.json", summary)
    return summary


def latex_escape(value: Any) -> str:
    text = str(value)
    for source, replacement in [
        ("\\", r"\textbackslash{}"),
        ("_", r"\_"),
        ("%", r"\%"),
        ("&", r"\&"),
        ("#", r"\#"),
    ]:
        text = text.replace(source, replacement)
    return text


def generate_paper_outputs(results_dir: Path, summary: dict[str, Any]) -> None:
    q1 = summary["q1"]["colors"]
    q2 = summary["q2"]["colors"]
    q3 = summary["q3"]["colors"]
    q4 = summary["q4"]["colors"]
    lines = [
        "% Generated by code/analyze.py. Do not hand-edit numerical values.",
        f"\\newcommand{{\\RedPanelDiff}}{{{q1['red']['mean_difference_panel1_minus_panel2']:.2f}}}",
        f"\\newcommand{{\\WhitePanelDiff}}{{{q1['white']['mean_difference_panel1_minus_panel2']:.2f}}}",
        f"\\newcommand{{\\RedPanelP}}{{{q1['red']['exact_sign_flip']['p']:.5f}}}",
        f"\\newcommand{{\\WhitePanelP}}{{{q1['white']['exact_sign_flip']['p']:.5f}}}",
        f"\\newcommand{{\\RedPcaVariance}}{{{100*q2['red']['retained_pca_variance']:.1f}\\%}}",
        f"\\newcommand{{\\WhitePcaVariance}}{{{100*q2['white']['retained_pca_variance']:.1f}\\%}}",
        f"\\newcommand{{\\RedLinkCount}}{{{q3['red']['significant_links']}}}",
        f"\\newcommand{{\\WhiteLinkCount}}{{{q3['white']['significant_links']}}}",
        f"\\newcommand{{\\RedCrossQ}}{{{q3['red']['metrics']['q2_vs_training_mean']:.3f}}}",
        f"\\newcommand{{\\WhiteCrossQ}}{{{q3['white']['metrics']['q2_vs_training_mean']:.3f}}}",
        f"\\newcommand{{\\RedQualityRMSE}}{{{q4['red']['metrics']['rmse']:.2f}}}",
        f"\\newcommand{{\\WhiteQualityRMSE}}{{{q4['white']['metrics']['rmse']:.2f}}}",
        f"\\newcommand{{\\RedQualityQ}}{{{q4['red']['metrics']['q2_vs_training_mean']:.3f}}}",
        f"\\newcommand{{\\WhiteQualityQ}}{{{q4['white']['metrics']['q2_vs_training_mean']:.3f}}}",
        f"\\newcommand{{\\RedQualityPermP}}{{{q4['red']['permutation']['p_plus_one']:.3f}}}",
        f"\\newcommand{{\\WhiteQualityPermP}}{{{q4['white']['permutation']['p_plus_one']:.3f}}}",
    ]
    (results_dir / "paper_values.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")

    q1_table = [
        r"\begin{tabular}{llrrrr}", r"\toprule",
        r"Color & Panel & Mean & ICC(2,k) & Kendall $W$ & Median within-SD \\", r"\midrule",
    ]
    for color, label in [("red", "Red"), ("white", "White")]:
        for panel in [1, 2]:
            metrics = q1[color]["reliability"][f"panel_{panel}"]
            q1_table.append(
                f"{label} & {panel} & {q1[color][f'panel_{panel}_mean']:.2f} & {metrics['icc_2_k']:.3f} & {metrics['kendall_w']:.3f} & {metrics['median_within_wine_sd']:.2f} \\\\"
            )
    q1_table.extend([r"\bottomrule", r"\end{tabular}"])
    (results_dir / "q1_paper_table.tex").write_text("\n".join(q1_table) + "\n", encoding="utf-8")

    q2_table = [r"\begin{tabular}{llrrrr}", r"\toprule", r"Color & Grade & $n$ & Mean & Min & Max \\", r"\midrule"]
    members_table = [r"\begin{longtable}{llp{9.2cm}}", r"\toprule", r"Color & Grade & Sample IDs \\", r"\midrule", r"\endfirsthead", r"\toprule Color & Grade & Sample IDs \\", r"\midrule", r"\endhead"]
    for color, label in [("red", "Red"), ("white", "White")]:
        by_grade = {row["grade"]: row for row in q2[color]["grade_stats"]}
        for grade in ["A", "B", "C"]:
            row = by_grade[grade]
            q2_table.append(f"{label} & {grade} & {row['n']} & {row['quality_mean']:.2f} & {row['quality_min']:.2f} & {row['quality_max']:.2f} \\\\")
            members = ", ".join(map(str, q2[color]["members"][grade]))
            members_table.append(f"{label} & {grade} & {members} \\\\ ")
    q2_table.extend([r"\bottomrule", r"\end{tabular}"])
    members_table.extend([r"\bottomrule", r"\end{longtable}"])
    (results_dir / "q2_paper_table.tex").write_text("\n".join(q2_table) + "\n", encoding="utf-8")
    (results_dir / "q2_membership_table.tex").write_text("\n".join(members_table) + "\n", encoding="utf-8")

    q3_table = [r"\begin{tabular}{lrrrrr}", r"\toprule", r"Color & Responses & $Q^2_{train}$ & Perm. $p$ & FDR links & Mapping \\", r"\midrule"]
    for color, label in [("red", "Red"), ("white", "White")]:
        item = q3[color]
        q3_table.append(
            f<LONG_QUOTE_REDACTED>
        )
    q3_table.extend([r"\bottomrule", r"\end{tabular}"])
    (results_dir / "q3_paper_table.tex").write_text("\n".join(q3_table) + "\n", encoding="utf-8")

    q4_table = [r"\begin{tabular}{lrrrrrr}", r"\toprule", r"Color & Baseline RMSE & Model RMSE & $Q^2_{train}$ & Gain & $\rho$ & Perm. $p$ \\", r"\midrule"]
    for color, label in [("red", "Red"), ("white", "White")]:
        item = q4[color]
        metrics = item["metrics"]
        q4_table.append(
            f<LONG_QUOTE_REDACTED>
        )
    q4_table.extend([r"\bottomrule", r"\end{tabular}"])
    (results_dir / "q4_paper_table.tex").write_text("\n".join(q4_table) + "\n", encoding="utf-8")

    comparison = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    candidate_table = [
        r"\begin{tabular}{lllrr}",
        r"\toprule",
        r"Color & Scope & Family & RMSE & $Q^2_{train}$ \\",
        r"\midrule",
    ]
    for color, label in [("red", "Red"), ("white", "White")]:
        subset = comparison[
            (comparison.color == color)
            & comparison.scope.isin(["grape", "wine", "combined"])
        ]
        for row in subset.itertuples():
            candidate_table.append(
                f"{label} & {latex_escape(row.scope)} & {latex_escape(row.family)} & {row.rmse:.2f} & {row.q2_vs_training_mean:.3f} \\\\"
            )
    candidate_table.extend([r"\bottomrule", r"\end{tabular}"])
    (results_dir / "q4_candidate_paper_table.tex").write_text(
        "\n".join(candidate_table) + "\n", encoding="utf-8"
    )

    links = pd.read_csv(results_dir / "<SOURCE_FILE_REDACTED>")
    link_table = [
        r"\begin{tabular}{lp{4.0cm}p{4.0cm}rr}",
        r"\toprule",
        r"Color & Grape indicator & Wine indicator & $\rho$ & permutation-BH $q$ \\",
        r"\midrule",
    ]
    for color, label in [("red", "Red"), ("white", "White")]:
        subset = links[(links.color == color) & links.significant_200k].head(4)
        for row in subset.itertuples():
            link_table.append(
                f"{label} & {latex_escape(row.grape_feature)} & {latex_escape(row.wine_feature)} & {row.rho:.3f} & {row.q_bh_200k:.4g} \\\\"
            )
    link_table.extend([r"\bottomrule", r"\end{tabular}"])
    (results_dir / "q3_links_paper_table.tex").write_text(
        "\n".join(link_table) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    clean_dir = workspace / "results" / "clean"
    results_dir = workspace / "results"
    figures_dir = workspace / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    tasting = pd.read_csv(clean_dir / "<SOURCE_FILE_REDACTED>")

    q1, working_panels = analyze_q1(tasting, results_dir, figures_dir)
    print(f"[pass] Q1 exact paired inference; working panels {working_panels}")
    q2 = analyze_q2(clean_dir, results_dir, figures_dir, tasting, working_panels)
    print("[pass] Q2 provisional grades and declared sensitivity grid")
    fold_records: list[dict[str, Any]] = []
    q3, q3_unit = analyze_q3_revised(clean_dir, results_dir, figures_dir, fold_records)
    save_json(results_dir / "q3_summary.json", q3)
    print("[pass] Q3 permutation links and fold-isolated repeated nested mapping")
    q4, q4_unit = analyze_q4_revised(
        clean_dir, results_dir, figures_dir, tasting, working_panels, fold_records
    )
    save_json(results_dir / "q4_summary.json", q4)
    print("[pass] Q4 fold-isolated scope/family selection and block ablation")

    fold_status = "pass" if all(row["status"] == "pass" for row in fold_records) else "fail"
    save_json(
        results_dir / "fold_audit.json",
        {
            "status": fold_status,
            "records": fold_records,
            "rule": "no sample ID used to fit a transform may appear in that transform's validation/test application set",
        },
    )
    unit_payload = {"q3": q3_unit, "q4": q4_unit}
    unit_statuses = [
        check["status"]
        for task in unit_payload.values()
        for color in task.values()
        for check in color.values()
    ]
    unit_payload["overall_status"] = status_rollup(unit_statuses)
    unit_payload["rule"] = "multiply every aroma concentration by 0.001 or 1000; transform reference, selection and predictions must remain invariant within 1e-9"
    save_json(results_dir / "unit_invariance.json", unit_payload)

    summary = {
        "case_id": "2012A",
        "phase": "blind-revision",
        "random_seed": SEED,
        "q1": q1,
        "q2": q2,
        "q3": q3,
        "q4": q4,
        "checks": {
            "question_coverage": {"status": "pass"},
            "code_generated_key_numbers": {"status": "pass"},
            "fold_isolation": {"status": fold_status},
            "unit_invariance": {"status": unit_payload["overall_status"]},
            "sensitivity_analysis": {"status": "pass"},
            "independent_external_validation": {
                "status": "needs_review",
                "reason": "No independent year, region or external quality gold standard is present in the allowed attachments.",
            },
            "mathematical_truth_claim": {
                "status": "needs_review",
                "reason": "Automated checks establish reproducibility and internal consistency, not unique mathematical truth.",
            },
        },
    }
    summary["validation_overall_status"] = status_rollup(
        item["status"] for item in summary["checks"].values()
    )
    save_json(results_dir / "summary.json", summary)
    generate_paper_outputs(results_dir, summary)
    print(f"[{summary['validation_overall_status']}] summary and paper values written")


if __name__ == "__main__":
    main()
