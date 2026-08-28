from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"
PAPER = ROOT / "paper"


def main() -> None:
    key = json.loads((RESULTS / "key_results.json").read_text(encoding="utf-8"))
    zone = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>").set_index("zone")
    source = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>").set_index("metal")
    model = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>").set_index("model")
    markdown = (PAPER / "paper.md").read_text(encoding="utf-8")
    tex = (PAPER / "main.tex").read_text(encoding="utf-8")
    macros = (PAPER / "generated" / "macros.tex").read_text(encoding="utf-8")
    tex_log = (PAPER / "main.log").read_text(encoding="utf-8", errors="replace")

    checks = []

    markdown_expected = [
        f"{model.loc['gaussian_nested', 'mean_rmse_log2']:.3f}",
        f"{model.loc['idw_p2_k12', 'mean_rmse_log2']:.3f}",
        f"{model.loc['constant_mean', 'mean_rmse_log2']:.3f}",
        *[f"{zone.loc[index, 'typical_geometric_enrichment']:.3f}" for index in [2, 4, 1, 5, 3]],
        *[
            f"({source.loc[metal, 'source_x_m'] / 1000:.2f}, {source.loc[metal, 'source_y_m'] / 1000:.2f})"
            for metal in ["As", "Cd", "Cr", "Cu", "Hg", "Ni", "Pb", "Zn"]
        ],
    ]
    missing_markdown = [value for value in markdown_expected if value not in markdown]
    checks.append(
        {
            "check": "markdown_key_numbers",
            "status": "pass" if not missing_markdown else "fail",
            "missing": missing_markdown,
        }
    )

    macro_expected = [
        f"\\newcommand{{\\SampleCount}}{{{key['data']['sample_count']}}}",
        f"\\newcommand{{\\GaussianRMSE}}{{{model.loc['gaussian_nested', 'mean_rmse_log2']:.3f}}}",
        f"\\newcommand{{\\IDWRMSE}}{{{model.loc['idw_p2_k12', 'mean_rmse_log2']:.3f}}}",
        f"\\newcommand{{\\ConstantRMSE}}{{{model.loc['constant_mean', 'mean_rmse_log2']:.3f}}}",
        f"\\newcommand{{\\IndustrialIndex}}{{{zone.loc[2, 'typical_geometric_enrichment']:.3f}}}",
        f"\\newcommand{{\\TrafficIndex}}{{{zone.loc[4, 'typical_geometric_enrichment']:.3f}}}",
    ]
    missing_macros = [value for value in macro_expected if value not in macros]
    checks.append(
        {
            "check": "tex_generated_macros",
            "status": "pass" if not missing_macros else "fail",
            "missing": missing_macros,
        }
    )

    required_inputs = [
        "generated/macros",
        "generated/table_spatial_models.tex",
        "generated/table_zone_summary.tex",
        "generated/table_nmf_loadings.tex",
        "generated/table_sources.tex",
        "generated/table_propagation.tex",
    ]
    missing_inputs = [value for value in required_inputs if value not in tex]
    checks.append(
        {
            "check": "tex_generated_inputs",
            "status": "pass" if not missing_inputs else "fail",
            "missing": missing_inputs,
        }
    )

    required_figures = [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]
    missing_figures = [
        name
        for name in required_figures
        if name not in tex or not (ROOT / "figures" / name).exists()
    ]
    checks.append(
        {
            "check": "paper_figure_references",
            "status": "pass" if not missing_figures else "fail",
            "missing": missing_figures,
        }
    )

    fatal_patterns = [
        r"! LaTeX Error",
        r"Undefined control sequence",
        r"Missing \$ inserted",
        r"Emergency stop",
    ]
    fatal_log_hits = [pattern for pattern in fatal_patterns if re.search(pattern, tex_log)]
    compiled = bool(
        re.search(r"Output written on main\.pdf \(13 pages\)", tex_log)
        and (PAPER / "<SOURCE_FILE_REDACTED>").exists()
    )
    checks.append(
        {
            "check": "xelatex_compile",
            "status": "pass" if compiled and not fatal_log_hits else "fail",
            "fatal_patterns": fatal_log_hits,
            "pages": 13 if compiled else None,
        }
    )

    reproduction = json.loads(
        (REPORTS / "reproduction-check.json").read_text(encoding="utf-8")
    )
    checks.append(
        {
            "check": "two_run_reproduction",
            "status": reproduction["status"],
            "compared_file_count": reproduction["compared_file_count"],
            "mismatch_count": len(reproduction["mismatches"]),
        }
    )

    status = "pass" if all(row["status"] == "pass" for row in checks) else "fail"
    payload = {
        "status": status,
        "checks": checks,
        "note": (
            "This check confirms numerical transcription, generated inputs, figure "
            "references and successful compilation; it does not upgrade scientific "
            "source identity or external validity."
        ),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "paper-consistency.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if status != "pass":
        raise SystemExit("fail: paper/result consistency check failed")
    print("pass: paper/result consistency and compilation checks completed")


if __name__ == "__main__":
    main()
