"""Stage-neutral, self-contained source and input integrity check."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


EXPECTED_INPUTS = {
    "input/data/<SOURCE_FILE_REDACTED>": "6ac7331378ab4b4275b65459d6e094663fee5ca41d1518d010bf5051bb4e0c63",
    "input/data/<SOURCE_FILE_REDACTED>": "0b6b845715fbe852ddde8b709e592455eba2c845ef0d3f2be144758f674d2fab",
    "input/data/<SOURCE_FILE_REDACTED>": "fdb95e99c881afaff62375182e4dd5d6ca2858297d2ee6450d747ce3b078d547",
    "input/problem/<SOURCE_FILE_REDACTED>": "15438c2878c21b16a3797363d52591467cd2dfcce671f6adc51fae09b4527d7a",
}

REQUIRED_SOURCES = [
    "problem-analysis.md",
    "data-audit.md",
    "assumptions.yaml",
    "variables.yaml",
    "model-selection.md",
    "solution-report.yaml",
    "reproducibility.yaml",
    "code/analyze.py",
    "code/fold_models.py",
    "code/permutation_links.py",
    "code/prepare_data.py",
    "code/extract_xls.ps1",
    "code/verify.py",
    "paper/main.tex",
    "paper/paper.md",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    failures: list[str] = []
    for relative, expected in EXPECTED_INPUTS.items():
        path = (workspace / relative).resolve()
        if workspace not in path.parents:
            failures.append(f"path escapes workspace: {relative}")
        elif not path.is_file():
            failures.append(f"missing input: {relative}")
        elif sha256(path) != expected:
            failures.append(f"input SHA-256 mismatch: {relative}")
    for relative in REQUIRED_SOURCES:
        if not (workspace / relative).is_file():
            failures.append(f"missing source: {relative}")
    if failures:
        for failure in failures:
            print(f"[fail] {failure}")
        return 1
    print(f"[pass] self-contained source check; {len(EXPECTED_INPUTS)} input hashes match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
