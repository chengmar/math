from __future__ import annotations

from cumcm_lab.security_sentinels import inspect_stage_workspace, sentinel_report


def test_physical_workspace_isolation_and_unexecuted_probe_are_distinguished(tmp_path):
    workspace = tmp_path / "workspaces" / "solve"
    (workspace / "input").mkdir(parents=True)
    (workspace / "input" / "opaque.txt").write_text("dummy", encoding="utf-8")
    probe = inspect_stage_workspace(workspace, phase="solve", case_id="2003A")
    assert probe["status"] == "pass"
    report = sentinel_report([probe], codex_probe_executed=False)
    assert report["status"] == "needs_review"
    assert report["codex_absolute_path_denial"]["status"] == "needs_review"


def test_reference_copy_in_solve_is_a_failure(tmp_path):
    workspace = tmp_path / "solve"
    (workspace / "approved-references").mkdir(parents=True)
    (workspace / "approved-references" / "opaque.txt").write_text("dummy", encoding="utf-8")
    assert inspect_stage_workspace(workspace, phase="solve", case_id="2003A")["status"] == "fail"
