from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TRAINER_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cumcm_lab.corpus_inventory import inventory_corpus, write_inventory_reports  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inventory CUMCM A-problem corpus without parsing document bodies.")
    parser.add_argument("--problems-path", type=Path, required=True)
    parser.add_argument("--papers-path", type=Path, required=True)
    parser.add_argument("--split-config", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = inventory_corpus(args.problems_path, args.papers_path, args.split_config)
        outputs = write_inventory_reports(report, args.report_dir)
    except (OSError, ValueError) as exc:
        print(f"inventory failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"summary": report["summary"], "outputs": outputs}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
