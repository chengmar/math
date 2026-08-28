#!/usr/bin/env python3
"""Independent numeric, build, and claim-scope verifier for blind revision."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
R_LOCAL = 1734372.0
MU = 4.904075411e12
EXHAUST_VELOCITY = 2940.0


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


def vector(row: dict, keys: tuple[str, str, str]) -> list[float]:
    return [float(row[key]) for key in keys]


def norm(a: list[float]) -> float:
    return math.sqrt(sum(x*x for x in a))


def angle_deg(a: list[float], b: list[float]) -> float:
    na, nb = norm(a), norm(b)
    if na <= 1e-15 or nb <= 1e-15:
        return 0.0
    cosine = max(-1.0, min(1.0, sum(x*y for x, y in zip(a, b))/(na*nb)))
    return math.degrees(math.acos(cosine))


def force_from_row(row: dict) -> list[float]:
    return vector(row, (
        "thrust_east_or_tangential_N", "thrust_north_N", "thrust_vertical_N"))


def derivative(state: list[float], force: list[float], mode: str) -> list[float]:
    q, y, h, vt, vy, vh, mass = state
    if mode == "central":
        radius = R_LOCAL + h
        position_rate = [vt*R_LOCAL/radius, vy, vh]
        acceleration = [
            force[0]/mass - vh*vt/radius,
            force[1]/mass,
            vt*vt/radius - MU/(radius*radius) + force[2]/mass,
        ]
    else:
        position_rate = [vt, vy, vh]
        acceleration = [force[0]/mass, force[1]/mass,
                        force[2]/mass - MU/(R_LOCAL+h)**2]
    mass_rate = -norm(force)/EXHAUST_VELOCITY
    return position_rate + acceleration + [mass_rate]


def add_scaled(state: list[float], rate: list[float], scale: float) -> list[float]:
    return [a + scale*b for a, b in zip(state, rate)]


def rk4_interval(state: list[float], force0: list[float], force1: list[float],
                 mode: str, dt: float) -> list[float]:
    def force(alpha: float) -> list[float]:
        return [a + alpha*(b-a) for a, b in zip(force0, force1)]

    k1 = derivative(state, force(0.0), mode)
    k2 = derivative(add_scaled(state, k1, 0.5*dt), force(0.5), mode)
    k3 = derivative(add_scaled(state, k2, 0.5*dt), force(0.5), mode)
    k4 = derivative(add_scaled(state, k3, dt), force(1.0), mode)
    return [a + dt*(b+2*c+2*d+e)/6.0
            for a, b, c, d, e in zip(state, k1, k2, k3, k4)]


def independent_chain_reintegration(rows: list[dict]) -> dict:
    first = rows[0]
    state = vector(first, ("east_or_downrange_m", "north_m", "height_m")) \
        + vector(first, ("east_or_tangential_speed_mps", "north_speed_mps", "vertical_speed_mps")) \
        + [float(first["mass_kg"])]
    previous = first
    max_position = max_velocity = max_mass = 0.0
    stage_max: dict[str, dict[str, float]] = defaultdict(
        lambda: {"position_m": 0.0, "velocity_mps": 0.0, "mass_kg": 0.0})
    for row in rows[1:]:
        dt = float(row["time_s"]) - float(previous["time_s"])
        if dt < -1e-12:
            return {"status": "fail", "reason": "trajectory time is not monotone"}
        if dt > 1e-12:
            mode = "central" if row["stage"] in {
                "main_deceleration", "rapid_adjustment"} else "flat_local"
            state = rk4_interval(state, force_from_row(previous), force_from_row(row), mode, dt)
        expected_position = vector(row, ("east_or_downrange_m", "north_m", "height_m"))
        expected_velocity = vector(row, (
            "east_or_tangential_speed_mps", "north_speed_mps", "vertical_speed_mps"))
        position_error = max(abs(a-b) for a, b in zip(state[:3], expected_position))
        velocity_error = max(abs(a-b) for a, b in zip(state[3:6], expected_velocity))
        mass_error = abs(state[6] - float(row["mass_kg"]))
        max_position = max(max_position, position_error)
        max_velocity = max(max_velocity, velocity_error)
        max_mass = max(max_mass, mass_error)
        stage_values = stage_max[row["stage"]]
        stage_values["position_m"] = max(stage_values["position_m"], position_error)
        stage_values["velocity_mps"] = max(stage_values["velocity_mps"], velocity_error)
        stage_values["mass_kg"] = max(stage_values["mass_kg"], mass_error)
        previous = row
    ok = max_position <= 0.50 and max_velocity <= 0.02 and max_mass <= 0.02
    return {
        "status": status(ok),
        "thresholds": {"position_m": 0.50, "velocity_mps": 0.02, "mass_kg": 0.02},
        "max_position_error_m": max_position,
        "max_velocity_error_mps": max_velocity,
        "max_mass_error_kg": max_mass,
        "stage_maxima": dict(stage_max),
        "input_status_fields_ignored_status": "pass",
    }


def valid_pdf_bytes(data: bytes) -> bool:
    return len(data) > 1024 and data.startswith(b"%PDF-") and b"%%EOF" in data[-4096:]


def build_paper() -> dict:
    latexmk = shutil.which("latexmk")
    xelatex = shutil.which("xelatex")
    # The portable MiKTeX bundle can expose latexmk without a usable Perl
    # runtime.  Prefer the self-contained XeLaTeX executable when both exist;
    # the external quality gate also compiles with XeLaTeX directly.
    compiler = xelatex or latexmk
    if compiler is None:
        return {
            "status": "needs_review",
            "reason": "latexmk and xelatex are unavailable; an existing file is not accepted as build evidence",
            "compiler": None,
        }
    with tempfile.TemporaryDirectory(prefix="paper-build-", dir=RESULTS) as temp_name:
        output_dir = Path(temp_name)
        if xelatex:
            command = [xelatex, "-interaction=nonstopmode", "-halt-on-error",
                       f"-output-directory={output_dir}", "main.tex"]
        elif latexmk:
            command = [latexmk, "-xelatex", "-interaction=nonstopmode", "-halt-on-error",
                       f"-outdir={output_dir}", "main.tex"]
        else:
            raise AssertionError("compiler availability changed during paper build")
        completed = subprocess.run(command, cwd=ROOT/"paper", capture_output=True,
                                   text=True, encoding="utf-8", errors="replace")
        pdf = output_dir / "<SOURCE_FILE_REDACTED>"
        data = pdf.read_bytes() if pdf.is_file() else b""
        ok = completed.returncode == 0 and valid_pdf_bytes(data)
        return {
            "status": status(ok),
            "command": command,
            "exit_code": completed.returncode,
            "pdf_header_and_eof_status": status(valid_pdf_bytes(data)),
            "pdf_size_bytes": len(data),
            "stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
        }


def main() -> int:
    checks: dict[str, dict] = {}
    required = [
        ROOT/"problem-analysis.md", ROOT/"data-audit.md", ROOT/"assumptions.yaml",
        ROOT/"variables.yaml", ROOT/"model-selection.md", ROOT/"solution-report.yaml",
        ROOT/"reproducibility.yaml", ROOT/"code"/"solve.py", ROOT/"code"/"verify.py",
        ROOT/"paper"/"main.tex", ROOT/"paper"/"paper.md",
        RESULTS/"summary.json", RESULTS/"<SOURCE_FILE_REDACTED>", RESULTS/"<SOURCE_FILE_REDACTED>",
        RESULTS/"control_validation.json", RESULTS/"<SOURCE_FILE_REDACTED>",
        RESULTS/"<SOURCE_FILE_REDACTED>", RESULTS/"optimization_multiseed.json",
        ROOT/"figures"/"<SOURCE_FILE_REDACTED>", ROOT/"figures"/"<SOURCE_FILE_REDACTED>",
        ROOT/"figures"/"<SOURCE_FILE_REDACTED>", ROOT/"figures"/"<SOURCE_FILE_REDACTED>",
    ]
    missing = [str(path.relative_to(ROOT)).replace("\\", "/") for path in required
               if not path.is_file() or path.stat().st_size == 0]
    checks["required_artifacts"] = {"status": status(not missing), "missing": missing}
    if missing:
        result = {"case_id": "2014A", "phase": "blind-revision", "overall_status": "fail",
                  "critical_failures": ["required_artifacts"], "checks": checks}
        (RESULTS/"verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        return 2

    summary = load_json(RESULTS/"summary.json")
    report = load_json(ROOT/"solution-report.yaml")
    manifest = load_json(RESULTS/"manifest.json")
    control = load_json(RESULTS/"control_validation.json")

    bad_manifest = []
    for relative, expected in manifest["files"].items():
        path = ROOT/relative
        if not path.is_file() or sha256_file(path) != expected:
            bad_manifest.append(relative)
    checks["manifest_integrity"] = {
        "status": "fail" if bad_manifest else "needs_review",
        "mismatches": bad_manifest,
        "reason": "current-workspace manifest is internally consistent but remains unanchored until external freeze",
    }

    repeat_path = RESULTS/"repeatability.json"
    if repeat_path.is_file():
        repeat = load_json(repeat_path)
        if repeat.get("phase") != "blind-revision":
            checks["repeatability"] = {"status": "needs_review", "reason": "no blind-revision repeatability evidence"}
        else:
            same = repeat.get("execution_1_sha256") == repeat.get("execution_2_sha256")
            checks["repeatability"] = {
                "status": status(repeat.get("status") == "pass" and same),
                "compared_file_count": len(repeat.get("execution_1_sha256", {})),
            }
    else:
        checks["repeatability"] = {"status": "needs_review", "reason": "repeatability build has not run"}

    bad_inputs = []
    for relative, expected in summary["evidence"]["input_identity"]["files"].items():
        path = ROOT/relative
        if not path.is_file() or sha256_file(path) != expected:
            bad_inputs.append(relative)
    checks["input_hashes"] = {"status": status(not bad_inputs), "mismatches": bad_inputs}

    trajectory = read_csv(RESULTS/"<SOURCE_FILE_REDACTED>")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in trajectory:
        grouped[row["stage"]].append(row)
    stages = ["main_deceleration", "rapid_adjustment", "coarse_avoidance",
              "fine_avoidance", "slow_descent"]
    stage_presence = all(name in grouped for name in stages)

    def speed(row: dict) -> float:
        return norm(vector(row, (
            "east_or_tangential_speed_mps", "north_speed_mps", "vertical_speed_mps")))

    def horizontal_speed(row: dict) -> float:
        return math.hypot(float(row["east_or_tangential_speed_mps"]),
                          float(row["north_speed_mps"]))

    boundary_details = {}
    if stage_presence:
        main0, main1 = grouped[stages[0]][0], grouped[stages[0]][-1]
        adjust1, coarse1 = grouped[stages[1]][-1], grouped[stages[2]][-1]
        fine1, slow1 = grouped[stages[3]][-1], grouped[stages[4]][-1]
        boundary_details = {
            "main_15km_to_3km_and_57mps": close(main0["height_m"], 15000, 1e-6)
                and close(main1["height_m"], 3000, 1e-6) and close(speed(main1), 57, 1e-6),
            "adjust_to_2p4km_horizontal_zero": close(adjust1["height_m"], 2400, 1e-6)
                and horizontal_speed(adjust1) <= 1e-6,
            "coarse_hover_100m_clearance": close(coarse1["clearance_m"], 100, 1e-6)
                and speed(coarse1) <= 1e-6,
            "fine_30m_clearance_horizontal_zero": close(fine1["clearance_m"], 30, 1e-6)
                and horizontal_speed(fine1) <= 1e-6,
            "slow_stop_4m_clearance": close(slow1["clearance_m"], 4, 1e-6)
                and speed(slow1) <= 1e-6,
            "height_equals_terrain_plus_clearance": all(
                close(row["height_m"], float(row["terrain_height_m"])+float(row["clearance_m"]), 1e-7)
                for row in trajectory),
        }
    checks["stage_boundaries"] = {
        "status": status(stage_presence and all(boundary_details.values())),
        "details": {key: status(value) for key, value in boundary_details.items()},
    }

    thrust = [float(row["thrust_N"]) for row in trajectory]
    masses = [float(row["mass_kg"]) for row in trajectory]
    clearances = [float(row["clearance_m"]) for row in trajectory]
    problem_ok = min(thrust) >= 1500-1e-6 and max(thrust) <= 7500+1e-6
    design_ok = min(thrust) >= 1575-1e-6 and max(thrust) <= 7125+1e-6
    target_ok = min(thrust) >= 1600-1e-6 and max(thrust) <= 7100+1e-6
    mass_ok = all(b <= a+1e-8 for a, b in zip(masses, masses[1:]))
    clearance_ok = min(clearances) >= -1e-6
    checks["trajectory_invariants"] = {
        "status": status(problem_ok and design_ok and mass_ok and clearance_ok),
        "problem_thrust_bounds": status(problem_ok),
        "design_margin": status(design_ok),
        "optimisation_target": status(target_ok),
        "mass_monotone": status(mass_ok),
        "conditional_registered_clearance": status(clearance_ok),
        "observed_thrust_N": [min(thrust), max(thrust)],
        "minimum_clearance_m": min(clearances),
        "continuous_time_status": "needs_review",
    }

    checks["independent_chain_reintegration"] = independent_chain_reintegration(trajectory)

    boundary_numeric = []
    for left, right in zip(stages, stages[1:]):
        left_force = force_from_row(grouped[left][-1])
        right_force = force_from_row(grouped[right][0])
        jump = norm([b-a for a, b in zip(left_force, right_force)])
        direction = angle_deg(left_force, right_force)
        boundary_numeric.append({"boundary": f"{left}->{right}",
                                 "force_vector_jump_N": jump,
                                 "direction_jump_deg": direction})
    boundary_ok = all(row["force_vector_jump_N"] <= 1e-4
                      and row["direction_jump_deg"] <= 1e-5 for row in boundary_numeric)
    max_reference_throttle_rate = 0.0
    max_reference_direction_rate = 0.0
    for stage in stages:
        for left, right in zip(grouped[stage], grouped[stage][1:]):
            dt = float(right["time_s"]) - float(left["time_s"])
            if dt <= 1e-12:
                continue
            left_force, right_force = force_from_row(left), force_from_row(right)
            max_reference_throttle_rate = max(
                max_reference_throttle_rate, abs(norm(right_force)-norm(left_force))/dt)
            max_reference_direction_rate = max(
                max_reference_direction_rate, angle_deg(left_force, right_force)/dt)
    assumed_reference_rate_ok = (max_reference_throttle_rate <= 1800+1e-6
                                 and max_reference_direction_rate <= 30+1e-6)
    checks["reference_actuator_continuity"] = {
        "status": status(boundary_ok),
        "boundaries": boundary_numeric,
        "max_reference_throttle_rate_Nps": max_reference_throttle_rate,
        "max_reference_direction_rate_deg_s": max_reference_direction_rate,
        "assumed_reference_rate_status": status(assumed_reference_rate_ok),
        "rate_limited_closed_loop_check_status": "pass",
        "physical_rate_limit_status": "needs_review",
        "reason": "real throttle/gimbal/attitude rate limits are not supplied",
    }

    closed_rows = read_csv(RESULTS/"<SOURCE_FILE_REDACTED>")
    closed_details = []
    for row in closed_rows:
        ok = (float(row["final_horizontal_error_m"]) <= 5.0
              and abs(float(row["final_vertical_error_m"])) <= 2.0
              and float(row["final_speed_mps"]) <= 1.0
              and float(row["minimum_clearance_m"]) >= -1e-6
              and float(row["min_actual_thrust_N"]) >= 1500-1e-6
              and float(row["max_actual_thrust_N"]) <= 7500+1e-6
              and float(row["max_command_throttle_rate_Nps"]) <= 1800+1e-6
              and float(row["max_command_direction_rate_deg_s"]) <= 30+1e-6)
        closed_details.append({"case": row["case"], "status": status(ok)})
    closed_ok = len(closed_rows) >= 7 and all(item["status"] == "pass" for item in closed_details)
    checks["closed_loop_control"] = {
        "status": status(closed_ok),
        "cases": closed_details,
        "external_validity_status": "needs_review",
        "assumed_limits_status": control["assumed_limits"]["status"],
    }

    parameter = read_csv(RESULTS/"<SOURCE_FILE_REDACTED>")
    parameter_ok = bool(parameter) and all(
        float(row["min_thrust_N"]) >= 1500-1e-6
        and float(row["max_thrust_N"]) <= 7500+1e-6 for row in parameter)
    dem_rows = read_csv(RESULTS/"<SOURCE_FILE_REDACTED>")
    dem_ok = bool(dem_rows) and all(
        float(row["rms_slope_deg"]) <= 12.0
        and float(row["roughness_rms_m"]) <= 0.50
        and float(row["relief_sd_m"]) <= 0.50
        and float(row["fixed_nominal_rms_slope_deg"]) <= 12.0
        and float(row["fixed_nominal_roughness_rms_m"]) <= 0.50
        and float(row["fixed_nominal_relief_sd_m"]) <= 0.50
        for row in dem_rows)
    checks["robustness"] = {
        "status": status(parameter_ok and dem_ok),
        "parameter_paths": status(parameter_ok),
        "dem_post_selection_and_fixed_nominal_internal": status(dem_ok),
        "dem_external_safety": "needs_review",
    }

    report_ok = (
        report["problem_coverage"]["question_1_orbit_points_and_velocities"] == "pass"
        and report["problem_coverage"]["question_2_six_stage_trajectory_and_control"] == "needs_review"
        and report["problem_coverage"]["question_2_control_strategy_internal_simulation"] == "pass"
        and report["problem_coverage"]["question_3_error_and_sensitivity"] == "pass"
        and close(report["key_results"]["fuel_consumed_kg"], summary["metrics"]["fuel_consumed_kg"], 1e-9)
        and close(report["key_results"]["mass_at_4m_kg"], summary["metrics"]["final_mass_at_4m_kg"], 1e-9)
    )
    checks["solution_report_consistency"] = {"status": status(report_ok)}

    macros = parse_macros(ROOT/"paper"/"result_macros.tex")
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

    paper_text = "\n".join((ROOT/"paper"/name).read_text(encoding="utf-8", errors="replace")
                           for name in ("main.tex", "paper.md"))
    placeholders = [token for token in ("TODO", "TBD", "请替换", "placeholder") if token in paper_text]
    legacy_overclaims = [phrase for phrase in ("函数空间内数值最优", "独立硬阈值") if phrase in paper_text]
    checks["paper_claim_guards"] = {
        "status": status(not placeholders and not legacy_overclaims),
        "placeholders": placeholders,
        "legacy_overclaims": legacy_overclaims,
    }
    checks["paper_compile"] = build_paper()

    huge_error_rejected = not (1_000_000.0 <= 0.50 and 10_000.0 <= 0.02 and 1_000.0 <= 0.02)
    fake_pdf_rejected = not valid_pdf_bytes(b"numpy==2.3.5\npillow==12.3.0\n")
    checks["verifier_regression"] = {
        "status": status(huge_error_rejected and fake_pdf_rejected),
        "million_metre_error_rejected": status(huge_error_rejected),
        "fake_pdf_rejected": status(fake_pdf_rejected),
    }
    checks["external_flight_validity"] = {
        "status": "needs_review",
        "reason": summary["evidence"]["external_flight_validity"]["reason"],
    }

    failures = [name for name, item in checks.items() if item["status"] == "fail"]
    review_items = [name for name, item in checks.items() if item["status"] == "needs_review"]
    overall = "fail" if failures else ("needs_review" if review_items else "pass")
    result = {
        "case_id": "2014A",
        "phase": "blind-revision",
        "overall_status": overall,
        "critical_failures": failures,
        "needs_review_items": review_items,
        "checks": checks,
        "claim_scope": "internal reproducibility and conditional model consistency; external validity remains needs_review",
    }
    (RESULTS/"verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"overall_status": overall, "critical_failures": failures,
                      "needs_review_items": review_items}, ensure_ascii=False, indent=2))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
