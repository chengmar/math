from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from cumcm_lab.session_runner import FORBIDDEN_CODEX_ARGS, build_codex_exec_command, run_stage_session


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_command_is_new_ephemeral_session_with_fixed_safe_flags(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    final = tmp_path / "run" / "final-message.md"
    command = build_codex_exec_command(
        executable="fake-codex",
        workspace=workspace,
        model="gpt-5.5",
        reasoning_effort="xhigh",
        final_message_path=final,
    )

    assert command == [
        "fake-codex",
        "exec",
        "-C",
        str(workspace.resolve()),
        "-m",
        "gpt-5.5",
        "-c",
        'model_reasoning_effort="xhigh"',
        "-c",
        'approval_policy="never"',
        "-s",
        "workspace-write",
        "--json",
        "-o",
        str(final.resolve()),
        "--skip-git-repo-check",
        "--ephemeral",
        "-",
    ]
    assert not FORBIDDEN_CODEX_ARGS.intersection(command)


def test_missing_codex_home_auth_is_blocked_without_starting_process(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def must_not_run(*args, **kwargs):  # pragma: no cover - failure message is the assertion
        raise AssertionError("runner must not be called without CODEX_HOME auth")

    result = run_stage_session(
        workspace=workspace,
        run_root=tmp_path / "runs",
        prompt="safe dummy prompt",
        codex_home=tmp_path / "codex-home",
        model="gpt-5.5",
        runner=must_not_run,
    )

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "codex_home_auth_missing"
    run_dir = Path(result["run_dir"])
    metadata = _read_json(run_dir / "run-metadata.json")
    assert metadata["status"] == "blocked"
    assert metadata["process_started"] is False
    assert metadata["auth_file_present"] is False
    assert not (run_dir / "final-message.md").exists()
    assert (run_dir / "events.jsonl").read_text(encoding="utf-8") == ""
    assert (run_dir / "stderr.log").read_text(encoding="utf-8") == ""
    assert _read_json(run_dir / "output-manifest.json")["status"] == "blocked"


def test_fake_runner_receives_stdin_and_writes_auditable_outputs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("fake-auth-present", encoding="utf-8")
    input_file = tmp_path / "allowed-input.txt"
    input_file.write_text("dummy input only", encoding="utf-8")
    prompt = "perform one dummy stage"
    calls: list[dict] = []

    def fake_runner(command, **kwargs):
        calls.append({"command": command, **kwargs})
        kwargs["stdout"].write('{"type":"done"}\n')
        kwargs["stderr"].write("fake diagnostic\n")
        final_path = Path(command[command.index("-o") + 1])
        final_path.write_text("fake final\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    result = run_stage_session(
        workspace=workspace,
        run_root=tmp_path / "runs",
        prompt=prompt,
        codex_home=codex_home,
        model="gpt-5.5",
        reasoning_effort="high",
        input_files=[input_file],
        executable="fake-codex",
        runner=fake_runner,
        base_env={"SAFE_TEST_ENV": "1"},
    )

    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["input"] == prompt
    assert call["cwd"] == str(workspace.resolve())
    assert call["env"] == {"SAFE_TEST_ENV": "1", "CODEX_HOME": str(codex_home.resolve())}
    assert call["shell"] is False
    assert call["check"] is False
    assert call["command"][-1] == "-"
    assert not FORBIDDEN_CODEX_ARGS.intersection(call["command"])

    run_dir = Path(result["run_dir"])
    expected_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert (run_dir / "prompt-hash.txt").read_text(encoding="utf-8").strip() == expected_hash
    input_manifest = _read_json(run_dir / "input-manifest.json")
    assert input_manifest["prompt_sha256"] == expected_hash
    assert input_manifest["files"][0]["sha256"] == hashlib.sha256(input_file.read_bytes()).hexdigest()
    assert (run_dir / "events.jsonl").read_text(encoding="utf-8") == '{"type":"done"}\n'
    assert (run_dir / "stderr.log").read_text(encoding="utf-8") == "fake diagnostic\n"
    assert (run_dir / "final-message.md").read_text(encoding="utf-8") == "fake final\n"
    metadata = _read_json(run_dir / "run-metadata.json")
    assert metadata["status"] == "completed"
    assert metadata["process_started"] is True
    assert metadata["reasoning_effort"] == "high"
    outputs = _read_json(run_dir / "output-manifest.json")
    assert outputs["status"] == "completed"
    assert all(item["exists"] for item in outputs["files"])


def test_each_attempt_gets_a_unique_session_run_id(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    kwargs = {
        "workspace": workspace,
        "run_root": tmp_path / "runs",
        "prompt": "dummy",
        "codex_home": tmp_path / "missing-auth-home",
        "model": "gpt-5.5",
    }
    first = run_stage_session(**kwargs)
    second = run_stage_session(**kwargs)
    assert first["session_run_id"] != second["session_run_id"]
    assert Path(first["run_dir"]).is_dir()
    assert Path(second["run_dir"]).is_dir()
