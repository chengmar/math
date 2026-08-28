from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "finalize_training_snapshot.py"
SPEC = importlib.util.spec_from_file_location("finalize_training_snapshot", MODULE_PATH)
assert SPEC and SPEC.loader
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


def test_tree_sha256_is_stable_and_path_sensitive(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "a.yaml").write_text("status: provisional_training\n", encoding="utf-8")
    first = snapshot.tree_sha256(root)
    assert first == snapshot.tree_sha256(root)

    (root / "b.yaml").write_text("status: provisional_training\n", encoding="utf-8")
    assert snapshot.tree_sha256(root) != first


def test_summarize_usage_records_decisions_and_years(tmp_path: Path) -> None:
    case = tmp_path / "2004A" / "workspaces" / "solve"
    case.mkdir(parents=True)
    (case / "retrieval-log.json").write_text(
        json.dumps({"cards": [{"id": "TM-001", "decision": "adapt"}]}),
        encoding="utf-8",
    )

    result = snapshot.summarize_usage(tmp_path)

    assert result["decision_counts"]["adapt"] == 1
    assert result["decision_counts"]["adopt"] == 0
    assert result["usage_years_by_card"] == {"TM-001": ["2004A"]}


def test_final_source_field_names_describe_pre_finalization_state() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "git_worktree_dirty_before_finalization" in source
    assert '"git_worktree_dirty"' not in source
