import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True)
args = parser.parse_args()
root = Path(args.workspace)
lock = json.loads((root / "phase-lock.json").read_text(encoding="utf-8"))
if lock.get("phase") != "reflection":
    raise SystemExit(f"[FAIL] phase={lock.get('phase')}，本 Skill 只允许 reflection")
for name in ("blind-final", "approved-references"):
    if not (root / name).exists():
        raise SystemExit(f"[FAIL] 缺少 {name}")
print("[PASS] reflection 阶段锁有效")
