from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


MODULE_PATH = Path(__file__).parents[1] / "runner" / "verify_candidate.py"
SPEC = importlib.util.spec_from_file_location("verify_candidate", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
verify_candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_candidate)


def make_candidate(root: Path) -> None:
    (root / "code").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "data" / "value.txt").write_text("7\n", encoding="utf-8")
    (root / "code" / "generate.py").write_text(
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "(root / 'results').mkdir(exist_ok=True)\n"
        "value = int((root / 'data' / 'value.txt').read_text())\n"
        "(root / 'results' / 'out.json').write_text('{' + f'\\\"value\\\": {value}' + '}\\n')\n",
        encoding="utf-8",
    )
    contract = {
        "generate_all": "python -B code/generate.py",
        "declared_outputs": ["results/out.json"],
    }
    (root / "reproducibility.yaml").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    subprocess.run(
        [sys.executable, "-B", "code/generate.py"], cwd=root, check=True
    )


def run_verify(source: Path, scratch: Path, report: Path) -> dict[str, object]:
    return verify_candidate.verify(
        argparse.Namespace(
            source=str(source),
            scratch=str(scratch),
            report=str(report),
            python=sys.executable,
            contract="reproducibility.yaml",
            timeout_seconds=30,
        )
    )


def test_clean_source_only_reproduction_passes(tmp_path: Path) -> None:
    source = tmp_path / "candidate"
    make_candidate(source)
    report = run_verify(source, tmp_path / "scratch", tmp_path / "report.json")
    assert report["status"] == "pass"
    assert report["mismatched_hashes"] == []
    assert not (tmp_path / "scratch" / "results" / "out.json").samefile(
        source / "results" / "out.json"
    )


def test_undeclared_generated_file_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "candidate"
    make_candidate(source)
    (source / "results" / "stale.txt").write_text("stale", encoding="utf-8")
    report = run_verify(source, tmp_path / "scratch", tmp_path / "report.json")
    assert report["status"] == "fail"
    assert report["undeclared_files_under_generated_roots"] == ["results/stale.txt"]
