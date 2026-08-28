#!/usr/bin/env python3
"""Cross-check required deliverables, paper numbers, figures, and hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
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
    checks.append(
        {
            "name": name,
            "judgment": "pass" if passed else "fail",
            "detail": detail,
        }
    )


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
    values = frame.loc[
        (frame["scenario"] == scenario) & (frame["year"] == year), column
    ]
    if len(values) != 1:
        raise ValueError(f"Expected one result for {scenario=} {year=} {column=}")
    return float(values.iloc[0])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    checks: list[dict[str, Any]] = []
    results = workspace / "results"
    report_path = results / "consistency_check.json"

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
        "results/key_results.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/validation.json",
        "results/run_manifest.json",
        "results/reproducibility_check.json",
        "results/generated_numbers.tex",
    ]
    missing = [item for item in required if not (workspace / item).is_file()]
    add_check(checks, "required_deliverables", not missing, {"missing": missing})

    yaml_files = [
        workspace / "assumptions.yaml",
        workspace / "variables.yaml",
        workspace / "solution-report.yaml",
        workspace / "reproducibility.yaml",
    ]
    yaml_errors: dict[str, str] = {}
    yaml_objects: dict[str, Any] = {}
    for path in yaml_files:
        if not path.is_file():
            yaml_errors[path.name] = "missing"
            continue
        try:
            yaml_objects[path.name] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - diagnostic path
            yaml_errors[path.name] = str(exc)
    add_check(checks, "yaml_syntax", not yaml_errors, yaml_errors or "all YAML files parsed")

    judgment_errors: list[dict[str, Any]] = []
    for name, obj in yaml_objects.items():
        for location, judgment in collect_judgments(obj, name):
            if judgment not in ALLOWED_JUDGMENTS:
                judgment_errors.append({"location": location, "value": judgment})
    for json_name in ["data_quality.json", "model_comparison.json", "validation.json"]:
        path = results / json_name
        if path.is_file():
            obj = json.loads(path.read_text(encoding="utf-8"))
            for location, judgment in collect_judgments(obj, json_name):
                if judgment not in ALLOWED_JUDGMENTS:
                    judgment_errors.append({"location": location, "value": judgment})
    add_check(
        checks,
        "judgment_vocabulary",
        not judgment_errors,
        judgment_errors or sorted(ALLOWED_JUDGMENTS),
    )

    projections_path = results / "<SOURCE_FILE_REDACTED>"
    comparison_path = results / "<SOURCE_FILE_REDACTED>"
    validation_path = results / "validation.json"
    key_path = results / "key_results.json"
    if all(path.is_file() for path in [projections_path, comparison_path, validation_path, key_path]):
        projections = pd.read_csv(projections_path)
        comparison = pd.read_csv(comparison_path)
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        key = json.loads(key_path.read_text(encoding="utf-8"))

        best_model = str(comparison.sort_values("composite_score").iloc[0]["model_id"])
        add_check(
            checks,
            "selected_model_numeric_rank",
            best_model == "multistate_cohort",
            {"best_model": best_model},
        )
        math_pass = validation.get("mathematical_judgment") == "pass"
        context_disclosed = (
            validation.get("overall_judgment") == "needs_review"
            and validation.get("contextual_judgment") == "fail"
        )
        add_check(checks, "mathematical_validation", math_pass, validation.get("conditions"))
        add_check(
            checks,
            "context_disclosure",
            context_disclosed,
            {
                "overall": validation.get("overall_judgment"),
                "contextual": validation.get("contextual_judgment"),
            },
        )
        scenario_values = [
            value_at(projections, name, 2050, "population") for name in ("low", "medium", "high")
        ]
        add_check(
            checks,
            "scenario_ordering_2050",
            scenario_values[0] <= scenario_values[1] <= scenario_values[2],
            scenario_values,
        )

        medium = projections[projections["scenario"] == "medium"].copy()
        peaks = {
            scenario: projections[projections["scenario"] == scenario].loc[
                projections[projections["scenario"] == scenario]["population"].idxmax()
            ]
            for scenario in ("low", "medium", "high")
        }
        working_peak = medium.loc[medium["working_15_64_population"].idxmax()]
        dependency_min = medium.loc[medium["dependency_ratio"].idxmin()]
        context = validation["appendix_context_checks"]
        expected_macros: dict[str, int | float] = {
            "ImpliedBasePopulation": float(key["population_scale"]["implied_population_2005"]) / 1e8,
            "RawEffectiveTFR": float(key["raw_effective_tfr"]),
            "MigrationRatePercent": 100.0 * float(key["annual_rural_reclassification_rate"]),
            "MediumPeakYear": int(peaks["medium"]["year"]),
            "MediumPeakPopulation": float(peaks["medium"]["population"]) / 1e8,
            "LowPeakYear": int(peaks["low"]["year"]),
            "LowPeakPopulation": float(peaks["low"]["population"]) / 1e8,
            "HighPeakYear": int(peaks["high"]["year"]),
            "HighPeakPopulation": float(peaks["high"]["population"]) / 1e8,
            "MediumPopulationTwentyTwenty": value_at(projections, "medium", 2020, "population") / 1e8,
            "MediumPopulationTwentyThirty": value_at(projections, "medium", 2030, "population") / 1e8,
            "MediumPopulationTwentyFifty": value_at(projections, "medium", 2050, "population") / 1e8,
            "MediumPopulationTwentyOneHundred": value_at(projections, "medium", 2100, "population") / 1e8,
            "MediumOlderShareTwentyTwenty": 100.0
            * value_at(projections, "medium", 2020, "older_65_share"),
            "MediumOlderShareTwentyFifty": 100.0
            * value_at(projections, "medium", 2050, "older_65_share"),
            "MediumDependencyTwentyFifty": 100.0
            * value_at(projections, "medium", 2050, "dependency_ratio"),
            "MediumUrbanShareTwentyFifty": 100.0
            * value_at(projections, "medium", 2050, "urban_share"),
            "MediumSexRatioTwentyFifty": value_at(
                projections, "medium", 2050, "sex_ratio_male_per_100_female"
            ),
            "MediumWorkingPeakYear": int(working_peak["year"]),
            "MediumWorkingPeakPopulation": float(working_peak["working_15_64_population"]) / 1e8,
            "MediumDependencyMinimumYear": int(dependency_min["year"]),
            "MediumDependencyMinimum": 100.0 * float(dependency_min["dependency_ratio"]),
            "HoldoutSelectedScore": float(
                comparison.loc[comparison["model_id"] == "multistate_cohort", "composite_score"].iloc[0]
            ),
            "HoldoutBaselineScore": float(
                comparison.loc[comparison["model_id"] == "persistence_baseline", "composite_score"].iloc[0]
            ),
            "ContextTotalErrorTwentyTwenty": 100.0
            * float(context["total_population_2020"]["relative_error"]),
            "ContextOlderSixtyErrorTwentyTwenty": 100.0
            * float(context["older_60_population_2020"]["relative_error"]),
            "ContextOlderSixtyFiveErrorTwentyTwenty": 100.0
            * float(context["older_65_population_2020"]["relative_error"]),
            "MaximumConservationResidual": float(
                validation["max_absolute_conservation_residual_people"]
            ),
        }
        macro_text = (results / "generated_numbers.tex").read_text(encoding="utf-8")
        actual_macros = dict(
            re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([^}]*)\}", macro_text)
        )
        macro_errors = []
        for name, value in expected_macros.items():
            expected = str(value) if isinstance(value, int) else f"{float(value):.3f}"
            if actual_macros.get(name) != expected:
                macro_errors.append(
                    {"macro": name, "expected": expected, "actual": actual_macros.get(name)}
                )
        add_check(checks, "generated_tex_macros", not macro_errors, macro_errors or "all match")

        md_text = (workspace / "paper" / "paper.md").read_text(encoding="utf-8")
        md_snippets = [
            "2005 年人口为 13.239 亿",
            "在 2025 年达到 14.364 亿",
            "2050 年 13.225 亿",
            "2100 年 8.937 亿",
            "2020 年 15.35%",
            "2050 年 26.11%",
            "总抚养比为 68.71%",
            "分别相差 28.70% 和 33.48%",
            "综合误差为 4.985，低于基线的 7.515",
        ]
        md_missing = [snippet for snippet in md_snippets if snippet not in md_text]
        add_check(checks, "markdown_key_numbers", not md_missing, {"missing": md_missing})

        tex_text = (workspace / "paper" / "main.tex").read_text(encoding="utf-8")
        required_macro_refs = [
            "ImpliedBasePopulation",
            "MediumPeakYear",
            "MediumPeakPopulation",
            "MediumPopulationTwentyFifty",
            "MediumPopulationTwentyOneHundred",
            "MediumOlderShareTwentyFifty",
            "MediumDependencyTwentyFifty",
            "HoldoutSelectedScore",
            "ContextOlderSixtyFiveErrorTwentyTwenty",
        ]
        missing_refs = [name for name in required_macro_refs if f"\\{name}" not in tex_text]
        add_check(checks, "latex_macro_references", not missing_refs, {"missing": missing_refs})

    figure_names = [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]
    tex_text = (workspace / "paper" / "main.tex").read_text(encoding="utf-8") if (workspace / "paper" / "main.tex").is_file() else ""
    md_text = (workspace / "paper" / "paper.md").read_text(encoding="utf-8") if (workspace / "paper" / "paper.md").is_file() else ""
    figure_errors = []
    for name in figure_names:
        if not (workspace / "figures" / name).is_file():
            figure_errors.append({"figure": name, "problem": "file missing"})
        if name not in tex_text:
            figure_errors.append({"figure": name, "problem": "not referenced by main.tex"})
        if name not in md_text:
            figure_errors.append({"figure": name, "problem": "not referenced by paper.md"})
    add_check(checks, "figure_files_and_references", not figure_errors, figure_errors or "all seven")

    manifest_path = results / "run_manifest.json"
    manifest_errors = []
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = list(manifest.get("inputs", {}).values())
        records.append(manifest.get("code", {}))
        records.extend(manifest.get("outputs", []))
        for record in records:
            relative_path = record.get("relative_path")
            expected_hash = record.get("sha256")
            if not relative_path:
                manifest_errors.append({"record": record, "problem": "missing relative_path"})
                continue
            path = workspace / relative_path
            if not path.is_file():
                manifest_errors.append({"path": relative_path, "problem": "file missing"})
            elif sha256_file(path) != expected_hash:
                manifest_errors.append({"path": relative_path, "problem": "sha256 mismatch"})
    else:
        manifest_errors.append({"problem": "run_manifest.json missing"})
    add_check(checks, "manifest_hashes", not manifest_errors, manifest_errors or "all hashes match")

    overall = "pass" if all(check["judgment"] == "pass" for check in checks) else "fail"
    report = {"judgment": overall, "checks": checks}
    results.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"judgment": overall, "check_count": len(checks)}, sort_keys=True))
    return 0 if overall == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
