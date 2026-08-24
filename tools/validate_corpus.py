from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TRAINER_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cumcm_lab.corpus_inventory import inventory_corpus  # noqa: E402
from cumcm_lab.corpus_validate import validate_corpus, write_validation_report  # noqa: E402
from cumcm_lab.util import read_json  # noqa: E402


def _load_paths(path: Path) -> dict[str, str]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    values = payload.get("paths", {})
    if not isinstance(values, dict):
        raise ValueError(f"local paths file has no [paths] table: {path}")
    return {str(key): str(value) for key, value in values.items()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate corpus splits, hashes, destinations and Git isolation.")
    parser.add_argument("--problems-path", type=Path, required=True)
    parser.add_argument("--papers-path", type=Path, required=True)
    parser.add_argument("--split-config", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--local-paths", type=Path, default=TRAINER_ROOT.parent / "local-paths.toml")
    parser.add_argument("--trainer-root", type=Path, default=TRAINER_ROOT)
    parser.add_argument("--import-result", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        paths = _load_paths(args.local_paths)
        paths["problems_intake"] = str(args.problems_path.resolve())
        paths["papers_intake"] = str(args.papers_path.resolve())
        inventory = inventory_corpus(args.problems_path, args.papers_path, args.split_config)
        import_result = read_json(args.import_result) if args.import_result else None
        report = validate_corpus(
            inventory,
            paths,
            args.split_config,
            trainer_root=args.trainer_root,
            import_result=import_result,
        )
        output = write_validation_report(report, args.report_dir / "corpus-validation.json")
    except (OSError, ValueError) as exc:
        print(f"corpus validation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "summary": report["summary"], "report": str(output)}, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
