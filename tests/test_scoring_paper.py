from pathlib import Path

import yaml

from cumcm_lab.cases import init_case
from cumcm_lab.freeze import freeze_solution
from cumcm_lab.paper import lint_paper
from cumcm_lab.phases import prepare_phase
from cumcm_lab.scoring import _update_leaderboard, score_case


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


def test_leaderboard_writer_uses_lf_line_endings(tmp_path):
    path = tmp_path / "leaderboard.csv"
    report = {
        "case_id": "dummy-a",
        "total": 26.0,
        "status": "needs_review",
        "scored_at": "2026-08-23T00:00:00+08:00",
        "categories": {
            "result_reliability": {"score": 26.0},
            "method_quality": {"score": 0.0},
            "paper_quality": {"score": 0.0},
        },
    }

    _update_leaderboard(path, report)

    assert b"\r\n" not in path.read_bytes()


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


def test_paper_code_outputs_accept_powershell_entrypoint(tmp_path):
    paper = tmp_path / "paper.md"
    paper.write_text(
        "# 摘要\n采用均值模型得到 3.0。\n# 参考文献\n# 支撑材料说明\n",
        encoding="utf-8",
    )
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "solve.ps1").write_text("Write-Output 3", encoding="utf-8")
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "summary.json").write_text("{}", encoding="utf-8")

    report = lint_paper(paper, ROOT / "config" / "competition-rules.yaml", artifact_root=tmp_path)

    check = next(item for item in report["checks"] if item["id"] == "code_outputs")
    assert check["status"] == "pass"


def test_latex_combined_sections_and_thebibliography_are_recognized(tmp_path):
    paper = tmp_path / "main.tex"
    paper.write_text(
        "\\begin{abstract}均值模型得到 3.0。关键词：均值。\\end{abstract}\n"
        "\\section{问题重述}\\section{问题分析}\\section{模型假设与符号}\n"
        "\\subsection{主要假设}\\subsection{主要符号}\\section{数据处理}\n"
        "\\section{人流模型}\\subsection{人流结果}\\section{独立验证}\n"
        "\\section{验证与敏感性分析}\\section{模型评价}\\section{结论}\n"
        "\\begin{thebibliography}{9}\\end{thebibliography}\n"
        "\\appendix\\section{复现说明}\n",
        encoding="utf-8",
    )
    report = lint_paper(paper, ROOT / "config" / "competition-rules.yaml")
    assert report["status"] != "fail"


def test_latex_model_build_and_solve_with_bibliography_are_recognized(tmp_path):
    paper = tmp_path / "main.tex"
    paper.write_text(
        "\\begin{abstract}采用均值模型得到结果 3.0。关键词：均值。\\end{abstract}\n"
        "\\section{问题重述}\\section{问题分析}\\section{模型假设与符号}\n"
        "\\subsection{主要假设}\\subsection{主要符号}\\section{数据审计}\n"
        "\\section{模型建立与求解}\\section{模型验证与敏感性}\n"
        "\\section{模型评价}\\section{结论}\n"
        "\\bibliography{references}\n"
        "\\appendix\\section{复现说明}\n",
        encoding="utf-8",
    )
    report = lint_paper(paper, ROOT / "config" / "competition-rules.yaml")
    required = next(item for item in report["checks"] if item["id"] == "required_sections")
    assert required["status"] == "pass"


def test_supporting_materials_and_reproduction_heading_is_recognized(tmp_path):
    paper = tmp_path / "main.tex"
    paper.write_text(
        "\\begin{abstract}采用均值模型得到结果 3.0。关键词：均值。\\end{abstract}\n"
        "\\section{问题重述}\\section{问题分析}\\section{模型假设}\\section{符号说明}\n"
        "\\section{数据处理}\\section{模型建立}\\section{模型求解}\\section{模型验证}\n"
        "\\section{敏感性分析}\\section{模型评价}\\section{结论}\n"
        "\\section{参考文献}\\appendix\\section{支撑材料与复现}\n",
        encoding="utf-8",
    )
    report = lint_paper(paper, ROOT / "config" / "competition-rules.yaml")
    required = next(item for item in report["checks"] if item["id"] == "required_sections")
    assert required["status"] == "pass"


def test_solution_results_and_robustness_headings_are_recognized(tmp_path):
    paper = tmp_path / "main.tex"
    paper.write_text(
        "\\begin{abstract}采用优化模型得到结果 3.0。关键词：优化。\\end{abstract}\n"
        "\\section{问题重述}\\section{问题分析与数据审计}\n"
        "\\section{模型假设与符号}\\subsection{主要假设}\\subsection{主要符号}\n"
        "\\section{模型建立与候选比较}\\section{求解结果}\n"
        "\\section{验证、敏感性与稳健性}\\section{模型评价、建议与结论}\n"
        "\\begin{thebibliography}{9}\\end{thebibliography}\n"
        "\\appendix\\section{复现说明}\n",
        encoding="utf-8",
    )
    report = lint_paper(paper, ROOT / "config" / "competition-rules.yaml")
    required = next(item for item in report["checks"] if item["id"] == "required_sections")
    assert required["status"] == "pass"
