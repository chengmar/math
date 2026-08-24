from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from .corpus_match import classify_path, load_split_config as _load_split_config


_YEAR_2023_RE = re.compile(r"(?<!\d)2023(?!\d)")


def load_split_config(path: Path | str) -> dict[str, Any]:
    return _load_split_config(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _opaque_id(source_kind: str, relative_path: str) -> str:
    token = hashlib.sha256(f"{source_kind}\0{relative_path}".encode("utf-8")).hexdigest()
    return f"file-{token[:20]}"


def _walk_files(root: Path) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []

    def on_error(error: OSError) -> None:
        errors.append(type(error).__name__)

    if not root.is_dir():
        raise FileNotFoundError(f"语料根目录不存在：{root}")
    for current, directories, names in os.walk(root, topdown=True, followlinks=False, onerror=on_error):
        current_path = Path(current)
        directories[:] = sorted(
            (name for name in directories if not (current_path / name).is_symlink()),
            key=str.casefold,
        )
        for name in sorted(names, key=str.casefold):
            files.append(current_path / name)
    return files, errors


def _merge_review_reason(entry: dict[str, Any], reason: str) -> None:
    reasons = [item for item in str(entry.get("review_reason") or "").split(";") if item]
    if reason not in reasons:
        reasons.append(reason)
    entry["review_reason"] = ";".join(reasons)
    entry["requires_review"] = True


def inventory_corpus(
    problems_path: Path,
    papers_path: Path,
    split_config: Path | str | Mapping[str, Any],
) -> dict[str, Any]:
    """Inventory corpus bytes without parsing, extracting or summarizing documents."""

    problems_path = Path(problems_path).resolve()
    papers_path = Path(papers_path).resolve()
    config = _load_split_config(split_config) if isinstance(split_config, (str, Path)) else dict(split_config)

    entries: list[dict[str, Any]] = []
    enumeration_errors: dict[str, list[str]] = {}
    for source_kind, root in (("problems", problems_path), ("papers", papers_path)):
        paths, errors = _walk_files(root)
        enumeration_errors[source_kind] = errors
        for path in paths:
            relative_path = path.relative_to(root).as_posix()
            classification = classify_path(path, source_kind, config, source_root=root)
            try:
                metadata = path.lstat()
                size = int(metadata.st_size)
            except OSError as exc:
                size = None
                metadata = None
                classification["requires_review"] = True
                _merge_review_reason(classification, "unreadable_metadata")
                private_error = type(exc).__name__
            else:
                private_error = None

            if path.is_symlink():
                digest = None
                _merge_review_reason(classification, "symlink_not_read")
            else:
                try:
                    digest = _sha256_file(path)
                except OSError as exc:
                    digest = None
                    private_error = type(exc).__name__
                    _merge_review_reason(classification, "unreadable_file")

            file_id = _opaque_id(source_kind, relative_path)
            year = classification.get("detected_year")
            # Conservatively seal every path carrying a 2023 scope marker, even
            # when another year in its filename makes normal year detection
            # ambiguous.  This prevents the ambiguity path from leaking a title.
            sealed_test = year == 2023 or bool(_YEAR_2023_RE.search(relative_path))
            entry: dict[str, Any] = {
                "file_id": file_id,
                "sha256": digest,
                "size": size,
                "extension": path.suffix.casefold(),
                "original_path": None if sealed_test else str(path),
                "relative_source_path": None if sealed_test else relative_path,
                "source_root": "sealed_test_source" if sealed_test else str(root),
                **classification,
                "destination_id": file_id if sealed_test else (
                    f"{classification['matched_case_id']}-{file_id}" if classification.get("matched_case_id") else None
                ),
                "duplicate_of": None,
                "_source_path": str(path),
                "_source_kind": source_kind,
                "_sealed_test": sealed_test,
            }
            if private_error:
                entry["_inventory_error"] = private_error
            entries.append(entry)

    entries.sort(key=lambda item: (str(item["_source_kind"]), str(item["_source_path"]).casefold()))
    seen_hashes: dict[str, dict[str, Any]] = {}
    cross_split_duplicates = 0
    for entry in entries:
        digest = entry.get("sha256")
        if not digest:
            continue
        original = seen_hashes.get(digest)
        if original is None:
            seen_hashes[digest] = entry
            continue
        entry["duplicate_of"] = original["file_id"]
        if entry.get("split") != original.get("split"):
            cross_split_duplicates += 1
            _merge_review_reason(entry, "duplicate_across_splits")

    split_counts = Counter(str(entry.get("split") or "unknown") for entry in entries)
    source_counts = Counter(str(entry.get("_source_kind") or "unknown") for entry in entries)
    type_counts = Counter(str(entry.get("document_type") or "unknown") for entry in entries)
    year_counts = Counter(
        str(entry["detected_year"]) if entry.get("detected_year") is not None else "unknown" for entry in entries
    )
    total_bytes = sum(int(entry["size"]) for entry in entries if isinstance(entry.get("size"), int))
    summary = {
        "total_files": len(entries),
        "total_bytes": total_bytes,
        "by_source": dict(sorted(source_counts.items())),
        "by_split": dict(sorted(split_counts.items())),
        "by_document_type": dict(sorted(type_counts.items())),
        "by_year": dict(sorted(year_counts.items())),
        "duplicates": sum(1 for entry in entries if entry.get("duplicate_of")),
        "cross_split_duplicates": cross_split_duplicates,
        "requires_review": sum(1 for entry in entries if entry.get("requires_review")),
        "quarantine": split_counts.get("quarantine", 0),
        "enumeration_errors": sum(len(value) for value in enumeration_errors.values()),
        "sealed_test_files": sum(1 for entry in entries if entry.get("_sealed_test")),
    }
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "roots": {"problems": str(problems_path), "papers": str(papers_path)},
        "files": entries,
        "summary": summary,
        "_enumeration_errors": enumeration_errors,
    }


def sanitize_manifest(report: Mapping[str, Any]) -> dict[str, Any]:
    """Remove private keys and reduce 2023 file records to sealed logistics metadata."""

    def clean(value: Any) -> Any:
        if isinstance(value, Mapping):
            if value.get("_sealed_test") is True or value.get("detected_year") == 2023 or value.get("matched_case_id") == "2023A":
                return {
                    "file_id": value.get("file_id"),
                    "sha256": value.get("sha256"),
                    "size": value.get("size"),
                    "count": 1,
                    "split": "test",
                }
            return {str(key): clean(item) for key, item in value.items() if not str(key).startswith("_")}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, tuple):
            return [clean(item) for item in value]
        return value

    return clean(report)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_inventory_reports(report: Mapping[str, Any], report_dir: Path) -> dict[str, str]:
    """Write sanitized JSON/YAML/CSV/Markdown reports without private source keys."""

    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    public = sanitize_manifest(report)
    json_path = report_dir / "corpus-manifest.json"
    yaml_path = report_dir / "corpus-manifest.yaml"
    csv_path = report_dir / "corpus-inventory.csv"
    markdown_path = report_dir / "corpus-inventory.md"

    _atomic_text(json_path, json.dumps(public, ensure_ascii=False, indent=2) + "\n")
    _atomic_text(yaml_path, yaml.safe_dump(public, allow_unicode=True, sort_keys=False))

    files = list(public.get("files", []))
    fieldnames = sorted({key for entry in files if isinstance(entry, Mapping) for key in entry})
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{csv_path.name}.", dir=str(report_dir))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for entry in files:
                writer.writerow(
                    {
                        key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                        for key, value in entry.items()
                    }
                )
        os.replace(temp_name, csv_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise

    summary = public.get("summary", {})
    lines = [
        "# Corpus inventory",
        "",
        f"Generated at: {public.get('generated_at')}",
        "",
        f"- Total files: {summary.get('total_files', 0)}",
        f"- Total bytes: {summary.get('total_bytes', 0)}",
        f"- Duplicate files: {summary.get('duplicates', 0)}",
        f"- Requires review: {summary.get('requires_review', 0)}",
        f"- Quarantine: {summary.get('quarantine', 0)}",
        f"- Sealed 2023 files: {summary.get('sealed_test_files', 0)}",
        "",
        "No document body text, paper title, formula, result, or solution summary is included.",
        "",
    ]
    _atomic_text(markdown_path, "\n".join(lines))
    return {
        "json": str(json_path),
        "yaml": str(yaml_path),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
    }
