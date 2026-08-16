from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_yaml(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    with path.open("r", encoding="utf-8-sig") as handle:
        value = yaml.safe_load(handle)
    return ({} if default is None else default) if value is None else value


def write_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False))


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(files: Iterable[Path], base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(base).as_posix()):
        rel = path.relative_to(base).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def iter_regular_files(root: Path, excluded: set[str] | None = None) -> list[Path]:
    excluded = excluded or {".git", ".venv", "__pycache__", ".pytest_cache"}
    files: list[Path] = []
    if not root.exists():
        return files
    for path in root.rglob("*"):
        if any(part in excluded for part in path.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"拒绝处理符号链接：{path}")
        if path.is_file():
            files.append(path)
    return files


def safe_copy_tree(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"拒绝复制符号链接：{path}")
        resolved = path.resolve()
        if not resolved.is_relative_to(source):
            raise ValueError(f"源路径越界：{path}")
        rel = path.relative_to(source)
        target = destination / rel
        if not target.resolve(strict=False).is_relative_to(destination):
            raise ValueError(f"目标路径越界：{target}")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def ensure_empty_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise FileExistsError(f"工作区非空，为防止覆盖已拒绝：{path}")


def git_snapshot(root: Path) -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
        return commit, bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        return None, None


def find_trainer_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "cumcm_lab").exists():
            return candidate
    raise FileNotFoundError("未找到 trainer 根目录；请从 D:\\CUMCM-A-Lab\\trainer 内运行。")


def load_lab_paths(trainer_root: Path) -> dict[str, str]:
    import tomllib

    path_file = trainer_root.parent / "codex-home" / "lab-paths.toml"
    if not path_file.exists():
        raise FileNotFoundError(f"缺少路径配置：{path_file}")
    with path_file.open("rb") as handle:
        data = tomllib.load(handle)
    return {key: str(value) for key, value in data.get("paths", {}).items()}

