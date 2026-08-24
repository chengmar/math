#!/usr/bin/env python3
"""Check required artifacts and paper-to-result numerical consistency."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_STATUSES = {"pass", "fail", "needs_review"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8-sig"))


def collect_status_errors(value, path="root"):
    errors = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if (key == "status" or key.endswith("_status")) and isinstance(child, str):
                if child not in ALLOWED_STATUSES:
                    errors.append(f"{child_path}={child!r}")
            errors.extend(collect_status_errors(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(collect_status_errors(child, f"{path}[{index}]") )
    return errors


def main() -> int:
    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/extract_problem.ps1",
        "code/solve.py",
        "code/check_consistency.py",
        "results/main_results.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/model_metrics.json",
        "results/validation.json",
        "results/sensitivity.json",
        "results/generated_numbers.tex",
        "results/center_table.tex",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/paper.md",
    ]
    checks = {}
    missing = [relative for relative in required if not (ROOT / relative).is_file()]
    empty = [relative for relative in required if (ROOT / relative).is_file() and (ROOT / relative).stat().st_size == 0]
    checks["required_artifacts_nonempty"] = "pass" if not missing and not empty else "fail"

    source = load_json("results/source_metadata.json")
    source_doc = ROOT / "input/problem/<SOURCE_FILE_REDACTED>"
    source_image = ROOT / "working/source-extract/docx-unpacked/word/media/<SOURCE_FILE_REDACTED>"
    checks["source_doc_hash"] = "pass" if sha256(source_doc) == source["source_doc_sha256"] else "fail"
    checks["source_image_hash"] = "pass" if sha256(source_image) == source["extracted_image_sha256"] else "fail"

    main_results = load_json("results/main_results.json")
    model_metrics = load_json("results/model_metrics.json")
    validation = load_json("results/validation.json")
    paper_md = (ROOT / "paper/paper.md").read_text(encoding="utf-8")
    paper_tex = (ROOT / "paper/main.tex").read_text(encoding="utf-8")
    macros = (ROOT / "results/generated_numbers.tex").read_text(encoding="utf-8")

    expected_md = [
        f"{main_results['main_metrics']['edge']['overall']['rmse_px']:.3f}",
        f"{main_results['validation']['mean_edge_rmse_px']:.3f}",
        f"{main_results['sensitivity']['threshold_max_shift_px']:.3f}",
        f"{main_results['sensitivity']['monte_carlo_max_radial_p95_px']:.3f}",
        f"{model_metrics['baseline']['leave_one_circle_out_mean_center_error_px']:.3f}",
        f"{validation['leave_one_circle_out']['calibrated_pose']['summary']['min_silhouette_iou']:.4f}",
    ]
    for row in main_results["centers"]:
        expected_md.extend(
            [
                f"{row['u_px']:.3f}",
                f"{row['v_px']:.3f}",
                f"{row['camera_x_px']:.3f}",
                f"{row['camera_y_px']:.3f}",
            ]
        )
    checks["paper_markdown_numbers"] = "pass" if all(token in paper_md for token in expected_md) else "fail"

    expected_macros = {
        "MainEdgeRMSE": f"{main_results['main_metrics']['edge']['overall']['rmse_px']:.3f}",
        "CVMeanRMSE": f"{main_results['validation']['mean_edge_rmse_px']:.3f}",
        "ThresholdMaxShift": f"{main_results['sensitivity']['threshold_max_shift_px']:.3f}",
        "MonteCarloMaxPctl": f"{main_results['sensitivity']['monte_carlo_max_radial_p95_px']:.3f}",
    }
    macro_ok = True
    for name, expected in expected_macros.items():
        match = re.search(rf"\\newcommand\{{\\{name}\}}\{{([^}}]+)\}}", macros)
        macro_ok &= bool(match and match.group(1) == expected)
    macro_ok &= "\\input{../results/generated_numbers.tex}" in paper_tex
    macro_ok &= "\\input{../results/center_table.tex}" in (ROOT / "paper/sections/05-results-validation.tex").read_text(encoding="utf-8")
    checks["paper_latex_generated_numbers"] = "pass" if macro_ok else "fail"

    placeholders = ("请替换", "关键词一", "题目名称（按匿名竞赛规则填写）")
    checks["paper_placeholders_removed"] = "pass" if not any(token in paper_md + paper_tex for token in placeholders) else "fail"

    figure_ok = True
    for relative in (
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
    ):
        try:
            with Image.open(ROOT / relative) as image:
                image.verify()
        except Exception:
            figure_ok = False
    checks["figures_decode"] = "pass" if figure_ok else "fail"

    yaml_documents = {}
    for relative in ("assumptions.yaml", "variables.yaml", "solution-report.yaml", "reproducibility.yaml"):
        yaml_documents[relative] = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
    status_errors = []
    for name, document in yaml_documents.items():
        status_errors.extend(collect_status_errors(document, name))
    checks["status_vocabulary"] = "pass" if not status_errors else "fail"

    result_checks = validation["checks"]
    checks["numerical_validation_checks"] = "pass" if all(value == "pass" for value in result_checks.values()) else "fail"
    checks["formal_mathematical_proof"] = "needs_review"

    decisive = [value for key, value in checks.items() if key != "formal_mathematical_proof"]
    overall = "pass" if all(value == "pass" for value in decisive) else "fail"
    report = {
        "status": overall,
        "checks": checks,
        "missing": missing,
        "empty": empty,
        "status_errors": status_errors,
    }
    output = ROOT / "results/paper_consistency.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
