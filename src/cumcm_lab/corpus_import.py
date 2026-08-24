from __future__ import annotations

import hashlib
import csv
import os
import shutil
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from .util import now_iso, read_json, sha256_file, write_json, write_yaml


PROBLEM_TYPES = {
    "problem_statement": "problem",
    "official_data": "data",
    "official_attachment": "attachments",
    "problem_instruction": "attachments",
    "archive_container": "attachments",
}
REFERENCE_TYPES = {
    "reference_paper",
    "reference_code",
    "reference_attachment",
    "commentary",
    "archive_container",
}


def _source_kind(record: dict[str, Any]) -> str | None:
    private = str(record.get("_source_kind") or "").casefold()
    if private in {"problem", "problems"}:
        return "problems"
    if private in {"paper", "papers", "reference", "references"}:
        return "papers"
    public = str(record.get("source_root") or "").replace("\\", "/").rstrip("/").casefold()
    if public.endswith("/problems-raw") or public == "problems-raw":
        return "problems"
    if public.endswith("/papers-raw") or public == "papers-raw":
        return "papers"
    return None


def _manifest_fingerprint(records: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in sorted(records, key=lambda value: (str(value.get("file_id")), str(value.get("source_root")))):
        digest.update(str(item.get("file_id", "")).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.get("sha256", "")).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _destination(record: dict[str, Any], paths: dict[str, str]) -> Path | None:
    split = record.get("split")
    case_id = record.get("matched_case_id")
    document_type = record.get("document_type")
    extension = str(record.get("extension") or "")
    file_name = f"{record['file_id']}{extension}"
    source_kind = _source_kind(record)
    if split == "train" and case_id:
        if source_kind == "problems" and document_type in PROBLEM_TYPES:
            return Path(paths["question_bank"]) / "train" / str(case_id) / PROBLEM_TYPES[document_type] / file_name
        if source_kind == "papers" and document_type in REFERENCE_TYPES:
            return Path(paths["reference_vault"]) / str(case_id) / file_name
    if split == "test" and case_id == "2023A":
        if source_kind == "problems" and document_type in PROBLEM_TYPES:
            return Path(paths["question_bank"]) / "test" / "2023A" / PROBLEM_TYPES[document_type] / file_name
        if source_kind == "papers" and document_type in REFERENCE_TYPES:
            return Path(paths["exam_vault"]) / "2023A" / file_name
    return None


def plan_import(inventory: dict[str, Any], paths: dict[str, str]) -> dict[str, Any]:
    records = inventory.get("files", [])
    hashes_by_split: dict[str, set[str]] = defaultdict(set)
    for record in records:
        split = record.get("split")
        digest = record.get("sha256")
        if digest and split in {"train", "dev", "test"}:
            hashes_by_split[str(digest)].add(str(split))
    conflicts = [digest for digest, splits in hashes_by_split.items() if len(splits) > 1]

    actions: list[dict[str, Any]] = []
    blocked_cases: set[str] = set()
    routed_hashes: set[tuple[str, str]] = set()
    for record in records:
        destination = _destination(record, paths)
        source_kind = _source_kind(record)
        reason = None
        action = "skip"
        if record.get("sha256") in conflicts:
            reason = "same_hash_in_multiple_splits"
        elif record.get("problem_letter") not in {None, "A"}:
            reason = "non_a_problem"
        elif record.get("requires_review"):
            reason = str(record.get("review_reason") or "requires_review")
            if source_kind == "problems" and record.get("document_type") in {
                "problem_statement",
                "unknown_problem_file",
            }:
                if record.get("matched_case_id"):
                    blocked_cases.add(str(record["matched_case_id"]))
        elif destination is not None:
            route_key = (str(record.get("sha256")), str(destination.parent).casefold())
            if record.get("duplicate_of") and route_key in routed_hashes:
                reason = "duplicate_file"
            else:
                action = "copy"
                routed_hashes.add(route_key)
        else:
            reason = "not_in_import_scope"
        actions.append(
            {
                "file_id": record.get("file_id"),
                "sha256": record.get("sha256"),
                "source_kind": source_kind,
                "source_relative": None if record.get("_sealed_test") else record.get("relative_source_path"),
                "destination": str(destination) if destination else None,
                "destination_id": (
                    f"vault://{record.get('split')}/{record.get('matched_case_id')}/{record.get('file_id')}"
                    if destination
                    else None
                ),
                "action": action,
                "reason": reason,
                "case_id": record.get("matched_case_id"),
                "split": record.get("split"),
            }
        )
    return {
        "schema_version": 1,
        "created_at": now_iso(),
        "status": "fail" if conflicts else ("needs_review" if blocked_cases else "pass"),
        "manifest_fingerprint": _manifest_fingerprint(records),
        "actions": actions,
        "cross_split_hash_conflicts": conflicts,
        "blocked_cases": sorted(blocked_cases),
    }


def write_dry_run_lock(plan: dict[str, Any], lock_path: Path) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "dry_run_completed": True,
        "dry_run_at": now_iso(),
        "manifest_fingerprint": plan["manifest_fingerprint"],
        "plan_status": plan["status"],
        "applied": False,
    }
    write_json(lock_path, payload)
    return payload


def write_import_reports(
    inventory: dict[str, Any],
    plan: dict[str, Any],
    report_dir: Path,
) -> dict[str, str]:
    """Write logistics-only import reports; test records remain opaque."""

    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    records = list(inventory.get("files", []))

    def compact(record: dict[str, Any]) -> dict[str, Any]:
        base = {
            "file_id": record.get("file_id"),
            "sha256": record.get("sha256"),
            "split": record.get("split"),
            "matched_case_id": record.get("matched_case_id"),
            "document_type": record.get("document_type"),
            "requires_review": bool(record.get("requires_review")),
            "review_reason": record.get("review_reason"),
        }
        if record.get("split") == "test":
            return {key: base[key] for key in ("file_id", "sha256", "split")}
        return base

    duplicates = [
        {"file_id": item.get("file_id"), "duplicate_of": item.get("duplicate_of"), "sha256": item.get("sha256"), "split": item.get("split")}
        for item in records
        if item.get("duplicate_of")
    ]
    manual_review = [compact(item) for item in records if item.get("requires_review")]
    quarantine = [compact(item) for item in records if item.get("split") == "quarantine" or item.get("problem_letter") not in {None, "A"}]
    unmatched = [compact(item) for item in records if not item.get("matched_case_id") and item.get("split") in {"train", "test"}]
    matched_train = {str(item.get("matched_case_id")) for item in records if item.get("split") == "train" and item.get("matched_case_id")}
    missing = [f"{year}A" for year in range(2003, 2022) if f"{year}A" not in matched_train]

    outputs = {
        "duplicates": report_dir / "duplicates.yaml",
        "manual_review": report_dir / "manual-review.yaml",
        "quarantine": report_dir / "quarantine.yaml",
        "missing_years": report_dir / "missing-years.yaml",
        "unmatched_files": report_dir / "unmatched-files.yaml",
        "report_csv": report_dir / "import-report.csv",
        "report_markdown": report_dir / "import-report.md",
    }
    write_yaml(outputs["duplicates"], {"schema_version": 1, "files": duplicates})
    write_yaml(outputs["manual_review"], {"schema_version": 1, "files": manual_review})
    write_yaml(outputs["quarantine"], {"schema_version": 1, "files": quarantine})
    write_yaml(outputs["missing_years"], {"schema_version": 1, "case_ids": missing})
    write_yaml(outputs["unmatched_files"], {"schema_version": 1, "files": unmatched})

    fieldnames = ["file_id", "action", "reason", "case_id", "split", "destination_id"]
    descriptor, temp_name = tempfile.mkstemp(prefix=".import-report.csv.", dir=str(report_dir))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for action in plan.get("actions", []):
                writer.writerow({key: action.get(key) for key in fieldnames})
        os.replace(temp_name, outputs["report_csv"])
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    counts = {"copy": 0, "skip": 0}
    for action in plan.get("actions", []):
        counts[str(action.get("action"))] = counts.get(str(action.get("action")), 0) + 1
    lines = [
        "# Corpus import report",
        "",
        f"- Status: {plan.get('status')}",
        f"- Copy actions: {counts.get('copy', 0)}",
        f"- Skipped records: {counts.get('skip', 0)}",
        f"- Duplicates: {len(duplicates)}",
        f"- Manual review: {len(manual_review)}",
        f"- Quarantine: {len(quarantine)}",
        f"- Missing train cases: {len(missing)}",
        f"- Unmatched in-scope files: {len(unmatched)}",
        "",
        "The report contains logistics metadata only; 2023 records are opaque IDs and hashes.",
        "",
    ]
    outputs["report_markdown"].write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {key: str(value) for key, value in outputs.items()}


def _copy_atomic_verified(source: Path, destination: Path) -> str:
    source_hash = sha256_file(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise FileExistsError(f"目标不是普通文件，拒绝覆盖：{destination}")
        if sha256_file(destination) == source_hash:
            return "reused"
        raise FileExistsError(f"目标已存在且哈希不同，拒绝覆盖：{destination}")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        if sha256_file(temporary) != source_hash:
            raise IOError(f"复制后哈希不一致：{source}")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    if sha256_file(source) != source_hash:
        raise IOError(f"复制期间源文件发生变化：{source}")
    return "copied"


def _write_vault_hash_index(inventory: dict[str, Any], index_path: Path) -> Path:
    hashes = [
        {
            "sha256": str(record["sha256"]),
            "file_id": str(record["file_id"]),
            "split": str(record.get("split") or "unassigned"),
        }
        for record in inventory.get("files", [])
        if _source_kind(record) == "papers" and record.get("sha256")
    ]
    hashes.sort(key=lambda item: (item["sha256"], item["file_id"]))
    write_json(
        index_path,
        {
            "schema_version": 1,
            "generated_at": now_iso(),
            "hashes": hashes,
        },
    )
    return index_path


def apply_import(
    inventory: dict[str, Any],
    plan: dict[str, Any],
    paths: dict[str, str],
    *,
    lock_path: Path,
    resume: bool = False,
) -> dict[str, Any]:
    if plan.get("status") == "fail":
        raise ValueError("导入计划存在系统级冲突，拒绝 apply。")
    lock = read_json(lock_path, {})
    if not lock.get("dry_run_completed"):
        raise ValueError("必须先完成 dry-run。")
    if lock.get("manifest_fingerprint") != plan.get("manifest_fingerprint"):
        raise ValueError("dry-run 后语料清单已变化，必须重新 dry-run。")
    if lock.get("applied") and not resume:
        raise FileExistsError("该导入计划已执行；如需幂等核验请使用 --resume。")

    records = {str(item.get("file_id")): item for item in inventory.get("files", [])}
    results: list[dict[str, Any]] = []
    exam_source_map: list[dict[str, str]] = []
    for action in plan.get("actions", []):
        if action.get("action") != "copy":
            results.append({**action, "result": "skipped"})
            continue
        record = records.get(str(action.get("file_id")))
        if not record:
            raise ValueError(f"导入计划引用未知 file_id：{action.get('file_id')}")
        expected_destination = _destination(record, paths)
        destination = Path(str(action["destination"])).resolve(strict=False)
        if expected_destination is None or destination != expected_destination.resolve(strict=False):
            raise ValueError(f"导入目标超出确定性路由：{record.get('file_id')}")
        source_value = record.get("_source_path")
        if not source_value:
            source_kind = _source_kind(record)
            if source_kind not in {"problems", "papers"}:
                raise ValueError(f"无法确定源类型：{record.get('file_id')}")
            root_key = "problems_intake" if source_kind == "problems" else "papers_intake"
            source_value = str(Path(paths[root_key]) / str(action.get("source_relative")))
        source = Path(str(source_value)).resolve()
        source_kind = _source_kind(record)
        if source_kind not in {"problems", "papers"}:
            raise ValueError(f"无法确定源类型：{record.get('file_id')}")
        expected_root = Path(paths["problems_intake"] if source_kind == "problems" else paths["papers_intake"]).resolve()
        if not source.is_relative_to(expected_root) or source.is_symlink() or not source.is_file():
            raise ValueError(f"源文件未通过 Intake 边界检查：{record.get('file_id')}")
        if sha256_file(source) != record.get("sha256"):
            raise IOError(f"源文件哈希与 inventory 不一致：{record.get('file_id')}")
        result = _copy_atomic_verified(source, destination)
        if sha256_file(destination) != record.get("sha256"):
            raise IOError(f"目标文件哈希验证失败：{record.get('file_id')}")
        results.append({**action, "result": result})
        record["destination_id"] = action.get("destination_id")
        if record.get("split") == "test":
            exam_source_map.append(
                {
                    "file_id": str(record["file_id"]),
                    "sha256": str(record["sha256"]),
                    "destination_id": str(action.get("destination_id")),
                }
            )

    if exam_source_map:
        exam_map_path = Path(paths["exam_vault"]) / "2023A" / "SOURCE-MAP.json"
        write_json(exam_map_path, {"schema_version": 1, "files": exam_source_map})

    vault_hash_index = None
    if paths.get("vault_hash_index"):
        vault_hash_index = _write_vault_hash_index(inventory, Path(paths["vault_hash_index"]))

    lock.update({"applied": True, "applied_at": now_iso(), "result_count": len(results)})
    write_json(lock_path, lock)
    return {
        "schema_version": 1,
        "status": "needs_review" if plan.get("blocked_cases") else "pass",
        "applied_at": now_iso(),
        "manifest_fingerprint": plan.get("manifest_fingerprint"),
        "results": results,
        "blocked_cases": plan.get("blocked_cases", []),
        "vault_hash_index": str(vault_hash_index) if vault_hash_index else None,
    }
