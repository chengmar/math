import os
import shutil
from pathlib import Path

import pytest

from cumcm_lab.verify import _isolated_reproduction_arguments, _powershell_reproduction_command, _safe_workspace_invocation, verify_case


def test_powershell_reproduction_passes_declared_workspace() -> None:
    command = _powershell_reproduction_command(
        "pwsh",
        code_text="param([string]$Workspace)\nWrite-Output $Workspace\n",
        temp_root=Path("C:/isolated/case"),
    )

    assert command[-2:] == ["-Workspace", "C:\\isolated\\case"]


def test_powershell_reproduction_uses_script_root_without_unknown_parameter() -> None:
    command = _powershell_reproduction_command(
        "pwsh",
        code_text="$workspaceRoot = Resolve-Path (Join-Path $PSScriptRoot '..')\n",
        temp_root=Path("C:/isolated/case"),
    )

    assert command[-2:] != ["-Workspace", "C:\\isolated\\case"]
    assert command[-1] == "code/run_all.ps1"


def test_verify_accepts_recorded_command_list(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "commands:\n  - python code/run.py\nrandomness:\n  seed: 7\n",
        encoding="utf-8",
    )
    (source / "code" / "run.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["run_command_recorded"] is True


def test_verify_accepts_top_level_seed_and_full_pipeline_command(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "random_seed: 2006\nfull_pipeline:\n  command: python code/run.py\n  status: pass\n",
        encoding="utf-8",
    )
    (source / "code" / "run.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["random_seed_detected"] is True
    assert report["run_command_recorded"] is True


def test_verify_accepts_top_level_authoritative_command(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "authoritative_command: python code/run.py\n"
        "randomness:\n  seed: 2008\n",
        encoding="utf-8",
    )
    (source / "code" / "run.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["run_command_recorded"] is True
    assert report["run_scripts"] == [str(source / "code" / "run.py")]


def test_verify_accepts_named_full_numerical_run_list(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "random_seed: 2009\n"
        "commands:\n"
        "  full_numerical_run:\n"
        "    - python code/run.py\n",
        encoding="utf-8",
    )
    (source / "code" / "run.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["random_seed_detected"] is True
    assert report["run_scripts"] == [str(source / "code" / "run.py")]


def test_verify_accepts_primary_command_and_seed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "seed: 2009\nprimary_command: python code/run.py\n",
        encoding="utf-8",
    )
    (source / "code" / "run.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["random_seed_detected"] is True


def test_isolated_reproduction_arguments_redirects_declared_workspace(tmp_path: Path) -> None:
    arguments = ["-Workspace", "D:/original/case", "-VerifyPaper"]

    rewritten = _isolated_reproduction_arguments(tmp_path, arguments)

    assert rewritten == ["-Workspace", str(tmp_path), "-VerifyPaper"]
    assert arguments[1] == "D:/original/case"


def test_windows_backslash_powershell_entry_is_recognized(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "code").mkdir(parents=True)
    (source / "code" / "run_all.ps1").write_text("exit 0\n", encoding="utf-8")

    invocation = _safe_workspace_invocation(
        r"powershell -File code\run_all.ps1 -Workspace C:\original\case",
        source,
    )

    assert invocation is not None
    assert invocation[0] == "powershell"
    assert invocation[1].as_posix() == "code/run_all.ps1"


def test_powershell_call_operator_entry_is_recognized(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "code").mkdir(parents=True)
    (source / "code" / "run_all.ps1").write_text("exit 0\n", encoding="utf-8")

    invocation = _safe_workspace_invocation(
        r"& '.\code\run_all.ps1' -Workspace (Get-Location).Path",
        source,
    )

    assert invocation is not None
    assert invocation[0] == "powershell"
    assert invocation[1].as_posix() == "code/run_all.ps1"


@pytest.mark.skipif(os.name != "nt" or not (shutil.which("pwsh") or shutil.which("powershell")), reason="Windows PowerShell required")
def test_verify_accepts_one_numeric_run_with_call_operator(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "random_seed: 7\n"
        "commands:\n"
        "  one_numeric_run: \"& '.\\\\code\\\\run_all.ps1' -Workspace (Get-Location).Path\"\n",
        encoding="utf-8",
    )
    (source / "code" / "run_all.ps1").write_text(
        "param([string]$Workspace)\n"
        "New-Item -ItemType Directory -Force -Path (Join-Path $Workspace 'results') | Out-Null\n"
        "Set-Content -LiteralPath (Join-Path $Workspace 'results/out.txt') -Value 'ok'\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["entrypoint_kind"] == "powershell"
    assert report["random_seed_detected"] is True


def test_batch_entry_is_recognized(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "code").mkdir(parents=True)
    (source / "code" / "run_all.cmd").write_text("exit /b 0\n", encoding="utf-8")

    invocation = _safe_workspace_invocation(r"cmd /c code\run_all.cmd", source)

    assert invocation is not None
    assert invocation[0] == "batch"
    assert invocation[1].as_posix() == "code/run_all.cmd"


@pytest.mark.skipif(os.name != "nt" or not (shutil.which("pwsh") or shutil.which("powershell")), reason="Windows PowerShell required")
def test_verify_accepts_named_all_powershell_pipeline(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "random_seed: 7\ncommands:\n  all: powershell -File code\\run_all.ps1 -Workspace C:\\original\\case\n",
        encoding="utf-8",
    )
    (source / "code" / "run_all.ps1").write_text(
        "param([string]$Workspace)\nNew-Item -ItemType Directory -Force -Path (Join-Path $Workspace 'results') | Out-Null\nSet-Content -LiteralPath (Join-Path $Workspace 'results/out.txt') -Value 'ok'\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["entrypoint_kind"] == "powershell"
    assert report["command_source"] == "reproducibility.yaml"
    assert len(report["entrypoint_sha256"]) == 1
    assert report["command_results"][0]["elapsed_seconds"] >= 0


def test_verify_accepts_full_command_mapping(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "randomness:\n  seed: 2006\nfull_command:\n  command: python code/run.py\n",
        encoding="utf-8",
    )
    (source / "code" / "run.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["run_command_recorded"] is True


def test_verify_runs_structured_command_pipeline_with_named_python_entry(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "commands:\n"
        "  - purpose: generate outputs\n"
        "    command: python code/solve_population.py --workspace .\n"
        "randomness:\n"
        "  seed: 2007\n",
        encoding="utf-8",
    )
    (source / "code" / "solve_population.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["entrypoint_kind"] == "python"
    assert report["run_scripts"] == [str(source / "code" / "solve_population.py")]


def test_verify_rejects_recorded_entrypoint_outside_workspace_code(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "commands:\n  - command: python ../outside.py\nrandomness:\n  seed: 7\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "fail"
    assert report["run_script"] is None


def test_verify_prefers_portable_solve_and_independent_check_over_full_conversion(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "randomness:\n  seed: 2006\n"
        "solve:\n  command: python code/solve.py --workspace .\n"
        "independent_verification:\n  command: python code/verify.py --workspace .\n"
        "full_command:\n  command: powershell -File code/unportable.ps1\n",
        encoding="utf-8",
    )
    (source / "code" / "solve.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    (source / "code" / "verify.py").write_text(
        "from pathlib import Path\nassert Path('results/out.txt').read_text() == 'ok'\n",
        encoding="utf-8",
    )
    (source / "code" / "unportable.ps1").write_text("exit 1\n", encoding="utf-8")

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["run_scripts"] == [
        str(source / "code" / "solve.py"),
        str(source / "code" / "verify.py"),
    ]


def test_verify_can_continue_after_failed_preparatory_conversion(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "commands:\n"
        "  - command: python code/convert.py\n"
        "  - command: python code/solve.py\n"
        "  - command: python code/check.py\n"
        "randomness:\n  seed: 2007\n",
        encoding="utf-8",
    )
    (source / "code" / "convert.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    (source / "code" / "solve.py").write_text(
        "from pathlib import Path\nPath('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    (source / "code" / "check.py").write_text(
        "from pathlib import Path\nassert Path('results/out.txt').read_text() == 'ok'\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["failed_preparatory_commands"] == ["code/convert.py"]
    assert report["command_results"][-1]["return_code"] == 0


def test_verify_preserves_prior_reproducibility_evidence_while_rebuilding_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "commands:\n"
        "  - command: python code/solve.py\n"
        "  - command: python code/check.py\n"
        "randomness:\n  seed: 7\n",
        encoding="utf-8",
    )
    (source / "results" / "reproducibility_check.json").write_text(
        '{"judgment":"pass"}\n', encoding="utf-8"
    )
    (source / "results" / "stale.txt").write_text("remove me", encoding="utf-8")
    (source / "code" / "solve.py").write_text(
        "from pathlib import Path\n"
        "assert not Path('results/stale.txt').exists()\n"
        "Path('results/new.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    (source / "code" / "check.py").write_text(
        "from pathlib import Path\n"
        "assert Path('results/new.txt').read_text() == 'ok'\n"
        "assert Path('results/reproducibility_check.json').is_file()\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["preserved_validation_evidence"] == ["results/reproducibility_check.json"]


def test_verify_removes_only_explicit_fresh_target_from_copied_workspace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for directory in ("code", "results", "paper", "_work/fresh", "_work/keep"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "solution-report.yaml").write_text("status: pass\n", encoding="utf-8")
    (source / "reproducibility.yaml").write_text(
        "commands:\n"
        "  - command: python code/run.py --target _work/fresh\n"
        "randomness:\n  seed: 7\n",
        encoding="utf-8",
    )
    (source / "_work" / "fresh" / "stale.txt").write_text("stale", encoding="utf-8")
    (source / "_work" / "keep" / "evidence.txt").write_text("keep", encoding="utf-8")
    (source / "code" / "run.py").write_text(
        "import argparse\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--target', required=True)\n"
        "args = parser.parse_args()\n"
        "target = Path(args.target)\n"
        "assert not target.exists()\n"
        "assert Path('_work/keep/evidence.txt').read_text() == 'keep'\n"
        "target.mkdir(parents=True)\n"
        "Path('results/out.txt').write_text('ok')\n",
        encoding="utf-8",
    )

    report = verify_case(tmp_path, source_root=source)

    assert report["status"] == "pass"
    assert report["cleaned_declared_targets"] == ["_work/fresh"]
