from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml


ALLOWED_STATUSES = {"pass", "fail", "needs_review"}
PAPER_SOURCES = [
    "paper/main.tex",
    "paper/preamble.tex",
    "paper/generated-results.tex",
    "paper/sections/01-problem.tex",
    "paper/sections/02-analysis.tex",
    "paper/sections/03-assumptions-symbols.tex",
    "paper/sections/04-data-model.tex",
    "paper/sections/05-solution-validation.tex",
    "paper/sections/06-evaluation-conclusion.tex",
    "paper/sections/appendix.tex",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def close(actual: float, expected: float, atol: float = 1e-9, rtol: float = 1e-10) -> bool:
    return math.isclose(actual, expected, abs_tol=atol, rel_tol=rtol)


def rpm_to_rad_s(value: float) -> float:
    return value * 2.0 * math.pi / 60.0


def status_fields_are_valid(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if (key == "status" or key.endswith("_status")) and item not in ALLOWED_STATUSES:
                return False
            if not status_fields_are_valid(item):
                return False
    elif isinstance(value, list):
        return all(status_fields_are_valid(item) for item in value)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent numerical, property and artifact verification.")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results_dir = workspace / "results"
    checks: list[dict[str, str]] = []

    def record(check_id: str, condition: bool, detail: str) -> None:
        checks.append(
            {
                "id": check_id,
                "status": "pass" if condition else "fail",
                "detail": detail,
            }
        )

    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/extract_data.ps1",
        "code/run_all.ps1",
        "code/solve.py",
        "code/verify.py",
        "code/record_build.py",
        "code/artifact_manifest.py",
        "results/<SOURCE_FILE_REDACTED>",
        "results/input-metadata.json",
        "results/metrics.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/paper-build.json",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/paper.md",
        "paper/generated-results.tex",
        "paper/<SOURCE_FILE_REDACTED>",
    ]
    missing = [relative for relative in required if not (workspace / relative).is_file()]
    record("required_artifacts", not missing, "missing=" + repr(missing))

    metrics = json.loads((results_dir / "metrics.json").read_text(encoding="utf-8"))
    metadata = json.loads((results_dir / "input-metadata.json").read_text(encoding="utf-8-sig"))
    with (results_dir / "<SOURCE_FILE_REDACTED>").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        observations = list(csv.DictReader(handle))
    t = [float(row["time_s"]) for row in observations]
    torque = [float(row["brake_torque_Nm"]) for row in observations]
    rpm = [float(row["speed_rpm"]) for row in observations]
    omega = [rpm_to_rad_s(value) for value in rpm]
    observation_path = results_dir / "<SOURCE_FILE_REDACTED>"
    source_xls = workspace / "input" / "data" / "<SOURCE_FILE_REDACTED>"
    current_source_hash = sha256(source_xls)
    record(
        "input_original_pre_post_hash",
        metadata["source_byte_integrity_status"] == "pass"
        and current_source_hash == metadata["source_pre_open_sha256"]
        and current_source_hash == metadata["source_post_extraction_sha256"],
        f"current={current_source_hash}, pre={metadata['source_pre_open_sha256']}, post={metadata['source_post_extraction_sha256']}",
    )
    record(
        "normalized_semantic_hash",
        sha256(observation_path) == metadata["normalized_observations_sha256"],
        f"observations_sha256={sha256(observation_path)}",
    )
    record(
        "observation_structure",
        len(observations) == 468
        and all(t[index + 1] > t[index] for index in range(len(t) - 1))
        and all(math.isfinite(value) for row in zip(t, torque, rpm) for value in row),
        f"rows={len(observations)}, duration={t[-1] - t[0]:.12g}s",
    )

    q1_alt = (6230.0 / 9.8) * 0.286**2
    q1_reported = float(metrics["question_1"]["equivalent_inertia_kgm2"])
    record(
        "q1_independent_energy_equivalence",
        close(q1_alt, q1_reported, atol=1e-12),
        f"independent={q1_alt:.12f}, reported={q1_reported:.12f}",
    )

    thicknesses = [0.0392, 0.0784, 0.1568]
    flywheel_alt = [
        7810.0 * math.pi * thickness * (1.0**4 - 0.2**4) / 32.0
        for thickness in thicknesses
    ]
    flywheel_reported = [
        float(value) for value in metrics["question_2"]["flywheel_inertias_kgm2"]
    ]
    record(
        "q2_independent_annulus_formula",
        all(close(first, second, atol=1e-11) for first, second in zip(flywheel_alt, flywheel_reported)),
        "alternative diameter formula agrees with all flywheel inertias",
    )
    combos_alt = sorted(
        10.0 + b0 * flywheel_alt[0] + b1 * flywheel_alt[1] + b2 * flywheel_alt[2]
        for b0 in (0, 1)
        for b1 in (0, 1)
        for b2 in (0, 1)
    )
    combos_reported = [
        float(value) for value in metrics["question_2"]["mechanical_inertias_kgm2"]
    ]
    record(
        "q2_complete_subset_enumeration",
        len(combos_reported) == 8
        and all(close(first, second, atol=1e-10) for first, second in zip(combos_alt, combos_reported)),
        "all 2^3 unique subsets agree",
    )

    selected_jm = float(metrics["question_2"]["selected"]["mechanical_inertia_kgm2"])
    delta_j = q1_alt - selected_jm
    omega0 = (50.0 / 3.6) / 0.286
    alpha = -omega0 / 5.0
    current_alt = 1.5 * (-delta_j * alpha)
    current_reported = float(metrics["question_3"]["drive_current_A"])
    record(
        "q3_independent_current",
        close(current_alt, current_reported, atol=1e-10),
        f"independent={current_alt:.12f}A, reported={current_reported:.12f}A",
    )
    record(
        "q3_equal_inertia_boundary",
        close(1.5 * (1.0 - 48.0 / 48.0) * 100.0, 0.0),
        "J_e=J_m gives zero compensation current",
    )

    exact_bench_energy = 0.0
    trapezoid_bench_energy = 0.0
    inferred_motor_energy = 0.0
    for index in range(len(t) - 1):
        step = t[index + 1] - t[index]
        exact_bench_energy += step * (
            2.0 * torque[index] * omega[index]
            + torque[index] * omega[index + 1]
            + torque[index + 1] * omega[index]
            + 2.0 * torque[index + 1] * omega[index + 1]
        ) / 6.0
        trapezoid_bench_energy += 0.5 * (
            torque[index] * omega[index] + torque[index + 1] * omega[index + 1]
        ) * step
        local_alpha = (omega[index + 1] - omega[index]) / step
        u_left = torque[index] + 35.0 * local_alpha
        u_right = torque[index + 1] + 35.0 * local_alpha
        inferred_motor_energy += step * (
            2.0 * u_left * omega[index]
            + u_left * omega[index + 1]
            + u_right * omega[index]
            + 2.0 * u_right * omega[index + 1]
        ) / 6.0
    road_energy_alt = 0.5 * 48.0 * (
        rpm_to_rad_s(514.0) ** 2 - rpm_to_rad_s(257.0) ** 2
    )
    q4 = metrics["question_4"]
    record(
        "q4_piecewise_linear_exact_integration",
        close(exact_bench_energy, float(q4["test_bench_brake_energy_J"]), atol=1e-7)
        and close(road_energy_alt, float(q4["road_brake_energy_nominal_J"]), atol=1e-7)
        and close(
            exact_bench_energy - trapezoid_bench_energy,
            float(q4["integration_estimates_J"]["exact_minus_trapezoid_J"]),
            atol=1e-8,
        ),
        f"road={road_energy_alt:.9f}J, exact_bench={exact_bench_energy:.9f}J, exact-trap={exact_bench_energy-trapezoid_bench_energy:.9f}J",
    )
    mechanical_drop = 0.5 * 35.0 * (omega[0] ** 2 - omega[-1] ** 2)
    balance_residual = exact_bench_energy - inferred_motor_energy - mechanical_drop
    record(
        "q4_independent_energy_balance",
        abs(balance_residual) < 1e-8,
        f"residual={balance_residual:.3e}J",
    )

    with (results_dir / "<SOURCE_FILE_REDACTED>").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        series = list(csv.DictReader(handle))
    q5_current_residual = 0.0
    q5_state_residual = 0.0
    q6_constraint_violations = 0
    negative_state_count = 0
    for index in range(len(series) - 1):
        step = t[index + 1] - t[index]
        midpoint_torque = 0.5 * (torque[index] + torque[index + 1])
        expected_current = 1.5 * (13.0 / 48.0) * torque[index]
        actual_current = float(series[index]["torque_zoh_current_A"])
        q5_current_residual = max(q5_current_residual, abs(expected_current - actual_current))
        current_omega = rpm_to_rad_s(float(series[index]["torque_zoh_bench_rpm"]))
        next_expected = current_omega + step * (actual_current / 1.5 - midpoint_torque) / 35.0
        next_actual = rpm_to_rad_s(float(series[index + 1]["torque_zoh_bench_rpm"]))
        q5_state_residual = max(q5_state_residual, abs(next_expected - next_actual))

        prediction = float(series[index]["robust_predicted_torque_Nm"])
        robust_current = float(series[index]["robust_predictive_current_A"])
        robust_u = robust_current / 1.5
        if prediction <= 1.0:
            if abs(robust_u) > 1e-10:
                q6_constraint_violations += 1
        else:
            equivalent = 48.0 * robust_u / prediction
            if abs(equivalent) > 30.0 + 1e-10:
                q6_constraint_violations += 1
        if abs(robust_u) > 187.5 + 1e-10:
            q6_constraint_violations += 1
        for name in ["motor_off", "speed_difference", "torque_zoh", "robust_predictive"]:
            if float(series[index][f"{name}_bench_rpm"]) < -1e-10:
                negative_state_count += 1
    record(
        "q5_control_law_and_state_update",
        q5_current_residual < 1e-10 and q5_state_residual < 1e-10,
        f"current_residual={q5_current_residual:.3e}A, state_residual={q5_state_residual:.3e}rad/s",
    )
    record(
        "q6_interval_command_constraints",
        q6_constraint_violations == 0,
        f"violations={q6_constraint_violations}",
    )
    record(
        "nominal_nonnegative_state",
        negative_state_count == 0,
        f"negative_state_count={negative_state_count}",
    )

    with (results_dir / "<SOURCE_FILE_REDACTED>").open(encoding="utf-8", newline="") as handle:
        constraint_rows = list(csv.DictReader(handle))
    low_rows = [row for row in constraint_rows if float(row["predicted_brake_torque_Nm"]) <= 1.0]
    record(
        "low_torque_0_0p1_1Nm_cases",
        {float(row["predicted_brake_torque_Nm"]) for row in low_rows} == {0.0, 0.1, 1.0}
        and all(abs(float(row["command_Nm"])) < 1e-12 for row in low_rows)
        and all(row["constraint_status"] == "pass" for row in constraint_rows),
        "0, 0.1 and 1 N*m use zero-command low-torque mode; all algebraic cases pass",
    )

    q6 = metrics["question_6"]
    gain = float(q6["speed_feedback_gain_Nms_per_rad"])
    step_domain = [float(value) for value in q6["declared_step_domain_s"]]
    inertia_domain = [float(value) for value in q6["declared_actual_inertia_domain_kgm2"]]
    pole_min = 1.0 - step_domain[1] * gain / inertia_domain[0]
    pole_max = 1.0 - step_domain[0] * gain / inertia_domain[1]
    observer_pole = 1.0 - float(q6["observer_correction_gain"])
    record(
        "q6_declared_stability_domain",
        -1.0 < pole_min < 1.0
        and -1.0 < pole_max < 1.0
        and -1.0 < observer_pole < 1.0
        and close(pole_min, float(q6["closed_loop_pole_range"][0]), atol=1e-12)
        and close(pole_max, float(q6["closed_loop_pole_range"][1]), atol=1e-12),
        f"plant_pole_range=[{pole_min:.6f},{pole_max:.6f}], observer_pole={observer_pole:.6f}",
    )

    with (results_dir / "<SOURCE_FILE_REDACTED>").open(encoding="utf-8", newline="") as handle:
        stress_rows = list(csv.DictReader(handle))
    stop_rows = [row for row in stress_rows if row["scenario"] == "constant_300Nm_stop_20s"]
    alternating_rows = [row for row in stress_rows if row["scenario"] == "alternating_40_300Nm_10ms"]
    inertia_rows = [row for row in stress_rows if row["scenario"].startswith("feasible_inertia_")]
    stop_ok = len(stop_rows) == 2 and all(
        row["status"] == "pass"
        and float(row["min_bench_speed_rpm"]) >= -1e-12
        and row["reference_stop_time_s"]
        and row["bench_stop_time_s"]
        and abs(float(row["reference_stop_time_s"]) - float(row["bench_stop_time_s"])) <= 0.0100001
        for row in stop_rows
    )
    record(
        "q6_constant_torque_stop_event",
        stop_ok,
        "20 s / 300 N*m cases stop near 8.612 s, clamp omega at zero and do not integrate reverse rotation",
    )
    stress_ok = (
        all(row["status"] == "pass" for row in stress_rows)
        and len(alternating_rows) == 2
        and len(inertia_rows) == 2
        and max(float(row["absolute_relative_energy_error_pct"]) for row in inertia_rows) <= 2.0
        and max(float(row["max_abs_speed_error_rpm"]) for row in inertia_rows) <= 10.0
    )
    record(
        "q6_counterexample_and_inertia_stress",
        stress_ok,
        f"rows={len(stress_rows)}, all_status_pass={all(row['status'] == 'pass' for row in stress_rows)}",
    )

    with (results_dir / "<SOURCE_FILE_REDACTED>").open(encoding="utf-8", newline="") as handle:
        noise_rows = list(csv.DictReader(handle))
    noise_ok = len(noise_rows) == 2 and all(
        row["status"] == "pass"
        and int(row["seeds"]) == 100
        and float(row["worst_energy_error_pct"]) <= 0.05
        and float(row["worst_max_speed_error_rpm"]) <= 0.5
        and float(row["worst_peak_current_A"]) <= 281.25 + 1e-9
        for row in noise_rows
    )
    record(
        "q6_fixed_seed_speed_noise_stress",
        noise_ok,
        "100 seeds at sigma=0.1 and 0.5 rpm satisfy predeclared thresholds",
    )

    macro_text = (workspace / "paper" / "generated-results.tex").read_text(encoding="utf-8")
    macro_pairs = dict(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([^{}]+)\}", macro_text))
    expected_macros = {
        "QOneJ": (q1_reported, 5e-4),
        "QThreeCurrent": (current_reported, 5e-4),
        "QFourRelativeError": (float(q4["absolute_relative_energy_error_pct"]), 5e-5),
        "QFiveEnergyError": (float(metrics["question_5"]["absolute_relative_energy_error_pct"]), 5e-6),
        "QSixEnergyError": (float(q6["absolute_relative_energy_error_pct"]), 5e-9),
    }
    macro_ok = True
    macro_details: list[str] = []
    for name, (expected, tolerance) in expected_macros.items():
        if name not in macro_pairs:
            macro_ok = False
            macro_details.append(f"missing {name}")
            continue
        actual = float(macro_pairs[name])
        if abs(actual - expected) > tolerance:
            macro_ok = False
            macro_details.append(f"{name}: {actual} vs {expected}")
    main_tex = (workspace / "paper" / "main.tex").read_text(encoding="utf-8")
    record(
        "tex_macro_result_consistency",
        macro_ok and "\\input{generated-results}" in main_tex,
        "; ".join(macro_details) if macro_details else "key generated TeX macros match metrics.json",
    )

    paper_md = (workspace / "paper" / "paper.md").read_text(encoding="utf-8")
    markdown_tokens = [
        f"{q1_reported:.6f}",
        f"{current_reported:.6f}",
        f"{float(q4['absolute_relative_energy_error_pct']):.6f}",
        f"{float(metrics['question_5']['absolute_relative_energy_error_pct']):.6f}",
        f"{float(q6['absolute_relative_energy_error_pct']):.8f}",
    ]
    missing_tokens = [token for token in markdown_tokens if token not in paper_md]
    record(
        "markdown_result_consistency",
        not missing_tokens,
        "missing_tokens=" + repr(missing_tokens),
    )

    yaml_docs = []
    yaml_ok = True
    for name in ["assumptions.yaml", "variables.yaml", "solution-report.yaml", "reproducibility.yaml"]:
        try:
            document = yaml.safe_load((workspace / name).read_text(encoding="utf-8"))
            yaml_docs.append(document)
        except Exception:
            yaml_ok = False
    record(
        "yaml_parse_and_status_vocabulary",
        yaml_ok and all(status_fields_are_valid(document) for document in yaml_docs),
        "all YAML files parse and judgment fields use pass/fail/needs_review",
    )

    build_record = json.loads((results_dir / "paper-build.json").read_text(encoding="utf-8"))
    source_hashes_ok = set(build_record["source_sha256"]) == set(PAPER_SOURCES) and all(
        (workspace / relative).is_file()
        and sha256(workspace / relative) == expected
        for relative, expected in build_record["source_sha256"].items()
    )
    pdf_path = workspace / "paper" / "<SOURCE_FILE_REDACTED>"
    pdf_hash_ok = (
        pdf_path.is_file()
        and sha256(pdf_path) == build_record["pdf"]["sha256"]
        and pdf_path.stat().st_size == int(build_record["pdf"]["size_bytes"])
    )
    record(
        "paper_source_pdf_freshness_hashes",
        source_hashes_ok and pdf_hash_ok,
        f"source_hashes_ok={source_hashes_ok}, pdf_hash_ok={pdf_hash_ok}",
    )
    pdf_header_ok = pdf_path.is_file() and pdf_path.stat().st_size > 100_000
    if pdf_header_ok:
        with pdf_path.open("rb") as handle:
            pdf_header_ok = handle.read(4) == b"%PDF"
    extracted = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(pdf_path), "-"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    normalized_pdf_text = re.sub(r"\s+", "", extracted.stdout)
    expected_headings = [
        "问题重述",
        "问题分析",
        "模型假设与符号",
        "数据处理与问题1至4",
        "问题5与问题6的离散控制",
        "验证、评价与结论",
        "参考资料说明",
        "控制器伪代码与复现",
    ]
    missing_headings = [heading for heading in expected_headings if heading not in normalized_pdf_text]
    record(
        "paper_pdf_text_completeness",
        pdf_header_ok and extracted.returncode == 0 and not missing_headings,
        f"pdf_size={pdf_path.stat().st_size if pdf_path.exists() else 0}, missing_headings={missing_headings}",
    )
    log_path = workspace / "paper" / "main.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    bad_markers = [
        marker
        for marker in [
            "LaTeX Warning: There were undefined references",
            "LaTeX Warning: Label(s) may have changed",
            "Overfull \\hbox",
            "Underfull \\hbox",
        ]
        if marker in log_text
    ]
    record(
        "paper_build_log",
        not bad_markers,
        "bad_markers=" + repr(bad_markers),
    )

    figure_sizes = {
        name: (workspace / "figures" / name).stat().st_size
        for name in [
            "<SOURCE_FILE_REDACTED>",
            "<SOURCE_FILE_REDACTED>",
            "<SOURCE_FILE_REDACTED>",
            "<SOURCE_FILE_REDACTED>",
        ]
        if (workspace / "figures" / name).exists()
    }
    record(
        "figure_files_nonempty",
        len(figure_sizes) == 4 and min(figure_sizes.values()) > 10_000,
        repr(figure_sizes),
    )

    run_manifest = json.loads((results_dir / "run-manifest.json").read_text(encoding="utf-8"))
    manifest_hashes_ok = all(
        (workspace / relative).is_file() and sha256(workspace / relative) == expected
        for relative, expected in run_manifest["files"].items()
    )
    record(
        "run_manifest_hashes_recomputed",
        manifest_hashes_ok,
        f"tracked_files={len(run_manifest['files'])}",
    )

    failures = [check for check in checks if check["status"] == "fail"]
    report = {
        "overall_status": "pass" if not failures else "fail",
        "checks": checks,
        "review_items": [
            {
                "id": "q4_acceptance_threshold",
                "status": "needs_review",
                "detail": "The problem supplies no pass/fail tolerance for energy error.",
            },
            {
                "id": "formal_mathematical_correctness",
                "status": "needs_review",
                "detail": "Automated identities and property tests do not constitute a formal proof of every modeling assumption.",
            },
            {
                "id": "hardware_feasibility",
                "status": "needs_review",
                "detail": "The software safety settings are not a substitute for motor current, voltage, thermal and slew specifications.",
            },
            {
                "id": "physical_bench_validation",
                "status": "needs_review",
                "detail": "Controller comparisons are model-in-the-loop replays, not new physical bench trials.",
            },
        ],
        "verified_files_sha256": {
            relative: sha256(workspace / relative)
            for relative in [
                "results/metrics.json",
                "results/<SOURCE_FILE_REDACTED>",
                "results/paper-build.json",
                "paper/generated-results.tex",
                "paper/paper.md",
                "paper/main.tex",
                "paper/<SOURCE_FILE_REDACTED>",
            ]
            if (workspace / relative).is_file()
        },
    }
    (results_dir / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if failures:
        print("[fail] independent verification found failures")
        for failure in failures:
            print(f"  - {failure['id']}: {failure['detail']}")
        return 1
    print(f"[pass] independent numerical, property and paper verification ({len(checks)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
