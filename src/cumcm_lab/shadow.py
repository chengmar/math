from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .util import iter_regular_files, now_iso, read_yaml, safe_copy_tree, tree_hash, write_json


class ShadowPolicyError(ValueError):
    """Raised when a shadow run could contaminate the official blind result."""


def _case_year(case_id: str) -> int:
    if len(case_id) != 5 or not case_id[:4].isdigit() or case_id[-1].upper() != "A":
        raise ShadowPolicyError(f"无效案例 ID：{case_id}")
    year = int(case_id[:4])
    if year == 2023:
        raise ShadowPolicyError("Shadow 永远禁止 2023A。")
    return year


def _frozen_hash(frozen: Path) -> str:
    if not frozen.is_dir():
        raise ShadowPolicyError(f"blind-final 不存在：{frozen}")
    return tree_hash(iter_regular_files(frozen), frozen)


def run_shadow_evaluation(
    case_dir: Path,
    candidate_paths: Iterable[Path],
    shadow_root: Path,
    executor: Callable[[Path, list[Path]], Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Create a one-off shadow workspace without copying references or blind-final."""

    case_dir = Path(case_dir).resolve()
    case_id = case_dir.name
    current_year = _case_year(case_id)
    candidates = [Path(path).resolve() for path in candidate_paths]
    if not 1 <= len(candidates) <= 3:
        raise ShadowPolicyError("Shadow 每次必须测试 1—3 张 candidate。")
    frozen = case_dir / "frozen" / "blind-final"
    before = _frozen_hash(frozen)
    seen_ids: set[str] = set()
    cards: list[dict[str, Any]] = []
    for path in candidates:
        card = read_yaml(path)
        if not isinstance(card, dict) or card.get("status") != "candidate":
            raise ShadowPolicyError(f"Shadow 只允许 candidate：{path}")
        origin_year = card.get("origin_year")
        if not isinstance(origin_year, int) or origin_year >= current_year:
            raise ShadowPolicyError(f"candidate 必须来自更早年份：{path}")
        card_id = str(card.get("id") or "")
        if not card_id or card_id in seen_ids:
            raise ShadowPolicyError("candidate ID 缺失或重复。")
        seen_ids.add(card_id)
        cards.append(card)

    workspace = Path(shadow_root) / case_id / f"shadow-{uuid.uuid4().hex}"
    (workspace / "input").mkdir(parents=True, exist_ok=False)
    safe_copy_tree(case_dir / "input", workspace / "input")
    candidate_dir = workspace / "candidates"
    candidate_dir.mkdir()
    local_candidates: list[Path] = []
    for source, card in zip(candidates, cards, strict=True):
        destination = candidate_dir / f"{card['id']}{source.suffix.casefold() or '.yaml'}"
        shutil.copy2(source, destination)
        local_candidates.append(destination)
    write_json(
        workspace / "shadow-manifest.json",
        {
            "schema_version": 1,
            "case_id": case_id,
            "created_at": now_iso(),
            "candidate_ids": sorted(seen_ids),
            "blind_final_sha256_before": before,
            "reference_material_copied": False,
        },
    )
    result = dict(executor(workspace, local_candidates) or {})
    after = _frozen_hash(frozen)
    if after != before:
        raise ShadowPolicyError("Shadow 运行修改了 blind-final；结果作废。")
    payload = {
        "schema_version": 1,
        "case_id": case_id,
        "status": "pass",
        "candidate_ids": sorted(seen_ids),
        "blind_final_sha256_before": before,
        "blind_final_sha256_after": after,
        "official_score_changed": False,
        "reference_material_copied": False,
        "result": result,
        "workspace": str(workspace),
        "finished_at": now_iso(),
    }
    write_json(workspace / "shadow-result.json", payload)
    return payload
