from __future__ import annotations

from pathlib import Path

from cumcm_lab.paper import lint_paper


ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "config" / "competition-rules.yaml"


def _complete_latex(*, model_build: str = "模型建立", model_solve: str = "模型求解") -> str:
    return (
        "\\begin{abstract}采用均值模型得到结果 3.0。关键词：均值。\\end{abstract}\n"
        "\\section{问题重述}\n"
        "\\section{问题分析}\n"
        "\\section{模型假设}\n"
        "\\section{符号说明}\n"
        "\\section{数据处理}\n"
        f"\\section{{{model_build}}}\n"
        f"\\section{{{model_solve}}}\n"
        "\\section{模型验证}\n"
        "\\section{敏感性分析}\n"
        "\\section{模型评价}\n"
        "\\section{结论}\n"
        "\\begin{thebibliography}{9}\\end{thebibliography}\n"
        "\\appendix\n"
        "\\section{支撑材料说明}\n"
    )


def _check(report: dict, check_id: str) -> dict:
    return next(item for item in report["checks"] if item["id"] == check_id)


def test_all_sections_in_main_tex_pass(tmp_path):
    main = tmp_path / "main.tex"
    main.write_text(_complete_latex(), encoding="utf-8")

    report = lint_paper(main, RULES)

    assert _check(report, "latex_document_graph")["status"] == "pass"
    assert _check(report, "required_sections")["status"] == "pass"
    assert report["document_graph"]["files"] == [str(main.resolve())]


def test_single_level_input_with_explicit_extension_passes_and_records_source(tmp_path):
    main = tmp_path / "main.tex"
    section = tmp_path / "sections.tex"
    main.write_text("\\input{sections.tex}\n", encoding="utf-8")
    section.write_text(_complete_latex(), encoding="utf-8")

    report = lint_paper(main, RULES)

    assert _check(report, "required_sections")["status"] == "pass"
    problem_matches = report["section_matches"]["问题重述"]
    assert any(item["file"] == str(section.resolve()) and item["line"] == 2 for item in problem_matches)


def test_nested_input_supports_current_and_root_relative_paths(tmp_path):
    main = tmp_path / "main.tex"
    sections = tmp_path / "sections"
    shared = tmp_path / "shared"
    sections.mkdir()
    shared.mkdir()
    main.write_text("\\input{sections/first}\n", encoding="utf-8")
    (sections / "first.tex").write_text(
        "\\begin{abstract}采用均值模型得到结果 3.0。关键词：均值。\\end{abstract}\n"
        "\\section{问题重述}\\section{问题分析}\\section{模型假设}\\section{符号说明}\n"
        "\\input{shared/second}\n",
        encoding="utf-8",
    )
    (shared / "second.tex").write_text(
        "\\section{数据处理}\\section{模型建立}\\section{模型求解}\\section{模型验证}\n"
        "\\section{敏感性分析}\\section{模型评价}\\section{结论}\n"
        "\\begin{thebibliography}{9}\\end{thebibliography}\\appendix\\section{支撑材料说明}\n",
        encoding="utf-8",
    )

    report = lint_paper(main, RULES)

    assert _check(report, "latex_document_graph")["status"] == "pass"
    assert _check(report, "required_sections")["status"] == "pass"
    assert len(report["document_graph"]["files"]) == 3


def test_include_without_tex_extension_passes(tmp_path):
    main = tmp_path / "main.tex"
    chapter = tmp_path / "chapter.tex"
    main.write_text("\\include{chapter}\n", encoding="utf-8")
    chapter.write_text(_complete_latex(), encoding="utf-8")

    report = lint_paper(main, RULES)

    assert _check(report, "latex_document_graph")["status"] == "pass"
    assert _check(report, "required_sections")["status"] == "pass"


def test_combined_headings_satisfy_only_configured_sections(tmp_path):
    main = tmp_path / "main.tex"
    main.write_text(
        "\\begin{abstract}采用优化模型得到结果 3.0。关键词：优化。\\end{abstract}\n"
        "\\section{问题重述与分析}\\section{模型假设与符号}\\section{数据处理}\n"
        "\\section{模型建立与求解}\\section{模型分析、检验与评价}\n"
        "\\section{敏感性分析}\\section{结论}\n"
        "\\begin{thebibliography}{9}\\end{thebibliography}\\appendix\\section{复现说明}\n",
        encoding="utf-8",
    )

    report = lint_paper(main, RULES)

    assert _check(report, "required_sections")["status"] == "pass"
    assert report["section_matches"]["模型建立"]
    assert report["section_matches"]["模型求解"]
    assert report["section_matches"]["模型验证"]
    assert report["section_matches"]["模型评价"]


def test_similar_heading_does_not_false_positive_model_build(tmp_path):
    main = tmp_path / "main.tex"
    text = _complete_latex().replace("\\section{模型建立}\n", "")
    text = text.replace("\\section{问题分析}\n", "\\section{问题分析与模型准备}\n")
    main.write_text(text, encoding="utf-8")

    report = lint_paper(main, RULES)

    required = _check(report, "required_sections")
    assert required["status"] == "fail"
    assert not report["section_matches"]["模型建立"]
    assert "模型建立" in required["evidence"]


def test_missing_child_file_is_reported_as_failure(tmp_path):
    main = tmp_path / "main.tex"
    main.write_text(_complete_latex() + "\\input{missing-child}\n", encoding="utf-8")

    report = lint_paper(main, RULES)

    graph = _check(report, "latex_document_graph")
    assert graph["status"] == "fail"
    assert any(issue["kind"] == "missing_include" for issue in report["document_graph"]["issues"])
    assert graph["locations"][0]["line"] > 0


def test_include_cycle_fails_without_recursing_forever(tmp_path):
    main = tmp_path / "main.tex"
    child = tmp_path / "child.tex"
    main.write_text(_complete_latex() + "\\input{child}\n", encoding="utf-8")
    child.write_text("\\input{main}\n", encoding="utf-8")

    report = lint_paper(main, RULES)

    assert _check(report, "latex_document_graph")["status"] == "fail"
    assert any(issue["kind"] == "include_cycle" for issue in report["document_graph"]["issues"])
    assert len(report["document_graph"]["files"]) == 2


def test_include_outside_paper_root_is_rejected(tmp_path):
    paper_root = tmp_path / "paper"
    paper_root.mkdir()
    main = paper_root / "main.tex"
    (tmp_path / "outside.tex").write_text(_complete_latex(), encoding="utf-8")
    main.write_text(_complete_latex() + "\\input{../outside}\n", encoding="utf-8")

    report = lint_paper(main, RULES)

    assert _check(report, "latex_document_graph")["status"] == "fail"
    assert any(issue["kind"] == "outside_root" for issue in report["document_graph"]["issues"])


def test_artifact_root_allows_confined_result_include_but_not_parent_escape(tmp_path):
    workspace = tmp_path / "workspace"
    paper_root = workspace / "paper"
    results_root = workspace / "results"
    paper_root.mkdir(parents=True)
    results_root.mkdir()
    main = paper_root / "main.tex"
    (results_root / "sections.tex").write_text(_complete_latex(), encoding="utf-8")
    main.write_text("\\input{../results/sections}\n", encoding="utf-8")

    allowed = lint_paper(main, RULES, artifact_root=workspace)

    assert _check(allowed, "latex_document_graph")["status"] == "pass"
    assert _check(allowed, "required_sections")["status"] == "pass"

    main.write_text("\\input{../../outside}\n", encoding="utf-8")
    rejected = lint_paper(main, RULES, artifact_root=workspace)
    assert _check(rejected, "latex_document_graph")["status"] == "fail"
    assert any(issue["kind"] == "outside_root" for issue in rejected["document_graph"]["issues"])


def test_commented_include_is_ignored(tmp_path):
    main = tmp_path / "main.tex"
    main.write_text(_complete_latex() + "% \\input{missing}\n", encoding="utf-8")

    report = lint_paper(main, RULES)

    assert _check(report, "latex_document_graph")["status"] == "pass"
    assert report["document_graph"]["issues"] == []
    assert len(report["document_graph"]["files"]) == 1


def test_escaped_percent_does_not_hide_following_include(tmp_path):
    main = tmp_path / "main.tex"
    child = tmp_path / "child.tex"
    main.write_text("保留 50\\% 的文本 \\input{child}\n", encoding="utf-8")
    child.write_text(_complete_latex(), encoding="utf-8")

    report = lint_paper(main, RULES)

    assert _check(report, "latex_document_graph")["status"] == "pass"
    assert len(report["document_graph"]["files"]) == 2


def test_duplicate_include_is_scanned_once(tmp_path):
    main = tmp_path / "main.tex"
    child = tmp_path / "child.tex"
    main.write_text("\\input{child}\n\\input{child}\n", encoding="utf-8")
    child.write_text(_complete_latex(), encoding="utf-8")

    report = lint_paper(main, RULES)

    assert _check(report, "latex_document_graph")["status"] == "pass"
    assert len(report["document_graph"]["files"]) == 2
    assert len(report["document_graph"]["duplicate_skips"]) == 1
    assert len(report["section_matches"]["问题重述"]) == 1
