from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import iter_regular_files, now_iso, read_json, read_yaml, sha256_file, write_json


PROTECTED_PHASES = {"solve", "audit", "blind-revision", "evaluation"}
FORBIDDEN_TOKENS = {
    "reference-vault",
    "exam-vault",
    "answer-key",
    "reference-paper",
    "award-paper",
    "winner-paper",
    "标准答案",
    "赛题讲评",
}


def _vault_hashes(vault_roots: list[Path]) -> tuple[dict[str, list[str]], list[str]]:
    hashes: dict[str, list[str]] = {}
    warnings: list[str] = []
    for root in vault_roots:
        if not root.exists():
            warnings.append(f"Vault 不存在，无法执行哈希比对：{root}")
            continue
        try:
            for path in iter_regular_files(root):
                hashes.setdefault(sha256_file(path), []).append(str(path))
        except (OSError, ValueError) as exc:
            warnings.append(f"Vault 扫描不完整：{root}: {exc}")
    return hashes, warnings


def check_leakage(
    workspace: Path,
    phase: str,
    *,
    vault_roots: list[Path] | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    warnings: list[str] = []
    vault_hashes, vault_warnings = _vault_hashes(vault_roots or [])
    warnings.extend(vault_warnings)
    if not workspace.exists():
        findings.append({"category": "workspace_missing", "evidence": str(workspace)})
        files: list[Path] = []
    else:
        try:
            files = iter_regular_files(workspace)
        except ValueError as exc:
            findings.append({"category": "symlink", "evidence": str(exc)})
            files = [path for path in workspace.rglob("*") if path.is_file() and not path.is_symlink()]

    if phase in PROTECTED_PHASES:
        lock_path = workspace / "phase-lock.json"
        if not lock_path.exists():
            findings.append({"category": "phase_lock_missing", "evidence": str(lock_path)})
        else:
            lock = read_json(lock_path)
            if lock.get("phase") != phase:
                findings.append(
                    {
                        "category": "phase_lock_mismatch",
                        "evidence": f"期望 {phase}，实际 {lock.get('phase')}",
                    }
                )

    for path in files:
        relative = path.relative_to(workspace).as_posix()
        lowered = relative.casefold()
        for token in FORBIDDEN_TOKENS:
            if token.casefold() in lowered:
                findings.append({"category": "forbidden_path_token", "evidence": relative})
                break
        try:
            digest = sha256_file(path)
        except OSError as exc:
            warnings.append(f"无法计算哈希：{relative}: {exc}")
            continue
        if digest in vault_hashes:
            findings.append(
                {
                    "category": "known_vault_hash",
                    "evidence": f"{relative} 与 Vault 文件哈希相同",
                }
            )
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                payload = read_yaml(path)
            except Exception as exc:  # YAML 语法错误由其他检查处理
                warnings.append(f"无法解析 YAML：{relative}: {exc}")
                continue
            if isinstance(payload, dict) and str(payload.get("status", "")).casefold() == "candidate":
                findings.append({"category": "candidate_knowledge", "evidence": relative})

    status = "fail" if findings else ("needs_review" if warnings else "pass")
    report = {
        "status": status,
        "phase": phase,
        "workspace": str(workspace.resolve(strict=False)),
        "checked_at": now_iso(),
        "checked_files": len(files),
        "findings": findings,
        "warnings": warnings,
        "limitation": "仅检测文件层面的明显泄漏，不能证明模型内部绝对未见过相似内容。",
    }
    if report_path:
        write_json(report_path, report)
    return report

