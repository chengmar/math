from __future__ import annotations

from pathlib import Path

from .util import find_trainer_root, git_snapshot, now_iso, read_yaml, write_yaml


STATES = {
    "initialized",
    "solve_ready",
    "solving",
    "blind_v1_frozen",
    "audit_ready",
    "audited",
    "blind_revision_ready",
    "blind_final_frozen",
    "reflection_ready",
    "reflected",
    "knowledge_proposed",
    "evaluation_ready",
    "evaluated",
    "archived",
    "deferred_platform_safety",
}

TRANSITIONS: dict[str, set[str]] = {
    "initialized": {"solve_ready", "evaluation_ready", "deferred_platform_safety"},
    "solve_ready": {"solving", "deferred_platform_safety"},
    "solving": {"blind_v1_frozen", "deferred_platform_safety"},
    "blind_v1_frozen": {"audit_ready"},
    "audit_ready": {"audited"},
    "audited": {"blind_revision_ready", "blind_final_frozen"},
    "blind_revision_ready": {"blind_final_frozen"},
    "blind_final_frozen": {"reflection_ready"},
    "reflection_ready": {"reflected"},
    "reflected": {"knowledge_proposed", "archived"},
    "knowledge_proposed": {"archived"},
    "evaluation_ready": {"evaluated"},
    "evaluated": {"archived"},
    "archived": set(),
    "deferred_platform_safety": set(),
}


def load_state(case_dir: Path) -> dict:
    data = read_yaml(case_dir / "case-state.yaml")
    state = data.get("state")
    if state not in STATES:
        raise ValueError(f"案例状态无效：{state!r}")
    data.setdefault("history", [])
    return data


def transition(
    case_dir: Path,
    target: str,
    *,
    command: str,
    actor: str = "cumcm_lab",
    reason: str,
    manifest_hash: str | None = None,
) -> dict:
    if target not in STATES:
        raise ValueError(f"未知目标状态：{target}")
    data = load_state(case_dir)
    current = data["state"]
    if target not in TRANSITIONS[current]:
        raise ValueError(f"非法状态迁移：{current} -> {target}")
    trainer_root = find_trainer_root(case_dir)
    commit, _ = git_snapshot(trainer_root)
    data["state"] = target
    data["history"].append(
        {
            "from": current,
            "to": target,
            "timestamp": now_iso(),
            "command": command,
            "actor": actor,
            "reason": reason,
            "git_commit": commit,
            "manifest_hash": manifest_hash,
        }
    )
    write_yaml(case_dir / "case-state.yaml", data)
    case_meta = read_yaml(case_dir / "case.yaml")
    case_meta["status"] = target
    write_yaml(case_dir / "case.yaml", case_meta)
    return data
