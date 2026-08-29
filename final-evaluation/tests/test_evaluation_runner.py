from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "runner" / "evaluation_runner.py"
SPEC = importlib.util.spec_from_file_location("evaluation_runner", MODULE)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_command_is_exact_workspace_write_contract(tmp_path: Path) -> None:
    command = runner.build_command("codex.exe", tmp_path, tmp_path / "final.txt")
    assert command[command.index("-s") + 1] == "workspace-write"
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--add-dir" not in command
    assert not any("bypass" in item for item in command)
    assert command[command.index("-m") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="max"' in command


def test_confined_workspace_rejects_runtime_root(tmp_path: Path) -> None:
    try:
        runner.confined_directory(tmp_path, tmp_path)
    except runner.EvaluationRunnerError:
        pass
    else:
        raise AssertionError("runtime root must not be accepted as an arm workspace")


def test_confined_file_rejects_outside_prompt(tmp_path: Path) -> None:
    arm = tmp_path / "arm"
    arm.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    try:
        runner.confined_file(outside, arm, "prompt")
    except runner.EvaluationRunnerError:
        pass
    else:
        raise AssertionError("outside prompt must be rejected")

