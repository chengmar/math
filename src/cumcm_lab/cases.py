from __future__ import annotations

import re
from pathlib import Path

from .util import now_iso, read_yaml, write_yaml


CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
SPLIT_DIRS = {"train": "train", "dev": "dev", "exam": "exam-stubs", "dummy": "dummy"}


def validate_case_id(case_id: str) -> None:
    if not CASE_ID_RE.fullmatch(case_id):
        raise ValueError("case-id 仅允许 2-64 位英文字母、数字、下划线或连字符，且首位必须为字母或数字。")


def init_case(
    trainer_root: Path,
    case_id: str,
    split: str,
    *,
    title: str | None = None,
    problem_family: str = "unspecified",
    task_types: list[str] | None = None,
    data_types: list[str] | None = None,
) -> Path:
    validate_case_id(case_id)
    if split not in SPLIT_DIRS:
        raise ValueError(f"split 必须是 {sorted(SPLIT_DIRS)} 之一。")
    case_dir = trainer_root / "cases" / SPLIT_DIRS[split] / case_id
    if case_dir.exists():
        raise FileExistsError(f"案例已存在，拒绝覆盖：{case_dir}")
    for rel in (
        "input/problem",
        "input/data",
        "workspaces/solve",
        "workspaces/audit",
        "workspaces/blind-revision",
        "workspaces/reflection",
        "workspaces/evaluation",
        "frozen",
        "reports",
        "logs",
        "manifests",
    ):
        (case_dir / rel).mkdir(parents=True, exist_ok=True)
    created = now_iso()
    case_meta = {
        "case_id": case_id,
        "title": title or case_id,
        "split": split,
        "problem_family": problem_family,
        "task_types": task_types or [],
        "data_types": data_types or [],
        "allowed_resources": [],
        "forbidden_resources": ["reference-vault", "exam-vault", "current-case-answer"],
        "reference_ids": [],
        "status": "initialized",
        "created_at": created,
    }
    write_yaml(case_dir / "case.yaml", case_meta)
    write_yaml(case_dir / "case-state.yaml", {"state": "initialized", "history": []})
    registry_path = trainer_root / "cases" / "registry.yaml"
    registry = read_yaml(registry_path, {"cases": []})
    registry.setdefault("cases", []).append(
        {"case_id": case_id, "split": split, "path": case_dir.relative_to(trainer_root).as_posix(), "created_at": created}
    )
    write_yaml(registry_path, registry)
    return case_dir


def find_case(trainer_root: Path, case_id: str) -> Path:
    registry = read_yaml(trainer_root / "cases" / "registry.yaml", {"cases": []})
    matches = [entry for entry in registry.get("cases", []) if entry.get("case_id") == case_id]
    if len(matches) != 1:
        raise FileNotFoundError(f"案例登记不存在或不唯一：{case_id}")
    case_dir = trainer_root / matches[0]["path"]
    if not case_dir.exists():
        raise FileNotFoundError(f"案例目录不存在：{case_dir}")
    return case_dir
