"""Check required files and semantic paper-to-result consistency."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def add(checks: list[dict], name: str, ok: bool, detail: str | None = None) -> None:
    row = {"check": name, "status": "pass" if ok else "fail"}
    if detail is not None:
        row["detail"] = detail
    checks.append(row)


def main():
    checks = []
    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/core.py",
        "code/oracle.py",
        "code/solve.py",
        "code/verify_outputs.py",
        "code/check_deliverables.py",
        "code/repro_check.py",
        "paper/main.tex",
        "paper/paper.md",
    ]
    for rel in required:
        path = ROOT / rel
        add(checks, f"required:{rel}", path.is_file() and path.stat().st_size > 0)

    yaml_values = {}
    for rel in ["assumptions.yaml", "variables.yaml", "solution-report.yaml", "reproducibility.yaml"]:
        try:
            value = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
            ok = isinstance(value, dict)
            detail = "parsed"
            yaml_values[rel] = value
        except Exception as exc:
            ok = False
            detail = str(exc)
        add(checks, f"yaml_parse:{rel}", ok, detail)

    all_results = json.loads((RESULTS / "all_results.json").read_text(encoding="utf-8"))
    verification = json.loads((RESULTS / "verification.json").read_text(encoding="utf-8"))
    q1 = json.loads((RESULTS / "q1_summary.json").read_text(encoding="utf-8"))
    q2 = json.loads((RESULTS / "q2_summary.json").read_text(encoding="utf-8"))
    q3 = json.loads((RESULTS / "q3_summary.json").read_text(encoding="utf-8"))
    q4 = json.loads((RESULTS / "q4_summary.json").read_text(encoding="utf-8"))

    add(checks, "overall_evidence_downgrade", all_results["status"] == "needs_review")
    add(checks, "independent_verification", verification["status"] == "pass")
    add(
        checks,
        "external_validity_not_overclaimed",
        verification["external_validity"] == "needs_review",
    )
    add(
        checks,
        "global_optimality_not_overclaimed",
        verification["global_optimality"] == "needs_review",
    )
    add(
        checks,
        "oracle_dependency_separation",
        verification["metric_oracle_dependency_status"] == "pass",
    )
    add(
        checks,
        "q3_full_area_mutation_rejected",
        verification["analytic_oracle_tests"]["checks"]["full_area_as_q3_mutation_rejected"]
        == "pass",
    )

    add(checks, "q3_objective_key", q3.get("objective_key") == "q3_rising_area_C_s")
    add(
        checks,
        "q3_rising_area_distinct_from_total",
        abs(q3["metrics"]["q3_rising_area_C_s"] - q3["metrics"]["total_area_above_217_C_s"])
        > 100.0,
    )
    add(checks, "q3_cooling_area_diagnostic_only", q3.get("cooling_area_role") == "diagnostic_only")
    add(checks, "q3_original_constraint_set", q3.get("constraint_set") == "original_problem_constraints")
    add(checks, "q4_primary_objective", q4.get("primary_objective_key") == "symmetry_area_abs_C_s")
    add(checks, "q4_corrected_area_cap_metric", q4.get("area_cap_metric") == "q3_rising_area_C_s")
    add(
        checks,
        "q4_primary_area_cap_satisfied",
        q4["metrics"]["q3_rising_area_C_s"] <= q4["area_cap_C_s"] + 0.1,
    )

    epsilon = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    add(
        checks,
        "q4_epsilon_values",
        len(epsilon) == 4
        and np.allclose(sorted(epsilon.epsilon_ratio), [1.0, 1.025, 1.05, 1.10]),
    )
    add(
        checks,
        "q4_all_epsilon_nominal_caps",
        bool((epsilon.q3_rising_area_C_s <= epsilon.area_cap_C_s + 1e-6).all()),
    )

    optimization = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    q3_de = optimization[
        (optimization.question == "Q3")
        & (optimization.method == "differential_evolution_plus_SLSQP")
    ]
    q4_de = optimization[
        (optimization.question == "Q4")
        & np.isclose(optimization.epsilon_ratio, 1.05)
        & (optimization.method == "differential_evolution_plus_SLSQP")
    ]
    add(checks, "q3_ten_unique_de_seeds", len(q3_de) >= 10 and q3_de.seed.nunique() >= 10)
    add(checks, "q4_ten_unique_de_seeds", len(q4_de) >= 10 and q4_de.seed.nunique() >= 10)
    add(checks, "alternative_global_method_recorded", bool(optimization.method.str.contains("Sobol").any()))

    result_csv = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>", encoding="gb18030")
    csv_ok = (
        len(result_csv) == 671
        and list(result_csv.columns) == ["时间(s)", "温度(摄氏度)"]
        and abs(float(result_csv.iloc[0, 0])) < 1e-12
        and abs(float(result_csv.iloc[-1, 0]) - 335.0) < 1e-12
        and set(result_csv.iloc[:, 0].diff().dropna().round(9)) == {0.5}
    )
    add(checks, "q1_result_csv_shape_header_time", csv_ok)

    paper_md = (ROOT / "paper" / "paper.md").read_text(encoding="utf-8")
    expected_strings = [
        f"{q1['markers']['zone_3_mid']['temperature_C']:.3f}",
        f"{q1['markers']['zone_6_mid']['temperature_C']:.3f}",
        f"{q1['markers']['zone_7_mid']['temperature_C']:.3f}",
        f"{q1['markers']['zone_8_end']['temperature_C']:.3f}",
        f"{q2['maximum_speed_cm_min']:.6f}",
        f"{q3['metrics']['q3_rising_area_C_s']:.3f}",
        f"{q4['metrics']['q3_rising_area_C_s']:.3f}",
        f"{q4['metrics']['symmetry_area_abs_C_s']:.3f}",
        f"{q4['metrics']['symmetry_ratio']:.6f}",
    ]
    for value in expected_strings:
        add(checks, f"paper_md_contains:{value}", value in paper_md)
    add(checks, "paper_md_q3_semantic_phrase", "升温侧阴影面积" in paper_md)
    add(checks, "paper_md_no_v1_wrong_numbers", "629.893" not in paper_md and "630.293" not in paper_md)

    macros = (RESULTS / "generated_macros.tex").read_text(encoding="utf-8")
    expected_macros = {
        "QOneZThree": f"{q1['markers']['zone_3_mid']['temperature_C']:.2f}",
        "QOneZSix": f"{q1['markers']['zone_6_mid']['temperature_C']:.2f}",
        "QOneZSeven": f"{q1['markers']['zone_7_mid']['temperature_C']:.2f}",
        "QOneZEightEnd": f"{q1['markers']['zone_8_end']['temperature_C']:.2f}",
        "QTwoSpeed": f"{q2['maximum_speed_cm_min']:.3f}",
        "QThreeArea": f"{q3['metrics']['q3_rising_area_C_s']:.2f}",
        "QFourArea": f"{q4['metrics']['q3_rising_area_C_s']:.2f}",
        "QFourSymmetryArea": f"{q4['metrics']['symmetry_area_abs_C_s']:.2f}",
        "QFourSymmetryRatio": f"{q4['metrics']['symmetry_ratio']:.4f}",
    }
    for name, value in expected_macros.items():
        needle = "\\newcommand{\\" + name + "}{" + value + "}"
        add(checks, f"tex_macro:{name}", needle in macros)

    tex = (ROOT / "paper" / "main.tex").read_text(encoding="utf-8")
    add(checks, "tex_q3_rising_integral", "t_{217}^{\\uparrow}" in tex and "A_3=" in tex)
    add(checks, "tex_no_v1_wrong_numbers", "629.893" not in tex and "630.293" not in tex)
    add(checks, "tex_balanced_braces", tex.count("{") == tex.count("}"))
    figure_names = [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
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
        referenced = name in tex or name == "<SOURCE_FILE_REDACTED>"
        add(checks, f"figure:{name}", path.is_file() and path.stat().st_size > 1000 and referenced)

    solution_report = yaml_values.get("solution-report.yaml", {})
    add(checks, "scope_compliance_record", solution_report.get("scope_compliance", {}).get("status") == "pass")
    add(checks, "freeze_status_not_overclaimed", solution_report.get("freeze_status") == "awaiting_external_freeze")

    tex_engine = shutil.which("xelatex") or shutil.which("pdflatex")
    checks.append(
        {
            "check": "paper_compile",
            "status": "needs_review",
            "detail": "No TeX engine detected"
            if tex_engine is None
            else "Engine detected; compilation is executed as a separate verification command",
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
