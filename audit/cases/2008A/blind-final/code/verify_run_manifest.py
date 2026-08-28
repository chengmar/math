#!/usr/bin/env python3
"""Independently recompute every hash declared by results/run_manifest.json."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    manifest_path = ROOT / "results/run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks = []
    for section in ("code", "inputs", "outputs"):
        for declared in manifest[section]:
            relative = declared["path"]
            path = ROOT / relative
            actual_bytes = path.stat().st_size if path.is_file() else None
            actual_sha256 = sha256(path) if path.is_file() else None
            item_status = (
                "pass"
                if actual_bytes == declared["bytes"] and actual_sha256 == declared["sha256"]
                else "fail"
            )
            checks.append(
                {
                    "section": section,
                    "path": relative,
                    "status": item_status,
                    "expected_bytes": declared["bytes"],
                    "actual_bytes": actual_bytes,
                    "expected_sha256": declared["sha256"],
                    "actual_sha256": actual_sha256,
                }
            )
    status = "pass" if checks and all(item["status"] == "pass" for item in checks) else "fail"
    report = {
        "status": status,
        "manifest": "results/run_manifest.json",
        "checked_entries": len(checks),
        "pass_count": sum(item["status"] == "pass" for item in checks),
        "fail_count": sum(item["status"] == "fail" for item in checks),
        "checks": checks,
        "whole_tree_freeze_manifest_status": "needs_review",
    }
    output = ROOT / "results/run_manifest_verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "checked_entries", "pass_count", "fail_count")}, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
