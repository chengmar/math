from __future__ import annotations

import gzip
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


UNSUPPORTED_ARCHIVE_EXTENSIONS = {".7z", ".rar"}


class UnsafeArchiveError(ValueError):
    pass


def _safe_relative_path(member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    if "\x00" in normalized:
        raise UnsafeArchiveError("archive_member_contains_nul")
    pure = PurePosixPath(normalized)
    parts = [part for part in pure.parts if part not in {"", "."}]
    if pure.is_absolute() or any(part == ".." for part in parts):
        raise UnsafeArchiveError("archive_path_traversal")
    if parts and ":" in parts[0]:
        raise UnsafeArchiveError("archive_absolute_drive_path")
    if not parts:
        raise UnsafeArchiveError("archive_empty_member_name")
    return Path(*parts)


def _preflight_destination(destination: Path, members: Iterable[tuple[Path, bool]]) -> None:
    resolved_destination = destination.resolve(strict=False)
    seen: set[str] = set()
    for relative, _is_directory in members:
        key = relative.as_posix().casefold()
        if key in seen:
            raise UnsafeArchiveError("duplicate_archive_member")
        seen.add(key)
        target = (destination / relative).resolve(strict=False)
        if not target.is_relative_to(resolved_destination):
            raise UnsafeArchiveError("archive_path_traversal")
        if target.exists() or target.is_symlink():
            raise FileExistsError("destination_member_exists")


def _merge_staging(staging: Path, destination: Path) -> None:
    if not destination.exists():
        os.replace(staging, destination)
        return
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(staging.rglob("*"), key=lambda item: (len(item.parts), item.as_posix())):
        relative = source.relative_to(staging)
        target = destination / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
    shutil.rmtree(staging, ignore_errors=True)


def _zip_plan(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, Path, bool]]:
    plan: list[tuple[zipfile.ZipInfo, Path, bool]] = []
    for info in archive.infolist():
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise UnsafeArchiveError("archive_symlink_rejected")
        relative = _safe_relative_path(info.filename)
        plan.append((info, relative, info.is_dir()))
    return plan


def _tar_plan(archive: tarfile.TarFile) -> list[tuple[tarfile.TarInfo, Path, bool]]:
    plan: list[tuple[tarfile.TarInfo, Path, bool]] = []
    for info in archive.getmembers():
        if info.issym() or info.islnk():
            raise UnsafeArchiveError("archive_link_rejected")
        if not (info.isdir() or info.isfile()):
            raise UnsafeArchiveError("archive_special_member_rejected")
        relative = _safe_relative_path(info.name)
        plan.append((info, relative, info.isdir()))
    return plan


def _extract_zip(archive_path: Path, staging: Path, destination: Path, dry_run: bool) -> tuple[int, int]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        plan = _zip_plan(archive)
        _preflight_destination(destination, ((relative, is_dir) for _, relative, is_dir in plan))
        if dry_run:
            return sum(not item[2] for item in plan), sum(item[2] for item in plan)
        for info, relative, is_directory in plan:
            target = staging / relative
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        return sum(not item[2] for item in plan), sum(item[2] for item in plan)


def _extract_tar(archive_path: Path, staging: Path, destination: Path, dry_run: bool) -> tuple[int, int]:
    with tarfile.open(archive_path, "r:*") as archive:
        plan = _tar_plan(archive)
        _preflight_destination(destination, ((relative, is_dir) for _, relative, is_dir in plan))
        if dry_run:
            return sum(not item[2] for item in plan), sum(item[2] for item in plan)
        for info, relative, is_directory in plan:
            target = staging / relative
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(info)
            if source is None:
                raise UnsafeArchiveError("archive_member_unreadable")
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
        return sum(not item[2] for item in plan), sum(item[2] for item in plan)


def _extract_gzip(archive_path: Path, staging: Path, destination: Path, dry_run: bool) -> tuple[int, int]:
    output_name = archive_path.stem
    relative = _safe_relative_path(output_name)
    _preflight_destination(destination, [(relative, False)])
    if dry_run:
        return 1, 0
    target = staging / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive_path, "rb") as source, target.open("xb") as output:
        shutil.copyfileobj(source, output)
    return 1, 0


def safe_extract(archive_path: Path, destination: Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Safely extract zip/tar/gz without overwriting or following archive links."""

    archive_path = Path(archive_path)
    destination = Path(destination)
    extension = archive_path.suffix.casefold()
    if extension in UNSUPPORTED_ARCHIVE_EXTENSIONS:
        return {
            "status": "unsupported",
            "requires_review": True,
            "review_reason": "unsupported_archive_format",
            "format": extension.lstrip("."),
            "extracted_files": 0,
        }
    if not archive_path.is_file():
        return {
            "status": "needs_review",
            "requires_review": True,
            "review_reason": "archive_missing_or_not_file",
            "format": extension.lstrip("."),
            "extracted_files": 0,
        }

    if extension == ".zip":
        archive_format = "zip"
        extractor = _extract_zip
    elif extension in {".tar", ".tgz"} or tarfile.is_tarfile(archive_path):
        archive_format = "tar"
        extractor = _extract_tar
    elif extension == ".gz":
        archive_format = "gz"
        extractor = _extract_gzip
    else:
        return {
            "status": "unsupported",
            "requires_review": True,
            "review_reason": "unsupported_archive_format",
            "format": extension.lstrip("."),
            "extracted_files": 0,
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.extract-", dir=str(destination.parent)))
    moved = False
    try:
        file_count, directory_count = extractor(archive_path, staging, destination, dry_run)
        if not dry_run:
            _merge_staging(staging, destination)
            moved = True
        return {
            "status": "dry_run" if dry_run else "pass",
            "requires_review": False,
            "review_reason": None,
            "format": archive_format,
            "extracted_files": file_count,
            "extracted_directories": directory_count,
        }
    except (UnsafeArchiveError, FileExistsError) as exc:
        return {
            "status": "needs_review",
            "requires_review": True,
            "review_reason": str(exc),
            "format": archive_format,
            "extracted_files": 0,
        }
    except (OSError, EOFError, gzip.BadGzipFile, tarfile.TarError, zipfile.BadZipFile):
        return {
            "status": "needs_review",
            "requires_review": True,
            "review_reason": "archive_read_or_extract_failed",
            "format": archive_format,
            "extracted_files": 0,
        }
    finally:
        if not moved and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


extract_archive = safe_extract
