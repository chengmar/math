#!/usr/bin/env python3
"""Independent result and paper consistency checks.

This verifier intentionally does not import solve.py. It checks artifacts by a
separate, simpler path and emits only pass/fail/needs_review statuses.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


BRANCHES = [
    "计算机类", "经管类", "数学类", "英语类", "两课类", "机械、能源类",
    "化学、化工类", "地理、地质类", "环境类",
]
MACROS = {
    "计算机类": "CS", "经管类": "Business", "数学类": "Math",
    "英语类": "English", "两课类": "Politics", "机械、能源类": "Mech",
    "化学、化工类": "Chem", "地理、地质类": "Geo", "环境类": "Env",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def add(checks: list[dict[str, Any]], check_id: str, condition: bool | None, evidence: str) -> None:
    status = "needs_review" if condition is None else ("pass" if condition else "fail")
    checks.append({"id": check_id, "status": status, "evidence": evidence})


def parse_machine_json(text: str, label: str) -> dict[str, Any] | None:
    match = re.search(rf"<!--\s*{re.escape(label)}\s*:\s*(\{{.*?\}})\s*-->", text)
    return json.loads(match.group(1)) if match else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.workspace.resolve()
    results = root / "results"
    checks: list[dict[str, Any]] = []

    manifest_path = root / "_working" / "converted" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_mismatches: list[str] = []
    for item in manifest:
        for key, hash_key in [("source", "source_sha256"), ("output", "output_sha256")]:
            path = root / item[key]
            if not path.is_file() or sha256(path) != item[hash_key]:
                manifest_mismatches.append(f"{key}:{item[key]}")
    add(checks, "conversion_hash_contract", not manifest_mismatches, f"objects={len(manifest)}, mismatches={manifest_mismatches}")

    final_rows = read_csv(results / "<SOURCE_FILE_REDACTED>")
    add(checks, "final_branch_rows", [row["branch"] for row in final_rows] == BRANCHES, f"rows={len(final_rows)}")
    allocation = {row["branch"]: int(row["robust_balanced"]) for row in final_rows}
    requests = {row["branch"]: int(row["request_2006"]) for row in final_rows}
    guarantees = {row["branch"]: int(row["guaranteed_half"]) for row in final_rows}
    caps = {row["branch"]: int(row["effective_cap"]) for row in final_rows}
    add(checks, "final_total", sum(allocation.values()) == 500, f"sum={sum(allocation.values())}")
    add(checks, "request_total", sum(requests.values()) == 784, f"sum={sum(requests.values())}")
    add(checks, "guarantee_total", sum(guarantees.values()) == 392, f"sum={sum(guarantees.values())}")
    add(
        checks,
        "final_hard_constraints",
        all(guarantees[b] <= allocation[b] <= min(requests[b], caps[b]) for b in BRANCHES),
        "half-request, request and effective-cap bounds checked independently",
    )

    course_rows = read_csv(results / "<SOURCE_FILE_REDACTED>")
    advisory = defaultdict(int)
    raw_request_total = 0
    course_bound_ok = True
    for row in course_rows:
        branch = row["branch"]
        value = int(row["advisory_course_allocation"])
        reconciled = int(row["reconciled_course_request"])
        advisory[branch] += value
        raw_request_total += int(row["request_2006"])
        course_bound_ok = course_bound_ok and 0 <= value <= reconciled
    add(checks, "course_rows", len(course_rows) == 72, f"rows={len(course_rows)}")
    add(checks, "course_request_conflict_reproduced", raw_request_total == 750, f"course-row sum={raw_request_total}")
    add(checks, "advisory_course_bounds", course_bound_ok, "all advisory course allocations are within reconciled requests")
    add(checks, "advisory_branch_sums", dict(advisory) == allocation, f"advisory={dict(advisory)}")

    comparison = {row["candidate"]: row for row in read_csv(results / "<SOURCE_FILE_REDACTED>")}
    robust_p10 = float(comparison["robust_balanced"]["stress_p10_revenue_proxy"])
    baseline_p10 = float(comparison["baseline_proportional"]["stress_p10_revenue_proxy"])
    robust_cvar = float(comparison["robust_balanced"]["stress_cvar10_revenue_proxy"])
    expected_cvar = float(comparison["expected_greedy"]["stress_cvar10_revenue_proxy"])
    add(checks, "candidate_downside_order", robust_p10 >= baseline_p10 and robust_cvar >= expected_cvar, f"p10={robust_p10:.3f}, cvar10={robust_cvar:.3f}")

    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    add(checks, "summary_allocation", summary["main_branch_allocation"] == allocation, str(summary["main_branch_allocation"]))
    add(checks, "fixed_seed", summary["random_seed"] == 2006, f"seed={summary['random_seed']}")

    macro_text = (results / "generated_metrics.tex").read_text(encoding="utf-8")
    macro_ok = all(f"\\newcommand{{\\Alloc{MACROS[b]}}}{{{allocation[b]}}}" in macro_text for b in BRANCHES)
    macro_ok = macro_ok and "\\newcommand{\\TotalSupply}{500}" in macro_text and "\\newcommand{\\TotalRequest}{784}" in macro_text
    add(checks, "latex_macro_contract", macro_ok, "allocation and total macros compared with CSV")

    paper_md = root / "paper" / "paper.md"
    if paper_md.is_file():
        paper_text = paper_md.read_text(encoding="utf-8")
        paper_allocation = parse_machine_json(paper_text, "RESULT-ALLOCATION")
        paper_metrics = parse_machine_json(paper_text, "RESULT-METRICS")
        add(checks, "paper_allocation_contract", paper_allocation == allocation, str(paper_allocation))
        expected_metrics = {
            "total_supply": 500,
            "total_request": 784,
            "guaranteed_total": 392,
            "forecast_wape_pct": round(float(summary["key_metrics"]["forecast_wape_pct"]), 2),
        }
        add(checks, "paper_metrics_contract", paper_metrics == expected_metrics, str(paper_metrics))
    else:
        add(checks, "paper_allocation_contract", False, "paper/paper.md is missing")
        add(checks, "paper_metrics_contract", False, "paper/paper.md is missing")

    main_tex = root / "paper" / "main.tex"
    if main_tex.is_file():
        tex_text = main_tex.read_text(encoding="utf-8")
        add(
            checks,
            "latex_generated_inputs",
            "../results/generated_metrics.tex" in tex_text and "../results/generated_allocation_rows.tex" in tex_text,
            "main.tex must consume generated numerical artifacts",
        )
    else:
        add(checks, "latex_generated_inputs", False, "paper/main.tex is missing")

    figure_names = [
        "<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>", "<SOURCE_FILE_REDACTED>",
    ]
    bad_figures: list[str] = []
    for name in figure_names:
        path = root / "figures" / name
        if not path.is_file() or path.stat().st_size < 10_000 or path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            bad_figures.append(name)
    add(checks, "figure_contract", not bad_figures, f"bad={bad_figures}")

    determinism_path = results / "determinism_check.json"
    if determinism_path.is_file():
        determinism = json.loads(determinism_path.read_text(encoding="utf-8"))
        add(checks, "deterministic_rerun", determinism.get("status") == "pass", str(determinism.get("mismatches", [])))
    else:
        add(checks, "deterministic_rerun", None, "run code/check_determinism.py")

    pdf = root / "paper" / "<SOURCE_FILE_REDACTED>"
    add(checks, "latex_compilation", pdf.is_file() and pdf.stat().st_size > 10_000 if pdf.exists() else None, "<SOURCE_FILE_REDACTED> present" if pdf.exists() else "XeLaTeX compilation not yet available")

    status = "fail" if any(item["status"] == "fail" for item in checks) else ("needs_review" if any(item["status"] == "needs_review" for item in checks) else "pass")
    report = {"status": status, "checks": checks}
    output = results / "verification_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"verification_status={status}")
    print(f"report={output}")
    return 1 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
