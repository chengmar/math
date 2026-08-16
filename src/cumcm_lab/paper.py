from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import now_iso, read_yaml, write_json


def lint_paper(
    paper_path: Path,
    rules_path: Path,
    *,
    artifact_root: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    if paper_path.suffix.lower() not in {".md", ".tex"}:
        raise ValueError("论文检查仅支持 .md 或 .tex。")
    if not paper_path.exists():
        raise FileNotFoundError(f"论文不存在：{paper_path}")
    rules = read_yaml(rules_path)
    text = paper_path.read_text(encoding="utf-8-sig", errors="replace")
    checks: list[dict[str, Any]] = []

    def add(check_id: str, status: str, evidence: str) -> None:
        checks.append({"id": check_id, "status": status, "evidence": evidence})

    lowered = text.casefold()
    has_abstract = "摘要" in text or "\\begin{abstract}" in lowered
    add("abstract_exists", "pass" if has_abstract else "fail", "检测摘要标题或 abstract 环境")
    abstract_text = text[: min(len(text), 2500)]
    model_terms = ("模型", "回归", "方程", "优化", "仿真", "baseline", "基线")
    add("abstract_model", "pass" if any(term in abstract_text for term in model_terms) else "fail", "摘要前 2500 字符中的模型词")
    add("abstract_result", "pass" if re.search(r"\d+(?:\.\d+)?\s*(?:%|个|次|秒|元|米|kg|h)?", abstract_text) else "fail", "摘要中的定量结果")

    required_sections = rules.get("required_sections") or []
    missing_sections = [section for section in required_sections if section not in text]
    add("required_sections", "pass" if not missing_sections else "fail", f"缺少：{missing_sections}" if missing_sections else "章节齐全")
    add("question_conclusions", "pass" if ("小结" in text or "结论" in text) else "needs_review", "需人工核对每一小问是否均有结论")

    figures = re.findall(r"!\[([^\]]*)\]\([^)]*\)|\\caption\{([^}]*)\}", text)
    if figures:
        missing_captions = [item for item in figures if not any(part.strip() for part in item)]
        add("figure_captions", "pass" if not missing_captions else "fail", f"检测到 {len(figures)} 个图表标题")
        referenced = bool(re.search(r"图\s*\d|表\s*\d|\\ref\{|如图|见表", text))
        add("figure_references", "pass" if referenced else "needs_review", "正文图表引用模式")
    else:
        add("figure_captions", "needs_review", "未检测到图表，需确认题目是否需要")
        add("figure_references", "needs_review", "无图表可检查")
    add("table_units", "needs_review", "单位语义无法可靠自动判断")

    placeholders = [pattern for pattern in (rules.get("placeholder_patterns") or []) if str(pattern).casefold() in lowered]
    add("placeholders", "fail" if placeholders else "pass", f"发现：{placeholders}" if placeholders else "未发现配置占位符")
    identities = [pattern for pattern in (rules.get("identity_patterns") or []) if str(pattern).casefold() in lowered]
    add("identity", "fail" if identities and rules.get("anonymous_required", True) else "pass", f"发现：{identities}" if identities else "未发现配置身份模式")
    add("undefined_symbols", "needs_review", "明显符号定义仍需人工核查")
    has_references = "参考文献" in text or "\\bibliography" in lowered or "\\printbibliography" in lowered
    add("references", "pass" if has_references else "fail", "参考文献章节/命令")
    has_support = "支撑材料" in text or "代码清单" in text
    add("supporting_materials", "pass" if has_support else "fail", "支撑材料说明")

    if rules.get("page_limit") is None:
        add("page_limit", "needs_review", "competition-rules.yaml 未填写当年官方页数")
    else:
        add("page_limit", "needs_review", "文本源文件无法可靠判断最终 PDF 页数")

    if artifact_root:
        has_code = (artifact_root / "code").exists() and any((artifact_root / "code").rglob("*.py"))
        has_results = (artifact_root / "results").exists() and any(path.is_file() for path in (artifact_root / "results").rglob("*"))
        add("code_outputs", "pass" if has_code and has_results else "fail", f"code={has_code}, results={has_results}")
        add("number_consistency", "needs_review", "数字一致性需结合结构化结果和人工抽查")
    else:
        add("code_outputs", "needs_review", "未提供 artifact_root")
        add("number_consistency", "needs_review", "未提供结构化结果路径")

    status = "fail" if any(item["status"] == "fail" for item in checks) else (
        "needs_review" if any(item["status"] == "needs_review" for item in checks) else "pass"
    )
    report = {
        "status": status,
        "checked_at": now_iso(),
        "paper": str(paper_path),
        "rules_status": rules.get("status", "needs_review"),
        "checks": checks,
        "limitation": "结构检查不能替代数学正确性、论证质量或人工排版评审。",
    }
    if report_path:
        write_json(report_path, report)
    return report

