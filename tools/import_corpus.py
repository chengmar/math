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

from cumcm_lab.corpus_import import apply_import, plan_import, write_dry_run_lock, write_import_reports  # noqa: E402
from cumcm_lab.corpus_inventory import inventory_corpus, write_inventory_reports  # noqa: E402
from cumcm_lab.training_queue import seal_final_test  # noqa: E402
from cumcm_lab.util import write_json  # noqa: E402


def _load_paths(path: Path) -> dict[str, str]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    paths = payload.get("paths", {})
    if not isinstance(paths, dict):
        raise ValueError(f"local paths file has no [paths] table: {path}")
    result = {str(key): str(value) for key, value in paths.items()}
    required = {"question_bank", "reference_vault", "exam_vault"}
    missing = sorted(required - result.keys())
    if missing:
        raise ValueError(f"local paths file is missing: {', '.join(missing)}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run or apply the deterministic CUMCM corpus import plan.")
    parser.add_argument("--problems-path", type=Path, required=True)
    parser.add_argument("--papers-path", type=Path, required=True)
    parser.add_argument("--split-config", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--local-paths", type=Path, default=TRAINER_ROOT.parent / "local-paths.toml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Write a dry-run plan (the default).")
    mode.add_argument("--apply", action="store_true", help="Apply a previously recorded matching dry-run plan.")
    parser.add_argument("--resume", action="store_true", help="Idempotently verify/reuse destinations from an applied plan.")
    parser.add_argument("--force-reindex", action="store_true", help="Replace the dry-run fingerprint with a fresh inventory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.resume and not args.apply:
        print("--resume requires --apply", file=sys.stderr)
        return 2
    if args.force_reindex and args.apply:
        print("--force-reindex cannot be combined with --apply; review a new dry-run first", file=sys.stderr)
        return 2
    try:
        paths = _load_paths(args.local_paths)
        paths["problems_intake"] = str(args.problems_path.resolve())
        paths["papers_intake"] = str(args.papers_path.resolve())
        inventory = inventory_corpus(args.problems_path, args.papers_path, args.split_config)
        outputs = write_inventory_reports(inventory, args.report_dir)
        plan = plan_import(inventory, paths)
        plan_path = args.report_dir / "import-plan.json"
        write_json(plan_path, plan)
        import_reports = write_import_reports(inventory, plan, args.report_dir)
        lock_path = args.report_dir / "import-lock.json"
        result_path = None
        if args.apply:
            result = apply_import(inventory, plan, paths, lock_path=lock_path, resume=args.resume)
            sealed_records = [
                item
                for item in inventory.get("files", [])
                if item.get("split") == "test" and item.get("matched_case_id") == "2023A"
            ]
            seal = seal_final_test(Path(paths["exam_vault"]) / "2023A" / "SEALED.json", sealed_records)
            result["final_test_seal"] = {
                "case_id": seal["case_id"],
                "status": seal["status"],
                "file_count": seal["file_count"],
                "manifest_sha256": seal["manifest_sha256"],
            }
            result_path = args.report_dir / "import-result.json"
            write_json(result_path, result)
        else:
            write_dry_run_lock(plan, lock_path)
            result = None
    except (OSError, ValueError) as exc:
        print(f"corpus import failed: {exc}", file=sys.stderr)
        return 2

    payload = {
        "mode": "apply" if args.apply else "dry_run",
        "plan_status": plan["status"],
        "plan": str(plan_path),
        "lock": str(lock_path),
        "inventory_outputs": outputs,
        "import_reports": import_reports,
        "result": str(result_path) if result_path else None,
        "copy_actions": sum(1 for item in plan["actions"] if item["action"] == "copy"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if plan["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
