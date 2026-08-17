from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.autopilot import request_autopilot_stop  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Request a safe stop at the next durable queue checkpoint.")
    parser.add_argument("--runtime-dir", type=Path, default=TRAINER_ROOT / "runtime")
    parser.add_argument("--queue", type=Path, default=TRAINER_ROOT / "runtime" / "training-queue-state.json")
    args = parser.parse_args(argv)
    try:
        result = request_autopilot_stop(args.runtime_dir, args.queue)
    except (OSError, ValueError) as exc:
        print(f"stop request failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
