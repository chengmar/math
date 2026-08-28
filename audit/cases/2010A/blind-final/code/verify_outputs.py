"""Structural, provenance, paper-linkage, and status-vocabulary checks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


ALLOWED_STATUSES = {"pass", "fail", "needs_review"}
EXPECTED_BLIND_V1_TREE = "c0cbd712f1a085fa634971b10d2b5cb78c5ea9de71912dd7fbb2e70557c20f4c"
EXPECTED_BLIND_V1_FILES = 140


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_hash(root: Path) -> tuple[str, int]:
    """Reproduce the exact PowerShell snapshot algorithm used before edits."""

    environment = os.environ.copy()
    environment["CUMCM_READONLY_TREE_ROOT"] = str(root.resolve())
    script = r"""
$rootPath = (Resolve-Path -LiteralPath $env:CUMCM_READONLY_TREE_ROOT).Path
$records = Get-ChildItem -LiteralPath $rootPath -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($rootPath.Length + 1).Replace('\','/')
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
    "$relative`0$hash`n"
} | Sort-Object
$bytes = [System.Text.Encoding]::UTF8.GetBytes(($records -join ''))
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $treeHash = [System.BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()
}
finally { $sha.Dispose() }
[ordered]@{ file_count = $records.Count; tree_sha256 = $treeHash } | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"PowerShell tree snapshot failed: {completed.stderr}")
    payload = json.loads(completed.stdout)
    return str(payload["tree_sha256"]), int(payload["file_count"])


def collect_invalid_statuses(value, location: str = "root") -> list[dict[str, str]]:
    invalid: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if (key == "status" or key.endswith("_status")) and child not in ALLOWED_STATUSES:
                invalid.append({"location": child_location, "value": str(child)})
            invalid.extend(collect_invalid_statuses(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            invalid.extend(collect_invalid_statuses(child, f"{location}[{index}]"))
    return invalid


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    results = root / "results"
    checks: dict[str, dict] = {}

    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "requirements-lock.txt",
        "code/model.py",
        "code/solve.py",
        "code/extract_inputs.ps1",
        "code/render_paper.py",
        "code/verify_independent.py",
        "code/verify_outputs.py",
        "code/build_manifest.py",
        "code/run_all.ps1",
        "results/summary.json",
        "results/parameters.json",
        "results/data_audit.json",
        "results/run_metadata.json",
        "results/independent-verification.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/preamble.tex",
        "paper/paper.template.md",
        "paper/paper.md",
    ]
    missing = [relative for relative in required if not (root / relative).is_file()]
    checks["required_outputs"] = {
        "status": "pass" if not missing else "fail",
        "missing": missing,
    }

    forbidden_stale = [
        "code/geometry.py",
        "code/verify.py",
        "results/run_manifest.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
    ]
    stale_present = [relative for relative in forbidden_stale if (root / relative).exists()]
    checks["single_authoritative_generation"] = {
        "status": "pass" if not stale_present else "fail",
        "stale_or_conflicting_paths": stale_present,
    }

    small_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    actual_table = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    small_grid = bool(
        len(small_table) == 121
        and np.array_equal(small_table["height_mm"].to_numpy(), np.arange(0, 1201, 10))
    )
    actual_grid = bool(
        len(actual_table) == 31
        and np.array_equal(actual_table["height_mm"].to_numpy(), np.arange(0, 3001, 100))
    )
    monotone = bool(
        np.all(np.diff(small_table["volume_l_numeric"]) >= 0.0)
        and np.all(np.diff(actual_table["volume_l_numeric"]) >= 0.0)
    )
    envelopes = bool(
        np.all(
            small_table["model_envelope_lower_l_numeric"]
            <= small_table["volume_l_numeric"]
        )
        and np.all(
            small_table["volume_l_numeric"]
            <= small_table["model_envelope_upper_l_numeric"]
        )
        and np.all(
            actual_table["diagnostic_stress_lower_l_numeric"]
            <= actual_table["volume_l_numeric"]
        )
        and np.all(
            actual_table["volume_l_numeric"]
            <= actual_table["diagnostic_stress_upper_l_numeric"]
        )
    )
    reporting_precision = bool(
        np.issubdtype(small_table["volume_l_reported"].dtype, np.integer)
        and np.issubdtype(actual_table["volume_l_reported"].dtype, np.integer)
    )
    checks["tables"] = {
        "status": "pass"
        if small_grid and actual_grid and monotone and envelopes and reporting_precision
        else "fail",
        "small_grid": small_grid,
        "actual_grid": actual_grid,
        "monotone": monotone,
        "point_inside_diagnostic_envelopes": envelopes,
        "paper_reporting_precision_is_integer_litres": reporting_precision,
    }

    small_candidates = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    actual_candidates = pd.read_csv(results / "<SOURCE_FILE_REDACTED>")
    role_columns = bool(
        {"training_rmse_l", "time_check_rmse_l"}.issubset(small_candidates.columns)
        and {
            "selection_first_segment_rmse_l",
            "post_selection_time_check_rmse_l",
            "post_selection_refill_error_l",
        }.issubset(actual_candidates.columns)
    )
    checks["data_role_schema"] = {
        "status": "pass" if role_columns else "fail",
        "selection_and_check_columns_separated": role_columns,
    }

    independent = json.loads(
        (results / "independent-verification.json").read_text(encoding="utf-8")
    )
    checks["independent_numerical_verification"] = {
        "status": "pass" if independent.get("status") == "pass" else "fail",
        "reported_status": independent.get("status"),
    }

    metadata = json.loads((results / "run_metadata.json").read_text(encoding="utf-8"))
    hash_mismatch: list[str] = []
    for relative, expected in metadata["input_sha256"].items():
        path = root / relative
        if not path.is_file() or sha256(path) != expected:
            hash_mismatch.append(relative)
    checks["input_hashes"] = {
        "status": "pass" if not hash_mismatch else "fail",
        "mismatch": hash_mismatch,
    }

    blind_hash, blind_count = tree_hash(root / "blind-v1")
    blind_unchanged = (
        blind_hash == EXPECTED_BLIND_V1_TREE and blind_count == EXPECTED_BLIND_V1_FILES
    )
    checks["blind_v1_preserved"] = {
        "status": "pass" if blind_unchanged else "fail",
        "expected_file_count": EXPECTED_BLIND_V1_FILES,
        "actual_file_count": blind_count,
        "expected_tree_sha256": EXPECTED_BLIND_V1_TREE,
        "actual_tree_sha256": blind_hash,
        "algorithm": "initial PowerShell snapshot: Sort-Object(relative_path + NUL + sha256 + LF)",
    }

    summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
    status_documents = [
        ("results/summary.json", summary),
        ("results/parameters.json", json.loads((results / "parameters.json").read_text(encoding="utf-8"))),
        ("results/data_audit.json", json.loads((results / "data_audit.json").read_text(encoding="utf-8"))),
        ("results/independent-verification.json", independent),
    ]
    for relative in [
        "assumptions.yaml",
        "variables.yaml",
        "solution-report.yaml",
        "reproducibility.yaml",
    ]:
        status_documents.append(
            (relative, yaml.safe_load((root / relative).read_text(encoding="utf-8")))
        )
    invalid_statuses: list[dict[str, str]] = []
    for relative, document in status_documents:
        for item in collect_invalid_statuses(document, relative):
            invalid_statuses.append(item)
    checks["status_vocabulary"] = {
        "status": "pass" if not invalid_statuses else "fail",
        "invalid": invalid_statuses,
    }

    paper_markdown = (root / "paper" / "paper.md").read_text(encoding="utf-8")
    paper_tex = (root / "paper" / "main.tex").read_text(encoding="utf-8")
    alpha = summary["actual_tank"]["conditional_production_model"]["alpha_deg"]
    beta = summary["actual_tank"]["conditional_production_model"]["beta_abs_deg"]
    prohibited_claims = ["三层独立检验", "独立补油验证", "候选模型独立比较", "独立留出"]
    claims_found = [claim for claim in prohibited_claims if claim in paper_markdown or claim in paper_tex]
    paper_linked = bool(
        f"{alpha:.4f}" in paper_markdown
        and f"{beta:.4f}" in paper_markdown
        and "@@" not in paper_markdown
        and "\\input{../results/generated_values.tex}" in paper_tex
        and not claims_found
    )
    checks["paper_result_linkage_and_claims"] = {
        "status": "pass" if paper_linked else "fail",
        "markdown_contains_parameters": f"{alpha:.4f}" in paper_markdown and f"{beta:.4f}" in paper_markdown,
        "latex_uses_generated_values": "\\input{../results/generated_values.tex}" in paper_tex,
        "unreplaced_tokens": "fail" if "@@" in paper_markdown else "pass",
        "prohibited_overclaims_found": claims_found,
    }

    expected_figures = {
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    }
    actual_figures = {path.name for path in (root / "figures").glob("*.png")}
    small_figures = [
        path.name
        for path in (root / "figures").glob("*.png")
        if path.stat().st_size < 5000
    ]
    figures_ok = actual_figures == expected_figures and not small_figures
    checks["figures"] = {
        "status": "pass" if figures_ok else "fail",
        "expected": sorted(expected_figures),
        "actual": sorted(actual_figures),
        "too_small": small_figures,
    }

    pdf = root / "paper" / "<SOURCE_FILE_REDACTED>"
    pdf_status = "needs_review"
    pdf_evidence: dict[str, object] = {"exists": pdf.is_file()}
    if pdf.is_file():
        try:
            completed = subprocess.run(
                ["pdftotext", "-f", "1", "-l", "2", str(pdf), "-"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            extracted = completed.stdout
            pdf_ok = (
                completed.returncode == 0
                and "储油罐的变位识别与罐容表标定" in extracted.replace(" ", "")
                and f"{alpha:.4f}" in extracted
            )
            pdf_status = "pass" if pdf_ok else "fail"
            pdf_evidence.update(
                {
                    "pdftotext_exit_code": completed.returncode,
                    "title_found": "储油罐的变位识别与罐容表标定" in extracted.replace(" ", ""),
                    "alpha_found": f"{alpha:.4f}" in extracted,
                    "bytes": pdf.stat().st_size,
                }
            )
        except FileNotFoundError:
            pdf_status = "needs_review"
            pdf_evidence["note"] = "pdftotext is unavailable"
    checks["paper_compilation"] = {"status": pdf_status, **pdf_evidence}

    pycache = [
        path.relative_to(root).as_posix()
        for base in (root / "code", root / "results", root / "paper", root / "figures")
        for path in base.rglob("*")
        if "__pycache__" in path.parts or path.suffix == ".pyc"
    ]
    checks["no_executable_cache_artifacts"] = {
        "status": "pass" if not pycache else "fail",
        "paths": pycache,
    }

    failed = [name for name, item in checks.items() if item["status"] == "fail"]
    report = {
        "schema_version": 2,
        "case_id": "2010A",
        "phase": "blind-revision",
        "status": "fail" if failed else "pass",
        "failed_checks": failed,
        "checks": checks,
        "engineering_readiness": {
            "status": "needs_review",
            "note": "Meter, probe, and geometry tolerances are not supplied; diagnostic scenario ranges are not confidence bounds.",
        },
    }
    (results / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if failed:
        print("[FAIL] output verification: " + ", ".join(failed))
        sys.exit(1)
    print("[PASS] structure, provenance, status vocabulary, paper linkage, and V1 preservation")


if __name__ == "__main__":
    main()
