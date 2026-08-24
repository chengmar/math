from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.autopilot import preflight_autopilot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight the first queue item without consuming an attempt.")
    parser.add_argument("--queue", type=Path, default=TRAINER_ROOT / "runtime" / "training-queue-state.json")
    parser.add_argument("--runtime-dir", type=Path, default=TRAINER_ROOT / "runtime")
    parser.add_argument("--codex-home", type=Path, default=TRAINER_ROOT.parent / "codex-home")
    args = parser.parse_args(argv)
    try:
        result = preflight_autopilot(args.queue, args.runtime_dir, args.codex_home)
    except (OSError, ValueError) as exc:
        print(f"autopilot preflight failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ready" else 3


if __name__ == "__main__":
    raise SystemExit(main())
