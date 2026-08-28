from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.curator import run_curator_classification  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one isolated, logistics-only corpus curator decision.")
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--curator-home", type=Path, default=TRAINER_ROOT.parent / "curator-codex-home")
    parser.add_argument("--run-root", type=Path, default=TRAINER_ROOT / "runtime" / "curator-runs")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning", choices=["low", "medium", "high", "xhigh"], default="medium")
    args = parser.parse_args(argv)
    try:
        record = json.loads(args.record.read_text(encoding="utf-8-sig"))
        result = run_curator_classification(
            record,
            args.preview,
            curator_home=args.curator_home,
            run_root=args.run_root,
            model=args.model,
            reasoning_effort=args.reasoning,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"curator failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "completed") == "completed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
