from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.cases import init_runtime_case  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create one external, non-Git runtime case from question-bank.")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--trainer-root", type=Path, default=TRAINER_ROOT)
    args = parser.parse_args(argv)
    try:
        case_dir = init_runtime_case(args.trainer_root, args.case_id)
    except (OSError, ValueError) as exc:
        print(f"runtime case initialization failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "initialized", "case_id": args.case_id, "case_dir": str(case_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
