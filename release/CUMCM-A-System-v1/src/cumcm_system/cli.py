from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .core import SystemError, add_references, case_dir, case_status, export_submission, new_case, pause_case, resume_case, run_case, set_mode, system_root, verify_freeze, verify_system


def paths(values: list[str]) -> list[Path]:
    return [Path(value) for value in values]


def main() -> int:
    parser = argparse.ArgumentParser(prog="cumcm-system")
    parser.add_argument("--root", type=Path, default=system_root())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify")
    new = sub.add_parser("new-case"); new.add_argument("--id", required=True); new.add_argument("--problem", action="append", default=[]); new.add_argument("--data", action="append", default=[])
    run = sub.add_parser("run"); run.add_argument("--id", required=True); run.add_argument("--executor", choices=["fake", "codex"], default="codex"); run.add_argument("--max-stages", type=int)
    status = sub.add_parser("status"); status.add_argument("--id", required=True)
    pause = sub.add_parser("pause"); pause.add_argument("--id", required=True)
    resume = sub.add_parser("resume"); resume.add_argument("--id", required=True); resume.add_argument("--executor", choices=["fake", "codex"], default="codex")
    final = sub.add_parser("verify-final"); final.add_argument("--id", required=True)
    refs = sub.add_parser("add-references"); refs.add_argument("--id", required=True); refs.add_argument("--reference", action="append", default=[]); refs.add_argument("--executor", choices=["fake", "codex"], default="codex")
    export = sub.add_parser("export"); export.add_argument("--id", required=True); export.add_argument("--destination", type=Path, required=True)
    mode = sub.add_parser("set-mode"); mode.add_argument("--mode", required=True, choices=["workflow-only", "full-trained"])
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    try:
        if args.command == "verify": result = verify_system(root)
        elif args.command == "new-case": result = new_case(root, args.id, paths(args.problem), paths(args.data))
        elif args.command == "run": result = run_case(root, args.id, args.executor, args.max_stages)
        elif args.command == "status": result = case_status(root, args.id)
        elif args.command == "pause": result = pause_case(root, args.id)
        elif args.command == "resume": result = resume_case(root, args.id, args.executor)
        elif args.command == "verify-final":
            case = case_dir(root, args.id); result = verify_freeze(case / "frozen" / "blind-final", case / "frozen" / "FROZEN_BLIND_FINAL.json")
        elif args.command == "add-references": result = add_references(root, args.id, paths(args.reference), args.executor)
        elif args.command == "export": result = export_submission(root, args.id, args.destination)
        elif args.command == "set-mode": result = set_mode(root, args.mode)
        else: raise AssertionError(args.command)
    except (SystemError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "fail", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "pass") != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
