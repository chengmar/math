from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> tuple[list[dict[str, object]], str]:
    files: list[dict[str, object]] = []
    tree = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_symlink():
            raise ValueError(f"symlink prohibited in frozen snapshot: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest = sha256_file(path)
        size = path.stat().st_size
        files.append({"path": relative, "sha256": digest, "size": size})
        tree.update(relative.encode("utf-8"))
        tree.update(b"\0")
        tree.update(digest.encode("ascii"))
        tree.update(b"\0")
        tree.update(str(size).encode("ascii"))
        tree.update(b"\n")
    return files, tree.hexdigest()


def confined(path: Path, runtime_root: Path, *, must_exist: bool) -> Path:
    resolved = path.resolve(strict=must_exist)
    root = runtime_root.resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError(f"path escapes runtime root: {resolved}")
    return resolved


def freeze(args: argparse.Namespace) -> dict[str, object]:
    runtime_root = Path(args.runtime_root)
    source = confined(Path(args.source), runtime_root, must_exist=True)
    destination = confined(Path(args.destination), runtime_root, must_exist=False)
    manifest_path = confined(Path(args.manifest), runtime_root, must_exist=False)
    if not source.is_dir():
        raise ValueError("source must be a directory")
    if destination.exists() or manifest_path.exists():
        raise FileExistsError("freeze destination or manifest already exists")
    source_files, source_tree = inventory(source)
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    frozen_files, frozen_tree = inventory(destination)
    if source_files != frozen_files or source_tree != frozen_tree:
        raise RuntimeError("copied snapshot differs from source")
    evidence: list[dict[str, str]] = []
    for value in args.evidence:
        evidence_path = confined(Path(value), runtime_root, must_exist=True)
        if not evidence_path.is_file():
            raise ValueError(f"evidence is not a regular file: {evidence_path}")
        evidence.append({"path": str(evidence_path), "sha256": sha256_file(evidence_path)})
    manifest: dict[str, object] = {
        "schema_version": 1,
        "status": "frozen",
        "label": args.label,
        "created_at": now_iso(),
        "hash_algorithm": "sha256",
        "tree_algorithm": (
            "sha256(records sorted by relative POSIX path casefold; each record is "
            "path_utf8 + NUL + file_sha256_ascii + NUL + size_ascii + LF)"
        ),
        "source": str(source),
        "snapshot": str(destination),
        "file_count": len(frozen_files),
        "total_bytes": sum(int(item["size"]) for item in frozen_files),
        "tree_sha256": frozen_tree,
        "files": frozen_files,
        "evidence": evidence,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(args: argparse.Namespace) -> dict[str, object]:
    runtime_root = Path(args.runtime_root)
    manifest_path = confined(Path(args.manifest), runtime_root, must_exist=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    snapshot = confined(Path(str(manifest["snapshot"])), runtime_root, must_exist=True)
    files, tree = inventory(snapshot)
    status = "pass" if files == manifest.get("files") and tree == manifest.get("tree_sha256") else "fail"
    return {
        "status": status,
        "manifest": str(manifest_path),
        "snapshot": str(snapshot),
        "file_count": len(files),
        "tree_sha256": tree,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--runtime-root", required=True)
    result.add_argument("--manifest", required=True)
    result.add_argument("--verify", action="store_true")
    result.add_argument("--source")
    result.add_argument("--destination")
    result.add_argument("--label")
    result.add_argument("--evidence", action="append", default=[])
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    if arguments.verify:
        payload = verify(arguments)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(0 if payload["status"] == "pass" else 1)
    if not arguments.source or not arguments.destination or not arguments.label:
        raise SystemExit("--source, --destination and --label are required for freeze")
    print(json.dumps(freeze(arguments), ensure_ascii=False, indent=2))
