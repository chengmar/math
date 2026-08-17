from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import find_trainer_root, iter_regular_files, now_iso, read_json, read_yaml, sha256_file, write_json


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


def _load_rules(workspace: Path) -> tuple[set[str], set[str]]:
    try:
        trainer_root = find_trainer_root(workspace)
        rules_path = trainer_root / "config" / "leakage-rules.yaml"
        if not rules_path.is_file():
            return set(PROTECTED_PHASES), set(FORBIDDEN_TOKENS)
        rules = read_yaml(rules_path)
    except (FileNotFoundError, OSError, ValueError):
        return set(PROTECTED_PHASES), set(FORBIDDEN_TOKENS)
    phases = {str(item) for item in rules.get("protected_phases", [])}
    tokens = {str(item) for item in rules.get("forbidden_path_tokens", [])}
    if not phases or not tokens:
        raise ValueError("leakage-rules.yaml 缺少 protected_phases 或 forbidden_path_tokens。")
    return phases, tokens


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


def _indexed_vault_hashes(index_path: Path) -> tuple[dict[str, list[str]], list[str]]:
    if not index_path.is_file():
        return {}, [f"Vault 哈希索引不存在：{index_path}"]
    try:
        payload = read_json(index_path)
        hashes: dict[str, list[str]] = {}
        for item in payload.get("hashes", []):
            digest = str(item.get("sha256", ""))
            if len(digest) == 64:
                hashes.setdefault(digest, []).append("indexed-vault-file")
        if not hashes:
            return {}, [f"Vault 哈希索引为空：{index_path}"]
        return hashes, []
    except (OSError, ValueError, TypeError) as exc:
        return {}, [f"Vault 哈希索引无法读取：{index_path}: {exc}"]


def check_leakage(
    workspace: Path,
    phase: str,
    *,
    vault_roots: list[Path] | None = None,
    vault_hash_index: Path | None = None,
    strict_vaults: bool = False,
    report_path: Path | None = None,
) -> dict[str, Any]:
    protected_phases, forbidden_tokens = _load_rules(workspace)
    findings: list[dict[str, str]] = []
    warnings: list[str] = []
    if vault_hash_index is not None:
        vault_hashes, vault_warnings = _indexed_vault_hashes(vault_hash_index)
    else:
        vault_hashes, vault_warnings = _vault_hashes(vault_roots or [])
    warnings.extend(vault_warnings)
    if strict_vaults and vault_warnings:
        findings.append({"category": "vault_hash_index_unavailable", "evidence": "真实训练要求完整的预计算 Vault 哈希索引"})
    if not workspace.exists():
        findings.append({"category": "workspace_missing", "evidence": str(workspace)})
        files: list[Path] = []
    else:
        try:
            files = iter_regular_files(workspace)
        except ValueError as exc:
            findings.append({"category": "symlink", "evidence": str(exc)})
            files = [path for path in workspace.rglob("*") if path.is_file() and not path.is_symlink()]

    if phase in protected_phases:
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
            for manifest_name, hash_field in (
                ("allowed-paths.json", "allowed_paths_sha256"),
                ("forbidden-paths.json", "forbidden_paths_sha256"),
            ):
                manifest_path = workspace / manifest_name
                if not manifest_path.exists():
                    findings.append({"category": "phase_manifest_missing", "evidence": manifest_name})
                elif lock.get(hash_field) != sha256_file(manifest_path):
                    findings.append({"category": "phase_manifest_tampered", "evidence": manifest_name})

    for path in files:
        relative = path.relative_to(workspace).as_posix()
        lowered = relative.casefold()
        for token in forbidden_tokens:
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
