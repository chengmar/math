#!/usr/bin/env python3
"""Check required deliverables and paper/result number consistency."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"


def main():
    key = json.loads((RESULTS / "key_results.json").read_text(encoding="utf-8"))
    paper_md = (ROOT / "paper" / "paper.md").read_text(encoding="utf-8")
    main_tex = (ROOT / "paper" / "main.tex").read_text(encoding="utf-8")
    checks = []

    def add(name, passed, evidence):
        checks.append({"check": name, "status": "pass" if passed else "fail", "evidence": evidence})

    required = [
        "problem-analysis.md", "data-audit.md", "assumptions.yaml", "variables.yaml",
        "model-selection.md", "solution-report.yaml", "reproducibility.yaml",
        "code/solve.py", "results/key_results.json", "figures/q1_chain_profiles.svg",
        "paper/main.tex", "paper/paper.md",
    ]
    missing = [path for path in required if not (ROOT / path).exists()]
    add("required_deliverables", not missing, "missing=" + repr(missing))

    macro_inputs = [
        "../results/paper_numbers.tex", "../results/q1_table.tex",
        "../results/q2_table.tex", "../results/q3_table.tex",
    ]
    missing_inputs = [value for value in macro_inputs if value not in main_tex]
    add("latex_generated_inputs", not missing_inputs, "missing_inputs=" + repr(missing_inputs))

    q1_12 = key["q1"]["Q1-W12"]
    q1_24 = key["q1"]["Q1-W24"]
    q2 = key["q2"]
    q3 = key["q3"]
    q3m = q3["nominal_33_case_metrics"]
    expected = {
        "q1_w12_drum": "%.3f" % q1_12["drum_deg"],
        "q1_w12_draft": "%.3f" % q1_12["draft_m"],
        "q1_w12_radius": "%.3f" % q1_12["wander_radius_m"],
        "q1_w24_drum": "%.3f" % q1_24["drum_deg"],
        "q1_w24_draft": "%.3f" % q1_24["draft_m"],
        "q1_w24_radius": "%.3f" % q1_24["wander_radius_m"],
        "q2_threshold": "%.1f" % q2["continuous_minimum_ball_mass_kg"],
        "q2_ball": "%.0f" % q2["recommended_ball_mass_kg"],
        "q2_drum": "%.3f" % q2["recommended"]["drum_deg"],
        "q2_anchor": "%.3f" % q2["recommended"]["anchor_angle_deg"],
        "q3_length": "%.2f" % q3["selected_design"]["chain_length_m"],
        "q3_ball": "%.0f" % q3["selected_design"]["ball_mass_kg"],
        "q3_drum": "%.3f" % q3m["max_drum_angle_deg"],
        "q3_anchor": "%.3f" % q3m["max_anchor_angle_deg"],
        "q3_draft": "%.3f" % q3m["max_draft_m"],
        "q3_radius": "%.3f" % q3m["max_wander_radius_m"],
    }
    absent = {name: value for name, value in expected.items() if value not in paper_md}
    add("markdown_key_numbers", not absent, "absent=" + repr(absent))

    generated_tables = [RESULTS / "q1_table.tex", RESULTS / "q2_table.tex", RESULTS / "q3_table.tex"]
    empty_tables = [str(path.relative_to(ROOT)) for path in generated_tables if path.stat().st_size == 0]
    add("generated_tables_nonempty", not empty_tables, "empty=" + repr(empty_tables))

    figures = sorted((ROOT / "figures").glob("*.svg")) + sorted((ROOT / "figures").glob("*.pdf"))
    add("figures_present", len(figures) >= 8 and all(path.stat().st_size > 1000 for path in figures),
        "count=%d" % len(figures))

    status = "pass" if all(row["status"] == "pass" for row in checks) else "fail"
    payload = {"status": status, "checks": checks}
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "paper-consistency.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": status, "checks": len(checks)}, ensure_ascii=False, sort_keys=True))
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
