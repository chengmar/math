from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from .cases import find_case, init_case
from .compare import compare_runs
from .freeze import freeze_solution, verify_frozen
from .inventory import inventory_skills, render_profile_config, write_inventory
from .knowledge import promote_lesson, retrieve_knowledge
from .leakage import check_leakage
from .paper import lint_paper
from .phases import complete_phase, prepare_phase
from .regression import run_regression
from .scoring import score_case
from .state import load_state
from .util import find_trainer_root, load_lab_paths, read_json
from .verify import artifact_root, verify_case


def _root(value: str | None) -> Path:
    return Path(value).resolve() if value else find_trainer_root()


def _status_exit(status: str) -> int:
    return 0 if status == "pass" else (2 if status == "needs_review" else 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CUMCM A 题训练实验室工作流")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-case")
    init.add_argument("--root")
    init.add_argument("--case-id", required=True)
    init.add_argument("--split", required=True, choices=["train", "dev", "exam", "dummy"])
    init.add_argument("--title")
    init.add_argument("--problem-family", default="unspecified")

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--root")
    prepare.add_argument("--case-id", required=True)
    prepare.add_argument("--phase", required=True, choices=["solve", "audit", "blind-revision", "reflection", "evaluation"])

    freeze = sub.add_parser("freeze")
    freeze.add_argument("--root")
    freeze.add_argument("--case-id", required=True)
    freeze.add_argument("--version", required=True, choices=["blind-v1", "blind-final"])
    freeze.add_argument("--random-seed", type=int)
    freeze.add_argument("--run-command")

    verify_f = sub.add_parser("verify-frozen")
    verify_f.add_argument("--root")
    verify_f.add_argument("--case-id", required=True)
    verify_f.add_argument("--version", required=True, choices=["blind-v1", "blind-final"])

    verify_c = sub.add_parser("verify-case")
    verify_c.add_argument("--root")
    verify_c.add_argument("--case-id", required=True)

    complete = sub.add_parser("complete")
    complete.add_argument("--root")
    complete.add_argument("--case-id", required=True)
    complete.add_argument("--phase", required=True, choices=["audit", "reflection", "evaluation"])

    status = sub.add_parser("status")
    status.add_argument("--root")
    status.add_argument("--case-id", required=True)

    leak = sub.add_parser("check-leakage")
    leak.add_argument("--root")
    leak.add_argument("--workspace", required=True)
    leak.add_argument("--phase", required=True, choices=["solve", "audit", "blind-revision", "evaluation"])
    leak.add_argument("--report")

    retrieve = sub.add_parser("retrieve")
    retrieve.add_argument("--root")
    retrieve.add_argument("--query", default="{}", help="JSON 查询对象")
    retrieve.add_argument("--phase", default="solve", choices=["solve", "reflection", "evaluation"])
    retrieve.add_argument("--limit", type=int, default=5)
    retrieve.add_argument("--log")

    lint = sub.add_parser("lint-paper")
    lint.add_argument("--root")
    lint.add_argument("--paper", required=True)
    lint.add_argument("--artifact-root")
    lint.add_argument("--report")

    score = sub.add_parser("score")
    score.add_argument("--root")
    score.add_argument("--case-id", required=True)
    score.add_argument("--judge-scores")

    promote = sub.add_parser("promote")
    promote.add_argument("--root")
    promote.add_argument("--candidate", required=True)
    promote.add_argument("--human-approved", action="store_true")
    promote.add_argument("--approved-by")

    regression = sub.add_parser("regression")
    regression.add_argument("--root")

    compare = sub.add_parser("compare")
    compare.add_argument("--root")
    compare.add_argument("--input", required=True)

    inventory = sub.add_parser("inventory")
    inventory.add_argument("--root")

    initialize = sub.add_parser("init")
    initialize.add_argument("--root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _root(getattr(args, "root", None))
        if args.command == "init-case":
            result = init_case(root, args.case_id, args.split, title=args.title, problem_family=args.problem_family)
            print(result)
        elif args.command == "prepare":
            print(prepare_phase(root, args.case_id, args.phase))
        elif args.command == "freeze":
            case_dir = find_case(root, args.case_id)
            print(freeze_solution(case_dir, args.version, random_seed=args.random_seed, run_command=args.run_command))
        elif args.command == "verify-frozen":
            case_dir = find_case(root, args.case_id)
            report = verify_frozen(case_dir, args.version, report_path=case_dir / "reports" / f"verify-{args.version}.json")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return _status_exit(report["status"])
        elif args.command == "verify-case":
            case_dir = find_case(root, args.case_id)
            report = verify_case(case_dir, report_path=case_dir / "reports" / "verify-case.json")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return _status_exit(report["status"])
        elif args.command == "complete":
            print(json.dumps(complete_phase(root, args.case_id, args.phase), ensure_ascii=False, indent=2))
        elif args.command == "status":
            print(json.dumps(load_state(find_case(root, args.case_id)), ensure_ascii=False, indent=2))
        elif args.command == "check-leakage":
            paths = load_lab_paths(root)
            vaults = [Path(paths["reference_vault"]), Path(paths["exam_vault"])]
            report = check_leakage(Path(args.workspace), args.phase, vault_roots=vaults, report_path=Path(args.report) if args.report else None)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return _status_exit(report["status"])
        elif args.command == "retrieve":
            query = json.loads(args.query)
            result = retrieve_knowledge(root / "knowledge", query, phase=args.phase, limit=args.limit, log_path=Path(args.log) if args.log else None)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "lint-paper":
            report = lint_paper(Path(args.paper), root / "config" / "competition-rules.yaml", artifact_root=Path(args.artifact_root) if args.artifact_root else None, report_path=Path(args.report) if args.report else None)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return _status_exit(report["status"])
        elif args.command == "score":
            report = score_case(find_case(root, args.case_id), root, judge_scores_path=Path(args.judge_scores) if args.judge_scores else None)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return _status_exit(report["status"])
        elif args.command == "promote":
            proposal = promote_lesson(root / "knowledge", Path(args.candidate), human_approved=args.human_approved, approved_by=args.approved_by)
            print(json.dumps(proposal, ensure_ascii=False, indent=2))
            return _status_exit("pass" if proposal["status"] == "approved" else "needs_review")
        elif args.command == "regression":
            report = run_regression(root)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return _status_exit(report["status"])
        elif args.command == "compare":
            print(json.dumps(compare_runs(Path(args.input), root / "reports"), ensure_ascii=False, indent=2))
        elif args.command == "inventory":
            user = Path.home()
            roots = [user / ".agents" / "skills", user / ".codex" / "skills", user / ".codex" / "plugins", root / ".agents" / "skills"]
            report = inventory_skills(root, roots)
            lab_environment = root.parent / "environment"
            write_inventory(report, lab_environment / "skill-inventory.json", lab_environment / "skill-inventory.md")
            (root.parent / "codex-home" / "config.toml").write_text(render_profile_config(report), encoding="utf-8")
            (root.parent / "baseline-codex-home" / "config.toml").write_text(render_profile_config(report, baseline=True), encoding="utf-8")
            print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
        elif args.command == "init":
            print(json.dumps({"status": "pass", "trainer_root": str(root), "paths": load_lab_paths(root)}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
