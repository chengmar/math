from pathlib import Path

import pytest
import yaml

from cumcm_lab.autopilot import CodexPhaseExecutor
from cumcm_lab.cases import find_case, init_runtime_case
from cumcm_lab.util import find_trainer_root


def _configure_runtime(trainer: Path, lab_root: Path, vault_root: Path) -> tuple[Path, Path]:
    question_bank = vault_root / "question-bank"
    runtime_cases = lab_root / "runtime-cases"
    runtime_cases.mkdir()
    (lab_root / "local-paths.toml").write_text(
        "[paths]\n"
        f'runtime_cases = "{runtime_cases.as_posix()}"\n'
        f'question_bank = "{question_bank.as_posix()}"\n',
        encoding="utf-8",
    )
    return question_bank, runtime_cases


def test_runtime_case_is_external_and_uses_only_current_question(lab_factory) -> None:
    trainer, lab_root, vault_root = lab_factory()
    question_bank, runtime_cases = _configure_runtime(trainer, lab_root, vault_root)
    source = question_bank / "train" / "2003A"
    (source / "problem").mkdir(parents=True)
    (source / "data").mkdir()
    (source / "problem" / "file-problem.txt").write_text("dummy", encoding="utf-8")
    (source / "data" / "file-data.txt").write_text("1,2", encoding="utf-8")
    references = vault_root / "reference-vault" / "2003A"
    references.mkdir(parents=True)
    (references / "ref-opaque.pdf").write_bytes(b"dummy")

    case_dir = init_runtime_case(trainer, "2003A")

    assert case_dir == runtime_cases / "2003A"
    assert not case_dir.is_relative_to(trainer)
    assert (case_dir / "input" / "problem" / "file-problem.txt").exists()
    assert (case_dir / "input" / "data" / "file-data.txt").exists()
    assert yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))["reference_ids"] == []
    assert find_case(trainer, "2003A") == case_dir
    assert find_trainer_root(case_dir) == trainer


def test_runtime_case_hard_rejects_2023(lab_factory) -> None:
    trainer, lab_root, vault_root = lab_factory()
    _configure_runtime(trainer, lab_root, vault_root)
    with pytest.raises(ValueError, match="2023A"):
        init_runtime_case(trainer, "2023A")


def test_solve_preparation_initializes_missing_runtime_case(lab_factory) -> None:
    trainer, lab_root, vault_root = lab_factory()
    question_bank, runtime_cases = _configure_runtime(trainer, lab_root, vault_root)
    source = question_bank / "train" / "2006A"
    (source / "problem").mkdir(parents=True)
    (source / "problem" / "file-problem.txt").write_text("dummy", encoding="utf-8")

    executor = CodexPhaseExecutor(trainer, lab_root / "codex-home")
    case_dir, workspace, already_complete = executor._prepare_or_reuse("2006A", "solve")

    assert case_dir == runtime_cases / "2006A"
    assert already_complete is False
    assert (workspace / "input" / "problem" / "file-problem.txt").is_file()
    assert (workspace / "phase-lock.json").is_file()
