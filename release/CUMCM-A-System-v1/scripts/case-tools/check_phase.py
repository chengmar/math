from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def fail(message: str) -> int:
    print(json.dumps({"status": "fail", "error": message}, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--phase")
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    phase_lock = workspace / "phase-lock.json"
    allowed_file = workspace / "allowed-paths.json"
    if not phase_lock.is_file() or not allowed_file.is_file():
        return fail("phase-lock.json 或 allowed-paths.json 缺失；禁止向工作区外搜索替代文件")
    lock = json.loads(phase_lock.read_text(encoding="utf-8-sig"))
    allowed = json.loads(allowed_file.read_text(encoding="utf-8-sig"))
    declared = str(lock.get("active_phase") or "")
    requested = args.phase or declared
    if not declared or requested != declared or allowed.get("phase") != declared:
        return fail("阶段锁与允许路径清单不一致")
    missing: list[str] = []
    unsafe: list[str] = []
    for role in ("read", "write"):
        for raw in allowed.get(role, []):
            relative = Path(str(raw))
            if relative.is_absolute() or ".." in relative.parts:
                unsafe.append(str(raw))
                continue
            candidate = (workspace / relative).resolve(strict=False)
            try:
                candidate.relative_to(workspace)
            except ValueError:
                unsafe.append(str(raw))
                continue
            if role == "read" and not candidate.exists():
                missing.append(str(raw))
    if unsafe:
        return fail(f"允许路径清单包含越界项：{unsafe}")
    if missing:
        return fail(f"阶段只读输入缺失：{missing}")
    print(json.dumps({
        "status": "pass",
        "case_id": lock.get("case_id"),
        "phase": declared,
        "allowed_paths": str(allowed_file.relative_to(workspace)),
        "outside_workspace_search_forbidden": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
