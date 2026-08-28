from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


OWNED_FILES = sorted(
    [
        "assumptions.yaml",
        "code/README.md",
        "code/artifact_manifest.py",
        "code/check_consistency.py",
        "code/extract_mdb.ps1",
        "code/independent_verify.py",
        "code/requirements.txt",
        "code/run_all.ps1",
        "code/solve.py",
        "data-audit.md",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "figures/<SOURCE_FILE_REDACTED>",
        "model-selection.md",
        "paper/<SOURCE_FILE_REDACTED>",
        "paper/main.tex",
        "paper/paper.md",
        "paper/preamble.tex",
        "paper/references.bib",
        "problem-analysis.md",
        "reproducibility.yaml",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/consecutive-rerun.json",
        "results/data-audit.json",
        "results/environment.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/independent-verification.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/paper-consistency.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "results/summary.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/tables/flow-allocation.md",
        "results/tables/flow-allocation.tex",
        "results/tables/key-values.tex",
        "results/tables/survey-key.tex",
        "results/validation.json",
        "results/<SOURCE_FILE_REDACTED>",
        "results/<SOURCE_FILE_REDACTED>",
        "revision-response.md",
        "solution-report.yaml",
        "variables.yaml",
    ]
)
MANIFEST_RELATIVE = "results/checksums.sha256"
LINE_PATTERN = re.compile(r"^([0-9a-f]{64})  ([^\\]+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write or verify the revision artifact manifest.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--verify", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_manifest(workspace: Path) -> int:
    missing = [relative for relative in OWNED_FILES if not (workspace / relative).is_file()]
    if missing:
        print(json.dumps({"status": "fail", "missing": missing}, ensure_ascii=False))
        return 1
    lines = [f"{digest(workspace / relative)}  {relative}" for relative in OWNED_FILES]
    manifest = workspace / MANIFEST_RELATIVE
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "entries": len(lines), "manifest": MANIFEST_RELATIVE}))
    return 0


def verify_manifest(workspace: Path, report_path: Path | None) -> int:
    manifest = workspace / MANIFEST_RELATIVE
    failures: list[dict[str, str]] = []
    invalid_lines = 0
    path_escapes = 0
    entries: dict[str, str] = {}
    if not manifest.is_file():
        report = {
            "status": "fail",
            "manifest": MANIFEST_RELATIVE,
            "entries": 0,
            "passed": 0,
            "failed": 0,
            "missing": OWNED_FILES,
            "unexpected": [],
            "invalid_lines": 0,
            "path_escapes": 0,
            "failures": [],
        }
    else:
        for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
            match = LINE_PATTERN.fullmatch(line)
            if match is None:
                invalid_lines += 1
                failures.append({"path": f"line:{line_number}", "reason": "invalid_manifest_line"})
                continue
            expected_hash, relative = match.groups()
            if relative in entries:
                invalid_lines += 1
                failures.append({"path": relative, "reason": "duplicate_manifest_entry"})
                continue
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace)
            except ValueError:
                path_escapes += 1
                failures.append({"path": relative, "reason": "path_escape"})
                continue
            entries[relative] = expected_hash

        expected_set = set(OWNED_FILES)
        actual_set = set(entries)
        missing = sorted(expected_set - actual_set)
        unexpected = sorted(actual_set - expected_set)
        passed = 0
        for relative in sorted(actual_set & expected_set):
            target = workspace / relative
            if not target.is_file():
                failures.append({"path": relative, "reason": "missing_file"})
                continue
            actual_hash = digest(target)
            if actual_hash != entries[relative]:
                failures.append(
                    {
                        "path": relative,
                        "reason": "sha256_mismatch",
                        "expected_sha256": entries[relative],
                        "actual_sha256": actual_hash,
                    }
                )
            else:
                passed += 1

        overall = not failures and not missing and not unexpected and invalid_lines == 0 and path_escapes == 0
        report = {
            "status": "pass" if overall else "fail",
            "manifest": MANIFEST_RELATIVE,
            "entries": len(entries),
            "passed": passed,
            "failed": len(failures),
            "missing": missing,
            "unexpected": unexpected,
            "invalid_lines": invalid_lines,
            "path_escapes": path_escapes,
            "failures": failures,
        }

    if report_path is not None:
        output = report_path if report_path.is_absolute() else workspace / report_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    if args.write:
        return write_manifest(workspace)
    return verify_manifest(workspace, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
