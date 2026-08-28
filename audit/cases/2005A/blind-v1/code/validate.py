from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    checks: dict[str, dict] = {}

    required = [
        ROOT / "problem-analysis.md",
        ROOT / "data-audit.md",
        ROOT / "assumptions.yaml",
        ROOT / "variables.yaml",
        ROOT / "model-selection.md",
        ROOT / "solution-report.yaml",
        ROOT / "reproducibility.yaml",
        ROOT / "paper" / "main.tex",
        ROOT / "paper" / "paper.md",
        RESULTS / "summary.json",
        RESULTS / "data_audit.json",
        RESULTS / "reproducibility_check.json",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.is_file()]
    checks["required_outputs"] = {"status": "pass" if not missing else "fail", "missing": missing}

    monthly = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    expected_monthly = 17 * 28
    grade_match = bool((monthly["official_grade"] == monthly["computed_grade"]).all())
    checks["monthly_shape_and_grade"] = {
        "status": "pass" if len(monthly) == expected_monthly and grade_match else "fail",
        "rows": len(monthly),
        "expected_rows": expected_monthly,
        "grade_match_count": int((monthly["official_grade"] == monthly["computed_grade"]).sum()),
    }

    annual = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    normalized_columns = [f"{name}_pct" for name in ["I", "II", "III", "IV", "V", "bad"]]
    maximum_simplex_error = float((annual[normalized_columns].sum(axis=1) - 100.0).abs().max())
    checks["annual_simplex"] = {
        "status": "pass" if maximum_simplex_error < 1e-9 else "fail",
        "maximum_sum_error_pp": maximum_simplex_error,
    }

    source_monthly = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    mass_error = (
        source_monthly["attenuated_upstream_load_kg_day"]
        + source_monthly["net_increment_kg_day"]
        - source_monthly["downstream_load_kg_day"]
    ).abs().max()
    checks["source_mass_balance_identity"] = {
        "status": "pass" if mass_error < 1e-6 else "fail",
        "maximum_abs_error_kg_day": float(mass_error),
    }

    sensitivity = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    top_counts = sensitivity[sensitivity["rank"] == 1].groupby("pollutant")["segment_display"].nunique()
    top_stable = bool((top_counts == 1).all())
    checks["source_top_rank_k_0_1_to_0_5"] = {
        "status": "pass" if top_stable else "needs_review",
        "distinct_top_segments": top_counts.to_dict(),
    }

    forecast = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    forecast_simplex_error = float((forecast[normalized_columns].sum(axis=1) - 100.0).abs().max())
    forecast_nonnegative = bool((forecast[normalized_columns] >= -1e-12).all().all())
    checks["forecast_constraints"] = {
        "status": "pass" if forecast_simplex_error < 1e-9 and forecast_nonnegative else "fail",
        "maximum_sum_error_pp": forecast_simplex_error,
        "nonnegative": forecast_nonnegative,
    }

    treatment = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    target_ok = bool(
        (treatment["post_control_iv_v_pct"] <= 20.0 + 1e-10).all()
        and (treatment["post_control_bad_v_pct"].abs() < 1e-10).all()
    )
    volume_ok = bool(
        (treatment["required_treatment_100m_t"] >= -1e-10).all()
        and (
            treatment["required_treatment_100m_t"]
            <= treatment["predicted_wastewater_100m_t"] + 1e-10
        ).all()
    )
    checks["treatment_constraints_under_stated_mapping"] = {
        "status": "pass" if target_ok and volume_ok else "fail",
        "iv_v_target_met": target_ok,
        "treatment_within_predicted_discharge": volume_ok,
    }
    checks["treatment_mapping_external_validity"] = {
        "status": "needs_review",
        "reason": "附件没有给出污水处理量与污染河长的因果响应，结果依赖论文明确的比例代理假设",
    }

    comparison = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    selected_count = comparison.groupby("task")["decision_status"].apply(lambda values: (values == "pass").sum())
    winner_ok = bool((selected_count == 1).all())
    checks["model_selection_unique_winner"] = {
        "status": "pass" if winner_ok else "fail",
        "selected_count_by_task": selected_count.to_dict(),
    }
    checks["forecast_sample_size"] = {
        "status": "needs_review",
        "annual_observations": 10,
        "rolling_origin_tests": 6,
        "reason": "10年外推长度等于历史样本长度，区间只描述残差重采样而非制度变化",
    }

    reproducibility = load_json(RESULTS / "reproducibility_check.json")
    checks["deterministic_rerun"] = {
        "status": reproducibility["status"],
        "tracked_file_count": reproducibility["tracked_file_count"],
        "rerun_return_code": reproducibility["rerun_return_code"],
    }

    figure_paths = sorted(FIGURES.glob("*.png"))
    valid_figures = [path for path in figure_paths if path.stat().st_size > 10_000 and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"]
    checks["figures"] = {
        "status": "pass" if len(valid_figures) >= 6 and len(valid_figures) == len(figure_paths) else "fail",
        "png_count": len(figure_paths),
        "valid_png_count": len(valid_figures),
    }

    paper_md_path = ROOT / "paper" / "paper.md"
    paper_tex_path = ROOT / "paper" / "main.tex"
    numbers = load_json(RESULTS / "paper_numbers.json")
    if paper_md_path.is_file():
        paper_text = paper_md_path.read_text(encoding="utf-8")
        missing_numbers = [key for key, value in numbers.items() if value not in paper_text]
    else:
        missing_numbers = list(numbers)
    tex_uses_macros = paper_tex_path.is_file() and "../results/generated_macros.tex" in paper_tex_path.read_text(encoding="utf-8")
    checks["paper_result_consistency"] = {
        "status": "pass" if not missing_numbers and tex_uses_macros else "fail",
        "missing_markdown_number_keys": missing_numbers,
        "tex_uses_generated_macros": tex_uses_macros,
    }

    manifest = load_json(RESULTS / "run_manifest.json")
    input_hash_ok = True
    for relative_path, expected in manifest["input_sha256"].items():
        input_hash_ok &= sha256(ROOT / relative_path) == expected
    checks["input_hashes"] = {"status": "pass" if input_hash_ok else "fail", "count": len(manifest["input_sha256"])}

    failed = [name for name, result in checks.items() if result["status"] == "fail"]
    needs_review = [name for name, result in checks.items() if result["status"] == "needs_review"]
    overall = "fail" if failed else ("needs_review" if needs_review else "pass")
    payload = {
        "overall_status": overall,
        "failed_checks": failed,
        "needs_review_checks": needs_review,
        "checks": checks,
    }
    (RESULTS / "validation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"overall_status": overall, "failed": failed, "needs_review": needs_review}, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
