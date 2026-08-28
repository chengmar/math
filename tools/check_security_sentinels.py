from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.security_sentinels import inspect_stage_workspace, sentinel_report, write_sentinel_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check physical stage copies and record Codex sandbox sentinel status.")
    parser.add_argument("--case-dir", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--output", type=Path, default=TRAINER_ROOT / "reports" / "security-sentinels.json")
    parser.add_argument("--codex-probe-results", type=Path)
    args = parser.parse_args(argv)
    probes = []
    if args.case_dir:
        case_id = args.case_id or args.case_dir.name
        for phase in ("solve", "audit", "blind-revision", "reflection"):
            workspace = args.case_dir / "workspaces" / phase
            if workspace.exists():
                probes.append(inspect_stage_workspace(workspace, phase=phase, case_id=case_id))
    codex_results = []
    executed = False
    if args.codex_probe_results:
        codex_results = json.loads(args.codex_probe_results.read_text(encoding="utf-8-sig"))
        if not isinstance(codex_results, list):
            raise SystemExit("--codex-probe-results 必须是 JSON 数组")
        executed = True
    report = sentinel_report(probes, codex_probe_executed=executed, codex_probe_results=codex_results)
    write_sentinel_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
