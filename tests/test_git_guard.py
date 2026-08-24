from __future__ import annotations

import subprocess

from cumcm_lab.git_guard import inspect_git_tree


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_git_guard_detects_disguised_binary_and_real_hash(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "safe.py").write_text("print('ok')\n", encoding="utf-8")
    hidden = tmp_path / "hidden.txt"
    hidden.write_bytes(b"%PDF-1.7 fake")
    _git(tmp_path, "add", "safe.py", "hidden.txt")
    report = inspect_git_tree(tmp_path, real_hashes=[])
    assert report["status"] == "fail"
    assert any(item["reason"] == "binary_magic:pdf" for item in report["findings"])


def test_git_guard_passes_text_only_tree(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / "safe.py").write_text("print('ok')\n", encoding="utf-8")
    _git(tmp_path, "add", "safe.py")
    assert inspect_git_tree(tmp_path)["status"] == "pass"
