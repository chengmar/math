"""Final consistency checks and last-write staging manifest generation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml


EXPECTED_INPUT_HASHES = {
    "input/problem/<SOURCE_FILE_REDACTED>": "cea35513e302801d4504f3febdf444af783d928c4748bdf91a21724a910e271a",
    "input/attachments/<SOURCE_FILE_REDACTED>": "ddd7b8e70aa727a2858e2476ddfeda7e3042be09d55b304297f990a203071e4f",
}
EXCLUDED_ROOTS = {
    "audit": "独立审计输入，只作为修订证据，不属于待冻结交付树。",
    "blind-v1": "保留的冻结V1，只作为修订基线，不属于待冻结交付树。",
}
EXCLUDED_SUBTREES = {
    "working/pdf-preview": "论文视觉抽查的临时位图，不属于待冻结交付树。",
    "working/pdf-preview-final": "最终论文视觉抽查的临时位图，不属于待冻结交付树。",
}
MANIFEST_RELATIVE = "results/reproduction-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    results = workspace / "results"

    required = [
        "problem-analysis.md",
        "data-audit.md",
        "assumptions.yaml",
        "variables.yaml",
        "model-selection.md",
        "solution-report.yaml",
        "reproducibility.yaml",
        "audit-response.md",
        "code/extract_docx.py",
        "code/build_data.py",
        "code/solve_case.py",
        "code/check_reproducibility.py",
        "code/validate.py",
        "code/check_paper_build.py",
        "code/verify_results.py",
        "code/verify_manifest.py",
        "code/run_all.ps1",
        "results/key_results.json",
        "results/verification.json",
        "results/reproducibility_check.json",
        "results/validation.json",
        "results/paper_build.json",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/paper.md",
        "paper/<SOURCE_FILE_REDACTED>",
        "paper/main.log",
        "paper/generated-values.tex",
        "paper/generated-paper-values.json",
    ]
    missing = [relative for relative in required if not (workspace / relative).is_file()]
    tests: dict[str, str] = {
        "required_outputs_exist": "pass" if not missing else "fail",
    }

    input_hashes = {relative: sha256(workspace / relative) for relative in EXPECTED_INPUT_HASHES}
    tests["original_input_hashes"] = "pass" if input_hashes == EXPECTED_INPUT_HASHES else "fail"

    validation = load_json(results / "validation.json")
    reproducibility = load_json(results / "reproducibility_check.json")
    paper_build = load_json(results / "paper_build.json")
    tests["validation_has_no_fail"] = "pass" if not validation["failed_checks"] else "fail"
    tests["canonical_python_pipeline_exact_rerun"] = reproducibility["status"]
    tests["paper_build"] = paper_build["status"]

    generated_values = load_json(workspace / "paper" / "generated-paper-values.json")
    generated_tex = (workspace / "paper" / "generated-values.tex").read_text(encoding="utf-8")
    main_tex = (workspace / "paper" / "main.tex").read_text(encoding="utf-8")
    paper_md = (workspace / "paper" / "paper.md").read_text(encoding="utf-8")
    macro_ok = all(
        f"\\newcommand{{\\{name}}}{{{value}}}" in generated_tex
        for name, value in generated_values.items()
    )
    markdown_ok = all(
        generated_values[name] in paper_md
        for name in ["OverallDrinkablePct", "OverallIVVPct", "OverallInferiorPct", "TreatmentFirst", "TreatmentLast"]
    )
    tests["paper_result_consistency"] = (
        "pass" if macro_ok and markdown_ok and "\\input{generated-values}" in main_tex else "fail"
    )

    with (workspace / "solution-report.yaml").open(encoding="utf-8") as handle:
        solution_report = yaml.safe_load(handle)
    with (workspace / "reproducibility.yaml").open(encoding="utf-8") as handle:
        reproducibility_yaml = yaml.safe_load(handle)
    tests["solution_report_phase_is_blind_revision"] = (
        "pass" if solution_report.get("phase") == "blind-revision" else "fail"
    )
    tests["no_freeze_or_cross_phase_claim"] = (
        "pass"
        if solution_report.get("freeze") is False
        and solution_report.get("cross_phase_resume") is False
        and solution_report.get("audit_invoked") is False
        else "fail"
    )
    tests["reproducibility_command_recorded"] = (
        "pass" if reproducibility_yaml.get("commands") else "fail"
    )

    audit_response = (workspace / "audit-response.md").read_text(encoding="utf-8")
    tests["audit_findings_traced"] = (
        "pass" if "AUD-001" in audit_response and "AUD-002" in audit_response else "fail"
    )
    tests["external_authoritative_freeze"] = "needs_review"
    tests["external_mathematical_and_policy_guarantee"] = "needs_review"

    if "fail" in tests.values():
        overall = "fail"
    elif "needs_review" in tests.values():
        overall = "needs_review"
    else:
        overall = "pass"
    final = {
        "schema_version": 2,
        "phase": "blind-revision",
        "overall": overall,
        "tests": tests,
        "missing": missing,
        "validation_overall": validation["overall_status"],
        "validation_needs_review": validation["needs_review_checks"],
        "reproducibility_tracked_file_count": reproducibility["tracked_file_count"],
        "paper_build": {
            "status": paper_build["status"],
            "pages": paper_build["pages"],
            "pdf_sha256": paper_build["pdf_sha256"],
            "log_sha256": paper_build["log_sha256"],
        },
    }
    (results / "final-verification.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    entries = []
    excluded_counts = {root: 0 for root in EXCLUDED_ROOTS}
    excluded_subtree_counts = {root: 0 for root in EXCLUDED_SUBTREES}
    excluded_self = 0
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        first = relative.split("/", 1)[0]
        if first in EXCLUDED_ROOTS:
            excluded_counts[first] += 1
            continue
        excluded_subtree = next(
            (
                root
                for root in EXCLUDED_SUBTREES
                if relative == root or relative.startswith(root + "/")
            ),
            None,
        )
        if excluded_subtree is not None:
            excluded_subtree_counts[excluded_subtree] += 1
            continue
        if relative == MANIFEST_RELATIVE:
            excluded_self += 1
            continue
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    entries.sort(key=lambda entry: entry["path"])
    manifest = {
        "schema_version": 2,
        "case_id": "2005A",
        "phase": "blind-revision",
        "status": "pass",
        "algorithm": "sha256",
        "scope": {
            "coverage_status": "pass",
            "description": "当前工作区除显式证据根目录和清单自身外的全部文件。",
            "excluded_roots": [
                {"path": root, "reason": reason, "file_count": excluded_counts[root]}
                for root, reason in EXCLUDED_ROOTS.items()
            ],
            "excluded_subtrees": [
                {"path": root, "reason": reason, "file_count": excluded_subtree_counts[root]}
                for root, reason in EXCLUDED_SUBTREES.items()
            ],
            "excluded_files": [
                {
                    "path": MANIFEST_RELATIVE,
                    "reason": "SHA-256清单不能递归绑定自身；由外部冻结摘要绑定。",
                    "file_count": excluded_self,
                }
            ],
        },
        "file_count": len(entries),
        "total_size_bytes": sum(entry["size_bytes"] for entry in entries),
        "files": entries,
        "input_hashes": input_hashes,
        "read_only_post_write_verification": "needs_review",
        "authoritative_external_freeze": "needs_review",
        "note": "本清单是blind-revision交付前的暂存清单；外部冻结脚本必须在生成blind-final后重新覆盖完整冻结树并绑定清单自身。",
    }
    (results / "reproduction-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "overall": overall,
                "manifest_status": "pass",
                "manifest_file_count": len(entries),
                "authoritative_external_freeze": "needs_review",
            },
            ensure_ascii=False,
        )
    )
    if overall == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
