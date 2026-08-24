from pathlib import Path

import pytest

from cumcm_lab.cases import init_case
from cumcm_lab.leakage import check_leakage
from cumcm_lab.phases import prepare_phase
from cumcm_lab.util import read_yaml, write_yaml


def test_phase_manifest_tampering_is_detected(lab_factory) -> None:
    trainer, _, vault_root = lab_factory()
    case_dir = init_case(trainer, "lock-test", "dummy")
    (case_dir / "input" / "problem" / "problem.txt").write_text("dummy", encoding="utf-8")
    workspace = prepare_phase(trainer, "lock-test", "solve")
    (workspace / "allowed-paths.json").write_text('{"phase":"solve","paths":[]}', encoding="utf-8")

    report = check_leakage(
        workspace,
        "solve",
        vault_roots=[vault_root / "reference-vault", vault_root / "exam-vault"],
    )

    assert report["status"] == "fail"
    assert any(item["category"] == "phase_manifest_tampered" for item in report["findings"])


def test_reflection_rejects_cross_year_reference_before_copy(lab_factory, monkeypatch) -> None:
    trainer, _, vault_root = lab_factory()
    case_dir = init_case(trainer, "2010A", "dummy")
    (case_dir / "frozen" / "blind-final" / "result.txt").parent.mkdir(parents=True, exist_ok=True)
    (case_dir / "frozen" / "blind-final" / "result.txt").write_text("dummy", encoding="utf-8")
    write_yaml(case_dir / "case-state.yaml", {"state": "blind_final_frozen", "history": []})
    case_meta = read_yaml(case_dir / "case.yaml")
    case_meta["status"] = "blind_final_frozen"
    case_meta["reference_ids"] = ["2011A/ref-opaque.pdf"]
    write_yaml(case_dir / "case.yaml", case_meta)
    wrong_reference = vault_root / "reference-vault" / "2011A" / "ref-opaque.pdf"
    wrong_reference.parent.mkdir(parents=True)
    wrong_reference.write_bytes(b"dummy")
    monkeypatch.setattr("cumcm_lab.phases.verify_frozen", lambda *_args, **_kwargs: {"status": "pass"})

    with pytest.raises(ValueError, match="当前案例目录 2010A"):
        prepare_phase(trainer, "2010A", "reflection")

    reflection = case_dir / "workspaces" / "reflection"
    assert not (reflection / "approved-references").exists()
    assert not (reflection / "blind-final").exists()
