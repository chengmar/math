#!/usr/bin/env python3
"""One-command solve, clean repeatability check, verify, and provenance build."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def dump(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def clean_solve(parent: Path, label: str) -> dict:
    workspace = parent / label
    workspace.mkdir()
    shutil.copytree(ROOT/"input", workspace/"input")
    shutil.copytree(ROOT/"code", workspace/"code")
    (workspace/"paper").mkdir()
    completed = run([sys.executable, "-I", "-B", "code/solve.py"], workspace)
    manifest_path = workspace/"results"/"manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"files": {}}
    return {
        "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
        "files": manifest.get("files", {}),
    }


def canonical_tree(files: dict[str, dict]) -> str:
    payload = "".join(
        f"{path}\t{files[path]['size_bytes']}\t{files[path]['sha256']}\n"
        for path in sorted(files)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    root_run = run([sys.executable, "-I", "-B", "code/solve.py"], ROOT)
    if root_run.returncode != 0:
        print(root_run.stdout, end="")
        print(root_run.stderr, end="", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="repeatability-", dir=RESULTS) as temp_name:
        parent = Path(temp_name)
        first = clean_solve(parent, "run-1")
        second = clean_solve(parent, "run-2")

    same = (first["exit_code"] == 0 and second["exit_code"] == 0
            and first["files"] == second["files"] and bool(first["files"]))
    repeatability = {
        "case_id": "2014A",
        "phase": "blind-revision",
        "status": "pass" if same else "fail",
        "command": f"{sys.executable} -I -B code/solve.py",
        "isolated_mode": "pass",
        "execution_1_exit_code": first["exit_code"],
        "execution_2_exit_code": second["exit_code"],
        "execution_1_stdout_sha256": first["stdout_sha256"],
        "execution_2_stdout_sha256": second["stdout_sha256"],
        "execution_1_stderr_sha256": first["stderr_sha256"],
        "execution_2_stderr_sha256": second["stderr_sha256"],
        "execution_1_sha256": first["files"],
        "execution_2_sha256": second["files"],
        "comparison": "byte_identical" if same else "fail",
    }
    dump(RESULTS/"repeatability.json", repeatability)

    reproducibility_path = ROOT/"reproducibility.yaml"
    reproducibility = json.loads(reproducibility_path.read_text(encoding="utf-8"))
    reproducibility.update({
        "status": "pass" if same else "fail",
        "entrypoint": "code/build.py",
        "command": "python -I -B code/build.py",
        "solve_command": "python -I -B code/solve.py",
        "verification_command": "python -I -B code/verify.py",
        "repeatability_evidence": {
            "path": "results/repeatability.json",
            "status": repeatability["status"],
            "comparison": repeatability["comparison"],
        },
    })
    dump(reproducibility_path, reproducibility)

    verified = run([sys.executable, "-I", "-B", "code/verify.py"], ROOT)
    verification_path = RESULTS/"verification.json"
    verification = json.loads(verification_path.read_text(encoding="utf-8")) if verification_path.is_file() else {}
    reproducibility["verification_evidence"] = {
        "path": "results/verification.json",
        "status": verification.get("overall_status", "fail"),
        "exit_code": verified.returncode,
    }
    dump(reproducibility_path, reproducibility)

    manifest = json.loads((RESULTS/"manifest.json").read_text(encoding="utf-8"))
    authoritative = set(manifest["files"])
    authoritative.update({
        "problem-analysis.md", "data-audit.md", "assumptions.yaml", "variables.yaml",
        "model-selection.md", "solution-report.yaml", "reproducibility.yaml",
        "code/solve.py", "code/verify.py", "code/build.py", "code/run_all.ps1",
        "code/requirements.txt", "paper/main.tex", "paper/paper.md",
        "results/manifest.json", "results/repeatability.json", "results/verification.json",
    })
    file_records = {}
    for relative in sorted(authoritative):
        path = ROOT/relative
        if path.is_file():
            file_records[relative.replace("\\", "/")] = {
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
    build_manifest = {
        "case_id": "2014A",
        "phase": "blind-revision",
        "status": "needs_review",
        "status_reason": "internally reproducible but not externally anchored until the user-authorized freeze step",
        "canonicalization_version": 1,
        "tree_hash_algorithm": (
            "SHA-256 over UTF-8 lines path<TAB>size_bytes<TAB>sha256<LF>; "
            "paths use forward slashes and are sorted by Unicode code point"
        ),
        "files": file_records,
        "tree_sha256": canonical_tree(file_records),
    }
    dump(RESULTS/"build-manifest.json", build_manifest)

    print(json.dumps({
        "solve_status": "pass",
        "repeatability_status": repeatability["status"],
        "verification_status": verification.get("overall_status", "fail"),
        "build_manifest_status": build_manifest["status"],
    }, ensure_ascii=False, indent=2))
    return 0 if same and verified.returncode == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
