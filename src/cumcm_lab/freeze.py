from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .leakage import check_leakage
from .state import load_state, transition
from .util import (
    git_snapshot,
    iter_regular_files,
    now_iso,
    read_json,
    read_yaml,
    safe_copy_tree,
    sha256_file,
    tree_hash,
    write_json,
)


VERSION_MAP = {
    "blind-v1": ("FROZEN_BLIND_V1.json", "blind-v1", "blind_v1_frozen"),
    "blind-final": ("FROZEN_BLIND_FINAL.json", "blind-final", "blind_final_frozen"),
}


def _trainer_root(case_dir: Path) -> Path:
    current = case_dir.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise FileNotFoundError("无法从案例路径定位 trainer 根目录。")


def freeze_solution(case_dir: Path, version: str, *, random_seed: int | None = None, run_command: str | None = None) -> Path:
    if version not in VERSION_MAP:
        raise ValueError("version 必须是 blind-v1 或 blind-final。")
    manifest_name, snapshot_name, target_state = VERSION_MAP[version]
    state = load_state(case_dir)["state"]
    if version == "blind-v1":
        if state != "solving":
            raise ValueError(f"blind-v1 只能从 solving 冻结，当前为 {state}。")
        source = case_dir / "workspaces" / "solve"
        phase = "solve"
    else:
        if state not in {"audited", "blind_revision_ready"}:
            raise ValueError(f"blind-final 只能从 audited 或 blind_revision_ready 冻结，当前为 {state}。")
        source = case_dir / "workspaces" / ("blind-revision" if state == "blind_revision_ready" else "solve")
        phase = "blind-revision" if state == "blind_revision_ready" else "solve"

    manifest_path = case_dir / "frozen" / manifest_name
    snapshot = case_dir / "frozen" / snapshot_name
    if manifest_path.exists() or snapshot.exists():
        raise FileExistsError(f"冻结版本已存在，拒绝覆盖：{manifest_path}")
    if not source.exists() or not any(source.rglob("*")):
        raise FileNotFoundError(f"解答工作区为空：{source}")
    trainer_root = _trainer_root(case_dir)
    lab_root = trainer_root.parent
    vault_roots = [lab_root.parent / "CUMCM-A-Vaults" / "reference-vault", lab_root.parent / "CUMCM-A-Vaults" / "exam-vault"]
    leakage_report = check_leakage(
        source,
        phase,
        vault_roots=vault_roots,
        report_path=case_dir / "reports" / f"leakage-{version}.json",
    )
    if leakage_report["status"] == "fail":
        raise ValueError("冻结前泄漏检查失败；详见案例 reports 目录。")
    snapshot.mkdir(parents=True, exist_ok=False)
    try:
        safe_copy_tree(source, snapshot)
        files = iter_regular_files(snapshot)
        if not files:
            raise ValueError("没有可冻结的常规文件。")
        file_entries = [
            {"path": path.relative_to(snapshot).as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)}
            for path in sorted(files, key=lambda item: item.relative_to(snapshot).as_posix())
        ]
        commit, dirty = git_snapshot(trainer_root)
        reproducibility = read_yaml(source / "reproducibility.yaml", {})
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "case_id": read_yaml(case_dir / "case.yaml").get("case_id"),
            "version": version,
            "created_at": now_iso(),
            "snapshot": snapshot_name,
            "tree_sha256": tree_hash(files, snapshot),
            "git_commit": commit,
            "git_dirty": dirty,
            "python_version": sys.version,
            "operating_system": platform.platform(),
            "dependencies": reproducibility.get("dependencies", []),
            "random_seed": random_seed if random_seed is not None else reproducibility.get("random_seed"),
            "run_command": run_command or reproducibility.get("run_command"),
            "knowledge_cards": reproducibility.get("knowledge_cards", []),
            "leakage_status": leakage_report["status"],
            "files": file_entries,
        }
        write_json(manifest_path, manifest)
        manifest_hash = sha256_file(manifest_path)
        transition(
            case_dir,
            target_state,
            command=f"freeze --version {version}",
            reason=f"冻结 {version} 解答并记录 SHA-256",
            manifest_hash=manifest_hash,
        )
        return manifest_path
    except Exception:
        if snapshot.exists() and not manifest_path.exists():
            shutil.rmtree(snapshot)
        raise


def verify_frozen(case_dir: Path, version: str, *, report_path: Path | None = None) -> dict[str, Any]:
    if version not in VERSION_MAP:
        raise ValueError("version 必须是 blind-v1 或 blind-final。")
    manifest_name, snapshot_name, _ = VERSION_MAP[version]
    manifest_path = case_dir / "frozen" / manifest_name
    snapshot = case_dir / "frozen" / snapshot_name
    if not manifest_path.exists():
        raise FileNotFoundError(f"冻结清单不存在：{manifest_path}")
    manifest = read_json(manifest_path)
    expected = {entry["path"]: entry for entry in manifest.get("files", [])}
    actual_files = iter_regular_files(snapshot) if snapshot.exists() else []
    actual = {path.relative_to(snapshot).as_posix(): path for path in actual_files}
    results: list[dict[str, Any]] = []
    for rel, entry in expected.items():
        path = actual.get(rel)
        if path is None:
            results.append({"path": rel, "status": "fail", "reason": "missing"})
            continue
        digest = sha256_file(path)
        results.append(
            {
                "path": rel,
                "status": "pass" if digest == entry.get("sha256") else "fail",
                "expected_sha256": entry.get("sha256"),
                "actual_sha256": digest,
            }
        )
    for rel in sorted(set(actual) - set(expected)):
        results.append({"path": rel, "status": "fail", "reason": "unexpected_file"})
    computed_tree = tree_hash(actual_files, snapshot) if actual_files else None
    tree_ok = computed_tree == manifest.get("tree_sha256")
    status = "pass" if tree_ok and results and all(item["status"] == "pass" for item in results) else "fail"
    report = {
        "status": status,
        "version": version,
        "verified_at": now_iso(),
        "manifest": str(manifest_path),
        "tree_hash_match": tree_ok,
        "expected_tree_sha256": manifest.get("tree_sha256"),
        "actual_tree_sha256": computed_tree,
        "files": results,
    }
    if report_path:
        write_json(report_path, report)
    return report
