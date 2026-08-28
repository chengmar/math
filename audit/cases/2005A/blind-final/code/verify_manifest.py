"""Read-only verification of the staging delivery manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EXCLUDED_ROOTS = {"audit", "blind-v1"}
EXCLUDED_SUBTREES = {"working/pdf-preview", "working/pdf-preview-final"}
MANIFEST_PATH = "results/reproduction-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def included_files(workspace: Path) -> dict[str, Path]:
    files = {}
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative == MANIFEST_PATH or relative.split("/", 1)[0] in EXCLUDED_ROOTS:
            continue
        if any(relative == root or relative.startswith(root + "/") for root in EXCLUDED_SUBTREES):
            continue
        files[relative] = path
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    manifest = json.loads((workspace / MANIFEST_PATH).read_text(encoding="utf-8"))
    listed = {entry["path"]: entry for entry in manifest["files"]}
    actual = included_files(workspace)

    coverage_status = "pass" if set(listed) == set(actual) else "fail"
    missing_from_manifest = sorted(set(actual) - set(listed))
    missing_from_tree = sorted(set(listed) - set(actual))
    mismatches = []
    for relative in sorted(set(listed) & set(actual)):
        entry = listed[relative]
        current_hash = sha256(actual[relative])
        current_size = actual[relative].stat().st_size
        if current_hash != entry["sha256"] or current_size != entry["size_bytes"]:
            mismatches.append(
                {
                    "path": relative,
                    "expected_sha256": entry["sha256"],
                    "actual_sha256": current_hash,
                    "expected_size_bytes": entry["size_bytes"],
                    "actual_size_bytes": current_size,
                    "status": "fail",
                }
            )
    hash_status = "pass" if not mismatches else "fail"
    overall = "pass" if coverage_status == "pass" and hash_status == "pass" else "fail"
    report = {
        "status": overall,
        "coverage_status": coverage_status,
        "hash_status": hash_status,
        "listed_file_count": len(listed),
        "actual_file_count": len(actual),
        "missing_from_manifest": missing_from_manifest,
        "missing_from_tree": missing_from_tree,
        "mismatches": mismatches,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if overall == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
