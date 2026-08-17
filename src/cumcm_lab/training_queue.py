from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from .util import now_iso, read_json, read_yaml, write_json, write_yaml


QUEUE_SCHEMA_VERSION = 1
FINAL_TEST_CASE_ID = "2023A"
TRAIN_PHASES = ("solve", "audit", "blind-revision", "reflection")
ITEM_STATUSES = {"pending", "running", "blocked", "completed"}
PUBLIC_QUEUE_STATUSES = {
    "pending",
    "ready",
    "import_blocked",
    "solving",
    "blind_v1_frozen",
    "auditing",
    "audited",
    "blind_revision",
    "blind_final_frozen",
    "online_evaluated",
    "reflecting",
    "knowledge_proposed",
    "shadow_evaluation",
    "regression_pending",
    "completed",
    "failed",
    "leakage_invalid",
    "manually_paused",
    "test_sealed",
    "ready_for_final_test",
    "test_consumed",
}
CONSUME_CONFIRMATION = "CONSUME-2023A-IRREVERSIBLY"
_TRAIN_CASE_RE = re.compile(r"^(20\d{2})A$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class QueueError(ValueError):
    """Raised when queue data violates the fixed training policy."""


class FinalTestExecutionDenied(QueueError):
    """Raised whenever an automatic path attempts to execute 2023A."""


class FinalTestSealError(QueueError):
    """Raised when the final-test seal would be weakened or reversed."""


def assert_training_case(case_id: str) -> int:
    """Return the case year, rejecting every non-training case.

    This is the first of two 2023 guards.  The autopilot repeats the check
    immediately before dispatch so that a hand-edited queue cannot bypass it.
    """

    normalized = str(case_id).strip().upper()
    if normalized == FINAL_TEST_CASE_ID:
        raise FinalTestExecutionDenied("2023A 是封存的最终测试，禁止加入自动训练队列。")
    match = _TRAIN_CASE_RE.fullmatch(normalized)
    if not match:
        raise QueueError(f"训练案例 ID 必须形如 2003A：{case_id!r}")
    year = int(match.group(1))
    if not 2003 <= year <= 2021:
        raise QueueError(f"自动训练仅允许 2003A—2021A：{normalized}")
    return year


def _new_item(case_id: str, ordinal: int, blocked_reason: str | None = None) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "ordinal": ordinal,
        "split": "train",
        "status": "blocked" if blocked_reason else "pending",
        "lifecycle_status": "import_blocked" if blocked_reason else "ready",
        "current_phase": TRAIN_PHASES[0],
        "completed_phases": [],
        "attempts": {phase: 0 for phase in TRAIN_PHASES},
        "blocked_reason": blocked_reason,
        "last_error": None,
        "updated_at": now_iso(),
    }


def create_training_queue(
    case_ids: Iterable[str],
    queue_path: Path,
    *,
    blocked: Mapping[str, str] | None = None,
    max_retries: int = 1,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a deterministic, metadata-only queue for 2003A—2021A."""

    queue_path = Path(queue_path)
    if queue_path.exists() and not overwrite:
        raise FileExistsError(f"训练队列已存在，拒绝覆盖：{queue_path}")
    if max_retries != 1:
        raise QueueError("安全策略固定 max_retries=1。")
    blocked = {str(key).upper(): str(value) for key, value in (blocked or {}).items()}
    normalized: list[tuple[int, str]] = []
    seen: set[str] = set()
    for raw_case_id in case_ids:
        case_id = str(raw_case_id).strip().upper()
        year = assert_training_case(case_id)
        if case_id in seen:
            raise QueueError(f"训练队列包含重复案例：{case_id}")
        seen.add(case_id)
        normalized.append((year, case_id))
    normalized.sort()
    created_at = now_iso()
    payload = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "queue_id": str(uuid.uuid4()),
        "created_at": created_at,
        "updated_at": created_at,
        "max_retries": 1,
        "stop_requested": False,
        "items": [
            _new_item(case_id, ordinal, blocked.get(case_id))
            for ordinal, (_, case_id) in enumerate(normalized, start=1)
        ],
    }
    validate_training_queue(payload)
    write_json(queue_path, payload)
    return payload


def write_public_queue_plan(
    queue: Mapping[str, Any],
    plan_path: Path,
    *,
    final_test_status: str = "test_sealed",
) -> dict[str, Any]:
    """Write the metadata-only, reviewable queue plan kept outside runtime state."""

    validate_training_queue(queue)
    if final_test_status not in {"test_sealed", "ready_for_final_test", "test_consumed"}:
        raise QueueError(f"2023A 公开状态无效：{final_test_status}")
    payload = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "allowed_statuses": sorted(PUBLIC_QUEUE_STATUSES),
        "max_retries": 1,
        "items": [
            {
                "ordinal": item["ordinal"],
                "case_id": item["case_id"],
                "split": "train",
                "status": item.get("lifecycle_status", "ready"),
                "blocked_reason": item.get("blocked_reason"),
            }
            for item in queue["items"]
        ],
        "final_test": {"case_id": FINAL_TEST_CASE_ID, "split": "test", "status": final_test_status},
    }
    write_yaml(Path(plan_path), payload)
    return payload


def _read_queue(path: Path) -> Any:
    return read_yaml(path) if path.suffix.casefold() in {".yaml", ".yml"} else read_json(path)


def _write_queue(path: Path, value: Any) -> None:
    if path.suffix.casefold() in {".yaml", ".yml"}:
        write_yaml(path, value)
    else:
        write_json(path, value)


def validate_training_queue(queue: Mapping[str, Any]) -> None:
    if queue.get("schema_version") != QUEUE_SCHEMA_VERSION:
        raise QueueError("训练队列 schema_version 无效。")
    if queue.get("max_retries") != 1:
        raise QueueError("训练队列 max_retries 必须等于 1。")
    items = queue.get("items")
    if not isinstance(items, list):
        raise QueueError("训练队列 items 必须是数组。")
    seen: set[str] = set()
    previous_year = 0
    for expected_ordinal, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise QueueError("训练队列项必须是对象。")
        case_id = str(item.get("case_id", "")).upper()
        year = assert_training_case(case_id)
        if case_id in seen:
            raise QueueError(f"训练队列包含重复案例：{case_id}")
        if year <= previous_year:
            raise QueueError("训练队列必须按年份严格升序。")
        seen.add(case_id)
        previous_year = year
        if item.get("ordinal") != expected_ordinal:
            raise QueueError(f"训练队列 ordinal 不连续：{case_id}")
        if item.get("split") != "train":
            raise QueueError(f"训练队列只允许 split=train：{case_id}")
        if item.get("status") not in ITEM_STATUSES:
            raise QueueError(f"训练队列项状态无效：{case_id}")
        if item.get("lifecycle_status") not in PUBLIC_QUEUE_STATUSES:
            raise QueueError(f"训练生命周期状态无效：{case_id}")
        phase = item.get("current_phase")
        if phase is not None and phase not in TRAIN_PHASES:
            raise QueueError(f"训练阶段无效：{case_id}: {phase}")
        completed = item.get("completed_phases")
        if not isinstance(completed, list) or completed != list(TRAIN_PHASES[: len(completed)]):
            raise QueueError(f"完成阶段不是合法前缀：{case_id}")
        attempts = item.get("attempts")
        if not isinstance(attempts, dict) or set(attempts) != set(TRAIN_PHASES):
            raise QueueError(f"阶段尝试记录不完整：{case_id}")
        if any(not isinstance(value, int) or value < 0 or value > 2 for value in attempts.values()):
            raise QueueError(f"阶段尝试次数越界：{case_id}")
        if item.get("status") == "completed":
            if completed != list(TRAIN_PHASES) or phase is not None:
                raise QueueError(f"已完成案例的阶段记录不完整：{case_id}")


def load_training_queue(queue_path: Path) -> dict[str, Any]:
    queue_path = Path(queue_path)
    if not queue_path.is_file():
        raise FileNotFoundError(f"训练队列不存在：{queue_path}")
    payload = _read_queue(queue_path)
    if not isinstance(payload, dict):
        raise QueueError("训练队列根节点必须是对象。")
    validate_training_queue(payload)
    return payload


def save_training_queue(queue_path: Path, queue: dict[str, Any]) -> None:
    queue["updated_at"] = now_iso()
    validate_training_queue(queue)
    _write_queue(Path(queue_path), queue)


def queue_summary(queue: Mapping[str, Any]) -> dict[str, Any]:
    counts = {status: 0 for status in sorted(ITEM_STATUSES)}
    for item in queue.get("items", []):
        counts[str(item["status"])] += 1
    current = next(
        (
            {"case_id": item["case_id"], "phase": item.get("current_phase"), "status": item["status"]}
            for item in queue.get("items", [])
            if item["status"] in {"pending", "running"}
        ),
        None,
    )
    return {
        "queue_id": queue.get("queue_id"),
        "stop_requested": bool(queue.get("stop_requested")),
        "max_retries": queue.get("max_retries"),
        "counts": counts,
        "current": current,
        "total": len(queue.get("items", [])),
        "updated_at": queue.get("updated_at"),
    }


def next_runnable_item(queue: Mapping[str, Any]) -> dict[str, Any] | None:
    if queue.get("stop_requested"):
        return None
    for item in queue.get("items", []):
        if item.get("status") in {"pending", "running"}:
            # Repeat the final-test guard even for manually edited in-memory data.
            assert_training_case(str(item.get("case_id", "")))
            return item
    return None


def _find_item(queue: Mapping[str, Any], case_id: str) -> dict[str, Any]:
    matches = [item for item in queue.get("items", []) if item.get("case_id") == case_id]
    if len(matches) != 1:
        raise QueueError(f"队列案例不存在或不唯一：{case_id}")
    return matches[0]


def begin_phase(queue_path: Path, case_id: str) -> tuple[dict[str, Any], int]:
    queue = load_training_queue(queue_path)
    if queue.get("stop_requested"):
        raise QueueError("训练队列已请求停止。")
    assert_training_case(case_id)
    item = _find_item(queue, case_id)
    if item["status"] not in {"pending", "running"}:
        raise QueueError(f"案例不可运行：{case_id}: {item['status']}")
    phase = item.get("current_phase")
    if phase not in TRAIN_PHASES:
        raise QueueError(f"案例没有可运行阶段：{case_id}")
    attempts = int(item["attempts"][phase]) + 1
    if attempts > int(queue["max_retries"]) + 1:
        raise QueueError(f"阶段已超过最大重试次数：{case_id}/{phase}")
    item["attempts"][phase] = attempts
    item["status"] = "running"
    item["lifecycle_status"] = {
        "solve": "solving",
        "audit": "auditing",
        "blind-revision": "blind_revision",
        "reflection": "reflecting",
    }[phase]
    item["last_error"] = None
    item["updated_at"] = now_iso()
    save_training_queue(queue_path, queue)
    return item, attempts


def mark_phase_success(queue_path: Path, case_id: str, phase: str) -> dict[str, Any]:
    queue = load_training_queue(queue_path)
    item = _find_item(queue, case_id)
    if item.get("status") != "running" or item.get("current_phase") != phase:
        raise QueueError(f"成功回写与当前阶段不一致：{case_id}/{phase}")
    expected_index = len(item["completed_phases"])
    if TRAIN_PHASES[expected_index] != phase:
        raise QueueError(f"阶段完成顺序错误：{case_id}/{phase}")
    item["completed_phases"].append(phase)
    if len(item["completed_phases"]) == len(TRAIN_PHASES):
        item["status"] = "completed"
        item["lifecycle_status"] = "completed"
        item["current_phase"] = None
    else:
        item["status"] = "pending"
        item["current_phase"] = TRAIN_PHASES[len(item["completed_phases"])]
        item["lifecycle_status"] = {
            "solve": "ready",
            "audit": "blind_v1_frozen",
            "blind-revision": "audited",
            "reflection": "blind_final_frozen",
        }[item["current_phase"]]
    item["last_error"] = None
    item["updated_at"] = now_iso()
    save_training_queue(queue_path, queue)
    return item


def mark_phase_failure(
    queue_path: Path,
    case_id: str,
    phase: str,
    error: str,
    *,
    transient: bool,
) -> dict[str, Any]:
    queue = load_training_queue(queue_path)
    item = _find_item(queue, case_id)
    if item.get("current_phase") != phase:
        raise QueueError(f"失败回写与当前阶段不一致：{case_id}/{phase}")
    attempts = int(item["attempts"][phase])
    retry_available = transient and attempts <= int(queue["max_retries"])
    item["status"] = "pending" if retry_available else "blocked"
    item["lifecycle_status"] = (
        {
            "solve": "ready",
            "audit": "blind_v1_frozen",
            "blind-revision": "audited",
            "reflection": "blind_final_frozen",
        }[phase]
        if retry_available
        else "failed"
    )
    item["blocked_reason"] = None if retry_available else ("retry_exhausted" if transient else "case_error")
    item["last_error"] = str(error)
    item["updated_at"] = now_iso()
    save_training_queue(queue_path, queue)
    return item


def set_stop_requested(queue_path: Path, requested: bool) -> dict[str, Any]:
    queue = load_training_queue(queue_path)
    queue["stop_requested"] = bool(requested)
    save_training_queue(queue_path, queue)
    return queue


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sanitize_seal_records(
    records: Iterable[Mapping[str, Any]], *, reject_extra: bool = False
) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        extra = set(record) - {"file_id", "sha256", "size"}
        if extra and reject_extra:
            raise FinalTestSealError("2023A seal 文件项含禁止的路径、标题或正文元数据。")
        if extra:
            # Never persist source names, titles, local paths, or extracted text.
            record = {key: record.get(key) for key in ("file_id", "sha256", "size")}
        file_id = str(record.get("file_id", "")).strip()
        digest = str(record.get("sha256", "")).strip().lower()
        size = record.get("size")
        if not file_id or any(char in file_id for char in ("/", "\\", ":")):
            raise FinalTestSealError("seal file_id 必须是不含路径的 opaque ID。")
        if file_id in seen:
            raise FinalTestSealError(f"seal 包含重复 file_id：{file_id}")
        if not _SHA256_RE.fullmatch(digest):
            raise FinalTestSealError(f"seal SHA-256 无效：{file_id}")
        if not isinstance(size, int) or size < 0:
            raise FinalTestSealError(f"seal size 无效：{file_id}")
        seen.add(file_id)
        sanitized.append({"file_id": file_id, "sha256": digest, "size": size})
    if not sanitized:
        raise FinalTestSealError("2023A seal 至少需要一个文件指纹。")
    return sorted(sanitized, key=lambda item: item["file_id"])


def seal_final_test(seal_path: Path, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Create an idempotent, content-free 2023A seal."""

    seal_path = Path(seal_path)
    records = list(records)
    problem_ids = sorted(
        str(item.get("file_id"))
        for item in records
        if str(item.get("source_kind") or item.get("_source_kind") or "").casefold() in {"problem", "problems"}
    )
    reference_ids = sorted(
        str(item.get("file_id"))
        for item in records
        if str(item.get("source_kind") or item.get("_source_kind") or "").casefold()
        in {"paper", "papers", "reference", "references"}
    )
    files = _sanitize_seal_records(records)
    digest = _canonical_hash(files)
    if seal_path.exists():
        existing = read_final_test_seal(seal_path)
        if existing.get("state") == "consumed":
            raise FinalTestSealError("2023A 已永久标记 consumed，不能重新封存。")
        if existing.get("manifest_sha256") != digest:
            raise FinalTestSealError("已有 2023A seal 与新指纹不一致，拒绝覆盖。")
        return existing
    payload = {
        "schema_version": 1,
        "case_id": FINAL_TEST_CASE_ID,
        "state": "sealed",
        "status": "test_sealed",
        "sealed_at": now_iso(),
        "file_count": len(files),
        "problem_file_count": len(problem_ids),
        "sealed_reference_count": len(reference_ids),
        "problem_ids": problem_ids,
        "sealed_reference_ids": reference_ids,
        "manifest_sha256": digest,
        "files": files,
    }
    write_json(seal_path, payload)
    return payload


def read_final_test_seal(seal_path: Path) -> dict[str, Any]:
    seal_path = Path(seal_path)
    if not seal_path.is_file():
        raise FileNotFoundError(f"2023A seal 不存在：{seal_path}")
    payload = read_json(seal_path)
    if not isinstance(payload, dict):
        raise FinalTestSealError("2023A seal 根节点必须是对象。")
    allowed_top = {
        "schema_version",
        "case_id",
        "state",
        "status",
        "sealed_at",
        "file_count",
        "problem_file_count",
        "sealed_reference_count",
        "problem_ids",
        "sealed_reference_ids",
        "manifest_sha256",
        "files",
        "consumed_at",
        "consumption_id",
    }
    if set(payload) - allowed_top:
        raise FinalTestSealError("2023A seal 含禁止的路径、标题或正文元数据。")
    if payload.get("case_id") != FINAL_TEST_CASE_ID or payload.get("state") not in {"sealed", "consumed"}:
        raise FinalTestSealError("2023A seal 状态无效。")
    expected_status = "test_consumed" if payload.get("state") == "consumed" else payload.get("status")
    if expected_status not in {"test_sealed", "ready_for_final_test", "test_consumed"}:
        raise FinalTestSealError("2023A seal 公开状态无效。")
    files = _sanitize_seal_records(payload.get("files") or [], reject_extra=True)
    if payload.get("file_count") != len(files) or payload.get("manifest_sha256") != _canonical_hash(files):
        raise FinalTestSealError("2023A seal 指纹校验失败。")
    return payload


def consume_final_test(seal_path: Path, confirmation: str) -> dict[str, Any]:
    payload = read_final_test_seal(seal_path)
    if confirmation != CONSUME_CONFIRMATION:
        raise FinalTestSealError("消费 2023A 需要精确的不可逆确认字符串。")
    if payload["state"] == "consumed":
        return payload
    payload["state"] = "consumed"
    payload["status"] = "test_consumed"
    payload["consumed_at"] = now_iso()
    payload["consumption_id"] = str(uuid.uuid4())
    write_json(Path(seal_path), payload)
    return payload
