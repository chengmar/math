#!/usr/bin/env python3
"""Read-only validation of the non-authoritative pre-freeze payload manifest."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def tree_digest(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in entries:
        digest.update(item["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def expected_paths(workspace: Path, policy: dict[str, Any]) -> tuple[set[str], list[str]]:
    payload = policy["payload"]
    exclusions = list(payload.get("exclude", []))
    selected: set[str] = set()
    for pattern in payload["include"]:
        for path in workspace.glob(pattern):
            if path.is_symlink():
                raise RuntimeError(f"symlink is forbidden: {path.relative_to(workspace).as_posix()}")
            if path.is_file():
                name = path.relative_to(workspace).as_posix()
                if not matches_any(name, exclusions):
                    selected.add(name)
    selected.discard(policy["candidate_manifest"]["path"])

    unexpected: list[str] = []
    for root_name in payload["roots_checked_for_unlisted_files"]:
        root = workspace / root_name
        for path in root.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(f"symlink is forbidden: {path.relative_to(workspace).as_posix()}")
            if not path.is_file():
                continue
            name = path.relative_to(workspace).as_posix()
            if name not in selected and not matches_any(name, exclusions):
                unexpected.append(name)
    return selected, sorted(unexpected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    policy = yaml.safe_load((workspace / "freeze-policy.yaml").read_text(encoding="utf-8"))
    manifest_path = workspace / policy["candidate_manifest"]["path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    checks: dict[str, dict[str, Any]] = {}

    def record(name: str, condition: bool, observed: Any, expected: Any) -> None:
        checks[name] = {
            "status": "pass" if condition else "fail",
            "observed": observed,
            "expected": expected,
        }

    expected, unexpected = expected_paths(workspace, policy)
    entries = manifest.get("entries", [])
    names = [item.get("path") for item in entries]
    record("manifest_kind", manifest.get("kind") == "delivery_candidate_manifest", manifest.get("kind"), "delivery_candidate_manifest")
    record("candidate_not_authoritative", manifest.get("authority", {}).get("authoritative") is False, manifest.get("authority"), {"authoritative": False})
    record("freeze_status", manifest.get("freeze_status") == "needs_review", manifest.get("freeze_status"), "needs_review")
    record("self_excluded", manifest_path.relative_to(workspace).as_posix() not in names, manifest.get("manifest_self_included"), False)
    record("unique_paths", len(names) == len(set(names)), len(names) - len(set(names)), 0)
    record("path_set", set(names) == expected, sorted(set(names) ^ expected), [])
    record("unlisted_payload_files", not unexpected, unexpected, [])
    record("entry_count", manifest.get("entry_count") == len(entries), manifest.get("entry_count"), len(entries))

    mismatches: list[str] = []
    for item in entries:
        path = workspace / item["path"]
        if (
            not path.is_file()
            or path.stat().st_size != item.get("bytes")
            or sha256_file(path) != item.get("sha256")
        ):
            mismatches.append(item["path"])
    record("bytes_and_sha256", not mismatches, mismatches, [])
    record("tree_digest", manifest.get("tree_sha256") == tree_digest(entries), manifest.get("tree_sha256"), tree_digest(entries))

    status = "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    report = {
        "status": status,
        "scope": "read-only consistency check of the mutable pre-freeze payload",
        "freeze_status": "needs_review",
        "authoritative_freeze_claim": False,
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
