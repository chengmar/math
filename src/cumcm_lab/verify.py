from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .util import now_iso, read_yaml, safe_copy_tree, write_json


def artifact_root(case_dir: Path) -> Path:
    final = case_dir / "frozen" / "blind-final"
    if final.exists():
        return final
    first = case_dir / "frozen" / "blind-v1"
    if first.exists():
        return first
    return case_dir / "workspaces" / "solve"


def verify_case(case_dir: Path, *, report_path: Path | None = None) -> dict[str, Any]:
    source = artifact_root(case_dir)
    required = ["solution-report.yaml", "reproducibility.yaml", "code", "results", "paper"]
    missing = [name for name in required if not (source / name).exists()]
    run_script = source / "code" / "run.py"
    stdout = ""
    stderr = ""
    return_code: int | None = None
    seed_found = False
    output_generated = False
    if run_script.exists():
        code_text = run_script.read_text(encoding="utf-8-sig", errors="replace")
        seed_found = "SEED" in code_text or "random.seed" in code_text or "np.random.seed" in code_text
        with tempfile.TemporaryDirectory(prefix="cumcm-verify-") as temp_name:
            temp_root = Path(temp_name) / "case"
            temp_root.mkdir(parents=True)
            safe_copy_tree(source, temp_root)
            generated_results = temp_root / "results"
            if generated_results.exists():
                shutil.rmtree(generated_results)
            generated_results.mkdir()
            completed = subprocess.run(
                [sys.executable, "code/run.py"],
                cwd=temp_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            return_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            output_generated = any(path.is_file() for path in generated_results.rglob("*"))
    reproduction = read_yaml(source / "reproducibility.yaml", {})
    command_recorded = bool(reproduction.get("run_command"))
    status = "pass" if not missing and return_code == 0 and seed_found and command_recorded and output_generated else "fail"
    report = {
        "status": status,
        "checked_at": now_iso(),
        "source": str(source),
        "missing_required": missing,
        "run_script": str(run_script),
        "return_code": return_code,
        "stdout": stdout,
        "stderr": stderr,
        "random_seed_detected": seed_found,
        "run_command_recorded": command_recorded,
        "outputs_regenerated": output_generated,
        "claim_boundary": "pass 仅表示干净临时目录中成功重跑并生成输出，不表示数值或数学结论正确。",
    }
    if report_path:
        write_json(report_path, report)
    return report

