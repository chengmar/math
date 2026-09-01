from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "runner" / "freeze_snapshot.py"
SPEC = importlib.util.spec_from_file_location("freeze_snapshot", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
freeze_snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(freeze_snapshot)


def test_freeze_and_verify_detects_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "B.txt").write_text("upper\n", encoding="utf-8")
    (source / "a.txt").write_text("lower\n", encoding="utf-8")
    destination = tmp_path / "frozen" / "snapshot"
    manifest_path = tmp_path / "frozen" / "manifest.json"

    manifest = freeze_snapshot.freeze(
        argparse.Namespace(
            runtime_root=str(tmp_path),
            source=str(source),
            destination=str(destination),
            manifest=str(manifest_path),
            label="test",
            evidence=[],
        )
    )

    assert "casefold" in str(manifest["tree_algorithm"])
    assert [item["path"] for item in manifest["files"]] == ["a.txt", "B.txt"]
    assert freeze_snapshot.verify(
        argparse.Namespace(runtime_root=str(tmp_path), manifest=str(manifest_path))
    )["status"] == "pass"

    (destination / "a.txt").write_text("tampered\n", encoding="utf-8")
    assert freeze_snapshot.verify(
        argparse.Namespace(runtime_root=str(tmp_path), manifest=str(manifest_path))
    )["status"] == "fail"


def test_manifest_is_valid_json(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("payload", encoding="utf-8")
    destination = tmp_path / "snapshot"
    manifest_path = tmp_path / "manifest.json"
    freeze_snapshot.freeze(
        argparse.Namespace(
            runtime_root=str(tmp_path),
            source=str(source),
            destination=str(destination),
            manifest=str(manifest_path),
            label="json-test",
            evidence=[],
        )
    )
    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert parsed["status"] == "frozen"
    assert parsed["file_count"] == 1
