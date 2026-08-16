import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True)
args = parser.parse_args()
root = Path(args.workspace)
lock = json.loads((root / "phase-lock.json").read_text(encoding="utf-8"))
if lock.get("phase") != "audit":
    raise SystemExit(f"[FAIL] phase={lock.get('phase')}，本 Skill 只允许 audit")
if not (root / "frozen-solution").exists():
    raise SystemExit("[FAIL] 缺少 frozen-solution")
print("[PASS] audit 阶段锁有效")

