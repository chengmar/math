"""Independent checks for the canonical blind-revision outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


EXPECTED_INPUT_HASHES = {
    "input/problem/<SOURCE_FILE_REDACTED>": "cea35513e302801d4504f3febdf444af783d928c4748bdf91a21724a910e271a",
    "input/attachments/<SOURCE_FILE_REDACTED>": "ddd7b8e70aa727a2858e2476ddfeda7e3042be09d55b304297f990a203071e4f",
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def status(condition: bool) -> str:
    return "pass" if condition else "fail"


def safe_pressure_cap(history: pd.DataFrame, totals: pd.DataFrame) -> float:
    frame = history.merge(
        totals[["year", "wastewater_1e8_t", "river_flow_1e8_m3"]], on="year"
    )
    frame["pressure_fraction"] = frame["wastewater_1e8_t"] / frame["river_flow_1e8_m3"]
    frame = frame.sort_values("pressure_fraction")
    cap = 0.0
    for threshold in frame["pressure_fraction"].unique():
        subset = frame[frame["pressure_fraction"] <= threshold + 1e-15]
        meets = (subset["iv_v_pct"] <= 20.0 + 1e-12).all() and (
            subset["inferior_pct"].abs() <= 1e-12
        ).all()
        if not meets:
            break
        cap = float(threshold)
    return cap


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    results = root / "results"
    figures = root / "figures"
    checks: dict[str, dict] = {}

    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/extract_docx.py",
        "code/build_data.py",
        "code/solve_case.py",
        "code/check_reproducibility.py",
        "code/validate.py",
        "code/run_all.ps1",
        "results/data/<SOURCE_FILE_REDACTED>",
        "results/data/<SOURCE_FILE_REDACTED>",
        "results/data/<SOURCE_FILE_REDACTED>",
        "results/data/<SOURCE_FILE_REDACTED>",
        "results/data/input_audit.json",
        "results/key_results.json",
        "results/verification.json",
        "results/reproducibility_check.json",
        "paper/main.tex",
        "paper/paper.md",
        "paper/generated-values.tex",
        "paper/generated-paper-values.json",
    ]
    missing = [relative for relative in required if not (root / relative).is_file()]
    checks["required_prebuild_outputs"] = {"status": status(not missing), "missing": missing}
    if missing:
        payload = {
            "overall_status": "fail",
            "failed_checks": ["required_prebuild_outputs"],
            "needs_review_checks": [],
            "checks": checks,
        }
        results.mkdir(parents=True, exist_ok=True)
        (results / "validation.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        raise SystemExit(1)

    monthly = pd.read_csv(results / "data" / "<SOURCE_FILE_REDACTED>")
    hydrology = pd.read_csv(results / "data" / "<SOURCE_FILE_REDACTED>")
    annual_quality = pd.read_csv(results / "data" / "<SOURCE_FILE_REDACTED>")
    annual_totals = pd.read_csv(results / "data" / "<SOURCE_FILE_REDACTED>")
    data_shape_ok = (
        len(monthly) == 476
        and monthly[["date", "station"]].duplicated().sum() == 0
        and len(hydrology) == 91
        and hydrology[["date", "station_short"]].duplicated().sum() == 0
        and len(annual_quality) == 90
        and len(annual_totals) == 10
    )
    checks["data_shapes_and_primary_keys"] = {
        "status": status(data_shape_ok),
        "monthly_rows": len(monthly),
        "hydrology_rows": len(hydrology),
        "annual_quality_rows": len(annual_quality),
        "annual_total_rows": len(annual_totals),
    }

    classified = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    overall_distribution = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    q1_ok = (
        len(classified) == 476
        and (classified["reported_grade"] == classified["calculated_grade"]).all()
        and math.isclose(float(overall_distribution["percentage"].sum()), 100.0, abs_tol=1e-10)
    )
    checks["q1_threshold_reproduction"] = {
        "status": status(q1_ok),
        "rows": len(classified),
        "grade_match_count": int((classified["reported_grade"] == classified["calculated_grade"]).sum()),
        "class_share_sum_pct": float(overall_distribution["percentage"].sum()),
    }

    q2_monthly = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    q2_summary = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    q2_bootstrap = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    balance_error = (
        q2_monthly["downstream_load_g_s"]
        - q2_monthly["arriving_upstream_load_g_s"]
        - q2_monthly["signed_increment_t_day"] / 0.0864
    ).abs().max()
    q2_physics_ok = (
        len(q2_monthly) == 13 * 6 * 2 * 4
        and balance_error < 1e-8
        and (q2_monthly["travel_days"] > 0).all()
        and (q2_monthly["travel_days"] < 31).all()
        and q2_monthly["attenuation"].between(0, 1, inclusive="both").all()
    )
    checks["q2_mass_balance_units_and_bounds"] = {
        "status": status(q2_physics_ok),
        "rows": len(q2_monthly),
        "maximum_balance_error_g_s": float(balance_error),
        "minimum_travel_days": float(q2_monthly["travel_days"].min()),
        "maximum_travel_days": float(q2_monthly["travel_days"].max()),
    }

    top = q2_summary[q2_summary["rank"] == 1][
        ["degradation_k_day", "pollutant", "segment_zh"]
    ]
    in_range = top[top["degradation_k_day"].isin([0.1, 0.2, 0.5])]
    in_range_ok = (
        len(in_range) == 6 and (in_range["segment_zh"] == "宜昌—岳阳").all()
    )
    k_zero = top[np.isclose(top["degradation_k_day"], 0.0)]
    k_zero_ok = len(k_zero) == 2 and (k_zero["segment_zh"] == "攀枝花—重庆").all()
    checks["q2_degradation_sensitivity"] = {
        "status": status(in_range_ok and k_zero_ok),
        "problem_range_top_segment": "宜昌—岳阳" if in_range_ok else "needs_review",
        "zero_decay_top_segment": "攀枝花—重庆" if k_zero_ok else "needs_review",
    }
    bootstrap_sums = q2_bootstrap.groupby("pollutant")["top_rank_frequency_pct"].sum()
    checks["q2_bootstrap_probability_closure"] = {
        "status": status(len(q2_bootstrap) == 12 and np.allclose(bootstrap_sums.to_numpy(), 100.0, atol=1e-10)),
        "CODMn_sum_pct": float(bootstrap_sums.get("CODMn", np.nan)),
        "NH3N_sum_pct": float(bootstrap_sums.get("NH3-N", np.nan)),
    }

    composition_cv = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    scalar_cv = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    all_cv = pd.concat([composition_cv, scalar_cv], ignore_index=True)
    cv_years_ok = set(all_cv["validation_year"].unique()) == set(range(2000, 2005))
    no_future_leakage = (
        (all_cv["training_start_year"] == 1995).all()
        and (all_cv["training_end_year"] == all_cv["validation_year"] - 1).all()
        and (
            all_cv["training_count"]
            == all_cv["training_end_year"] - all_cv["training_start_year"] + 1
        ).all()
    )
    checks["q3_rolling_origin_no_future_leakage"] = {
        "status": status(cv_years_ok and no_future_leakage),
        "validation_years": sorted(int(value) for value in all_cv["validation_year"].unique()),
        "minimum_training_count": int(all_cv["training_count"].min()),
        "maximum_training_count": int(all_cv["training_count"].max()),
    }

    model_cv = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    winners = {
        task: group.loc[group["mae"].idxmin(), "model"]
        for task, group in model_cv.groupby("task")
    }
    expected_winners = {
        "water_quality_composition": "linear_simplex",
        "wastewater": "linear_ols",
        "river_flow": "expanding_mean",
    }
    checks["q3_candidate_selection_by_rolling_mae"] = {
        "status": status(winners == expected_winners),
        "winners": winners,
    }

    forecast = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    component_columns = ["good_pct", "iv_v_pct", "inferior_pct"]
    closure_error = float((forecast[component_columns].sum(axis=1) - 100.0).abs().max())
    quantile_order_ok = all(
        (forecast[f"{name}_p10"] <= forecast[name] + 1e-12).all()
        and (forecast[name] <= forecast[f"{name}_p90"] + 1e-12).all()
        for name in component_columns
    )
    forecast_ok = (
        forecast["year"].astype(int).tolist() == list(range(2005, 2015))
        and closure_error < 1e-9
        and (forecast[component_columns] >= -1e-12).all().all()
        and quantile_order_ok
    )
    checks["q3_forecast_constraints"] = {
        "status": status(forecast_ok),
        "rows": len(forecast),
        "maximum_simplex_error_pct_point": closure_error,
    }

    history = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    treatment = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    sensitivity = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    derived_cap_pct = safe_pressure_cap(history, annual_totals) * 100.0
    reported_caps = treatment["safe_pressure_cap_pct"].unique()
    post_pressure = (
        (treatment["forecast_wastewater_1e8_t"] - treatment["treatment_equivalent_1e8_t"])
        / treatment["forecast_flow_1e8_m3"]
        * 100.0
    )
    q4_bounds_ok = (
        treatment["year"].astype(int).tolist() == list(range(2005, 2015))
        and len(reported_caps) == 1
        and math.isclose(float(reported_caps[0]), derived_cap_pct, rel_tol=0, abs_tol=1e-10)
        and (treatment["treatment_equivalent_1e8_t"] >= -1e-12).all()
        and (
            treatment["treatment_equivalent_1e8_t"]
            <= treatment["forecast_wastewater_1e8_t"] + 1e-12
        ).all()
        and (post_pressure <= derived_cap_pct + 1e-10).all()
        and np.allclose(
            treatment["treatment_volume_at_80pct_removal_1e8_t"],
            treatment["treatment_equivalent_1e8_t"] / 0.8,
            atol=1e-10,
        )
        and (
            treatment["treatment_equivalent_1e8_t_p10"]
            <= treatment["treatment_equivalent_1e8_t"] + 1e-12
        ).all()
        and (
            treatment["treatment_equivalent_1e8_t"]
            <= treatment["treatment_equivalent_1e8_t_p90"] + 1e-12
        ).all()
    )
    checks["q4_internal_constraints_and_efficiency_units"] = {
        "status": status(q4_bounds_ok),
        "derived_safe_pressure_cap_pct": derived_cap_pct,
        "maximum_post_treatment_pressure_pct": float(post_pressure.max()),
    }

    monotone = True
    for (_, cap), group in sensitivity.groupby(["year", "pressure_cap_pct"]):
        ordered = group.sort_values("pollutant_removal_efficiency")
        monotone &= bool(np.all(np.diff(ordered["required_treatment_1e8_t"]) <= 1e-10))
    for (_, efficiency), group in sensitivity.groupby(["year", "pollutant_removal_efficiency"]):
        ordered = group.sort_values("pressure_cap_pct")
        monotone &= bool(np.all(np.diff(ordered["required_treatment_1e8_t"]) <= 1e-10))
    checks["q4_sensitivity_monotonicity"] = {
        "status": status(monotone),
        "scenario_rows": len(sensitivity),
    }

    reproducibility = load_json(results / "reproducibility_check.json")
    checks["canonical_pipeline_exact_rerun"] = {
        "status": reproducibility["status"],
        "tracked_file_count": reproducibility["tracked_file_count"],
        "rerun_return_code": reproducibility["rerun_return_code"],
    }

    expected_figures = {
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    }
    actual_figures = {path.name for path in figures.glob("*.png")}
    valid_pngs = {
        path.name
        for path in figures.glob("*.png")
        if path.stat().st_size > 10_000 and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    }
    checks["figure_set_and_signatures"] = {
        "status": status(actual_figures == expected_figures and valid_pngs == expected_figures),
        "png_count": len(actual_figures),
        "valid_png_count": len(valid_pngs),
    }

    generated_values = load_json(root / "paper" / "generated-paper-values.json")
    generated_tex = (root / "paper" / "generated-values.tex").read_text(encoding="utf-8")
    main_tex = (root / "paper" / "main.tex").read_text(encoding="utf-8")
    paper_md = (root / "paper" / "paper.md").read_text(encoding="utf-8")
    macro_ok = all(f"\\newcommand{{\\{name}}}{{{value}}}" in generated_tex for name, value in generated_values.items())
    markdown_keys = ["OverallDrinkablePct", "OverallIVVPct", "OverallInferiorPct", "TreatmentFirst", "TreatmentLast"]
    markdown_ok = all(generated_values[name] in paper_md for name in markdown_keys)
    checks["paper_generated_value_consistency"] = {
        "status": status(macro_ok and markdown_ok and "\\input{generated-values}" in main_tex),
        "generated_macro_count": len(generated_values),
    }

    key_results = load_json(results / "key_results.json")
    with (root / "solution-report.yaml").open(encoding="utf-8") as handle:
        solution_report = yaml.safe_load(handle)
    report_values_ok = (
        solution_report.get("phase") == "blind-revision"
        and math.isclose(solution_report["questions"]["q1"]["primary_results"]["I_to_III_pct"], key_results["q1"]["overall_drinkable_pct"], abs_tol=1e-3)
        and math.isclose(solution_report["questions"]["q4"]["primary_results"]["treatment_2014_1e8_t"], key_results["q4"]["treatment_2014_1e8_t"], abs_tol=1e-3)
    )
    checks["solution_report_result_consistency"] = {"status": status(report_values_ok)}

    input_hashes = {relative: sha256(root / relative) for relative in EXPECTED_INPUT_HASHES}
    checks["original_input_hashes"] = {
        "status": status(input_hashes == EXPECTED_INPUT_HASHES),
        "count": len(input_hashes),
        "hashes": input_hashes,
    }

    checks["forecast_sample_size_external_validity"] = {
        "status": "needs_review",
        "annual_observations": 10,
        "rolling_origin_tests": 5,
        "reason": "预测期与历史期同为10年，残差bootstrap不覆盖制度和监测口径变化。",
    }
    checks["treatment_mapping_external_validity"] = {
        "status": "needs_review",
        "reason": "附件没有给出污水处理量到水质类别河长的因果剂量响应；安全压力包络仅是规划代理。",
    }
    checks["raw_doc_to_docx_repeatability"] = {
        "status": "needs_review",
        "reason": "默认链使用逐文件哈希已固定的DOCX缓存；Word COM二进制转换不声明跨环境字节稳定。",
    }

    failed = [name for name, result in checks.items() if result["status"] == "fail"]
    needs_review = [name for name, result in checks.items() if result["status"] == "needs_review"]
    overall = "fail" if failed else ("needs_review" if needs_review else "pass")
    payload = {
        "schema_version": 2,
        "phase": "blind-revision",
        "overall_status": overall,
        "failed_checks": failed,
        "needs_review_checks": needs_review,
        "checks": checks,
    }
    (results / "validation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"overall_status": overall, "failed": failed, "needs_review": needs_review}, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
