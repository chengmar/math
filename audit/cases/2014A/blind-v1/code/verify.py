#!/usr/bin/env python3
"""Independent consistency and boundary verifier for generated solution files."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def status(ok: bool) -> str:
    return "pass" if ok else "fail"


def close(a: float, b: float, tol: float) -> bool:
    return abs(float(a) - float(b)) <= tol


def parse_macros(path: Path) -> dict[str, float]:
    text = path.read_text(encoding="utf-8")
    pairs = re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([-+0-9.]+)\}", text)
    return {key: float(value) for key, value in pairs}


def main() -> int:
    checks: dict[str, dict] = {}
    required = [
        ROOT / "problem-analysis.md", ROOT / "data-audit.md", ROOT / "assumptions.yaml",
        ROOT / "variables.yaml", ROOT / "model-selection.md", ROOT / "solution-report.yaml",
        ROOT / "reproducibility.yaml", ROOT / "code" / "solve.py",
        ROOT / "paper" / "main.tex", ROOT / "paper" / "paper.md",
        RESULTS / "summary.json", RESULTS / "<SOURCE_FILE_REDACTED>", RESULTS / "<SOURCE_FILE_REDACTED>",
        ROOT / "figures" / "<SOURCE_FILE_REDACTED>", ROOT / "figures" / "<SOURCE_FILE_REDACTED>",
        ROOT / "figures" / "<SOURCE_FILE_REDACTED>", ROOT / "figures" / "<SOURCE_FILE_REDACTED>",
    ]
    missing = [str(p.relative_to(ROOT)).replace("\\", "/") for p in required
               if not p.is_file() or p.stat().st_size == 0]
    checks["required_artifacts"] = {"status": status(not missing), "missing": missing}

    summary = load_json(RESULTS / "summary.json")
    report = load_json(ROOT / "solution-report.yaml")
    manifest = load_json(RESULTS / "manifest.json")

    bad_manifest = []
    for rel, expected in manifest["files"].items():
        path = ROOT / rel
        if not path.is_file() or sha256_file(path) != expected:
            bad_manifest.append(rel)
    checks["manifest_hashes"] = {"status": status(not bad_manifest), "mismatches": bad_manifest}

    repeat_path = RESULTS / "repeatability.json"
    if repeat_path.is_file():
        repeat = load_json(repeat_path)
        expected_now = repeat.get("execution_2_sha256", {})
        repeat_bad = [rel for rel, expected in expected_now.items()
                      if not (ROOT / rel).is_file() or sha256_file(ROOT / rel) != expected]
        repeat_ok = (repeat.get("status") == "pass"
                     and repeat.get("execution_1_sha256") == repeat.get("execution_2_sha256")
                     and not repeat_bad)
        checks["repeatability"] = {"status": status(repeat_ok), "mismatches": repeat_bad}
    else:
        checks["repeatability"] = {"status": "needs_review", "reason": "no repeated-run evidence file"}

    bad_inputs = []
    for rel, expected in summary["evidence"]["input_identity"]["files"].items():
        path = ROOT / rel
        if not path.is_file() or sha256_file(path) != expected:
            bad_inputs.append(rel)
    checks["input_hashes"] = {"status": status(not bad_inputs), "mismatches": bad_inputs}

    trajectory = read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    grouped: dict[str, list[dict]] = {}
    for row in trajectory:
        grouped.setdefault(row["stage"], []).append(row)
    expected_stages = ["main_deceleration", "rapid_adjustment", "coarse_avoidance",
                       "fine_avoidance", "slow_descent"]
    stage_presence = all(name in grouped for name in expected_stages)

    boundary_details = {}
    if stage_presence:
        main_end = grouped["main_deceleration"][-1]
        adjust_end = grouped["rapid_adjustment"][-1]
        coarse_end = grouped["coarse_avoidance"][-1]
        fine_end = grouped["fine_avoidance"][-1]
        slow_end = grouped["slow_descent"][-1]

        def speed(row: dict) -> float:
            return math.sqrt(float(row["east_or_tangential_speed_mps"])**2
                             + float(row["north_speed_mps"])**2
                             + float(row["vertical_speed_mps"])**2)

        def horizontal(row: dict) -> float:
            return math.hypot(float(row["east_or_tangential_speed_mps"]),
                              float(row["north_speed_mps"]))

        boundary_details = {
            "main_15km_to_3km_and_57mps": close(grouped["main_deceleration"][0]["height_m"], 15000, 1e-6)
                and close(main_end["height_m"], 3000, 1e-6) and close(speed(main_end), 57, 1e-6),
            "adjust_to_2p4km_horizontal_zero": close(adjust_end["height_m"], 2400, 1e-6)
                and horizontal(adjust_end) <= 1e-6,
            "coarse_hover_at_100m": close(coarse_end["height_m"], 100, 1e-6)
                and speed(coarse_end) <= 1e-6,
            "fine_at_30m_horizontal_zero": close(fine_end["height_m"], 30, 1e-6)
                and horizontal(fine_end) <= 1e-6,
            "slow_stop_at_4m": close(slow_end["height_m"], 4, 1e-6)
                and speed(slow_end) <= 1e-6,
        }
    checks["stage_boundaries"] = {
        "status": status(stage_presence and all(boundary_details.values())),
        "details": {k: status(v) for k, v in boundary_details.items()},
    }

    thrust = [float(r["thrust_N"]) for r in trajectory]
    masses = [float(r["mass_kg"]) for r in trajectory]
    heights_by_stage = [[float(r["height_m"]) for r in grouped.get(s, [])] for s in expected_stages]
    thrust_ok = min(thrust) >= 1500.0 - 1e-6 and max(thrust) <= 7500.0 + 1e-6
    mass_ok = all(b <= a + 1e-8 for a, b in zip(masses, masses[1:]))
    height_ok = all(all(b <= a + 1e-6 for a, b in zip(h, h[1:])) for h in heights_by_stage)
    checks["trajectory_invariants"] = {
        "status": status(thrust_ok and mass_ok and height_ok),
        "thrust_bounds": status(thrust_ok),
        "mass_monotone": status(mass_ok),
        "height_monotone_by_stage": status(height_ok),
        "observed_thrust_N": [min(thrust), max(thrust)],
    }

    reintegration = read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    reintegration_ok = len(reintegration) == 5 and all(r["status"] == "pass" for r in reintegration)
    checks["independent_reintegration"] = {
        "status": status(reintegration_ok),
        "max_position_error_m": max(float(r["max_abs_position_error_m"]) for r in reintegration),
        "max_velocity_error_mps": max(float(r["max_abs_velocity_error_mps"]) for r in reintegration),
    }

    parameter = read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    parameter_ok = bool(parameter) and all(r["problem_thrust_bounds_status"] == "pass" for r in parameter)
    dem_robust = read_csv(RESULTS / "<SOURCE_FILE_REDACTED>")
    dem_ok = bool(dem_robust) and all(r["hard_threshold_status"] == "pass" for r in dem_robust)
    checks["robustness"] = {
        "status": status(parameter_ok and dem_ok),
        "parameter_cases": status(parameter_ok),
        "dem_scale_weight_cases": status(dem_ok),
    }

    report_ok = (
        report["problem_coverage"]["question_1_orbit_points_and_velocities"] == "pass"
        and report["problem_coverage"]["question_2_six_stage_trajectory_and_control"] == "pass"
        and report["problem_coverage"]["question_3_error_and_sensitivity"] == "pass"
        and close(report["key_results"]["fuel_consumed_kg"], summary["metrics"]["fuel_consumed_kg"], 1e-9)
        and close(report["key_results"]["mass_at_4m_kg"], summary["metrics"]["final_mass_at_4m_kg"], 1e-9)
    )
    checks["solution_report_consistency"] = {"status": status(report_ok)}

    macros = parse_macros(ROOT / "paper" / "result_macros.tex")
    macro_expected = {
        "FuelMass": summary["metrics"]["fuel_consumed_kg"],
        "FinalMass": summary["metrics"]["final_mass_at_4m_kg"],
        "PoweredTime": summary["metrics"]["total_powered_time_s"],
        "PericenterSpeed": summary["orbit"]["pericenter"]["speed_mps"],
        "ApocenterSpeed": summary["orbit"]["apocenter"]["speed_mps"],
        "FineRow": summary["dem"]["fine"]["selected"]["row_one_based"],
        "FineColumn": summary["dem"]["fine"]["selected"]["column_one_based"],
    }
    macro_bad = [key for key, value in macro_expected.items()
                 if key not in macros or not close(macros[key], value, 0.005)]
    checks["paper_numeric_macros"] = {"status": status(not macro_bad), "mismatches": macro_bad}

    paper_text = ""
    for p in (ROOT / "paper" / "main.tex", ROOT / "paper" / "paper.md"):
        if p.is_file():
            paper_text += p.read_text(encoding="utf-8", errors="replace")
    placeholders = [token for token in ("TODO", "TBD", "请替换", "placeholder") if token in paper_text]
    checks["paper_placeholders"] = {"status": status(not placeholders), "found": placeholders}

    memory_text = (ROOT / "reports" / "training-memory-usage.md").read_text(encoding="utf-8")
    decisions = re.findall(r"decision:\s*(\w+)", memory_text)
    memory_ok = len(decisions) == 3 and all(d in {"adopt", "adapt", "reject"} for d in decisions)
    checks["training_memory_decisions"] = {"status": status(memory_ok), "decisions": decisions}

    paper_pdf = ROOT / "paper" / "<SOURCE_FILE_REDACTED>"
    checks["paper_compile"] = {
        "status": "pass" if paper_pdf.is_file() and paper_pdf.stat().st_size > 0 else "needs_review",
        "reason": "compiled PDF found" if paper_pdf.is_file() else "XeLaTeX compilation not yet evidenced",
    }
    checks["external_flight_validity"] = {
        "status": "needs_review",
        "reason": summary["evidence"]["external_flight_validity"]["reason"],
    }

    critical_failures = [name for name, item in checks.items() if item["status"] == "fail"]
    result = {
        "case_id": "2014A",
        "phase": "solve",
        "overall_status": "pass" if not critical_failures else "fail",
        "critical_failures": critical_failures,
        "checks": checks,
        "claim_scope": "internal reproducibility and model-consistency only; external validity remains needs_review",
    }
    (RESULTS / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    print(json.dumps({"overall_status": result["overall_status"],
                      "critical_failures": critical_failures}, ensure_ascii=False, indent=2))
    return 0 if not critical_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
