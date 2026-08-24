#!/usr/bin/env python3
"""Compare deterministic artifacts from two independently completed pipeline runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NONDETERMINISTIC_OR_LOG_OUTPUTS = {
    "results/run_all.stdout.log",
    "results/run_all.stderr.log",
    "paper/<SOURCE_FILE_REDACTED>",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, default=Path("results/run_manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("results/repeatability.json"))
    return parser.parse_args()


def indexed(manifest: dict, section: str) -> dict[str, dict]:
    return {item["path"]: item for item in manifest[section]}


def main() -> int:
    args = parse_args()
    baseline_path = args.baseline if args.baseline.is_absolute() else ROOT / args.baseline
    current_path = args.current if args.current.is_absolute() else ROOT / args.current
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = json.loads(current_path.read_text(encoding="utf-8"))

    checks = []
    for section in ("code", "inputs", "outputs"):
        first = indexed(baseline, section)
        second = indexed(current, section)
        paths = sorted(set(first) | set(second))
        if section == "outputs":
            paths = [path for path in paths if path not in NONDETERMINISTIC_OR_LOG_OUTPUTS]
        for path in paths:
            first_item = first.get(path)
            second_item = second.get(path)
            item_status = (
                "pass"
                if first_item is not None
                and second_item is not None
                and first_item["bytes"] == second_item["bytes"]
                and first_item["sha256"] == second_item["sha256"]
                else "fail"
            )
            checks.append(
                {
                    "section": section,
                    "path": path,
                    "status": item_status,
                    "baseline_sha256": first_item.get("sha256") if first_item else None,
                    "current_sha256": second_item.get("sha256") if second_item else None,
                }
            )

    status = "pass" if checks and all(item["status"] == "pass" for item in checks) else "fail"
    report = {
        "status": status,
        "baseline_manifest": baseline_path.relative_to(ROOT).as_posix(),
        "current_manifest": current_path.relative_to(ROOT).as_posix(),
        "comparison_scope": (
            "All declared code and inputs plus deterministic outputs; execution logs and PDF are excluded."
        ),
        "checked_entries": len(checks),
        "pass_count": sum(item["status"] == "pass" for item in checks),
        "fail_count": sum(item["status"] == "fail" for item in checks),
        "checks": checks,
        "pdf_repeatability_status": "needs_review",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "checked_entries", "pass_count", "fail_count")}, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
