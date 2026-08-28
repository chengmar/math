"""Build a deterministic manifest for every declared generated artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generator_for(relative: str) -> tuple[str, str]:
    if relative.startswith("results/extracted/"):
        return "code/extract_xls.ps1", "pwsh -NoProfile -NonInteractive -File code\\extract_xls.ps1 -Workspace ."
    if relative.startswith("results/clean/"):
        return "code/prepare_data.py", "python code\\prepare_data.py --workspace ."
    if relative == "results/verification.json":
        return "code/verify.py", "python code\\verify.py --workspace ."
    if relative == "results/reproduction-test.json":
        return "code/clean_reproduce.ps1", "pwsh -NoProfile -NonInteractive -File code\\clean_reproduce.ps1 -Workspace ."
    if relative == "paper/<SOURCE_FILE_REDACTED>":
        return "paper/main.tex + results/paper_values.tex", "xelatex -interaction=nonstopmode -halt-on-error main.tex (twice in paper/)"
    return "code/analyze.py + code/fold_models.py + code/permutation_links.py", "python code\\analyze.py --workspace ."


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    manifest_path = workspace / "results" / "artifact-manifest.json"
    roots = [workspace / "results", workspace / "figures"]
    candidates: list[Path] = []
    for root in roots:
        if root.is_dir():
            candidates.extend(path for path in root.rglob("*") if path.is_file())
    paper_pdf = workspace / "paper" / "<SOURCE_FILE_REDACTED>"
    if paper_pdf.is_file():
        candidates.append(paper_pdf)
    candidates = sorted(
        {path.resolve() for path in candidates if path.resolve() != manifest_path.resolve()},
        key=lambda path: path.relative_to(workspace).as_posix(),
    )
    input_paths = sorted((workspace / "input").rglob("*"))
    input_hashes = {
        path.relative_to(workspace).as_posix(): sha256(path)
        for path in input_paths
        if path.is_file()
    }
    entries: list[dict[str, Any]] = []
    for path in candidates:
        relative = path.relative_to(workspace).as_posix()
        source, command = generator_for(relative)
        entries.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "source_or_generator": source,
                "command": command,
                "input_set": "case_inputs",
            }
        )
    serialization = "for each entry in path order: UTF-8 path, NUL, lowercase SHA-256, LF"
    tree_bytes = b"".join(
        item["path"].encode("utf-8") + b"\0" + item["sha256"].encode("ascii") + b"\n"
        for item in entries
    )
    payload = {
        "schema_version": 2,
        "case_id": "2012A",
        "phase": "blind-revision",
        "status": "pass",
        "self_manifest_policy": "results/artifact-manifest.json is excluded to avoid a circular self-hash; the external freeze manifest covers it",
        "input_sets": {"case_inputs": input_hashes},
        "tree_hash_serialization": serialization,
        "tree_sha256": hashlib.sha256(tree_bytes).hexdigest(),
        "artifact_count": len(entries),
        "artifacts": entries,
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[pass] artifact manifest covers {len(entries)} generated files")


if __name__ == "__main__":
    main()
