import yaml

from cumcm_lab.compare import compare_runs
from cumcm_lab.regression import compare_score_reports


def score(a, b, c):
    return {"categories": {"result_reliability": {"score": a}, "method_quality": {"score": b}, "paper_quality": {"score": c}}}


def test_regression_detects_dimension_drop():
    report = compare_score_reports(score(45, 20, 20), score(45, 17, 20), tolerance=2)
    assert report["status"] == "fail"
    assert report["dimensions"]["method_quality"]["status"] == "fail"


def test_regression_does_not_hide_drop_with_total_gain():
    report = compare_score_reports(score(40, 24, 20), score(46, 20, 20), tolerance=2)
    assert report["status"] == "fail"


def test_single_ab_run_is_not_conclusive(tmp_path):
    input_path = tmp_path / "runs.yaml"
    input_path.write_text(
        yaml.safe_dump(
            {
                "runs": [
                    {
                        "case_id": "c1",
                        "variant": "baseline",
                        "codex_version": "test",
                        "model": "same-model",
                        "reasoning_setting": "same",
                        "time_budget": "1h",
                        "allowed_tools": [],
                        "run_id": "r1",
                        "scores": {"total": 80},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = compare_runs(input_path, tmp_path / "reports")
    assert report["status"] == "needs_review"

