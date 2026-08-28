from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .util import now_iso, sha256_file, write_json


PROJECT_SKILLS = {"cumcm-a-solve", "cumcm-a-audit", "cumcm-a-reflect", "cumcm-a-evaluate"}


def classify_skill(path: Path, trainer_root: Path) -> tuple[str, str, str]:
    normalized = path.as_posix().casefold()
    name = path.parent.name
    if "/.codex/skills/.system/" in normalized:
        return "system_bundled", "preserve", "Codex 系统内置 Skill，禁止修改或禁用"
    if "/.codex/plugins/" in normalized:
        return "plugin_provided", "disable_in_profile", "插件提供的 Skill；仅在专用 Profile 中禁用，不卸载插件"
    project_skills_root = (trainer_root / ".agents" / "skills").resolve()
    try:
        is_project = path.resolve().is_relative_to(project_skills_root)
    except OSError:
        is_project = False
    if is_project:
        if name in PROJECT_SKILLS:
            return "repo_custom", "keep_enabled", "本项目明确创建的阶段 Skill"
        return "repo_custom", "disable_in_profile", "仓库级非项目阶段 Skill"
    if "/.codex/skills/" in normalized or "/.agents/skills/" in normalized:
        return "user_custom", "disable_in_profile", "用户可写自定义 Skill；保留源文件，仅在专用 Profile 中禁用"
    if "/program files/" in normalized or "/windowsapps/" in normalized:
        return "admin_managed", "preserve", "管理员或安装目录管理，禁止修改"
    return "unknown", "record_only", "来源无法确认，仅记录"


def inventory_skills(trainer_root: Path, roots: list[Path]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("SKILL.md")):
            key = str(path.resolve(strict=False)).casefold()
            if key in seen:
                continue
            seen.add(key)
            try:
                classification, action, reason = classify_skill(path, trainer_root)
                entry = {
                    "name": path.parent.name,
                    "skill_md_path": str(path),
                    "resolved_path": str(path.resolve(strict=False)),
                    "scope": "repository" if classification == "repo_custom" else "user_or_installation",
                    "source": str(root),
                    "is_symlink": path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != parent.parent),
                    "sha256": sha256_file(path),
                    "classification": classification,
                    "action": action,
                    "reason": reason,
                }
            except OSError as exc:
                entry = {
                    "name": path.parent.name,
                    "skill_md_path": str(path),
                    "resolved_path": str(path),
                    "scope": "unknown",
                    "source": str(root),
                    "is_symlink": False,
                    "sha256": None,
                    "classification": "unknown",
                    "action": "record_only",
                    "reason": f"读取失败：{exc}",
                }
            entries.append(entry)
    counts = Counter(entry["classification"] for entry in entries)
    actions = Counter(entry["action"] for entry in entries)
    return {
        "generated_at": now_iso(),
        "roots": [str(root) for root in roots],
        "total": len(entries),
        "counts": dict(sorted(counts.items())),
        "actions": dict(sorted(actions.items())),
        "skills": entries,
    }


def write_inventory(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    write_json(json_path, report)
    lines = ["# Skills 清点报告", "", f"生成时间：{report['generated_at']}", "", "## 汇总", ""]
    for key in ("system_bundled", "admin_managed", "user_custom", "repo_custom", "plugin_provided", "unknown"):
        lines.append(f"- `{key}`：{report['counts'].get(key, 0)}")
    lines.extend(["", f"- 专用 Profile 禁用：{report['actions'].get('disable_in_profile', 0)}", "", "## 明细", ""])
    lines.append("| 名称 | 分类 | 动作 | SHA-256 | 路径 |")
    lines.append("|---|---|---|---|---|")
    for item in report["skills"]:
        lines.append(
            f"| {item['name']} | {item['classification']} | {item['action']} | {item['sha256'] or '读取失败'} | `{item['resolved_path']}` |"
        )
    lines.extend(["", "未删除、未修改任何被清点的现有 Skill。", ""])
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def render_profile_config(report: dict[str, Any], *, baseline: bool = False) -> str:
    lines = [
        "# 由 CUMCM-A-Lab 生成；仅作用于本 CODEX_HOME。",
        "[features]",
        "memories = false",
        "",
        "[memories]",
        "use_memories = false",
        "generate_memories = false",
        "disable_on_external_context = true",
        "",
    ]
    disabled = []
    for item in report["skills"]:
        should_disable = item["action"] == "disable_in_profile" or (baseline and item["classification"] == "repo_custom")
        if should_disable:
            disabled.append(item)
    for item in sorted(disabled, key=lambda entry: entry["resolved_path"].casefold()):
        path = Path(item["resolved_path"]).as_posix().replace('"', '\\"')
        lines.extend(["[[skills.config]]", f'path = "{path}"', "enabled = false", ""])
    return "\n".join(lines)
