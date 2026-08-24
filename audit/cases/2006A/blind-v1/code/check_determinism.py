#!/usr/bin/env python3
"""Run the numerical pipeline twice and compare result/figure hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    excluded = {"determinism_check.json", "verification_report.json"}
    files = [path for path in (root / "results").glob("*") if path.is_file() and path.name not in excluded]
    files += [path for path in (root / "figures").glob("*.png") if path.is_file()]
    return {path.relative_to(root).as_posix(): digest(path) for path in sorted(files)}


def run_solver(root: Path, total: int) -> None:
    command = [sys.executable, str(root / "code" / "solve.py"), "--workspace", str(root), "--total-titles", str(total)]
    subprocess.run(command, cwd=root, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--total-titles", type=int, default=500)
    args = parser.parse_args()
    root = args.workspace.resolve()
    run_solver(root, args.total_titles)
    first = snapshot(root)
    run_solver(root, args.total_titles)
    second = snapshot(root)
    names = sorted(set(first) | set(second))
    mismatches = [name for name in names if first.get(name) != second.get(name)]
    status = "pass" if not mismatches else "fail"
    report = {
        "status": status,
        "runs": 2,
        "total_titles": args.total_titles,
        "compared_files": len(names),
        "mismatches": mismatches,
        "second_run_sha256": second,
    }
    output = root / "results" / "determinism_check.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"determinism_status={status}")
    print(f"compared_files={len(names)}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
