from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .util import now_iso, read_yaml, safe_copy_tree, write_json


def _recorded_reproduction_commands(reproduction: dict[str, Any]) -> list[str]:
    """Return the declared reproduction pipeline without accepting shell syntax.

    A single top-level full pipeline takes precedence.  Otherwise a structured
    ``commands`` list is treated as an ordered pipeline.  Parsing and path
    confinement are handled separately by ``_safe_workspace_invocation``.
    """

    for command_key in ("run_command", "primary_command"):
        run_command = reproduction.get(command_key)
        if isinstance(run_command, str) and run_command.strip():
            return [run_command.strip()]
    authoritative_command = reproduction.get("authoritative_command")
    if isinstance(authoritative_command, str) and authoritative_command.strip():
        return [authoritative_command.strip()]
    solve = reproduction.get("solve")
    if isinstance(solve, dict):
        solve_command = solve.get("command")
        if isinstance(solve_command, str) and solve_command.strip():
            commands = [solve_command.strip()]
            verification = reproduction.get("independent_verification")
            if isinstance(verification, dict):
                verification_command = verification.get("command")
                if isinstance(verification_command, str) and verification_command.strip():
                    commands.append(verification_command.strip())
            return commands
    for key in ("full_pipeline", "full_command"):
        value = reproduction.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, dict):
            command = value.get("command")
            if isinstance(command, str) and command.strip():
                return [command.strip()]
    raw_commands = reproduction.get("commands")
    if isinstance(raw_commands, dict):
        # Several completed cases use a named aggregate pipeline rather than
        # the literal ``full_pipeline`` key.  Accept only a small allow-list of
        # aggregate names and keep the same downstream path-confinement rules.
        for key in (
            "full_pipeline",
            "full_command",
            "full_numerical_run",
            "authoritative_command",
            "run_command",
        ):
            value = raw_commands.get(key)
            if isinstance(value, str) and value.strip():
                return [value.strip()]
            if isinstance(value, dict):
                command = value.get("command")
                if isinstance(command, str) and command.strip():
                    return [command.strip()]
            if isinstance(value, list):
                commands = [
                    item.strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                ]
                if commands:
                    return commands
        return []
    if isinstance(raw_commands, list):
        commands: list[str] = []
        for item in raw_commands:
            if isinstance(item, str) and item.strip():
                commands.append(item.strip())
            elif isinstance(item, dict):
                command = item.get("command")
                if isinstance(command, str) and command.strip():
                    commands.append(command.strip())
        return commands
    return []


def _safe_workspace_invocation(command_text: str, source: Path) -> tuple[str, Path, list[str]] | None:
    """Parse a recorded Python/PowerShell command confined to ``source/code``."""

    try:
        tokens = shlex.split(command_text, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    executable = Path(tokens[0]).name.casefold()
    kind: str
    script_token: str
    arguments: list[str]
    if executable in {"python", "python.exe", "py", "py.exe"}:
        script_index = 1
        while script_index < len(tokens) and tokens[script_index].startswith("-"):
            script_index += 1
        if script_index >= len(tokens):
            return None
        kind = "python"
        script_token = tokens[script_index]
        arguments = tokens[script_index + 1 :]
    elif executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}:
        try:
            file_index = next(index for index, token in enumerate(tokens) if token.casefold() == "-file")
        except StopIteration:
            return None
        if file_index + 1 >= len(tokens):
            return None
        kind = "powershell"
        script_token = tokens[file_index + 1]
        arguments = tokens[file_index + 2 :]
    else:
        return None
    relative = Path(script_token.replace("\\", "/"))
    if relative.is_absolute() or not relative.parts or relative.parts[0].casefold() != "code":
        return None
    if any(part in {"", ".", ".."} for part in relative.parts):
        return None
    expected_suffix = ".py" if kind == "python" else ".ps1"
    if relative.suffix.casefold() != expected_suffix:
        return None
    script = source.joinpath(*relative.parts)
    try:
        script.resolve(strict=False).relative_to((source / "code").resolve(strict=False))
    except ValueError:
        return None
    if not script.is_file():
        return None
    return kind, relative, arguments


def _powershell_reproduction_command(
    shell: str,
    *,
    code_text: str,
    temp_root: Path,
) -> list[str]:
    command = [
        shell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "code/run_all.ps1",
    ]
    # The verifier already executes a copied script from the isolated temporary
    # workspace.  Pass -Workspace only when the script explicitly declares or
    # references that exact parameter; many valid entries locate their root via
    # $PSScriptRoot and reject unknown named parameters.
    if re.search(r"(?i)\$Workspace\b", code_text):
        command.extend(["-Workspace", str(temp_root)])
    return command


def _clean_declared_reproduction_targets(
    temp_root: Path,
    invocations: list[tuple[str, Path, list[str]]],
) -> list[str]:
    """Remove copied targets that a recorded command declares must be fresh.

    Model workspaces can retain evidence from an earlier successful isolated run.
    Copying that evidence into the verifier's temporary workspace would make a
    correctly defensive ``-Target``/``--target`` command fail before it starts.
    Only explicit, relative, workspace-confined targets are removed.
    """

    cleaned: list[str] = []
    for _, _, arguments in invocations:
        for index, argument in enumerate(arguments[:-1]):
            if argument.casefold() not in {"-target", "--target"}:
                continue
            relative = Path(arguments[index + 1].replace("\\", "/"))
            if relative.is_absolute() or not relative.parts or any(
                part in {"", ".", ".."} for part in relative.parts
            ):
                continue
            target = temp_root.joinpath(*relative.parts)
            try:
                target.resolve(strict=False).relative_to(temp_root.resolve(strict=False))
            except ValueError:
                continue
            if target.is_dir():
                shutil.rmtree(target)
                cleaned.append(relative.as_posix())
            elif target.exists():
                target.unlink()
                cleaned.append(relative.as_posix())
    return cleaned


def _isolated_reproduction_arguments(temp_root: Path, arguments: list[str]) -> list[str]:
    """Redirect an explicitly declared workspace argument to the temp copy."""

    isolated = list(arguments)
    for index, argument in enumerate(isolated[:-1]):
        if argument.casefold() in {"-workspace", "--workspace"}:
            isolated[index + 1] = str(temp_root)
    return isolated


def artifact_root(case_dir: Path) -> Path:
    final = case_dir / "frozen" / "blind-final"
    if final.exists():
        return final
    first = case_dir / "frozen" / "blind-v1"
    if first.exists():
        return first
    return case_dir / "workspaces" / "solve"


def verify_case(
    case_dir: Path,
    *,
    source_root: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    source = Path(source_root) if source_root is not None else artifact_root(case_dir)
    required = ["solution-report.yaml", "reproducibility.yaml", "code", "results", "paper"]
    missing = [name for name in required if not (source / name).exists()]
    stdout = ""
    stderr = ""
    return_code: int | None = None
    command_results: list[dict[str, Any]] = []
    cleaned_declared_targets: list[str] = []
    seed_found = False
    output_generated = False
    preserved_validation_evidence: list[str] = []
    reproduction = read_yaml(source / "reproducibility.yaml", {})
    recorded_commands = _recorded_reproduction_commands(reproduction)
    invocations = [
        invocation
        for command_text in recorded_commands
        if (invocation := _safe_workspace_invocation(command_text, source)) is not None
    ]
    command_recorded = bool(recorded_commands)
    randomness = reproduction.get("randomness") if isinstance(reproduction.get("randomness"), dict) else {}
    if invocations:
        code_text = "\n".join(
            (source.joinpath(*relative.parts)).read_text(encoding="utf-8-sig", errors="replace")
            for _, relative, _ in invocations
        )
        seed_found = (
            reproduction.get("random_seed") is not None
            or reproduction.get("seed") is not None
            or randomness.get("seed") is not None
            or "SEED" in code_text
            or "random.seed" in code_text
            or "np.random.seed" in code_text
        )
        with tempfile.TemporaryDirectory(prefix="cumcm-verify-") as temp_name:
            temp_root = Path(temp_name) / "case"
            temp_root.mkdir(parents=True)
            safe_copy_tree(source, temp_root)
            cleaned_declared_targets = _clean_declared_reproduction_targets(temp_root, invocations)
            generated_results = temp_root / "results"
            if generated_results.exists():
                shutil.rmtree(generated_results)
            generated_results.mkdir()
            for evidence_name in ("reproducibility_check.json",):
                evidence_source = source / "results" / evidence_name
                if evidence_source.is_file():
                    shutil.copy2(evidence_source, generated_results / evidence_name)
                    preserved_validation_evidence.append(f"results/{evidence_name}")
            return_code = 0
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            for kind, relative, arguments in invocations:
                isolated_arguments = _isolated_reproduction_arguments(temp_root, arguments)
                if kind == "python":
                    command = [sys.executable, relative.as_posix(), *isolated_arguments]
                else:
                    shell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
                    command = [
                        shell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        relative.as_posix(),
                        *isolated_arguments,
                    ]
                completed = subprocess.run(
                    command,
                    cwd=temp_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                    check=False,
                )
                stdout_parts.append(completed.stdout)
                stderr_parts.append(completed.stderr)
                return_code = completed.returncode
                command_results.append(
                    {
                        "script": relative.as_posix(),
                        "kind": kind,
                        "return_code": completed.returncode,
                    }
                )
            stdout = "\n".join(stdout_parts)
            stderr = "\n".join(stderr_parts)
            output_generated = any(path.is_file() for path in generated_results.rglob("*"))
    status = "pass" if not missing and return_code == 0 and seed_found and command_recorded and output_generated else "fail"
    report = {
        "status": status,
        "checked_at": now_iso(),
        "source": str(source),
        "missing_required": missing,
        "run_script": str(source.joinpath(*invocations[0][1].parts)) if invocations else None,
        "run_scripts": [str(source.joinpath(*relative.parts)) for _, relative, _ in invocations],
        "entrypoint_kind": invocations[0][0] if invocations else None,
        "return_code": return_code,
        "command_results": command_results,
        "cleaned_declared_targets": cleaned_declared_targets,
        "failed_preparatory_commands": [
            item["script"] for item in command_results[:-1] if item["return_code"] != 0
        ],
        "stdout": stdout,
        "stderr": stderr,
        "random_seed_detected": seed_found,
        "run_command_recorded": command_recorded,
        "outputs_regenerated": output_generated,
        "preserved_validation_evidence": preserved_validation_evidence,
        "claim_boundary": "pass 仅表示干净临时目录中成功重跑并生成输出，不表示数值或数学结论正确。",
    }
    if report_path:
        write_json(report_path, report)
    return report
