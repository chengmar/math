from __future__ import annotations

import pytest

from cumcm_lab.curator import CuratorPolicyError, validate_curator_request, validate_curator_result


def test_curator_only_accepts_medium_non_test_metadata():
    validate_curator_request({"file_id": "opaque-1", "classification_confidence": "medium", "detected_year": 2010, "split": "train"})
    with pytest.raises(CuratorPolicyError, match="2023"):
        validate_curator_request({"file_id": "opaque-2", "classification_confidence": "medium", "detected_year": 2023, "split": "test"})
    with pytest.raises(CuratorPolicyError, match="中置信度"):
        validate_curator_request({"file_id": "opaque-3", "classification_confidence": "high", "detected_year": 2010, "split": "train"})


def test_curator_cannot_emit_method_summary_or_change_file_id():
    base = {
        "file_id": "opaque-1",
        "decision": "requires_review",
        "detected_year": 2010,
        "problem_letter": "A",
        "document_type": "unknown_problem_file",
        "confidence": "medium",
        "requires_review": True,
        "reason_codes": ["insufficient_logistics_metadata"],
    }
    assert validate_curator_result(base, expected_file_id="opaque-1")["decision"] == "requires_review"
    with pytest.raises(CuratorPolicyError, match="越权字段"):
        validate_curator_result({**base, "method": "forbidden"}, expected_file_id="opaque-1")
    with pytest.raises(CuratorPolicyError, match="不一致"):
        validate_curator_result(base, expected_file_id="opaque-2")
