#!/usr/bin/env python3
"""Cross-check revised deliverables, formulas, judgments, figures, and hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ALLOWED_JUDGMENTS = {"pass", "fail", "needs_review"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: Any) -> None:
    checks.append({"name": name, "judgment": "pass" if passed else "fail", "detail": detail})


def collect_judgments(value: Any, location: str = "root") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key == "judgment" or key.endswith("_judgment"):
                found.append((child_location, child))
            found.extend(collect_judgments(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(collect_judgments(child, f"{location}[{index}]"))
    return found


def value_at(frame: pd.DataFrame, scenario: str, year: int, column: str) -> float:
    values = frame.loc[(frame["scenario"] == scenario) & (frame["year"] == year), column]
    if len(values) != 1:
        raise ValueError(f"Expected one result for {scenario=} {year=} {column=}")
    return float(values.iloc[0])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results = workspace / "results"
    checks: list[dict[str, Any]] = []

    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/convert_inputs.ps1",
        "code/solve_population.py",
        "code/check_consistency.py",
        "code/verify_reproducibility.ps1",
        "paper/main.tex",
        "paper/<SOURCE_FILE_REDACTED>",
        "paper/paper.md",
        "results/data_quality.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/model_comparison.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/key_results.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/sensitivity_summary.json",
        "results/validation.json",
        "results/run_manifest.json",
        "results/reproducibility_check.json",
        "results/generated_numbers.tex",
    ]
    missing = [relative for relative in required if not (workspace / relative).is_file()]
    add_check(checks, "required_deliverables", not missing, {"missing": missing})

    yaml_names = ["assumptions.yaml", "variables.yaml", "solution-report.yaml", "reproducibility.yaml"]
    yaml_objects: dict[str, Any] = {}
    yaml_errors: dict[str, str] = {}
    for name in yaml_names:
        path = workspace / name
        try:
            yaml_objects[name] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            yaml_errors[name] = str(exc)
    add_check(checks, "yaml_syntax", not yaml_errors, yaml_errors or "all YAML files parsed")

    json_names = [
        "data_quality.json",
        "model_comparison.json",
        "key_results.json",
        "sensitivity_summary.json",
        "validation.json",
        "run_manifest.json",
        "reproducibility_check.json",
    ]
    json_objects: dict[str, Any] = {}
    json_errors: dict[str, str] = {}
    for name in json_names:
        path = results / name
        try:
            json_objects[name] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            json_errors[name] = str(exc)
    add_check(checks, "json_syntax", not json_errors, json_errors or "all JSON files parsed")

    judgment_errors: list[dict[str, Any]] = []
    for name, obj in {**yaml_objects, **json_objects}.items():
        for location, judgment in collect_judgments(obj, name):
            if judgment not in ALLOWED_JUDGMENTS:
                judgment_errors.append({"location": location, "value": judgment})
    add_check(
        checks,
        "judgment_vocabulary",
        not judgment_errors,
        judgment_errors or sorted(ALLOWED_JUDGMENTS),
    )

    core_paths = [
        results / "<SOURCE_FILE_REDACTED>",
        results / "<SOURCE_FILE_REDACTED>",
        results / "<SOURCE_FILE_REDACTED>",
        results / "<SOURCE_FILE_REDACTED>",
        results / "<SOURCE_FILE_REDACTED>",
    ]
    if all(path.is_file() for path in core_paths) and not json_errors:
        projections = pd.read_csv(core_paths[0])
        comparison = pd.read_csv(core_paths[1])
        rolling = pd.read_csv(core_paths[2])
        factorial = pd.read_csv(core_paths[3])
        additional = pd.read_csv(core_paths[4])
        validation = json_objects["validation.json"]
        model_meta = json_objects["model_comparison.json"]
        sensitivity_meta = json_objects["sensitivity_summary.json"]
        key = json_objects["key_results.json"]

        best_mean_model = str(comparison.sort_values("composite_score").iloc[0]["model_id"])
        add_check(
            checks,
            "selected_model_mean_numeric_rank",
            best_mean_model == "multistate_cohort",
            {"best_mean_model": best_mean_model},
        )
        winners = rolling.loc[rolling["fold_rank"] == 1, "model_id"].tolist()
        selection_disclosed = (
            model_meta.get("selection_judgment") == "needs_review"
            and model_meta.get("ranking_stability_judgment") == "needs_review"
            and model_meta.get("multi_step_national_validation_judgment") == "needs_review"
            and len(set(winners)) > 1
        )
        add_check(
            checks,
            "rolling_selection_disclosure",
            selection_disclosed,
            {"fold_winners": winners, "selection": model_meta.get("selection_judgment")},
        )

        judgment_layers_ok = (
            validation.get("execution_judgment") == "pass"
            and validation.get("internal_invariants_judgment") == "pass"
            and validation.get("scientific_validation_judgment") == "needs_review"
            and validation.get("overall_judgment") == "needs_review"
            and validation.get("contextual_judgment") == "fail"
        )
        add_check(
            checks,
            "validation_judgment_layers",
            judgment_layers_ok,
            {
                key_name: validation.get(key_name)
                for key_name in [
                    "execution_judgment",
                    "internal_invariants_judgment",
                    "scientific_validation_judgment",
                    "overall_judgment",
                    "contextual_judgment",
                ]
            },
        )
        add_check(
            checks,
            "internal_invariants",
            all(value == "pass" for value in validation.get("internal_invariants", {}).values()),
            validation.get("internal_invariants"),
        )
        definition_checks = validation.get("scientific_definition_checks", {})
        add_check(
            checks,
            "scientific_definition_checks",
            bool(definition_checks) and all(value == "pass" for value in definition_checks.values()),
            definition_checks,
        )

        active = projections[projections["year"] <= 2035]
        target_error = float(
            (active["national_standard_tfr"] - active["target_standard_tfr"]).abs().max()
        )
        post_active_count = int(
            projections.loc[projections["year"] > 2035, "tfr_target_active"].astype(bool).sum()
        )
        add_check(
            checks,
            "standard_tfr_policy",
            target_error < 1e-12 and post_active_count == 0,
            {"maximum_active_error": target_error, "post_2035_active_count": post_active_count},
        )

        factorial_ok = (
            len(factorial) == 54
            and (factorial["judgment"] == "pass").all()
            and sensitivity_meta.get("judgment") == "pass"
            and int(sensitivity_meta.get("observed_case_count", -1)) == 54
        )
        add_check(
            checks,
            "factorial_sensitivity_54_cases",
            factorial_ok,
            {
                "rows": len(factorial),
                "failed_rows": int((factorial["judgment"] != "pass").sum()),
                "summary": sensitivity_meta.get("judgment"),
            },
        )
        add_check(
            checks,
            "additional_sensitivity",
            len(additional) >= 16 and (additional["judgment"] == "pass").all(),
            {"rows": len(additional), "failed_rows": int((additional["judgment"] != "pass").sum())},
        )

        scenario_values = [value_at(projections, name, 2050, "population") for name in ("low", "medium", "high")]
        add_check(
            checks,
            "scenario_ordering_2050",
            scenario_values[0] <= scenario_values[1] <= scenario_values[2],
            scenario_values,
        )

        medium = projections[projections["scenario"] == "medium"]
        peaks = {
            scenario: projections[projections["scenario"] == scenario].loc[
                projections[projections["scenario"] == scenario]["population"].idxmax()
            ]
            for scenario in ("low", "medium", "high")
        }
        expected_macros: dict[str, int | float] = {
            "ImpliedBasePopulation": float(key["population_scale"]["implied_population_2005"]) / 1e8,
            "RawStandardTFR": float(key["fertility_definition"]["national_standard_tfr_raw_2005"]),
            "RawLegacyWeightedTFR": float(key["fertility_definition"]["legacy_area_weighted_tfr_raw_2005"]),
            "MediumPeakYear": int(peaks["medium"]["year"]),
            "MediumPeakPopulation": float(peaks["medium"]["population"]) / 1e8,
            "MediumPopulationTwentyFifty": value_at(projections, "medium", 2050, "population") / 1e8,
            "MediumPopulationTwentyOneHundred": value_at(projections, "medium", 2100, "population") / 1e8,
            "RollingSelectedMeanScore": float(
                comparison.loc[comparison["model_id"] == "multistate_cohort", "composite_score"].iloc[0]
            ),
            "FactorialMaximumPeakYear": int(sensitivity_meta["maximum_case"]["peak_year"]),
            "FactorialMaximumPeakPopulation": float(sensitivity_meta["maximum_case"]["peak_population"]) / 1e8,
            "FactorialMaximumPopulationTwentyOneHundred": float(
                sensitivity_meta["maximum_case"]["population_2100"]
            )
            / 1e8,
            "FactorialMinimumPopulationTwentyOneHundred": float(
                sensitivity_meta["minimum_2100_case"]["population_2100"]
            )
            / 1e8,
            "FactorialPeakOverFifteenCount": int(
                sensitivity_meta["peak_over_1_5_billion_count"]
            ),
            "ExposureCityMaleAgeTwenty": float(
                validation["exposure_denominator_regression"]["calculated_exposure"]
            ),
        }
        macro_text = (results / "generated_numbers.tex").read_text(encoding="utf-8")
        actual_macros = dict(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}", macro_text))
        macro_errors: list[dict[str, Any]] = []
        for name, value in expected_macros.items():
            expected = str(value) if isinstance(value, int) else f"{float(value):.3f}"
            if actual_macros.get(name) != expected:
                macro_errors.append({"macro": name, "expected": expected, "actual": actual_macros.get(name)})
        add_check(checks, "generated_tex_macros", not macro_errors, macro_errors or "critical macros match")

        md_text = (workspace / "paper" / "paper.md").read_text(encoding="utf-8")
        numeric_snippets = [
            f"{float(key['population_scale']['implied_population_2005']) / 1e8:.3f}",
            f"{float(peaks['medium']['population']) / 1e8:.3f}",
            f"{value_at(projections, 'medium', 2050, 'population') / 1e8:.3f}",
            f"{value_at(projections, 'medium', 2100, 'population') / 1e8:.3f}",
            f"{float(sensitivity_meta['maximum_case']['peak_population']) / 1e8:.3f}",
            f"{float(sensitivity_meta['minimum_2100_case']['population_2100']) / 1e8:.3f}",
            f"{float(validation['exposure_denominator_regression']['calculated_exposure']):.3f}",
            "needs_review",
        ]
        missing_numbers = [snippet for snippet in numeric_snippets if snippet not in md_text]
        add_check(checks, "markdown_key_numbers", not missing_numbers, {"missing": missing_numbers})

    tex_path = workspace / "paper" / "main.tex"
    md_path = workspace / "paper" / "paper.md"
    tex_text = tex_path.read_text(encoding="utf-8") if tex_path.is_file() else ""
    md_text = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    bad_controls = [
        {"index": index, "codepoint": ord(character)}
        for index, character in enumerate(md_text)
        if ord(character) < 32 and character not in {"\n", "\r"}
    ]
    add_check(checks, "markdown_c0_control_characters", not bad_controls, bad_controls[:20])
    delimiter_ok = (
        md_text.count("$") % 2 == 0
        and md_text.count("\\[") == md_text.count("\\]")
        and md_text.count("\\(") == md_text.count("\\)")
    )
    add_check(
        checks,
        "markdown_formula_delimiters",
        delimiter_ok,
        {
            "dollar_count": md_text.count("$"),
            "display_open": md_text.count("\\["),
            "display_close": md_text.count("\\]"),
        },
    )
    tex_formula_snippets = [
        "T_t^{\\mathrm{nat}}",
        "\\sum_r n_{r,f,a,t}f_{r,a}",
        "S_{r,t}=S_{r,m,t}+S_{r,f,t}",
        "R_t^B=100\\frac{\\sum_r B_{r,m,t}}{\\sum_r B_{r,f,t}}",
        "n_{r,s,90,t+1}",
        "n_{h,s,a,t+1}",
    ]
    md_formula_snippets = [
        "T_t^{\\mathrm{nat}}",
        "S_{r,t}=S_{r,m,t}+S_{r,f,t}",
        "R_t^B=100\\frac{\\sum_r B_{r,m,t}}{\\sum_r B_{r,f,t}}",
        "n_{r,s,90,t+1}",
    ]
    missing_tex_formulas = [snippet for snippet in tex_formula_snippets if snippet not in tex_text]
    missing_md_formulas = [snippet for snippet in md_formula_snippets if snippet not in md_text]
    add_check(
        checks,
        "critical_formula_text",
        not missing_tex_formulas and not missing_md_formulas,
        {"latex_missing": missing_tex_formulas, "markdown_missing": missing_md_formulas},
    )

    figure_names = [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]
    figure_errors: list[dict[str, str]] = []
    for name in figure_names:
        if not (workspace / "figures" / name).is_file():
            figure_errors.append({"figure": name, "problem": "file missing"})
        if name not in tex_text:
            figure_errors.append({"figure": name, "problem": "not referenced by main.tex"})
        if name not in md_text:
            figure_errors.append({"figure": name, "problem": "not referenced by paper.md"})
    add_check(checks, "figure_files_and_references", not figure_errors, figure_errors or "all seven")

    manifest_errors: list[dict[str, Any]] = []
    manifest = json_objects.get("run_manifest.json", {})
    records = list(manifest.get("inputs", {}).values())
    records.append(manifest.get("code", {}))
    records.extend(manifest.get("outputs", []))
    for record in records:
        relative_path = record.get("relative_path")
        if not relative_path:
            manifest_errors.append({"record": record, "problem": "missing relative_path"})
            continue
        path = (workspace / relative_path).resolve()
        try:
            path.relative_to(workspace)
        except ValueError:
            manifest_errors.append({"path": relative_path, "problem": "path escapes workspace"})
            continue
        if not path.is_file():
            manifest_errors.append({"path": relative_path, "problem": "file missing"})
        elif sha256_file(path) != record.get("sha256"):
            manifest_errors.append({"path": relative_path, "problem": "sha256 mismatch"})
        elif "bytes" in record and path.stat().st_size != int(record["bytes"]):
            manifest_errors.append({"path": relative_path, "problem": "byte count mismatch"})
    add_check(checks, "manifest_hashes", not manifest_errors, manifest_errors or "all hashes match")

    validation = json_objects.get("validation.json", {})
    manifest_semantics_ok = (
        manifest.get("judgment") == validation.get("overall_judgment")
        and manifest.get("execution_judgment") == validation.get("execution_judgment")
        and manifest.get("internal_invariants_judgment")
        == validation.get("internal_invariants_judgment")
        and manifest.get("scientific_validation_judgment")
        == validation.get("scientific_validation_judgment")
    )
    add_check(
        checks,
        "manifest_judgment_semantics",
        manifest_semantics_ok,
        {
            "manifest": {
                key: manifest.get(key)
                for key in [
                    "judgment",
                    "execution_judgment",
                    "internal_invariants_judgment",
                    "scientific_validation_judgment",
                ]
            },
            "validation_overall": validation.get("overall_judgment"),
        },
    )

    pdf_path = workspace / "paper" / "<SOURCE_FILE_REDACTED>"
    reproduction = yaml_objects.get("reproducibility.yaml", {}) or {}
    latex_record = reproduction.get("latex", {}) if isinstance(reproduction, dict) else {}
    pdf_record_ok = (
        pdf_path.is_file()
        and latex_record.get("pdf_sha256") == sha256_file(pdf_path)
        and int(latex_record.get("pdf_bytes", -1)) == pdf_path.stat().st_size
        and latex_record.get("record_match_judgment") == "pass"
    )
    add_check(
        checks,
        "pdf_recorded_hash",
        pdf_record_ok,
        {
            "recorded_sha256": latex_record.get("pdf_sha256"),
            "actual_sha256": sha256_file(pdf_path) if pdf_path.is_file() else None,
            "recorded_bytes": latex_record.get("pdf_bytes"),
            "actual_bytes": pdf_path.stat().st_size if pdf_path.is_file() else None,
        },
    )

    solution_report = yaml_objects.get("solution-report.yaml", {}) or {}
    report_semantics_ok = (
        solution_report.get("overall_judgment") == validation.get("overall_judgment")
        and solution_report.get("scope", {}).get("frozen") is False
        and solution_report.get("scope", {}).get("audited") is False
    )
    add_check(
        checks,
        "solution_report_semantics",
        report_semantics_ok,
        {
            "overall": solution_report.get("overall_judgment"),
            "frozen": solution_report.get("scope", {}).get("frozen"),
            "audited": solution_report.get("scope", {}).get("audited"),
        },
    )

    overall = "pass" if all(check["judgment"] == "pass" for check in checks) else "fail"
    report = {"judgment": overall, "checks": checks}
    report_path = results / "consistency_check.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"judgment": overall, "check_count": len(checks)}, sort_keys=True))
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
