from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"
PAPER = ROOT / "paper"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def main() -> None:
    key = json.loads((RESULTS / "key_results.json").read_text(encoding="utf-8"))
    zone = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>").set_index("zone")
    source = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>").set_index("metal")
    model = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>").set_index("model")
    nmf_cv = pd.read_csv(RESULTS / "<SOURCE_FILE_REDACTED>").set_index(
        "components"
    )
    markdown = (PAPER / "paper.md").read_text(encoding="utf-8")
    tex = (PAPER / "main.tex").read_text(encoding="utf-8")
    macros = (PAPER / "generated" / "macros.tex").read_text(encoding="utf-8")
    tex_log = (PAPER / "main.log").read_text(encoding="utf-8", errors="replace")
    solution_report = (ROOT / "solution-report.yaml").read_text(encoding="utf-8")
    reproducibility_text = (ROOT / "reproducibility.yaml").read_text(
        encoding="utf-8"
    )
    build_manifest = json.loads(
        (REPORTS / "paper-build.json").read_text(encoding="utf-8")
    )

    checks: list[dict] = []

    markdown_expected = [
        f"{model.loc['gaussian_nested', 'mean_rmse_log2']:.3f}",
        f"{model.loc['idw_p2_k12', 'mean_rmse_log2']:.3f}",
        f"{model.loc['constant_mean', 'mean_rmse_log2']:.3f}",
        *[
            f"{zone.loc[index, 'typical_geometric_enrichment']:.3f}"
            for index in [2, 4, 1, 5, 3]
        ],
        *[f"{nmf_cv.loc[index, 'cv_rmse_mean']:.5f}" for index in [2, 3, 4]],
        *[
            f"({source.loc[metal, 'source_x_m'] / 1000:.2f}, {source.loc[metal, 'source_y_m'] / 1000:.2f})"
            for metal in ["As", "Cd", "Cr", "Cu", "Hg", "Ni", "Pb", "Zn"]
        ],
    ]
    missing_markdown = [value for value in markdown_expected if value not in markdown]
    checks.append(
        {
            "check": "markdown_key_numbers",
            "status": "pass" if not missing_markdown else "fail",
            "missing": missing_markdown,
        }
    )

    macro_expected = [
        f"\\newcommand{{\\SampleCount}}{{{key['data']['sample_count']}}}",
        f"\\newcommand{{\\GaussianRMSE}}{{{model.loc['gaussian_nested', 'mean_rmse_log2']:.3f}}}",
        f"\\newcommand{{\\IDWRMSE}}{{{model.loc['idw_p2_k12', 'mean_rmse_log2']:.3f}}}",
        f"\\newcommand{{\\ConstantRMSE}}{{{model.loc['constant_mean', 'mean_rmse_log2']:.3f}}}",
        f"\\newcommand{{\\IndustrialIndex}}{{{zone.loc[2, 'typical_geometric_enrichment']:.3f}}}",
        f"\\newcommand{{\\TrafficIndex}}{{{zone.loc[4, 'typical_geometric_enrichment']:.3f}}}",
        f"\\newcommand{{\\NMFTwoRMSE}}{{{nmf_cv.loc[2, 'cv_rmse_mean']:.5f}}}",
        f"\\newcommand{{\\NMFThreeRMSE}}{{{nmf_cv.loc[3, 'cv_rmse_mean']:.5f}}}",
        f"\\newcommand{{\\NMFFourRMSE}}{{{nmf_cv.loc[4, 'cv_rmse_mean']:.5f}}}",
    ]
    missing_macros = [value for value in macro_expected if value not in macros]
    checks.append(
        {
            "check": "tex_generated_macros",
            "status": "pass" if not missing_macros else "fail",
            "missing": missing_macros,
        }
    )

    required_inputs = [
        "generated/macros",
        "generated/table_spatial_models.tex",
        "generated/table_zone_summary.tex",
        "generated/table_nmf_loadings.tex",
        "generated/table_sources.tex",
        "generated/table_propagation.tex",
    ]
    missing_inputs = [value for value in required_inputs if value not in tex]
    checks.append(
        {
            "check": "tex_generated_inputs",
            "status": "pass" if not missing_inputs else "fail",
            "missing": missing_inputs,
        }
    )

    required_figures = [
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
        "<SOURCE_FILE_REDACTED>",
    ]
    missing_figures = [
        name
        for name in required_figures
        if name not in tex or not (ROOT / "figures" / name).exists()
    ]
    checks.append(
        {
            "check": "paper_figure_references",
            "status": "pass" if not missing_figures else "fail",
            "missing": missing_figures,
        }
    )

    build_mismatches = []
    for record in build_manifest.get("source_files", []):
        path = ROOT / record["path"]
        if not path.is_file():
            build_mismatches.append({"path": record["path"], "reason": "missing"})
            continue
        actual_hash = sha256_file(path)
        if actual_hash != record["sha256"] or path.stat().st_size != record["bytes"]:
            build_mismatches.append(
                {
                    "path": record["path"],
                    "reason": "hash_or_size_changed_after_build",
                    "expected_sha256": record["sha256"],
                    "actual_sha256": actual_hash,
                }
            )
    for artifact in ["pdf", "log"]:
        record = build_manifest.get(artifact, {})
        path = ROOT / record.get("path", "missing")
        if not path.is_file() or sha256_file(path) != record.get("sha256"):
            build_mismatches.append(
                {"path": record.get("path"), "reason": f"{artifact}_manifest_mismatch"}
            )
    checks.append(
        {
            "check": "clean_build_manifest_identity",
            "status": "pass"
            if build_manifest.get("status") == "pass"
            and build_manifest.get("consecutive_compile_count") == 2
            and build_manifest.get("temporary_build_directory_removed_status")
            == "pass"
            and not build_mismatches
            else "fail",
            "mismatches": build_mismatches,
            "consecutive_compile_count": build_manifest.get(
                "consecutive_compile_count"
            ),
        }
    )

    fatal_patterns = [
        r"! LaTeX Error",
        r"Undefined control sequence",
        r"Missing \$ inserted",
        r"Emergency stop",
    ]
    fatal_log_hits = [pattern for pattern in fatal_patterns if re.search(pattern, tex_log)]
    page_match = re.search(r"\((\d+) pages?\)", tex_log)
    page_count = int(page_match.group(1)) if page_match else None
    compiled = bool(page_count and (PAPER / "<SOURCE_FILE_REDACTED>").exists())
    checks.append(
        {
            "check": "xelatex_compile",
            "status": "pass" if compiled and not fatal_log_hits else "fail",
            "fatal_patterns": fatal_log_hits,
            "pages": page_count,
        }
    )

    source_markers = [
        "固定采样设计置换",
        "空间块 bootstrap",
        "折内估计 RMS",
        "全样点 leave-one-out",
        "初始条件",
        "边界条件",
        "核归一化",
    ]
    missing_source_markers = [marker for marker in source_markers if marker not in tex]
    checks.append(
        {
            "check": "source_semantic_markers",
            "status": "pass" if not missing_source_markers else "fail",
            "missing": missing_source_markers,
        }
    )

    pdftotext = shutil.which("pdftotext")
    pdf_text = ""
    pdf_text_error = None
    if pdftotext:
        completed = subprocess.run(
            [pdftotext, "-layout", str(PAPER / "<SOURCE_FILE_REDACTED>"), "-"],
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0:
            pdf_text = completed.stdout.decode("utf-8", errors="replace")
            (REPORTS / "paper-extracted.txt").write_text(pdf_text, encoding="utf-8")
        else:
            pdf_text_error = completed.stderr.decode("utf-8", errors="replace")
    else:
        pdf_text_error = "pdftotext unavailable"
    normalized_pdf = normalized_text(pdf_text)
    pdf_expected = source_markers + [
        f"{model.loc['gaussian_nested', 'mean_rmse_log2']:.3f}",
        f"{zone.loc[2, 'typical_geometric_enrichment']:.3f}",
        f"{nmf_cv.loc[3, 'cv_rmse_mean']:.5f}",
        "needs_review",
    ]
    missing_pdf_semantics = [
        value for value in pdf_expected if normalized_text(value) not in normalized_pdf
    ]
    pdf_text_hash = hashlib.sha256(normalized_pdf.encode("utf-8")).hexdigest()
    checks.append(
        {
            "check": "pdf_extracted_semantics",
            "status": "pass"
            if pdf_text and not missing_pdf_semantics and pdf_text_error is None
            else "fail",
            "missing": missing_pdf_semantics,
            "error": pdf_text_error,
            "normalized_text_sha256": pdf_text_hash,
            "normalized_character_count": len(normalized_pdf),
        }
    )

    reproduction = json.loads(
        (REPORTS / "reproduction-check.json").read_text(encoding="utf-8")
    )
    checks.append(
        {
            "check": "two_run_reproduction",
            "status": reproduction["status"],
            "compared_file_count": reproduction["compared_file_count"],
            "mismatch_count": len(reproduction["mismatches"]),
        }
    )

    required_solution_sections = [
        "case_id",
        "questions",
        "baseline",
        "main_model",
        "validation",
        "limitations",
        "reproducibility",
    ]
    missing_solution_sections = [
        section
        for section in required_solution_sections
        if not re.search(rf"(?m)^{re.escape(section)}:", solution_report)
    ]
    metadata_expected = [
        ("solution_phase", "phase: blind-revision" in solution_report),
        ("solution_not_frozen", "  frozen: false" in solution_report),
        ("reproducibility_phase", "phase: blind-revision" in reproducibility_text),
        (
            "reproduction_file_count",
            f"compared_files: {reproduction['compared_file_count']}"
            in reproducibility_text,
        ),
        (
            "current_pdf_sha256",
            f"pdf_sha256: {sha256_file(PAPER / '<SOURCE_FILE_REDACTED>')}"
            in reproducibility_text,
        ),
        (
            "current_pdf_text_sha256",
            f"normalized_pdf_text_sha256: {pdf_text_hash}"
            in reproducibility_text,
        ),
        (
            "temporary_build_removed",
            "paper_build_removed_after_copy_status: pass"
            in reproducibility_text,
        ),
    ]
    failed_metadata = [name for name, passed in metadata_expected if not passed]
    checks.append(
        {
            "check": "output_contract_and_metadata",
            "status": "pass"
            if not missing_solution_sections and not failed_metadata
            else "fail",
            "missing_solution_sections": missing_solution_sections,
            "failed_metadata": failed_metadata,
        }
    )

    status = "pass" if all(row["status"] == "pass" for row in checks) else "fail"
    payload = {
        "status": status,
        "checks": checks,
        "pdf_normalized_text_sha256": pdf_text_hash,
        "note": (
            "This check binds current sources, generated tables, figures, the clean-build "
            "manifest, extracted PDF semantics and two-run numerical reproduction. It "
            "does not upgrade scientific source identity or external validity."
        ),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "paper-consistency.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if status != "pass":
        raise SystemExit("fail: paper/result/source/PDF consistency check failed")
    print("pass: source, generated results and PDF semantics belong to one build chain")


if __name__ == "__main__":
    main()
