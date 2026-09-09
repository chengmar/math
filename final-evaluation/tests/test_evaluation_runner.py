from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "runner" / "evaluation_runner.py"
SPEC = importlib.util.spec_from_file_location("evaluation_runner", MODULE)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class NoopSupervisor:
    def attach_child(self, pid: int, events_path: Path, final_message_path: Path) -> None:
        self.pid = pid

    def update(self, *, status: str, recovery_classification: str) -> None:
        pass


def test_command_is_exact_workspace_write_contract(tmp_path: Path) -> None:
    command = runner.build_command("codex.exe", tmp_path, tmp_path / "final.txt")
    assert "-s" not in command
    assert "--ephemeral" in command
    assert "--strict-config" in command
    assert "--ignore-rules" in command
    assert "--ignore-user-config" not in command
    assert "--add-dir" not in command
    assert not any("bypass" in item for item in command)
    assert not any("danger" in item for item in command)
    assert command[command.index("-m") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="max"' in command
    assert 'default_permissions="evaluation-workspace-write"' in command


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


def test_terminal_supervisor_reaps_only_completed_child_tree(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    stderr = tmp_path / "stderr.txt"
    final = tmp_path / "final-message.txt"
    child = (
        "import json,pathlib,time; "
        f"p=pathlib.Path({str(final)!r}); "
        "print(json.dumps({'type':'thread.started','thread_id':'thread-1'}),flush=True); "
        "p.write_text('done\\n',encoding='utf-8'); "
        "print(json.dumps({'type':'turn.completed','usage':{}}),flush=True); "
        "time.sleep(60)"
    )
    outcome = runner.run_codex_process(
        [sys.executable, "-c", child],
        prompt_text="synthetic",
        workspace=tmp_path,
        environment=dict(os.environ),
        events_path=events,
        stderr_path=stderr,
        final_message_path=final,
        timeout_seconds=10,
        terminal_grace_seconds=0.1,
        supervisor=NoopSupervisor(),
    )
    assert outcome["terminal_recovered"] is True
    assert outcome["timed_out"] is False
    assert outcome["event_summary"]["turn_completed"] == 1
    assert outcome["termination"]["pid"] == outcome["pid"]


def test_terminal_supervisor_classifies_real_timeout(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    stderr = tmp_path / "stderr.txt"
    final = tmp_path / "final-message.txt"
    child = "import time; time.sleep(60)"
    outcome = runner.run_codex_process(
        [sys.executable, "-c", child],
        prompt_text="synthetic",
        workspace=tmp_path,
        environment=dict(os.environ),
        events_path=events,
        stderr_path=stderr,
        final_message_path=final,
        timeout_seconds=0.1,
        terminal_grace_seconds=0.1,
        supervisor=NoopSupervisor(),
    )
    assert outcome["terminal_recovered"] is False
    assert outcome["timed_out"] is True


def test_prepare_environment_prepends_fixed_python(tmp_path: Path) -> None:
    tools = {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_directory": str(Path(sys.executable).resolve().parent),
        "xelatex_executable": None,
        "xelatex_directory": None,
    }
    environment = runner.prepare_environment(tmp_path, tools)
    assert environment["CUMCM_PYTHON_EXECUTABLE"] == tools["python_executable"]
    assert environment["PATH"].split(os.pathsep)[0] == tools["python_directory"]
