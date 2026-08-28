from __future__ import annotations

import argparse
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
    "revision-response.md",
    "code/extract_mdb.ps1",
    "code/run_all.ps1",
    "code/solve.py",
    "code/independent_verify.py",
    "code/check_consistency.py",
    "code/artifact_manifest.py",
    "paper/main.tex",
    "paper/paper.md",
    "paper/<SOURCE_FILE_REDACTED>",
    "results/summary.json",
    "results/validation.json",
    "results/independent-verification.json",
    "results/consecutive-rerun.json",
    "results/<SOURCE_FILE_REDACTED>",
    "results/<SOURCE_FILE_REDACTED>",
    "results/tables/key-values.tex",
    "results/tables/flow-allocation.tex",
]


def close(left: float, right: float, tolerance: float = 1e-10) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0, abs_tol=tolerance)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check paper/result consistency before manifest creation.")
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

    parsed_yaml: dict[str, object] = {}
    for filename in [
        "assumptions.yaml",
        "variables.yaml",
        "solution-report.yaml",
        "reproducibility.yaml",
    ]:
        try:
            parsed_yaml[filename] = yaml.safe_load((workspace / filename).read_text(encoding="utf-8"))
            checks[f"{filename}_parse_status"] = "pass"
        except Exception:
            checks[f"{filename}_parse_status"] = "fail"

    solution_report = parsed_yaml.get("solution-report.yaml", {})
    if isinstance(solution_report, dict):
        checks["revision_phase_metadata_status"] = (
            "pass" if solution_report.get("phase") == "blind-revision" else "fail"
        )
        checks["truthful_unfrozen_metadata_status"] = (
            "pass" if solution_report.get("frozen") is False else "fail"
        )
        checks["blind_scope_metadata_status"] = (
            "pass"
            if solution_report.get("network_search_performed") is False
            and solution_report.get("reference_solution_read") is False
            and solution_report.get("other_stage_called") is False
            else "fail"
        )
    else:
        checks["revision_phase_metadata_status"] = "fail"
        checks["truthful_unfrozen_metadata_status"] = "fail"
        checks["blind_scope_metadata_status"] = "fail"

    summary = json.loads((workspace / "results/summary.json").read_text(encoding="utf-8"))
    validation = json.loads((workspace / "results/validation.json").read_text(encoding="utf-8"))
    independent = json.loads(
        (workspace / "results/independent-verification.json").read_text(encoding="utf-8")
    )
    rerun = json.loads((workspace / "results/consecutive-rerun.json").read_text(encoding="utf-8"))
    flow = pd.read_csv(workspace / "results/<SOURCE_FILE_REDACTED>")
    allocation = pd.read_csv(workspace / "results/<SOURCE_FILE_REDACTED>")

    checks["main_summary_status"] = "pass" if summary.get("status") == "pass" else "fail"
    checks["independent_verification_status"] = (
        "pass" if independent.get("status") == "pass" else "fail"
    )
    checks["consecutive_rerun_status"] = "pass" if rerun.get("status") == "pass" else "fail"
    checks["validation_status_domain"] = (
        "pass"
        if all(value in {"pass", "fail", "needs_review"} for key, value in validation.items() if key.endswith("status"))
        else "fail"
    )

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
    pdf = workspace / "paper/<SOURCE_FILE_REDACTED>"
    checks["pdf_build_status"] = (
        "pass" if pdf.is_file() and pdf.stat().st_size > 1_000 and pdf.read_bytes()[:5] == b"%PDF-" else "fail"
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

    overall = all(value == "pass" for value in checks.values())
    report = {
        "status": "pass" if overall else "fail",
        "scope": "核对修订元数据、结构化结果、独立验证、连续重跑、论文核心数字、20 区方案表、图与 PDF；清单在本检查通过后生成。",
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
