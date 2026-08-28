"""Render the Markdown paper and LaTeX long tables from generated results."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def markdown_table(frame: pd.DataFrame) -> str:
    columns = [str(column) for column in frame.columns]
    rows = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for values in frame.itertuples(index=False, name=None):
        rows.append("| " + " | ".join(str(value) for value in values) + " |")
    return "\n".join(rows)


def latex_longtable(frame: pd.DataFrame, caption: str, label: str) -> str:
    columns = len(frame.columns)
    alignment = "r" * columns
    header = " & ".join(str(column) for column in frame.columns) + r" \\"
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
        lines.append(" & ".join(str(value) for value in row) + r" \\")
    lines.append(r"\end{longtable}")
    return "\n".join(lines) + "\n"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    results = root / "results"
    paper = root / "paper"
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    small = summary["small_tank"]
    actual = summary["actual_tank"]
    candidates = actual["candidate_comparison"]

    small_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")[
        ["height_cm", "tilted_volume_l", "tilt_minus_upright_l"]
    ].rename(
        columns={
            "height_cm": "油高/cm",
            "tilted_volume_l": "变位后容积/L",
            "tilt_minus_upright_l": "相对无变位差/L",
        }
    )
    actual_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")[
        ["height_cm", "displaced_volume_l", "displaced_minus_upright_l"]
    ].rename(
        columns={
            "height_cm": "油高/cm",
            "displaced_volume_l": "变位后容积/L",
            "displaced_minus_upright_l": "相对原表差/L",
        }
    )
    (results / "small_tank_calibration_table.tex").write_text(
        latex_longtable(small_table, "小椭圆罐变位后1 cm间隔罐容表", "tab:small-full"),
        encoding="utf-8",
    )
    (results / "actual_tank_calibration_table.tex").write_text(
        latex_longtable(actual_table, "实际储油罐变位后10 cm间隔罐容表", "tab:actual-full"),
        encoding="utf-8",
    )

    candidate_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>").copy()
    candidate_table = candidate_table[
        ["model", "alpha_deg", "beta_deg", "train_rmse_l", "test_rmse_l", "refill_error_l"]
    ]
    candidate_table.columns = ["模型", "alpha/deg", "beta/deg", "训练RMSE/L", "留出RMSE/L", "补油误差/L"]
    for column in candidate_table.columns[1:]:
        candidate_table[column] = candidate_table[column].map(lambda value: f"{value:.3f}")

    replacements = {
        "@@SMALL_SCALE@@": f"{small['volume_scale']:.6f}",
        "@@SMALL_EFFECTIVE_DIAMETER@@": f"{2*small['effective_horizontal_semiaxis_m']:.4f}",
        "@@SMALL_CAPACITY@@": f"{small['effective_capacity_l']:.2f}",
        "@@SMALL_MAX_EFFECT@@": f"{small['max_abs_effect_l']:.2f}",
        "@@SMALL_MAX_EFFECT_HEIGHT@@": f"{small['max_effect_height_cm']:.1f}",
        "@@SMALL_TILT_IN_RMSE@@": f"{small['validation']['tilted_in']['level']['rmse_l']:.2f}",
        "@@SMALL_TILT_OUT_RMSE@@": f"{small['validation']['tilted_out']['level']['rmse_l']:.2f}",
        "@@ALPHA@@": f"{actual['final_fit']['alpha_deg']:.4f}",
        "@@BETA@@": f"{actual['final_fit']['beta_deg']:.4f}",
        "@@FINAL_INCREMENT_RMSE@@": f"{actual['final_fit']['all_discharge_increment']['rmse_l']:.3f}",
        "@@REFILL_PREDICTION@@": f"{actual['refill']['predicted_l']:.2f}",
        "@@REFILL_ERROR@@": f"{actual['refill']['error_l']:.2f}",
        "@@ACTUAL_CAPACITY@@": f"{actual['capacity_l']:.2f}",
        "@@ACTUAL_ZERO_VOLUME@@": f"{actual['operational_zero_height_volume_l']:.2f}",
        "@@ACTUAL_300_VOLUME@@": f"{actual['operational_300cm_volume_l']:.2f}",
        "@@EMPTY_READING@@": f"{actual['physical_empty_reading_mm']:.1f}",
        "@@FULL_READING@@": f"{actual['physical_full_reading_mm']:.1f}",
        "@@SEGMENT_ENVELOPE@@": f"{actual['sensitivity']['segment_fit_table_envelope_l']:.2f}",
        "@@HEIGHT_SENSITIVITY@@": f"{actual['sensitivity']['height_plus_or_minus_1mm_max_l']:.2f}",
        "@@QUADRATURE_DIFFERENCE@@": f"{actual['sensitivity']['quadrature_refinement_max_difference_l']:.4f}",
        "@@BASE_TEST_RMSE@@": f"{candidates['upright_baseline']['test']['rmse_l']:.3f}",
        "@@PITCH_TEST_RMSE@@": f"{candidates['pitch_only']['test']['rmse_l']:.3f}",
        "@@FULL_TEST_RMSE@@": f"{candidates['pitch_and_roll']['test']['rmse_l']:.3f}",
        "@@CANDIDATE_TABLE@@": markdown_table(candidate_table),
        "@@SMALL_TABLE@@": markdown_table(small_table),
        "@@ACTUAL_TABLE@@": markdown_table(actual_table),
    }
    template = (paper / "paper.template.md").read_text(encoding="utf-8")
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    remaining = sorted({part.split("@@")[0] for part in rendered.split("@@")[1::2]})
    if "@@" in rendered:
        raise RuntimeError(f"Unreplaced paper tokens: {remaining}")
    (paper / "paper.md").write_text(rendered, encoding="utf-8")
    print("[PASS] rendered paper/paper.md and LaTeX calibration tables")


if __name__ == "__main__":
    main()
