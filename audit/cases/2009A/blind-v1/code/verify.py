from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import yaml


ALLOWED_STATUSES = {"pass", "fail", "needs_review"}


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
    parser = argparse.ArgumentParser(description="Independent numerical and artifact verification.")
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
        "results/<SOURCE_FILE_REDACTED>",
        "results/metrics.json",
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
        "paper/<SOURCE_FILE_REDACTED>",
    ]
    missing = [path for path in required if not (workspace / path).is_file()]
    record("required_artifacts", not missing, "missing=" + repr(missing))

    metrics = json.loads((results_dir / "metrics.json").read_text(encoding="utf-8"))
    with (results_dir / "<SOURCE_FILE_REDACTED>").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        observations = list(csv.DictReader(handle))
    t = [float(row["time_s"]) for row in observations]
    torque = [float(row["brake_torque_Nm"]) for row in observations]
    rpm = [float(row["speed_rpm"]) for row in observations]
    omega = [rpm_to_rad_s(value) for value in rpm]
    record(
        "observation_structure",
        len(observations) == 468
        and all(t[index + 1] > t[index] for index in range(len(t) - 1)),
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
        all(close(a, b, atol=1e-11) for a, b in zip(flywheel_alt, flywheel_reported)),
        "alternative diameter formula agrees with reported flywheel inertias",
    )
    combos_alt = sorted(
        10.0
        + bit0 * flywheel_alt[0]
        + bit1 * flywheel_alt[1]
        + bit2 * flywheel_alt[2]
        for bit0 in (0, 1)
        for bit1 in (0, 1)
        for bit2 in (0, 1)
    )
    combos_reported = [
        float(value) for value in metrics["question_2"]["mechanical_inertias_kgm2"]
    ]
    record(
        "q2_complete_subset_enumeration",
        len(combos_reported) == 8
        and all(close(a, b, atol=1e-10) for a, b in zip(combos_alt, combos_reported)),
        "all 2^3 flywheel subsets are present",
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
        "q3_boundary_equal_inertias",
        close(1.5 * (1.0 - 48.0 / 48.0) * 100.0, 0.0),
        "J_e=J_m gives zero compensation current",
    )

    bench_energy_alt = 0.0
    midpoint_energy = 0.0
    inferred_motor_energy = 0.0
    for index in range(len(t) - 1):
        h = t[index + 1] - t[index]
        bench_energy_alt += 0.5 * (
            torque[index] * omega[index] + torque[index + 1] * omega[index + 1]
        ) * h
        m_mid = 0.5 * (torque[index] + torque[index + 1])
        w_mid = 0.5 * (omega[index] + omega[index + 1])
        midpoint_energy += m_mid * w_mid * h
        inferred_u = m_mid + 35.0 * (omega[index + 1] - omega[index]) / h
        inferred_motor_energy += inferred_u * w_mid * h
    road_energy_alt = 0.5 * 48.0 * (
        rpm_to_rad_s(514.0) ** 2 - rpm_to_rad_s(257.0) ** 2
    )
    q4 = metrics["question_4"]
    record(
        "q4_independent_power_integration",
        close(bench_energy_alt, float(q4["test_bench_brake_energy_J"]), atol=1e-7)
        and close(road_energy_alt, float(q4["road_brake_energy_nominal_J"]), atol=1e-7),
        f"road={road_energy_alt:.9f}J, bench={bench_energy_alt:.9f}J",
    )
    mechanical_drop = 0.5 * 35.0 * (omega[0] ** 2 - omega[-1] ** 2)
    balance_residual = midpoint_energy - inferred_motor_energy - mechanical_drop
    record(
        "q4_independent_energy_balance",
        abs(balance_residual) < 1e-8,
        f"residual={balance_residual:.3e}J",
    )

    with (results_dir / "<SOURCE_FILE_REDACTED>").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        series = list(csv.DictReader(handle))
    q5_current_error = 0.0
    q5_state_error = 0.0
    q6_current_error = 0.0
    internal_ref = rpm_to_rad_s(514.0)
    gain = 13.0 / 48.0
    for index in range(len(series) - 1):
        h = t[index + 1] - t[index]
        m_mid = 0.5 * (torque[index] + torque[index + 1])
        q5_expected_current = 1.5 * gain * torque[index]
        q5_actual_current = float(series[index]["torque_zoh_current_A"])
        q5_current_error = max(q5_current_error, abs(q5_expected_current - q5_actual_current))
        q5_w = rpm_to_rad_s(float(series[index]["torque_zoh_bench_rpm"]))
        q5_w_next_expected = q5_w + h * (q5_actual_current / 1.5 - m_mid) / 35.0
        q5_w_next_actual = rpm_to_rad_s(float(series[index + 1]["torque_zoh_bench_rpm"]))
        q5_state_error = max(q5_state_error, abs(q5_w_next_expected - q5_w_next_actual))

        q6_w = rpm_to_rad_s(float(series[index]["predictive_feedback_bench_rpm"]))
        if index == 0:
            estimate = torque[index]
        else:
            previous_h = t[index] - t[index - 1]
            estimate = torque[index] + 0.5 * h / previous_h * (
                torque[index] - torque[index - 1]
            )
        raw_u = gain * estimate - 35.0 * (q6_w - internal_ref) / h
        limit = 30.0 * max(abs(estimate), 1.0) / 48.0
        expected_u = min(max(raw_u, -limit), limit)
        actual_u = float(series[index]["predictive_feedback_current_A"]) / 1.5
        q6_current_error = max(q6_current_error, abs(expected_u - actual_u))
        internal_ref -= h * m_mid / 48.0
    record(
        "q5_control_law_and_state_update",
        q5_current_error < 1e-10 and q5_state_error < 1e-10,
        f"max_current_residual={q5_current_error:.3e}A, max_state_residual={q5_state_error:.3e}rad/s",
    )
    record(
        "q6_predictive_feedback_law",
        q6_current_error < 1e-9,
        f"max_motor_torque_residual={q6_current_error:.3e}N*m",
    )

    macro_text = (workspace / "paper" / "generated-results.tex").read_text(encoding="utf-8")
    macro_pairs = dict(
        re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{([^{}]+)\}", macro_text)
    )
    expected_macros = {
        "QOneJ": (q1_reported, 5e-4),
        "QThreeCurrent": (current_reported, 5e-4),
        "QFourRelativeError": (float(q4["absolute_relative_energy_error_pct"]), 5e-5),
        "QFiveEnergyError": (
            float(metrics["question_5"]["absolute_relative_energy_error_pct"]),
            5e-6,
        ),
        "QSixEnergyError": (
            float(metrics["question_6"]["absolute_relative_energy_error_pct"]),
            5e-9,
        ),
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
        "; ".join(macro_details) if macro_details else "key TeX macros match metrics.json",
    )

    paper_md = (workspace / "paper" / "paper.md").read_text(encoding="utf-8")
    markdown_tokens = ["51.998886", "174.687844", "5.576031", "0.022087", "0.00000164"]
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
        "all YAML files parse and status fields use pass/fail/needs_review",
    )

    pdf_path = workspace / "paper" / "<SOURCE_FILE_REDACTED>"
    pdf_ok = pdf_path.is_file() and pdf_path.stat().st_size > 100_000
    if pdf_ok:
        with pdf_path.open("rb") as handle:
            pdf_ok = handle.read(4) == b"%PDF"
    log_text = (workspace / "paper" / "main.log").read_text(
        encoding="utf-8", errors="replace"
    ) if (workspace / "paper" / "main.log").is_file() else ""
    layout_issue = any(
        marker in log_text
        for marker in ["LaTeX Warning", "Overfull \\hbox", "Underfull \\hbox"]
    )
    record(
        "paper_pdf_and_build_log",
        pdf_ok and not layout_issue,
        f"pdf_size={pdf_path.stat().st_size if pdf_path.exists() else 0}, layout_issue={layout_issue}",
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

    manifest = json.loads((results_dir / "run-manifest.json").read_text(encoding="utf-8"))
    input_hashes_ok = all(
        (workspace / relative).is_file()
        and sha256(workspace / relative) == expected
        for relative, expected in manifest["files"].items()
        if relative.startswith("input/")
    )
    record(
        "input_hashes_recomputed",
        input_hashes_ok,
        "actual input bytes match run-manifest.json",
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
                "detail": "Automated identities do not constitute a formal proof of modeling assumptions.",
            },
            {
                "id": "hardware_feasibility",
                "status": "needs_review",
                "detail": "Absolute current, voltage, thermal, and torque-rate limits are unavailable.",
            },
        ],
        "verified_files_sha256": {
            relative: sha256(workspace / relative)
            for relative in [
                "results/metrics.json",
                "paper/generated-results.tex",
                "paper/paper.md",
                "paper/main.tex",
                "paper/<SOURCE_FILE_REDACTED>",
            ]
            if (workspace / relative).is_file()
        },
    }
    (results_dir / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if failures:
        print("[fail] independent verification found failures")
        for failure in failures:
            print(f"  - {failure['id']}: {failure['detail']}")
        return 1
    print("[pass] independent numerical and artifact verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
