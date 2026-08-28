"""Check required files and paper-to-result consistency."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def main():
    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/core.py",
        "code/solve.py",
        "code/verify_outputs.py",
        "paper/main.tex",
        "paper/paper.md",
    ]
    checks = []
    for rel in required:
        path = ROOT / rel
        checks.append(
            {
                "check": f"required:{rel}",
                "status": "pass" if path.is_file() and path.stat().st_size > 0 else "fail",
            }
        )

    for rel in ["assumptions.yaml", "variables.yaml", "solution-report.yaml", "reproducibility.yaml"]:
        try:
            value = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
            ok = isinstance(value, dict)
            detail = "parsed"
        except Exception as exc:
            ok = False
            detail = str(exc)
        checks.append({"check": f"yaml_parse:{rel}", "status": "pass" if ok else "fail", "detail": detail})

    all_results = json.loads((RESULTS / "all_results.json").read_text(encoding="utf-8"))
    verification = json.loads((RESULTS / "verification.json").read_text(encoding="utf-8"))
    q1 = json.loads((RESULTS / "q1_summary.json").read_text(encoding="utf-8"))
    q2 = json.loads((RESULTS / "q2_summary.json").read_text(encoding="utf-8"))
    q3 = json.loads((RESULTS / "q3_summary.json").read_text(encoding="utf-8"))
    q4 = json.loads((RESULTS / "q4_summary.json").read_text(encoding="utf-8"))
    checks.extend(
        [
            {"check": "overall_evidence_downgrade", "status": "pass" if all_results["status"] == "needs_review" else "fail"},
            {"check": "independent_verification", "status": "pass" if verification["status"] == "pass" else "fail"},
            {"check": "external_validity_not_overclaimed", "status": "pass" if verification["external_validity"] == "needs_review" else "fail"},
            {"check": "global_optimality_not_overclaimed", "status": "pass" if verification["global_optimality"] == "needs_review" else "fail"},
        ]
    )

    result_csv = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>", encoding="gb18030")
    csv_ok = (
        len(result_csv) == 671
        and list(result_csv.columns) == ["时间(s)", "温度(摄氏度)"]
        and abs(float(result_csv.iloc[0, 0])) < 1e-12
        and abs(float(result_csv.iloc[-1, 0]) - 335.0) < 1e-12
        and set(result_csv.iloc[:, 0].diff().dropna().round(9)) == {0.5}
    )
    checks.append({"check": "q1_result_csv_shape_header_time", "status": "pass" if csv_ok else "fail"})

    paper_md = (ROOT / "paper" / "paper.md").read_text(encoding="utf-8")
    expected_strings = [
        f"{q1['markers']['zone_3_mid']['temperature_C']:.3f}",
        f"{q1['markers']['zone_6_mid']['temperature_C']:.3f}",
        f"{q1['markers']['zone_7_mid']['temperature_C']:.3f}",
        f"{q1['markers']['zone_8_end']['temperature_C']:.3f}",
        f"{q2['maximum_speed_cm_min']:.6f}",
        f"{q3['metrics']['area_above_217_C_s']:.3f}",
        f"{q4['metrics']['area_above_217_C_s']:.3f}",
        f"{q4['metrics']['symmetry_ratio']:.6f}",
    ]
    for value in expected_strings:
        checks.append({"check": f"paper_md_contains:{value}", "status": "pass" if value in paper_md else "fail"})

    macros = (RESULTS / "generated_macros.tex").read_text(encoding="utf-8")
    expected_macros = {
        "QOneZThree": f"{q1['markers']['zone_3_mid']['temperature_C']:.2f}",
        "QOneZSix": f"{q1['markers']['zone_6_mid']['temperature_C']:.2f}",
        "QOneZSeven": f"{q1['markers']['zone_7_mid']['temperature_C']:.2f}",
        "QOneZEightEnd": f"{q1['markers']['zone_8_end']['temperature_C']:.2f}",
        "QTwoSpeed": f"{q2['maximum_speed_cm_min']:.3f}",
        "QThreeArea": f"{q3['metrics']['area_above_217_C_s']:.2f}",
        "QFourArea": f"{q4['metrics']['area_above_217_C_s']:.2f}",
        "QFourSymmetry": f"{q4['metrics']['symmetry_ratio']:.4f}",
    }
    for name, value in expected_macros.items():
        needle = "\\newcommand{\\" + name + "}{" + value + "}"
        checks.append({"check": f"tex_macro:{name}", "status": "pass" if needle in macros else "fail"})

    tex = (ROOT / "paper" / "main.tex").read_text(encoding="utf-8")
    figure_names = [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]
    for name in figure_names:
        path = ROOT / "figures" / name
        file_ok = path.is_file() and path.stat().st_size > 1000
        # candidate_validation is supporting rather than embedded in the paper.
        referenced = name in tex or name == "<SOURCE_FILE_REDACTED>"
        checks.append({"check": f"figure:{name}", "status": "pass" if file_ok and referenced else "fail"})

    memory = (ROOT / "reports" / "training-memory-usage.md").read_text(encoding="utf-8")
    decisions_ok = memory.count("- decision: adopt") + memory.count("- decision: adapt") + memory.count("- decision: reject") == 3
    checks.append({"check": "training_memory_three_decisions", "status": "pass" if decisions_ok and "pending" not in memory else "fail"})

    tex_engine = shutil.which("xelatex") or shutil.which("pdflatex")
    checks.append(
        {
            "check": "paper_compile",
            "status": "needs_review" if tex_engine is None else "needs_review",
            "detail": "No TeX engine detected" if tex_engine is None else "Engine detected but compilation is handled separately",
        }
    )
    hard_fail = any(row["status"] == "fail" for row in checks)
    report = {
        "status": "fail" if hard_fail else "pass",
        "checks": checks,
        "paper_compile": "needs_review",
    }
    (RESULTS / "deliverable_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
