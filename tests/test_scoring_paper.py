from pathlib import Path

import yaml

from cumcm_lab.cases import init_case
from cumcm_lab.freeze import freeze_solution
from cumcm_lab.paper import lint_paper
from cumcm_lab.phases import prepare_phase
from cumcm_lab.scoring import score_case


ROOT = Path(__file__).resolve().parents[1]


def test_scoring_weights_sum_to_100():
    rubric = yaml.safe_load((ROOT / "config" / "scoring-rubric.yaml").read_text(encoding="utf-8"))
    category_weights = sum(category["weight"] for category in rubric["categories"].values())
    criterion_weights = sum(item["weight"] for category in rubric["categories"].values() for item in category["criteria"].values())
    assert rubric["total"] == category_weights == criterion_weights == 100


def test_automatic_scoring_does_not_fill_human_items(lab_factory, solution_writer):
    trainer, _, _ = lab_factory()
    case_dir = init_case(trainer, "dummy-a", "dummy")
    (case_dir / "input" / "problem" / "problem.md").write_text("dummy", encoding="utf-8")
    workspace = prepare_phase(trainer, "dummy-a", "solve")
    solution_writer(workspace)
    freeze_solution(case_dir, "blind-v1")
    report = score_case(case_dir, trainer)
    assert report["status"] == "needs_review"
    assert report["human_review_needed"]
    assert report["total"] <= 46


def test_paper_placeholder_detection(tmp_path):
    paper = tmp_path / "paper.md"
    paper.write_text("# 摘要\n使用均值模型得到 3.0。\nTODO\n参考文献\n支撑材料说明\n", encoding="utf-8")
    report = lint_paper(paper, ROOT / "config" / "competition-rules.yaml")
    check = next(item for item in report["checks"] if item["id"] == "placeholders")
    assert check["status"] == "fail"


def test_paper_identity_detection(tmp_path):
    paper = tmp_path / "paper.md"
    paper.write_text("# 摘要\n均值模型得到 3.0。\n姓名：某同学\n参考文献\n支撑材料说明\n", encoding="utf-8")
    report = lint_paper(paper, ROOT / "config" / "competition-rules.yaml")
    check = next(item for item in report["checks"] if item["id"] == "identity")
    assert check["status"] == "fail"

