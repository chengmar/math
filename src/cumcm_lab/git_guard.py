from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

from .corpus_validate import SENSITIVE_SUFFIXES, TRACKED_BINARY_ALLOWLIST
from .util import sha256_file


MAGIC_PREFIXES = {
    b"%PDF-": "pdf",
    b"PK\x03\x04": "zip_or_office",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": "legacy_office",
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpeg",
}
FORBIDDEN_PATH_PARTS = {"runtime-cases", "question-bank", "reference-vault", "exam-vault", "problems-raw", "papers-raw"}


def _git_paths(root: Path, *, include_untracked: bool) -> list[str]:
    command = ["git", "-C", str(root), "ls-files", "-z", "--cached"]
    if include_untracked:
        command.extend(["--others", "--exclude-standard"])
    output = subprocess.run(command, check=True, capture_output=True).stdout
    return sorted({item.decode("utf-8", errors="replace") for item in output.split(b"\0") if item})


def inspect_git_tree(
    trainer_root: Path,
    *,
    real_hashes: Iterable[str] = (),
    include_untracked: bool = True,
    max_file_bytes: int = 5 * 1024 * 1024,
) -> dict[str, object]:
    trainer_root = Path(trainer_root).resolve()
    known_hashes = {str(item).casefold() for item in real_hashes if item}
    findings: list[dict[str, object]] = []
    paths = _git_paths(trainer_root, include_untracked=include_untracked)
    for relative in paths:
        normalized = relative.replace("\\", "/")
        file_path = trainer_root / relative
        if not file_path.is_file():
            continue
        allowlisted = normalized.startswith(TRACKED_BINARY_ALLOWLIST)
        parts = {part.casefold() for part in Path(normalized).parts}
        if parts & FORBIDDEN_PATH_PARTS:
            findings.append({"path": normalized, "reason": "forbidden_corpus_path"})
        if file_path.suffix.casefold() in SENSITIVE_SUFFIXES and not allowlisted:
            findings.append({"path": normalized, "reason": "sensitive_extension"})
        size = file_path.stat().st_size
        if size > max_file_bytes and not allowlisted:
            findings.append({"path": normalized, "reason": "large_file", "size": size})
        with file_path.open("rb") as handle:
            prefix = handle.read(16)
        for magic, kind in MAGIC_PREFIXES.items():
            if prefix.startswith(magic) and not allowlisted:
                findings.append({"path": normalized, "reason": f"binary_magic:{kind}"})
                break
        if known_hashes and sha256_file(file_path).casefold() in known_hashes:
            findings.append({"path": normalized, "reason": "real_corpus_hash"})
    return {
        "status": "pass" if not findings else "fail",
        "files_checked": len(paths),
        "include_untracked": include_untracked,
        "findings": findings,
    }
