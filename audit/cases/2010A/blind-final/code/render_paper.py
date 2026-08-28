"""Render Markdown and LaTeX tables from the single structured result set."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def markdown_table(frame: pd.DataFrame) -> str:
    columns = [str(column) for column in frame.columns]
    rows = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for values in frame.itertuples(index=False, name=None):
        rows.append("| " + " | ".join(str(value) for value in values) + " |")
    return "\n".join(rows)


def latex_escape(value: object) -> str:
    return str(value).replace("_", r"\_").replace("%", r"\%")


def latex_longtable(
    frame: pd.DataFrame,
    caption: str,
    label: str,
    alignment: str | None = None,
) -> str:
    columns = len(frame.columns)
    if alignment is None:
        alignment = "r" * columns
    header = " & ".join(latex_escape(column) for column in frame.columns) + r" \\"
    lines = [
        rf"\begin{{longtable}}{{{alignment}}}",
        rf"\caption{{{caption}}}\label{{{label}}}\\",
        r"\toprule",
        header,
        r"\midrule",
        r"\endfirsthead",
        rf"\multicolumn{{{columns}}}{{c}}{{续表~\ref{{{label}}}}}\\",
        r"\toprule",
        header,
        r"\midrule",
        r"\endhead",
        r"\midrule",
        rf"\multicolumn{{{columns}}}{{r}}{{下页续}}\\",
        r"\endfoot",
        r"\bottomrule",
        r"\endlastfoot",
    ]
    for row in frame.itertuples(index=False, name=None):
        lines.append(" & ".join(latex_escape(value) for value in row) + r" \\")
    lines.append(r"\end{longtable}")
    return "\n".join(lines) + "\n"


def format_small_table(frame: pd.DataFrame) -> pd.DataFrame:
    formatted = pd.DataFrame(
        {
            "油高/cm": frame["height_cm"].astype(int),
            "点表/L": frame["volume_l_reported"].astype(int),
            "模型包络/L": [
                f"[{lower}, {upper}]"
                for lower, upper in zip(
                    frame["model_envelope_lower_l_reported"].astype(int),
                    frame["model_envelope_upper_l_reported"].astype(int),
                )
            ],
            "相对无变位差/L": frame[
                "production_minus_upright_l_reported"
            ].astype(int),
        }
    )
    return formatted


def format_actual_table(frame: pd.DataFrame) -> pd.DataFrame:
    formatted = pd.DataFrame(
        {
            "油高/cm": frame["height_cm"].astype(int),
            "条件点表/L": frame["volume_l_reported"].astype(int),
            "诊断压力范围/L": [
                f"[{lower}, {upper}]"
                for lower, upper in zip(
                    frame["diagnostic_stress_lower_l_reported"].astype(int),
                    frame["diagnostic_stress_upper_l_reported"].astype(int),
                )
            ],
            "相对无变位差/L": frame["point_minus_upright_l_reported"].astype(int),
        }
    )
    return formatted


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    results = root / "results"
    paper = root / "paper"
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    small = summary["small_tank"]
    actual = summary["actual_tank"]

    small_table = format_small_table(
        pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    )
    actual_table = format_actual_table(
        pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    )
    (results / "small_tank_calibration_table.tex").write_text(
        latex_longtable(
            small_table,
            "小椭圆罐1 cm间隔点表及模型形式包络（均按1 L报告）",
            "tab:small-full",
            "rrlr",
        ),
        encoding="utf-8",
    )
    (results / "actual_tank_calibration_table.tex").write_text(
        latex_longtable(
            actual_table,
            "实际罐10 cm间隔条件点表及诊断压力范围（均按1 L报告）",
            "tab:actual-full",
            "rrlr",
        ),
        encoding="utf-8",
    )

    small_candidates = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    small_candidates = small_candidates[
        [
            "candidate",
            "horizontal_semiaxis_m",
            "level_offset_mm",
            "training_rmse_l",
            "time_check_rmse_l",
            "time_check_residual_height_correlation",
        ]
    ].copy()
    small_candidates.columns = [
        "模型",
        "横半轴/m",
        "零点/mm",
        "倾斜进油拟合RMSE/L",
        "倾斜出油时间检验RMSE/L",
        "检验残差-油高相关",
    ]
    for column in small_candidates.columns[1:]:
        small_candidates[column] = small_candidates[column].map(
            lambda value: f"{value:.3f}"
        )

    candidates = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    candidates = candidates[
        [
            "candidate",
            "alpha_deg",
            "beta_abs_deg",
            "selection_first_segment_rmse_l",
            "post_selection_time_check_rmse_l",
            "post_selection_refill_error_l",
        ]
    ].copy()
    candidates.columns = [
        "模型",
        "alpha/deg",
        "abs(beta)/deg",
        "第一段选择RMSE/L",
        "第二段事后检验RMSE/L",
        "补油事后误差/L",
    ]
    for column in candidates.columns[1:]:
        candidates[column] = candidates[column].map(lambda value: f"{value:.3f}")

    sensitivity = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    sensitivity = sensitivity[
        sensitivity["range_basis_status"] == "diagnostic_not_tolerance"
    ][
        ["scenario", "alpha_deg", "beta_abs_deg", "increment_rmse_l", "max_table_change_l"]
    ].copy()
    sensitivity.columns = ["压力场景", "alpha/deg", "abs(beta)/deg", "RMSE/L", "最大表差/L"]
    for column in sensitivity.columns[1:]:
        sensitivity[column] = sensitivity[column].map(
            lambda value: "—" if pd.isna(value) else f"{value:.3f}"
        )

    production = actual["conditional_production_model"]
    hac_interval = production["conditional_hac_lag1_95pct_interval_deg"]
    replacements = {
        "@@SMALL_TRANSFER_DIAMETER@@": f"{2*small['upright_scale_calibration']['transferable_horizontal_semiaxis_m']:.4f}",
        "@@SMALL_POSE_DIAMETER@@": f"{2*small['production_model']['horizontal_semiaxis_m']:.4f}",
        "@@SMALL_OFFSET_MM@@": f"{small['production_model']['level_offset_mm']:.2f}",
        "@@SMALL_TRAIN_RMSE@@": f"{small['production_model']['training_metrics']['rmse_l']:.2f}",
        "@@SMALL_CHECK_RMSE@@": f"{small['production_model']['time_ordered_check_metrics']['rmse_l']:.2f}",
        "@@SMALL_BASE_CHECK_RMSE@@": f"{small['transfer_model_time_check_rmse_l']:.2f}",
        "@@SMALL_BASE_CORR@@": f"{small['transfer_model_time_check_residual_height_correlation']:.3f}",
        "@@SMALL_NEW_CORR@@": f"{small['production_model']['time_check_residual_height_correlation']:.3f}",
        "@@SMALL_ENVELOPE@@": f"{small['model_envelope']['max_width_l']:.2f}",
        "@@SMALL_MAX_EFFECT@@": f"{small['max_abs_change_from_upright_l']:.2f}",
        "@@SMALL_CANDIDATES@@": markdown_table(small_candidates),
        "@@ACTUAL_ALPHA@@": f"{production['alpha_deg']:.4f}",
        "@@ACTUAL_BETA@@": f"{production['beta_abs_deg']:.4f}",
        "@@ACTUAL_RMSE@@": f"{production['all_discharge_increment']['rmse_l']:.3f}",
        "@@TIME_CHECK_RMSE@@": f"{actual['time_ordered_checks']['first_fit_second_segment_rmse_l']:.3f}",
        "@@TIME_REFILL_ERROR@@": f"{actual['time_ordered_checks']['first_fit_refill_error_l']:.2f}",
        "@@FINAL_REFILL_ERROR@@": f"{actual['time_ordered_checks']['final_refit_refill_error_l']:.2f}",
        "@@HAC_ALPHA_RANGE@@": f"[{hac_interval[0][0]:.4f}, {hac_interval[0][1]:.4f}]",
        "@@HAC_BETA_RANGE@@": f"[{hac_interval[1][0]:.4f}, {hac_interval[1][1]:.4f}]",
        "@@FIRST_LAG1@@": f"{actual['difference_error_diagnostics']['first_segment']['lag1_correlation']:.3f}",
        "@@SECOND_LAG1@@": f"{actual['difference_error_diagnostics']['second_segment']['lag1_correlation']:.3f}",
        "@@CUM_ALPHA@@": f"{actual['cumulative_state_cross_check']['alpha_deg']:.4f}",
        "@@CUM_BETA@@": f"{actual['cumulative_state_cross_check']['beta_abs_deg']:.4f}",
        "@@CUM_TABLE_CHANGE@@": f"{actual['cumulative_state_cross_check']['max_table_change_l']:.2f}",
        "@@FLOW_CORR@@": f"{actual['flow_scale_joint_fit']['beta_flow_scale_correlation']:.3f}",
        "@@FLOW_CHANGE@@": f"{actual['sensitivity']['flow_scale_plus_minus_0.1pct_max_table_change_l']:.2f}",
        "@@RADIUS_CHANGE@@": f"{actual['sensitivity']['radius_plus_minus_5mm_max_table_change_l']:.2f}",
        "@@LENGTH_CHANGE@@": f"{actual['sensitivity']['cylinder_length_plus_minus_10mm_max_table_change_l']:.2f}",
        "@@HEIGHT_DRIFT@@": f"{actual['sensitivity']['post_calibration_height_drift_plus_minus_1mm_max_table_change_l']:.2f}",
        "@@QUAD_DIFF@@": f"{actual['quadrature_refinement_max_difference_l']:.4f}",
        "@@ACTUAL_CAPACITY@@": f"{actual['capacity_l']:.2f}",
        "@@ACTUAL_EMPTY@@": f"{actual['physical_empty_reading_mm']:.1f}",
        "@@ACTUAL_FULL@@": f"{actual['physical_full_reading_mm']:.1f}",
        "@@CANDIDATES@@": markdown_table(candidates),
        "@@SENSITIVITY@@": markdown_table(sensitivity),
        "@@SMALL_TABLE@@": markdown_table(small_table),
        "@@ACTUAL_TABLE@@": markdown_table(actual_table),
    }
    template = (paper / "paper.template.md").read_text(encoding="utf-8")
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    if "@@" in rendered:
        raise RuntimeError("Unreplaced token remains in paper.template.md")
    (paper / "paper.md").write_text(rendered, encoding="utf-8")
    print("[PASS] rendered Markdown paper and both LaTeX calibration tables")


if __name__ == "__main__":
    main()
