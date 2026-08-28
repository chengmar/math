from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import pandas as pd
import yaml


REQUIRED_FILES = [
    "problem-analysis.md",
    "data-audit.md",
    "assumptions.yaml",
    "variables.yaml",
    "model-selection.md",
    "solution-report.yaml",
    "reproducibility.yaml",
    "code/extract_mdb.ps1",
    "code/run_all.ps1",
    "code/solve.py",
    "paper/main.tex",
    "paper/paper.md",
    "results/summary.json",
    "results/validation.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/tables/key-values.tex",
    "results/tables/flow-allocation.tex",
]


def close(left: float, right: float, tolerance: float = 1e-10) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0, abs_tol=tolerance)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    checks: dict[str, str] = {}

    checks["required_files_status"] = (
        "pass" if all((workspace / path).is_file() for path in REQUIRED_FILES) else "fail"
    )
    checks["required_directories_status"] = (
        "pass"
        if all((workspace / path).is_dir() for path in ["code", "results", "figures", "paper"])
        else "fail"
    )

    for filename in [
        "assumptions.yaml",
        "variables.yaml",
        "solution-report.yaml",
        "reproducibility.yaml",
    ]:
        try:
            yaml.safe_load((workspace / filename).read_text(encoding="utf-8"))
            checks[f"{filename}_parse_status"] = "pass"
        except Exception:
            checks[f"{filename}_parse_status"] = "fail"

    summary = json.loads((workspace / "results/summary.json").read_text(encoding="utf-8"))
    flow = pd.read_csv(workspace / "results/<SOURCE_FILE_REDACTED>")
    allocation = pd.read_csv(workspace / "results/<SOURCE_FILE_REDACTED>")

    checks["sample_count_status"] = "pass" if summary["sample_rows"] == 10_600 else "fail"
    checks["flow_share_status"] = "pass" if close(flow["footfall_share"].sum(), 1.0) else "fail"
    checks["transaction_share_status"] = (
        "pass" if close(flow["transaction_share"].sum(), 1.0) else "fail"
    )
    checks["revenue_share_status"] = "pass" if close(flow["revenue_share"].sum(), 1.0) else "fail"
    checks["flow_total_status"] = (
        "pass"
        if close(flow["footfall"].sum(), summary["flow"]["commercial_area_passages"], 1e-6)
        else "fail"
    )
    checks["allocation_totals_status"] = (
        "pass"
        if int(allocation["small_ms"].sum()) == summary["allocation"]["small_ms_total"]
        and int(allocation["large_ms"].sum()) == summary["allocation"]["large_ms_total"]
        and close(
            allocation["service_capacity"].sum(),
            summary["allocation"]["service_capacity_total"],
            1e-6,
        )
        else "fail"
    )
    checks["allocation_service_status"] = (
        "pass"
        if bool((allocation["service_capacity"] >= allocation["planning_checkout_demand"]).all())
        else "fail"
    )

    markdown = (workspace / "paper/paper.md").read_text(encoding="utf-8")
    markdown_tokens = [
        "10,600",
        "33.98%",
        "38.02%",
        "52.52%",
        "201.48",
        "116.00",
        "A9",
        "9.977%",
        "C1",
        "2.663%",
        "18 个小型",
        "25 个大型",
        "136,000",
        "324.70",
    ]
    checks["markdown_key_values_status"] = (
        "pass" if all(token in markdown for token in markdown_tokens) else "fail"
    )
    expected_rows = []
    merged = flow.merge(allocation[["zone", "small_ms", "large_ms"]], on="zone")
    for _, row in merged.iterrows():
        expected_rows.append(
            f"| {row['zone']} | {row['footfall']/10000:.3f} | "
            f"{100*row['footfall_share']:.3f} | "
            f"[{100*row['footfall_ci95_low']:.3f}, {100*row['footfall_ci95_high']:.3f}] | "
            f"{100*row['transaction_share']:.3f} | {int(row['small_ms'])} | {int(row['large_ms'])} |"
        )
    checks["markdown_zone_table_status"] = (
        "pass" if all(row in markdown for row in expected_rows) else "fail"
    )

    latex = (workspace / "paper/main.tex").read_text(encoding="utf-8")
    checks["latex_generated_values_status"] = (
        "pass"
        if "\\input{../results/tables/key-values}" in latex
        and "\\input{../results/tables/flow-allocation}" in latex
        else "fail"
    )
    checks["latex_placeholder_status"] = (
        "pass"
        if not any(token in latex for token in ["题目名称（按匿名", "模板文字", "关键词一"])
        else "fail"
    )
    figure_names = [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]
    checks["figure_status"] = (
        "pass" if all((workspace / "figures" / name).is_file() for name in figure_names) else "fail"
    )

    manifest_status = "pass"
    manifest = workspace / "results/checksums.sha256"
    try:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", maxsplit=1)
            target = (workspace / relative).resolve()
            target.relative_to(workspace)
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected:
                manifest_status = "fail"
                break
    except Exception:
        manifest_status = "fail"
    checks["checksum_manifest_status"] = manifest_status

    overall = all(value == "pass" for value in checks.values())
    report = {
        "status": "pass" if overall else "fail",
        "checks": checks,
        "mathematical_correctness_status": "needs_review",
        "competition_format_status": "needs_review",
    }
    output = workspace / "results/paper-consistency.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[{report['status']}] Paper-result consistency check completed.")
    if not overall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
