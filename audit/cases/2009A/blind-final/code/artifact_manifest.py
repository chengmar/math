from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


RESULT_FILES = {
    "results/<SOURCE_FILE_REDACTED>": "code/extract_data.ps1",
    "results/input-metadata.json": "code/extract_data.ps1",
    "results/metrics.json": "code/solve.py",
    "results/data-audit.json": "code/solve.py",
    "results/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "results/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "results/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "results/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "results/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "results/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "results/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "results/summary.md": "code/solve.py",
    "results/run-manifest.json": "code/solve.py",
    "results/paper-build.json": "code/record_build.py",
    "results/verification.json": "code/verify.py",
}
FIGURE_FILES = {
    "figures/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "figures/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "figures/<SOURCE_FILE_REDACTED>": "code/solve.py",
    "figures/<SOURCE_FILE_REDACTED>": "code/solve.py",
}
PAPER_GENERATED = {
    "paper/generated-results.tex": "code/solve.py",
    "paper/main.aux": "xelatex",
    "paper/main.log": "xelatex",
    "paper/main.out": "xelatex",
    "paper/<SOURCE_FILE_REDACTED>": "xelatex",
}
PAPER_SOURCES = {
    "paper/main.tex",
    "paper/preamble.tex",
    "paper/paper.md",
    "paper/sections/01-problem.tex",
    "paper/sections/02-analysis.tex",
    "paper/sections/03-assumptions-symbols.tex",
    "paper/sections/04-data-model.tex",
    "paper/sections/05-solution-validation.tex",
    "paper/sections/06-evaluation-conclusion.tex",
    "paper/sections/appendix.tex",
}
CODE_SOURCES = {
    "code/extract_data.ps1",
    "code/solve.py",
    "code/verify.py",
    "code/record_build.py",
    "code/artifact_manifest.py",
    "code/run_all.ps1",
}
MANIFEST_PATH = "results/artifact-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def listed_files(workspace: Path, directory: str) -> set[str]:
    root = workspace / directory
    if not root.exists():
        return set()
    return {
        path.relative_to(workspace).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def command_for(producer: str) -> str:
    return {
        "code/extract_data.ps1": "powershell -NoProfile -File code/extract_data.ps1 -Workspace <workspace>",
        "code/solve.py": "python code/solve.py --workspace <workspace>",
        "xelatex": "xelatex -interaction=nonstopmode -halt-on-error main.tex (twice)",
        "code/record_build.py": "python code/record_build.py --workspace <workspace>",
        "code/verify.py": "python code/verify.py --workspace <workspace>",
    }[producer]


def inputs_for(producer: str) -> list[str]:
    return {
        "code/extract_data.ps1": ["input/data/<SOURCE_FILE_REDACTED>"],
        "code/solve.py": ["results/<SOURCE_FILE_REDACTED>", "results/input-metadata.json", "code/solve.py"],
        "xelatex": sorted(PAPER_SOURCES | {"paper/generated-results.tex"}),
        "code/record_build.py": sorted(PAPER_SOURCES | {"paper/generated-results.tex", "paper/<SOURCE_FILE_REDACTED>"}),
        "code/verify.py": ["results/metrics.json", "results/<SOURCE_FILE_REDACTED>", "results/paper-build.json"],
    }[producer]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and validate the unique generated-artifact manifest.")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    generated = {**RESULT_FILES, **FIGURE_FILES, **PAPER_GENERATED}
    manifest_file = workspace / MANIFEST_PATH

    actual_results = listed_files(workspace, "results")
    expected_results_before = set(RESULT_FILES)
    if manifest_file.is_file():
        expected_results_before.add(MANIFEST_PATH)
    actual_figures = listed_files(workspace, "figures")
    actual_paper = listed_files(workspace, "paper")
    actual_code = listed_files(workspace, "code")
    unexpected = sorted(
        (actual_results - expected_results_before)
        | (actual_figures - set(FIGURE_FILES))
        | (actual_paper - (PAPER_SOURCES | set(PAPER_GENERATED)))
        | (actual_code - CODE_SOURCES)
    )
    missing = sorted(
        (set(RESULT_FILES) - actual_results)
        | (set(FIGURE_FILES) - actual_figures)
        | ((PAPER_SOURCES | set(PAPER_GENERATED)) - actual_paper)
        | (CODE_SOURCES - actual_code)
    )

    artifact_rows: list[dict[str, Any]] = []
    for relative, producer in sorted(generated.items()):
        path = workspace / relative
        if path.is_file():
            artifact_rows.append(
                {
                    "path": relative,
                    "sha256": sha256(path),
                    "size_bytes": path.stat().st_size,
                    "producer": producer,
                    "command": command_for(producer),
                    "inputs": inputs_for(producer),
                    "reproducibility_level": "content"
                    if relative == "paper/<SOURCE_FILE_REDACTED>"
                    else "byte",
                }
            )

    status = "pass" if not missing and not unexpected and len(artifact_rows) == len(generated) else "fail"
    report = {
        "status": status,
        "authoritative_entrypoint": "powershell -NoProfile -ExecutionPolicy Bypass -File code/run_all.ps1 -Workspace <workspace>",
        "policy": "Exactly one generated artifact set is retained; this manifest excludes itself to avoid a recursive hash.",
        "generated_artifact_count_excluding_manifest": len(generated),
        "recorded_artifact_count": len(artifact_rows),
        "missing": missing,
        "unexpected": unexpected,
        "artifacts": artifact_rows,
    }
    manifest_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    final_results = listed_files(workspace, "results")
    final_expected = set(RESULT_FILES) | {MANIFEST_PATH}
    final_status = "pass" if status == "pass" and final_results == final_expected else "fail"
    if final_status != "pass":
        print(f"[fail] artifact set mismatch; missing={missing}, unexpected={unexpected}")
        return 1
    print(f"[pass] unique artifact set recorded ({len(generated)} artifacts plus manifest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
