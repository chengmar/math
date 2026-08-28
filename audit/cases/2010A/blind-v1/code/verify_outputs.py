"""Independent structural and numerical checks for generated solution outputs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from model import ActualTank, SmallEllipticalTank


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    results = root / "results"
    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    metadata = json.loads((results / "run_metadata.json").read_text(encoding="utf-8"))
    checks: dict[str, dict] = {}

    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "code/model.py",
        "code/solve.py",
        "code/run_all.ps1",
        "results/summary.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/paper.md",
    ]
    missing = [path for path in required if not (root / path).exists()]
    checks["required_outputs"] = {
        "status": "pass" if not missing else "fail",
        "missing": missing,
    }

    small_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    actual_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    small_step_ok = (
        len(small_table) == 121
        and np.array_equal(small_table["height_mm"].to_numpy(), np.arange(0, 1201, 10))
    )
    actual_step_ok = (
        len(actual_table) == 31
        and np.array_equal(actual_table["height_mm"].to_numpy(), np.arange(0, 3001, 100))
    )
    monotone_ok = bool(
        np.all(np.diff(small_table["tilted_volume_l"]) >= 0.0)
        and np.all(np.diff(actual_table["displaced_volume_l"]) >= 0.0)
    )
    checks["table_schema_and_monotonicity"] = {
        "status": "pass" if small_step_ok and actual_step_ok and monotone_ok else "fail",
        "small_rows": int(len(small_table)),
        "actual_rows": int(len(actual_table)),
        "monotone": monotone_ok,
    }

    small_summary = summary["small_tank"]
    small_refined = SmallEllipticalTank(cell_width=0.005)
    small_recomputed = small_refined.volume_l(
        small_table["height_mm"].to_numpy(float) / 1000.0,
        4.1,
        small_summary["effective_horizontal_semiaxis_m"],
    )
    small_difference = float(
        np.max(np.abs(small_recomputed - small_table["tilted_volume_l"].to_numpy(float)))
    )
    actual_summary = summary["actual_tank"]
    alpha = actual_summary["final_fit"]["alpha_deg"]
    beta = actual_summary["final_fit"]["beta_deg"]
    actual_refined = ActualTank(cell_width=0.0125)
    actual_recomputed = actual_refined.volume_l(
        actual_table["height_mm"].to_numpy(float) / 1000.0, alpha, beta
    )
    actual_difference = float(
        np.max(
            np.abs(
                actual_recomputed
                - actual_table["displaced_volume_l"].to_numpy(float)
            )
        )
    )
    numerical_ok = small_difference <= 0.011 and actual_difference <= 0.011
    checks["independent_table_recomputation"] = {
        "status": "pass" if numerical_ok else "fail",
        "small_max_abs_difference_l": small_difference,
        "actual_max_abs_difference_l": actual_difference,
        "tolerance_l": 0.011,
    }

    actual_data = pd.read_csv(results / "extracted" / "<SOURCE_FILE_REDACTED>")
    height = actual_data["显示油高/mm"].to_numpy(float) / 1000.0
    inflow = actual_data["进油量/L"].to_numpy(float)
    outflow = actual_data["出油量/L"].fillna(0.0).to_numpy(float)
    refill = int(np.flatnonzero(inflow > 0.0)[0])
    index = np.concatenate([np.arange(1, refill), np.arange(refill + 1, len(actual_data))])
    volume = actual_refined.volume_l(height, alpha, beta)
    residual = volume[index] - volume[index - 1] - (inflow[index] - outflow[index])
    rmse = float(np.sqrt(np.mean(residual**2)))
    reported_rmse = float(actual_summary["final_fit"]["all_discharge_increment"]["rmse_l"])
    refill_error = float(volume[refill] - volume[refill - 1] - inflow[refill])
    reported_refill_error = float(actual_summary["refill"]["error_l"])
    mass_balance_ok = (
        abs(rmse - reported_rmse) <= 0.002
        and abs(refill_error - reported_refill_error) <= 0.05
    )
    checks["mass_balance_recomputation"] = {
        "status": "pass" if mass_balance_ok else "fail",
        "rmse_l": rmse,
        "reported_rmse_l": reported_rmse,
        "refill_error_l": refill_error,
        "reported_refill_error_l": reported_refill_error,
    }

    hash_mismatch = []
    for relative, expected in metadata["input_sha256"].items():
        path = root / relative
        actual = sha256(path) if path.exists() else None
        if actual != expected:
            hash_mismatch.append(relative)
    checks["input_hashes"] = {
        "status": "pass" if not hash_mismatch else "fail",
        "mismatch": hash_mismatch,
    }

    paper_md = (root / "paper" / "paper.md").read_text(encoding="utf-8")
    paper_tex = (root / "paper" / "main.tex").read_text(encoding="utf-8")
    paper_consistent = (
        f"{alpha:.4f}" in paper_md
        and f"{beta:.4f}" in paper_md
        and "\\input{../results/generated_values.tex}" in paper_tex
        and "@@" not in paper_md
    )
    checks["paper_result_linkage"] = {
        "status": "pass" if paper_consistent else "fail",
        "markdown_contains_final_parameters": paper_consistent,
        "tex_uses_generated_macros": "\\input{../results/generated_values.tex}" in paper_tex,
    }

    figure_paths = list((root / "figures").glob("*.png"))
    bad_figures = [str(path.name) for path in figure_paths if path.stat().st_size < 5000]
    checks["figures"] = {
        "status": "pass" if len(figure_paths) >= 5 and not bad_figures else "fail",
        "count": len(figure_paths),
        "too_small": bad_figures,
    }

    pdf = root / "paper" / "<SOURCE_FILE_REDACTED>"
    checks["paper_compilation"] = {
        "status": "pass" if pdf.exists() and pdf.stat().st_size > 10000 else "needs_review",
        "note": "Source is complete; PDF status is based only on presence of a nontrivial local build.",
    }
    checks["official_format_rules"] = {
        "status": "needs_review",
        "note": "The allowed template states that the official-year format configuration itself needs review.",
    }
    checks["mathematical_correctness"] = {
        "status": "needs_review",
        "note": "Numerical invariants and independent recomputation pass, but this script does not claim a proof of mathematical correctness.",
    }

    failed = [name for name, value in checks.items() if value["status"] == "fail"]
    report = {
        "status": "fail" if failed else "pass",
        "failed_checks": failed,
        "checks": checks,
    }
    (results / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if failed:
        print("[FAIL] " + ", ".join(failed))
        sys.exit(1)
    print("[PASS] numerical, structural, hash, table, figure, and paper-linkage checks")


if __name__ == "__main__":
    main()
