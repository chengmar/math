#!/usr/bin/env python3
"""Check required solve artifacts and paper/result consistency."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PAPER = ROOT / "paper"
ALLOWED_STATUSES = {"pass", "fail", "needs_review"}


def main() -> None:
    checks: list[dict[str, str]] = []

    def check(name: str, condition: bool, detail: str) -> None:
        checks.append(
            {"name": name, "status": "pass" if condition else "fail", "detail": detail}
        )

    required_files = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/extract_inputs.ps1",
        "code/analyze.py",
        "code/verify_outputs.py",
        "code/check_delivery.py",
        "code/run_all.ps1",
        "results/summary.json",
        "results/verification.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/paper.md",
        "paper/generated-results.tex",
    ]
    missing = [
        relative
        for relative in required_files
        if not (ROOT / relative).is_file() or (ROOT / relative).stat().st_size == 0
    ]
    check("required-artifacts", not missing, f"missing_or_empty={missing}")

    yaml_paths = [
        ROOT / "assumptions.yaml",
        ROOT / "variables.yaml",
        ROOT / "solution-report.yaml",
        ROOT / "reproducibility.yaml",
    ]
    documents: dict[str, Any] = {}
    yaml_errors: list[str] = []
    for path in yaml_paths:
        try:
            documents[path.name] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - reported as a check failure
            yaml_errors.append(f"{path.name}: {exc}")
    check("yaml-parse", not yaml_errors, f"errors={yaml_errors}")

    invalid_statuses: list[str] = []

    def walk_statuses(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if key == "status" or key.endswith("_status"):
                    if child not in ALLOWED_STATUSES:
                        invalid_statuses.append(f"{child_location}={child!r}")
                walk_statuses(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk_statuses(child, f"{location}[{index}]")

    for name, document in documents.items():
        walk_statuses(document, name)
    check("status-vocabulary", not invalid_statuses, f"invalid={invalid_statuses}")

    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    report = documents.get("solution-report.yaml", {})
    numerical_pairs = [
        (
            float(summary["question_1"]["equivalent_inertia_kg_m2"]),
            float(report["results"]["question_1"]["equivalent_inertia_kg_m2"]),
            "q1 inertia",
        ),
        (
            float(summary["question_3"]["motor_current_a"]),
            float(report["results"]["question_3"]["current_A"]),
            "q3 current",
        ),
        (
            float(summary["question_4"]["absolute_relative_energy_error_pct"]),
            float(report["results"]["question_4"]["absolute_relative_error_pct"]),
            "q4 energy error",
        ),
        (
            float(
                summary["questions_5_6"]["candidate_comparison"][1][
                    "absolute_relative_energy_error_pct"
                ]
            ),
            float(report["results"]["question_5"]["model_replay_energy_error_pct"]),
            "q5 replay error",
        ),
        (
            float(
                summary["questions_5_6"]["candidate_comparison"][2][
                    "absolute_relative_energy_error_pct"
                ]
            ),
            float(report["results"]["question_6"]["model_replay_energy_error_pct"]),
            "q6 replay error",
        ),
    ]
    mismatches = [
        f"{name}: summary={left}, report={right}"
        for left, right, name in numerical_pairs
        if not math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-10)
    ]
    check("solution-report-results", not mismatches, f"mismatches={mismatches}")

    markdown = (PAPER / "paper.md").read_text(encoding="utf-8")
    required_markdown_tokens = [
        "51.999",
        "174.69",
        "52.150",
        "49.242",
        "5.576%",
        "0.0516%",
        "0.000368%",
        "78.97%",
    ]
    absent_tokens = [token for token in required_markdown_tokens if token not in markdown]
    check("paper-md-result-tokens", not absent_tokens, f"absent={absent_tokens}")

    tex_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [PAPER / "main.tex", *sorted((PAPER / "sections").glob("*.tex"))]
    )
    expected_macros = [
        "QOneInertia",
        "QThreeCurrent",
        "QFourTargetEnergy",
        "QFourBenchEnergy",
        "QFourRelativeError",
        "QFiveReplayError",
        "QSixReplayError",
    ]
    absent_macros = [name for name in expected_macros if f"\\{name}" not in tex_sources]
    check("paper-tex-generated-macro-use", not absent_macros, f"absent={absent_macros}")

    placeholders = ["关键词一", "请替换", "模板文字", "题目名称（按匿名"]
    found_placeholders = [
        placeholder
        for placeholder in placeholders
        if placeholder in markdown or placeholder in tex_sources
    ]
    check("paper-template-placeholders", not found_placeholders, f"found={found_placeholders}")

    bad_qquads = re.findall(r"(?<!\\)qquad", tex_sources)
    check("paper-tex-control-sequences", not bad_qquads, f"bad_qquads={len(bad_qquads)}")

    pdf_path = PAPER / "build" / "<SOURCE_FILE_REDACTED>"
    pdf_valid = (
        pdf_path.is_file()
        and pdf_path.stat().st_size > 100_000
        and pdf_path.read_bytes()[:5] == b"%PDF-"
    )
    check(
        "compiled-paper-pdf",
        pdf_valid,
        f"bytes={pdf_path.stat().st_size if pdf_path.exists() else 0}",
    )
    freshness_inputs = [
        PAPER / "main.tex",
        PAPER / "preamble.tex",
        PAPER / "generated-results.tex",
        *sorted((PAPER / "sections").glob("*.tex")),
        *sorted((ROOT / "figures").glob("*.png")),
    ]
    latest_input_mtime = max(path.stat().st_mtime for path in freshness_inputs)
    pdf_fresh = pdf_path.exists() and pdf_path.stat().st_mtime >= latest_input_mtime
    check(
        "compiled-paper-freshness",
        pdf_fresh,
        f"pdf_mtime={pdf_path.stat().st_mtime if pdf_path.exists() else 0}; latest_input_mtime={latest_input_mtime}",
    )
    log_path = PAPER / "build" / "main.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    bad_log_markers = [
        marker
        for marker in ["Undefined control sequence", "LaTeX Error", "undefined references"]
        if marker.lower() in log_text.lower()
    ]
    check("paper-compile-log", not bad_log_markers, f"markers={bad_log_markers}")

    failures = [item for item in checks if item["status"] == "fail"]
    result = {
        "status": "pass" if not failures else "fail",
        "checks": checks,
        "official_format_rules_status": "needs_review",
        "official_format_rules_note": "The allowed template does not supply verified year-specific page and typography rules.",
        "physical_validation_status": "needs_review",
    }
    (RESULTS / "delivery-check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if failures:
        print(f"[fail] {len(failures)} delivery check(s) failed")
        sys.exit(1)
    print(f"[pass] {len(checks)} delivery and paper checks passed")


if __name__ == "__main__":
    main()
