from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Record paper sources used by the current PDF build.")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    pdf_path = workspace / "paper" / "<SOURCE_FILE_REDACTED>"
    missing = [relative for relative in PAPER_SOURCES if not (workspace / relative).is_file()]
    if not pdf_path.is_file():
        missing.append("paper/<SOURCE_FILE_REDACTED>")
    if missing:
        raise FileNotFoundError(f"Missing paper build inputs/outputs: {missing}")

    version = subprocess.run(
        ["xelatex", "--version"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.splitlines()[0]
    record = {
        "status": "pass",
        "producer": "code/run_all.ps1 (two XeLaTeX passes), then code/record_build.py",
        "command": "xelatex -interaction=nonstopmode -halt-on-error main.tex (twice)",
        "engine": version,
        "source_order": PAPER_SOURCES,
        "source_sha256": {
            relative: sha256(workspace / relative) for relative in PAPER_SOURCES
        },
        "pdf": {
            "path": "paper/<SOURCE_FILE_REDACTED>",
            "sha256": sha256(pdf_path),
            "size_bytes": pdf_path.stat().st_size,
            "reproducibility_level": "content/source freshness; timestamp-dependent PDF byte identity is not promised",
        },
    }
    output = workspace / "results" / "paper-build.json"
    output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("[pass] paper build source/PDF hashes recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
