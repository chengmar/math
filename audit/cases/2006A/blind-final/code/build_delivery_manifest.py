#!/usr/bin/env python3
"""Build a deterministic, non-authoritative manifest for the pre-freeze payload."""

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


def relative(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix()


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def load_policy(workspace: Path) -> dict[str, Any]:
    policy_path = workspace / "freeze-policy.yaml"
    value = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    if value.get("phase") != "blind-revision":
        raise RuntimeError("freeze-policy.yaml phase must be blind-revision")
    if value.get("candidate_manifest", {}).get("authoritative") is not False:
        raise RuntimeError("candidate manifest must be declared non-authoritative")
    return value


def select_payload(workspace: Path, policy: dict[str, Any]) -> tuple[dict[str, Path], list[str]]:
    payload = policy["payload"]
    include_patterns = list(payload["include"])
    exclude_patterns = list(payload.get("exclude", []))
    selected: dict[str, Path] = {}

    for pattern in include_patterns:
        for path in workspace.glob(pattern):
            if path.is_symlink():
                raise RuntimeError(f"symlink is forbidden in payload: {relative(path, workspace)}")
            if path.is_file():
                name = relative(path, workspace)
                if not matches_any(name, exclude_patterns):
                    selected[name] = path

    manifest_name = policy["candidate_manifest"]["path"]
    selected.pop(manifest_name, None)

    missing = [name for name in payload["required"] if name not in selected]
    if missing:
        raise RuntimeError(f"required payload files are missing: {missing}")

    unexpected: list[str] = []
    for root_name in payload["roots_checked_for_unlisted_files"]:
        root = workspace / root_name
        if not root.is_dir():
            raise RuntimeError(f"payload root is missing: {root_name}")
        for path in root.rglob("*"):
            if path.is_symlink():
                raise RuntimeError(f"symlink is forbidden in payload root: {relative(path, workspace)}")
            if not path.is_file():
                continue
            name = relative(path, workspace)
            if name not in selected and not matches_any(name, exclude_patterns):
                unexpected.append(name)

    if unexpected:
        raise RuntimeError(f"unlisted files exist inside payload roots: {sorted(unexpected)}")

    folded: dict[str, str] = {}
    for name in selected:
        key = name.casefold()
        if key in folded and folded[key] != name:
            raise RuntimeError(f"case-insensitive path collision: {folded[key]} and {name}")
        folded[key] = name

    return selected, sorted(unexpected)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    policy = load_policy(workspace)
    selected, unexpected = select_payload(workspace, policy)

    entries = [
        {
            "path": name,
            "bytes": selected[name].stat().st_size,
            "sha256": sha256_file(selected[name]),
        }
        for name in sorted(selected)
    ]
    manifest = {
        "schema_version": 1,
        "kind": "delivery_candidate_manifest",
        "phase": "blind-revision",
        "status": "pass",
        "authority": {
            "authoritative": False,
            "reason": "The manifest is generated inside the mutable revision workspace and has no external trusted anchor.",
        },
        "freeze_status": "needs_review",
        "policy": "freeze-policy.yaml",
        "path_format": "workspace-relative POSIX",
        "manifest_self_included": False,
        "external_anchor_required": True,
        "entry_count": len(entries),
        "unexpected_payload_files": unexpected,
        "tree_sha256": tree_digest(entries),
        "entries": entries,
    }

    output = workspace / policy["candidate_manifest"]["path"]
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(
        json.dumps(
            {
                "status": "pass",
                "entry_count": len(entries),
                "tree_sha256": manifest["tree_sha256"],
                "authoritative": False,
                "freeze_status": "needs_review",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
