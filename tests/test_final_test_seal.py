from __future__ import annotations

import json

import pytest

from cumcm_lab.training_queue import (
    CONSUME_CONFIRMATION,
    FinalTestSealError,
    consume_final_test,
    read_final_test_seal,
    seal_final_test,
)


def records():
    return [
        {
            "file_id": "opaque-q-001",
            "sha256": "a" * 64,
            "size": 123,
            "original_path": "forbidden/local/path.pdf",
            "title": "must not persist",
        },
        {"file_id": "opaque-r-002", "sha256": "b" * 64, "size": 456},
    ]


def test_seal_contains_only_opaque_ids_hashes_and_counts(tmp_path):
    path = tmp_path / "final-test-seal.json"
    payload = seal_final_test(path, records())
    serialized = path.read_text(encoding="utf-8")
    assert payload["state"] == "sealed"
    assert payload["file_count"] == 2
    assert "original_path" not in serialized
    assert "must not persist" not in serialized
    assert set(payload["files"][0]) == {"file_id", "sha256", "size"}
    assert seal_final_test(path, records()) == payload
    assert read_final_test_seal(path)["manifest_sha256"] == payload["manifest_sha256"]


def test_existing_seal_cannot_be_overwritten_with_different_hash(tmp_path):
    path = tmp_path / "final-test-seal.json"
    seal_final_test(path, records())
    changed = records()
    changed[0]["sha256"] = "c" * 64
    with pytest.raises(FinalTestSealError, match="不一致"):
        seal_final_test(path, changed)


def test_consume_is_explicit_idempotent_and_one_way(tmp_path):
    path = tmp_path / "final-test-seal.json"
    seal_final_test(path, records())
    with pytest.raises(FinalTestSealError, match="确认"):
        consume_final_test(path, "yes")
    consumed = consume_final_test(path, CONSUME_CONFIRMATION)
    assert consumed["state"] == "consumed"
    assert consume_final_test(path, CONSUME_CONFIRMATION)["consumption_id"] == consumed["consumption_id"]
    with pytest.raises(FinalTestSealError, match="不能重新封存"):
        seal_final_test(path, records())


def test_seal_tampering_and_metadata_injection_are_detected(tmp_path):
    path = tmp_path / "final-test-seal.json"
    payload = seal_final_test(path, records())
    payload["files"][0]["title"] = "leaking title"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FinalTestSealError, match="禁止"):
        read_final_test_seal(path)
