from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Mapping

from .session_runner import run_stage_session
from .util import sha256_file, write_json


CURATOR_KEYS = {
    "file_id",
    "decision",
    "detected_year",
    "problem_letter",
    "document_type",
    "confidence",
    "requires_review",
    "reason_codes",
}
CURATOR_DECISIONS = {"classified", "quarantine", "requires_review"}
FORBIDDEN_RESULT_KEYS = {
    "summary",
    "abstract",
    "method",
    "model",
    "formula",
    "result",
    "conclusion",
    "innovation",
    "knowledge",
}


class CuratorPolicyError(ValueError):
    """Raised when a curator request or result exceeds logistics-only scope."""


def validate_curator_request(record: Mapping[str, Any]) -> None:
    if record.get("detected_year") == 2023 or record.get("split") == "test" or record.get("matched_case_id") == "2023A":
        raise CuratorPolicyError("Curator 永远禁止读取或分类 2023A。")
    confidence = record.get("classification_confidence")
    if confidence not in {"medium", "中", 0.5, 0.6, 0.7, 0.75}:
        raise CuratorPolicyError("Curator 只处理确定性脚本仍无法解决的中置信度记录。")
    if not record.get("file_id"):
        raise CuratorPolicyError("Curator 请求缺少 opaque file_id。")


def validate_curator_result(result: Mapping[str, Any], *, expected_file_id: str) -> dict[str, Any]:
    extra = set(result) - CURATOR_KEYS
    if extra or set(result) & FORBIDDEN_RESULT_KEYS:
        raise CuratorPolicyError(f"Curator 输出含越权字段：{sorted(extra | (set(result) & FORBIDDEN_RESULT_KEYS))}")
    if result.get("file_id") != expected_file_id:
        raise CuratorPolicyError("Curator 输出 file_id 与请求不一致。")
    if result.get("decision") not in CURATOR_DECISIONS:
        raise CuratorPolicyError("Curator decision 无效。")
    if result.get("detected_year") == 2023:
        raise CuratorPolicyError("Curator 输出不得涉及 2023A。")
    reasons = result.get("reason_codes")
    if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons):
        raise CuratorPolicyError("Curator reason_codes 必须是非空字符串数组。")
    return dict(result)


def run_curator_classification(
    record: Mapping[str, Any],
    preview_file: Path,
    *,
    curator_home: Path,
    run_root: Path,
    model: str,
    reasoning_effort: str = "medium",
    executable: str = "codex",
) -> dict[str, Any]:
    """Run one ephemeral logistics-only curator session on a bounded text preview."""

    validate_curator_request(record)
    preview_file = Path(preview_file).resolve()
    if preview_file.suffix.casefold() not in {".txt", ".json"} or not preview_file.is_file():
        raise CuratorPolicyError("Curator 只接收预先截断的 txt/json 物流预览，不接收原始文档。")
    if preview_file.stat().st_size > 128 * 1024:
        raise CuratorPolicyError("Curator 预览超过 128 KiB。")

    run_dir = Path(run_root) / f"curator-{uuid.uuid4().hex}"
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    local_preview = workspace / f"preview{preview_file.suffix.casefold()}"
    shutil.copy2(preview_file, local_preview)
    request = {
        "file_id": str(record["file_id"]),
        "source_kind": record.get("_source_kind") or record.get("source_kind"),
        "detected_year": record.get("detected_year"),
        "problem_letter": record.get("problem_letter"),
        "current_type": record.get("document_type"),
    }
    write_json(workspace / "request.json", request)
    prompt = (
        "你是独立语料 Curator，只做文件物流分类。不得总结解法、模型、公式、数值、创新或结论；"
        "不得创建知识卡；不得联网。只读取工作区中的 request.json 与截断预览。"
        "最终消息必须是单个 JSON 对象，且字段严格为 file_id, decision, detected_year, "
        "problem_letter, document_type, confidence, requires_review, reason_codes。"
    )
    session = run_stage_session(
        workspace=workspace,
        run_root=run_dir / "session",
        prompt=prompt,
        codex_home=Path(curator_home),
        model=model,
        reasoning_effort=reasoning_effort,
        input_files=[workspace / "request.json", local_preview],
        executable=executable,
    )
    if session.get("status") != "completed":
        return {"status": session.get("status"), "blocked_reason": session.get("blocked_reason"), "run_dir": str(run_dir)}
    final_path = Path(str(session["run_dir"])) / "final-message.md"
    try:
        result = json.loads(final_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CuratorPolicyError(f"Curator 未输出严格 JSON：{exc}") from exc
    validated = validate_curator_result(result, expected_file_id=str(record["file_id"]))
    validated["preview_sha256"] = sha256_file(local_preview)
    validated["run_dir"] = str(run_dir)
    return validated
