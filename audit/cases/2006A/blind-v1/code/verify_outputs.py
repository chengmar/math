#!/usr/bin/env python3
"""Independent mechanical verification for the 2006A solve outputs.

This script intentionally re-parses constraints instead of importing the
optimizer.  A ``pass`` means the recorded allocation and paper links satisfy
the checked arithmetic/file conditions; it is not a claim that the model is a
mathematical truth.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


CATEGORIES = [
    "计算机类", "经管类", "数学类", "英语类", "两课类", "机械、能源类",
    "化学、化工类", "地理、地质类", "环境类",
]

EXPECTED_FIGURES = [
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
]

CURRENT_RESULT_FILES = [
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "<SOURCE_FILE_REDACTED>",
    "summary.json",
    "data_audit.json",
    "forecast_validation.json",
    "elasticity.json",
    "constraint_checks.json",
    "boundary_checks.json",
    "input_hashes.json",
    "run_metadata.json",
]


def normalize_category(text: str) -> str:
    return str(text).strip().replace("机械能源类", "机械、能源类")


def as_int(text: str) -> int | None:
    try:
        value = float(str(text).strip())
    except ValueError:
        return None
    if not math.isfinite(value) or abs(value - round(value)) > 1e-9:
        return None
    return int(round(value))


def read_tsv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-16", newline="") as stream:
        return [[cell.strip() for cell in row] for row in csv.reader(stream, delimiter="\t")]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one(root: Path, pattern: str) -> Path:
    paths = sorted(root.glob(pattern))
    if len(paths) != 1:
        raise RuntimeError(f"Expected one {pattern}, got {len(paths)}")
    return paths[0]


def parse_isbn_constraints(path: Path) -> tuple[pd.DataFrame, dict[str, int], dict[int, int]]:
    rows = read_tsv(path)
    start = next(i for i, row in enumerate(rows) if row and row[0] == "学科名称")
    current = ""
    detail: list[dict[str, Any]] = []
    published: dict[str, int] = {}
    historical = {year: 0 for year in range(2001, 2006)}
    for raw in rows[start + 1 :]:
        row = raw + [""] * max(0, 10 - len(raw))
        if row[0]:
            current = normalize_category(row[0])
        if row[1] == "总计":
            published[current] = as_int(row[8]) or 0
            continue
        code = as_int(row[2])
        if code is None:
            continue
        values = [as_int(value) for value in row[3:9]]
        if any(value is None for value in values):
            raise RuntimeError(f"Invalid ISBN row {code}")
        for offset, year in enumerate(range(2001, 2006)):
            historical[year] += int(values[offset])
        detail.append(
            {
                "course_code": code,
                "category": current,
                "request_2006_input": int(values[5]),
            }
        )
    return pd.DataFrame(detail), published, historical


def parse_hr_capacities(path: Path) -> dict[str, int]:
    rows = read_tsv(path)
    start = next(i for i, row in enumerate(rows) if row and row[0] == "所属分社")
    capacities: dict[str, int] = {}
    for raw in rows[start + 1 :]:
        row = raw + [""] * max(0, 7 - len(raw))
        values = [as_int(value) for value in row[1:7]]
        category = normalize_category(row[0])
        if not category or any(value is None for value in values):
            continue
        capacities[category] = min(
            int(values[0] * values[1]),
            int(values[2] * values[3]),
            int(values[4] * values[5]),
        )
    return capacities


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    converted = workspace / "input" / "converted"
    results = workspace / "results"
    figures = workspace / "figures"
    paper = workspace / "paper"

    input_isbn, published_requests, historical = parse_isbn_constraints(
        one(converted, "excel/附件4_*/*.tsv")
    )
    hr_caps = parse_hr_capacities(one(converted, "excel/附件5_*/*Sheet1.tsv"))
    allocation = pd.read_csv(results / "<SOURCE_FILE_REDACTED>", encoding="utf-8-sig")
    branch_output = pd.read_csv(results / "<SOURCE_FILE_REDACTED>", encoding="utf-8-sig")
    candidate_output = pd.read_csv(results / "<SOURCE_FILE_REDACTED>", encoding="utf-8-sig")
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))

    merged = input_isbn.merge(
        allocation[[
            "course_code", "category", "request_2006", "allocation_main", "price",
            "forecast_risk_adjusted", "isbn_2005",
        ]],
        on="course_code",
        suffixes=("_input", "_output"),
        validate="one_to_one",
    )
    checks: dict[str, dict[str, Any]] = {}

    def record(name: str, condition: bool, observed: Any, expected: Any) -> None:
        checks[name] = {
            "status": "pass" if bool(condition) else "fail",
            "observed": observed,
            "expected": expected,
        }

    record("course_count", len(merged) == 72, len(merged), 72)
    record(
        "course_categories_match_input",
        bool((merged["category_input"] == merged["category_output"]).all()),
        int((merged["category_input"] != merged["category_output"]).sum()),
        0,
    )
    record(
        "course_requests_match_input",
        bool((merged["request_2006_input"] == merged["request_2006"]).all()),
        int((merged["request_2006_input"] != merged["request_2006"]).sum()),
        0,
    )
    integer_allocations = bool(
        merged["allocation_main"].notna().all()
        and ((merged["allocation_main"] - merged["allocation_main"].round()).abs() < 1e-12).all()
    )
    record("integer_allocations", integer_allocations, integer_allocations, True)
    record(
        "course_bounds",
        bool(((merged["allocation_main"] >= 0) & (merged["allocation_main"] <= merged["request_2006_input"])).all()),
        int(((merged["allocation_main"] < 0) | (merged["allocation_main"] > merged["request_2006_input"])).sum()),
        0,
    )
    inferred_totals = list(historical.values())
    record("historical_budget_constant", len(set(inferred_totals)) == 1, historical, "one common value")
    inferred_budget = inferred_totals[0]
    record("allocation_total", int(merged["allocation_main"].sum()) == inferred_budget, int(merged["allocation_main"].sum()), inferred_budget)

    detailed_requests = input_isbn.groupby("category")["request_2006_input"].sum().to_dict()
    branch_allocations = merged.groupby("category_input")["allocation_main"].sum().round().astype(int).to_dict()
    detailed_minimums = {category: detailed_requests[category] // 2 for category in CATEGORIES}
    published_minimums = {category: published_requests[category] // 2 for category in CATEGORIES}
    record(
        "detailed_branch_half_guarantees",
        all(branch_allocations[c] >= detailed_minimums[c] for c in CATEGORIES),
        branch_allocations,
        detailed_minimums,
    )
    record(
        "published_total_row_half_guarantees",
        all(branch_allocations[c] >= published_minimums[c] for c in CATEGORIES),
        branch_allocations,
        published_minimums,
    )
    record(
        "human_resource_caps",
        all(branch_allocations[c] <= hr_caps[c] for c in CATEGORIES),
        branch_allocations,
        hr_caps,
    )
    reported_branch = branch_output.set_index("category")["allocation_main"].round().astype(int).to_dict()
    record("branch_file_matches_course_sum", branch_allocations == reported_branch, reported_branch, branch_allocations)
    record("summary_matches_branch_sum", summary["allocation_by_branch"] == branch_allocations, summary["allocation_by_branch"], branch_allocations)

    eta = float(summary["elasticity_used"])
    recomputed_risk_value = float(
        (
            merged["price"]
            * merged["forecast_risk_adjusted"]
            * merged["isbn_2005"]
            * (merged["allocation_main"] / merged["isbn_2005"]) ** eta
        ).sum()
    )
    reported_risk_value = float(
        candidate_output.loc[candidate_output["candidate"] == "main", "risk_adjusted_value"].iloc[0]
    )
    tolerance = max(1e-4, abs(reported_risk_value) * 1e-8)
    record(
        "independent_objective_recalculation",
        abs(recomputed_risk_value - reported_risk_value) <= tolerance,
        recomputed_risk_value,
        reported_risk_value,
    )

    required = [
        "problem-analysis.md", "data-audit.md", "assumptions.yaml", "variables.yaml",
        "model-selection.md", "solution-report.yaml", "reproducibility.yaml",
        "paper/main.tex", "paper/paper.md", "code/solve.py", "code/convert_inputs.ps1",
    ]
    missing = [relative for relative in required if not (workspace / relative).is_file()]
    record("required_files_exist", not missing, missing, [])
    expected_figure_paths = [figures / name for name in EXPECTED_FIGURES]
    missing_figures = [path.name for path in expected_figure_paths if not path.is_file()]
    bad_figures = [path.name for path in expected_figure_paths if path.is_file() and path.stat().st_size < 10_000]
    extra_figures = sorted(path.name for path in figures.glob("*.png") if path.name not in EXPECTED_FIGURES)
    record(
        "figures_nonempty",
        not missing_figures and not bad_figures,
        {"expected_count": len(EXPECTED_FIGURES), "missing": missing_figures, "bad": bad_figures, "unrelated_preserved": extra_figures},
        {"expected_count": len(EXPECTED_FIGURES), "missing": [], "bad": []},
    )

    main_tex = (paper / "main.tex").read_text(encoding="utf-8") if (paper / "main.tex").exists() else ""
    record(
        "tex_uses_generated_results",
        all(token in main_tex for token in ("generated/result_macros", "generated/branch_allocation", "generated/candidate_comparison")),
        "generated inputs present" if "generated/result_macros" in main_tex else "missing generated inputs",
        "all principal generated inputs",
    )
    paper_md = (paper / "paper.md").read_text(encoding="utf-8") if (paper / "paper.md").exists() else ""
    marker_match = re.search(r"<!--\s*code-results:\s*total=(\d+);\s*branches=([^;]+);\s*wape=([0-9.]+);\s*gain=([0-9.]+)\s*-->", paper_md)
    selected = summary["selected_forecast"]
    forecast_validation = json.loads((results / "forecast_validation.json").read_text(encoding="utf-8"))
    wape_pct = 100 * float(forecast_validation["variants"][selected]["holdout_2005"]["wape"])
    expected_branch_marker = ",".join(str(branch_allocations[c]) for c in CATEGORIES)
    marker_ok = bool(
        marker_match
        and int(marker_match.group(1)) == inferred_budget
        and marker_match.group(2) == expected_branch_marker
        and abs(float(marker_match.group(3)) - wape_pct) <= 0.005
        and abs(float(marker_match.group(4)) - float(summary["risk_value_gain_vs_baseline_pct"])) <= 0.005
    )
    record(
        "markdown_result_marker_matches",
        marker_ok,
        marker_match.groups() if marker_match else None,
        (inferred_budget, expected_branch_marker, round(wape_pct, 2), round(float(summary["risk_value_gain_vs_baseline_pct"]), 2)),
    )

    solution_report = yaml.safe_load((workspace / "solution-report.yaml").read_text(encoding="utf-8"))
    report_allocation = solution_report["decision"]["allocation_by_branch"]
    report_value = float(solution_report["candidate_results"]["risk_concave"]["risk_adjusted_value_cny"])
    report_checks = solution_report["completion_checks"]
    record(
        "solution_report_matches_results",
        report_allocation == branch_allocations
        and int(solution_report["decision"]["allocation_sum"]) == inferred_budget
        and abs(report_value - reported_risk_value) <= tolerance
        and all(value == "pass" for value in report_checks.values()),
        {
            "allocation": report_allocation,
            "sum": solution_report["decision"]["allocation_sum"],
            "risk_value": report_value,
            "completion_checks": report_checks,
        },
        {
            "allocation": branch_allocations,
            "sum": inferred_budget,
            "risk_value": reported_risk_value,
            "completion_checks": "all pass",
        },
    )
    reproducibility = yaml.safe_load((workspace / "reproducibility.yaml").read_text(encoding="utf-8"))
    record(
        "reproducibility_report_status",
        reproducibility["status"] == "pass"
        and reproducibility["second_run_hash_check"]["status"] == "pass"
        and reproducibility["paper_compile"]["status"] == "pass",
        {
            "overall": reproducibility["status"],
            "second_run": reproducibility["second_run_hash_check"]["status"],
            "paper_compile": reproducibility["paper_compile"]["status"],
        },
        {"overall": "pass", "second_run": "pass", "paper_compile": "pass"},
    )

    overall = "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    verification = {
        "status": overall,
        "scope": "mechanical arithmetic, constraints, independent objective recalculation, and paper-result linkage",
        "mathematical_truth_claim": "needs_review",
        "checks": checks,
    }
    (results / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    hash_targets = sorted(
        [
            *(results / name for name in CURRENT_RESULT_FILES),
            *(figures / name for name in EXPECTED_FIGURES),
            *(paper / "generated").glob("*.tex"),
        ],
        key=lambda path: path.relative_to(workspace).as_posix(),
    )
    hashes = [
        {
            "path": path.relative_to(workspace).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in hash_targets
    ]
    (results / "output_hashes.json").write_text(
        json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(verification, ensure_ascii=False, indent=2))
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
