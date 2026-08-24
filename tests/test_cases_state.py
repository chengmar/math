import pytest

from cumcm_lab.cases import find_case, init_case
from cumcm_lab.state import transition


def test_case_initialization(lab_factory):
    trainer, _, _ = lab_factory()
    case_dir = init_case(trainer, "dummy-a", "dummy", title="Dummy")
    assert case_dir.exists()
    assert (case_dir / "case-state.yaml").exists()
    assert find_case(trainer, "dummy-a") == case_dir


def test_case_initialization_refuses_overwrite(lab_factory):
    trainer, _, _ = lab_factory()
    init_case(trainer, "dummy-a", "dummy")
    with pytest.raises(FileExistsError):
        init_case(trainer, "dummy-a", "dummy")


def test_case_id_validation(lab_factory):
    trainer, _, _ = lab_factory()
    with pytest.raises(ValueError):
        init_case(trainer, "../bad", "train")


def test_illegal_state_transition_is_rejected(lab_factory):
    trainer, _, _ = lab_factory()
    case_dir = init_case(trainer, "dummy-a", "dummy")
    with pytest.raises(ValueError, match="非法状态迁移"):
        transition(case_dir, "reflected", command="test", reason="illegal")
