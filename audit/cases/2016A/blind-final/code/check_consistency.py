#!/usr/bin/env python3
"""Field- and semantic-location checks for paper, code and generated results."""

import csv
import hashlib
import json
import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"


def read_csv(name):
    with (RESULTS / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def section(text, heading):
    start = text.find(heading)
    if start < 0:
        return ""
    next_heading = text.find("\n## ", start + len(heading))
    return text[start:] if next_heading < 0 else text[start:next_heading]


def close(left, right, tolerance=1.0e-9):
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def main():
    key = json.loads((RESULTS / "key_results.json").read_text(encoding="utf-8"))
    validation = json.loads((RESULTS / "validation.json").read_text(encoding="utf-8"))
    manifest = json.loads((RESULTS / "generated-manifest.json").read_text(encoding="utf-8"))
    paper_md = (ROOT / "paper" / "paper.md").read_text(encoding="utf-8")
    main_tex = (ROOT / "paper" / "main.tex").read_text(encoding="utf-8")
    checks = []

    def add(name, passed, evidence):
        checks.append({
            "check": name,
            "status": "pass" if passed else "fail",
            "evidence": evidence,
        })

    required = [
        "problem-analysis.md", "data-audit.md", "assumptions.yaml", "variables.yaml",
        "model-selection.md", "solution-report.yaml", "reproducibility.yaml",
        "code/solve.py", "code/test_physics.py", "results/key_results.json",
        "results/<SOURCE_FILE_REDACTED>", "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>", "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>", "results/<SOURCE_FILE_REDACTED>",
        "figures/q1_chain_profiles.svg", "paper/main.tex", "paper/paper.md",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    add("required_deliverables", not missing, "missing=" + repr(missing))

    generated_paths = [row["path"] for row in manifest.get("generated_outputs", [])]
    source_paths = [row["path"] for row in manifest.get("source_inputs", [])]
    manifest_ok = (
        manifest.get("schema_version") == 2
        and manifest.get("manifest_self_included") is False
        and manifest.get("generated_output_count") == len(generated_paths)
        and manifest.get("generated_files_including_manifest") == len(generated_paths) + 1
        and not set(generated_paths).intersection(source_paths)
        and "results/README.md" in generated_paths
    )
    add(
        "manifest_schema_and_count_definition",
        manifest_ok,
        "sources=%d, generated=%d" % (len(source_paths), len(generated_paths)),
    )

    manifest_entries = manifest.get("source_inputs", []) + manifest.get("generated_outputs", [])
    manifest_mismatches = []
    for entry in manifest_entries:
        path = ROOT / entry["path"]
        if not path.is_file():
            manifest_mismatches.append({"path": entry["path"], "reason": "missing"})
            continue
        actual_bytes = path.stat().st_size
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_bytes != entry["bytes"] or actual_hash != entry["sha256"]:
            manifest_mismatches.append({
                "path": entry["path"], "reason": "size_or_sha256_mismatch",
            })
    add("manifest_entry_hashes", not manifest_mismatches, repr(manifest_mismatches))

    candidates = read_csv("<SOURCE_FILE_REDACTED>")
    selected_rows = [row for row in candidates if row["selected"] == "yes"]
    selected = key["q3"]["selected_design"]
    selected_ok = len(selected_rows) == 1
    if selected_ok:
        row = selected_rows[0]
        selected_ok = all([
            row["chain_type"] == selected["chain_type"],
            int(row["link_count"]) == int(selected["link_count"]),
            close(row["chain_length_m"], selected["chain_length_m"]),
            close(row["ball_effective_mass_kg"], selected["ball_effective_mass_kg"]),
            close(row["compromise_score"], selected["compromise_score"]),
        ])
    add("q3_selected_row_field_consistency", selected_ok, "selected_rows=%d" % len(selected_rows))

    scores = [float(row["compromise_score"]) for row in candidates]
    global_score_ok = selected_rows and close(selected_rows[0]["compromise_score"], min(scores), 1.0e-12)
    add(
        "q3_selected_score_global_minimum",
        bool(global_score_ok),
        "selected=%.12f,min=%.12f" % (
            float(selected_rows[0]["compromise_score"]) if selected_rows else float("nan"),
            min(scores),
        ),
    )

    per_structure = {}
    for row in candidates:
        structure = (row["chain_type"], int(row["link_count"]))
        if structure not in per_structure or float(row["compromise_score"]) < float(
                per_structure[structure]["compromise_score"]):
            per_structure[structure] = row
    structural_ranked = sorted(per_structure.values(), key=lambda row: float(row["compromise_score"]))
    second = key["q3"]["second_structural_design"]
    second_ok = (
        len(structural_ranked) >= 2
        and structural_ranked[1]["chain_type"] == second["chain_type"]
        and int(structural_ranked[1]["link_count"]) == int(second["link_count"])
        and close(structural_ranked[1]["compromise_score"], second["score"])
        and close(float(structural_ranked[1]["compromise_score"]) - min(scores), second["score_gap"])
    )
    add("q3_second_structural_field_consistency", second_ok, repr(second))

    convergence = read_csv("<SOURCE_FILE_REDACTED>")
    convergence_ok = (
        len(convergence) == 5
        and all(row["status"] == "pass" for row in convergence)
        and len({(row["selected_chain_type"], row["selected_link_count"]) for row in convergence}) == 1
    )
    add("q3_grid_and_boundary_convergence", convergence_ok, "rows=%d" % len(convergence))

    coverage = read_csv("<SOURCE_FILE_REDACTED>")
    coverage_ok = (
        len(coverage) == int(key["q3"]["integer_link_counts_enumerated"])
        and all(row["enumeration_status"] == "pass" for row in coverage)
        and any(row["chain_type"] == "V" and row["link_count"] == "116" for row in coverage)
        and any(row["chain_type"] == "V" and row["link_count"] == "117" for row in coverage)
    )
    add("q3_every_integer_link_coverage", coverage_ok, "rows=%d" % len(coverage))

    q2_rows = read_csv("<SOURCE_FILE_REDACTED>")
    q2_adjusted = [row for row in q2_rows if row["case"].startswith("recommended_")]
    q2 = key["q2"]
    q2_ok = len(q2_adjusted) == 1 and all([
        close(q2_adjusted[0]["ball_effective_mass_kg"], q2["recommended_ball_submerged_effective_mass_kg"]),
        close(q2_adjusted[0]["drum_deg"], q2["recommended"]["drum_deg"]),
        close(q2_adjusted[0]["anchor_angle_deg"], q2["recommended"]["anchor_angle_deg"]),
        q2_adjusted[0]["drum_constraint"] == "pass",
        q2_adjusted[0]["anchor_constraint"] == "pass",
    ])
    add("q2_structured_field_consistency", q2_ok, "adjusted_rows=%d" % len(q2_adjusted))

    macros_text = (RESULTS / "paper_numbers.tex").read_text(encoding="utf-8")
    macros = dict(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}", macros_text))
    macro_expected = {
        "QTwoBallEffectiveThreshold": "%.1f" % q2["continuous_minimum_ball_effective_mass_kg"],
        "QThreeLinkCount": "%d" % selected["link_count"],
        "QThreeChainLength": "%.3f" % selected["chain_length_m"],
        "QThreeBallMass": "%.1f" % selected["ball_dry_mass_kg_if_solid_steel"],
        "QThreeBallEffectiveMass": "%.1f" % selected["ball_effective_mass_kg"],
        "QThreeScore": "%.8f" % selected["compromise_score"],
    }
    macro_mismatch = {
        name: {"actual": macros.get(name), "expected": value}
        for name, value in macro_expected.items() if macros.get(name) != value
    }
    add("tex_macro_field_consistency", not macro_mismatch, repr(macro_mismatch))

    md_q1 = section(paper_md, "## 7.1 问题一")
    md_q2 = section(paper_md, "## 7.2 问题二")
    md_q3 = section(paper_md, "## 7.3 问题三")
    md_audit = section(paper_md, "## 8.3 审计反例闭环")
    markdown_semantics = all([
        "4.563°" in md_q1 and "6.092°" in md_q1 and "3.896°" in md_q1,
        "1927.888965 kg" in md_q2 and "1956.210191 kg" in md_q2,
        "4336.090641 kg" in md_q3 and "1.6261287314" in md_q3,
        "11,058" in md_q3 and "6,537" in md_q3,
        "17.5301°" in md_audit and "19.4818°" in md_audit,
        "V-117/5050" in md_audit and "V-116/5200" in md_audit,
    ])
    add("markdown_semantic_location_consistency", markdown_semantics, "q1/q2/q3/audit sections")

    latex_inputs = [
        "../results/paper_numbers.tex", "../results/q1_table.tex",
        "../results/q2_table.tex", "../results/q3_table.tex",
    ]
    latex_semantics = all(value in main_tex for value in latex_inputs) and all([
        "\\QTwoBallEffectiveThreshold" in main_tex,
        "\\QThreeBallEffectiveMass" in main_tex,
        "\\QThreeRobustAnchor" in main_tex,
        "17.5301^\\circ" in main_tex,
    ])
    add("latex_semantic_macro_locations", latex_semantics, "generated inputs and named macros")

    tables = [RESULTS / name for name in ("q1_table.tex", "q2_table.tex", "q3_table.tex")]
    add(
        "generated_tables_nonempty",
        all(path.stat().st_size > 100 for path in tables),
        repr({path.name: path.stat().st_size for path in tables}),
    )

    figure_validation = json.loads((RESULTS / "figure-validation.json").read_text(encoding="utf-8"))
    svg_texts = [path.read_text(encoding="utf-8") for path in sorted((ROOT / "figures").glob("*.svg"))]
    figure_ok = (
        figure_validation["status"] == "pass"
        and len(svg_texts) == 4
        and all('width="100%" height="100%"' in text for text in svg_texts)
        and all("100%%" not in text for text in svg_texts)
    )
    add("svg_syntax_and_background", figure_ok, "svg_count=%d" % len(svg_texts))

    add(
        "model_validation_components",
        validation["status"] == "pass"
        and all(value == "pass" for value in validation["component_status"].values()),
        repr(validation["component_status"]),
    )

    limitations_ok = all(term in paper_md for term in (
        "链流阻", "波浪", "锚土", "needs_review", "0.107 m",
    ))
    add("paper_limitations_semantic_location", limitations_ok, "required limitation terms")

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
