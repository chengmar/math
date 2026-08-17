from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cumcm_lab.shadow import ShadowPolicyError, run_shadow_evaluation


def _case(tmp_path: Path) -> Path:
    case = tmp_path / "2010A"
    (case / "input" / "problem").mkdir(parents=True)
    (case / "input" / "problem" / "opaque.txt").write_text("dummy problem", encoding="utf-8")
    (case / "frozen" / "blind-final").mkdir(parents=True)
    (case / "frozen" / "blind-final" / "answer.txt").write_text("frozen", encoding="utf-8")
    return case


def _candidate(tmp_path: Path, card_id: str, year: int = 2009) -> Path:
    path = tmp_path / f"{card_id}.yaml"
    path.write_text(yaml.safe_dump({"id": card_id, "status": "candidate", "origin_year": year}), encoding="utf-8")
    return path


def test_shadow_copies_only_current_input_and_at_most_three_candidates(tmp_path):
    case = _case(tmp_path)
    candidates = [_candidate(tmp_path, f"card-{index}") for index in range(3)]
    result = run_shadow_evaluation(case, candidates, tmp_path / "shadow", lambda workspace, cards: {"metric": 1})
    workspace = Path(result["workspace"])
    assert result["blind_final_sha256_before"] == result["blind_final_sha256_after"]
    assert not (workspace / "blind-final").exists()
    assert not (workspace / "references").exists()
    assert len(list((workspace / "candidates").iterdir())) == 3
    with pytest.raises(ShadowPolicyError, match="1—3"):
        run_shadow_evaluation(case, [*candidates, _candidate(tmp_path, "card-4")], tmp_path / "shadow2", lambda *_: None)


def test_shadow_detects_frozen_mutation_and_rejects_current_year_candidate(tmp_path):
    case = _case(tmp_path)
    with pytest.raises(ShadowPolicyError, match="更早年份"):
        run_shadow_evaluation(case, [_candidate(tmp_path, "late", 2010)], tmp_path / "shadow", lambda *_: None)

    def corrupt(_workspace, _cards):
        (case / "frozen" / "blind-final" / "answer.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(ShadowPolicyError, match="修改了 blind-final"):
        run_shadow_evaluation(case, [_candidate(tmp_path, "early")], tmp_path / "shadow2", corrupt)
