from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

trainer = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(trainer / "src"))

from cumcm_lab.autopilot import autopilot_status


def main() -> int:
    parser = argparse.ArgumentParser(description="显示 Autopilot 锁、PID、断点和队列状态")
    parser.add_argument("--runtime-dir", type=Path, default=trainer / "runtime")
    parser.add_argument("--queue", type=Path, default=trainer / "runtime" / "training-queue-state.json")
    args = parser.parse_args()
    try:
        print(json.dumps(autopilot_status(args.runtime_dir, args.queue), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
