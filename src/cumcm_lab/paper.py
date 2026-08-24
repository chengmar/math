from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import now_iso, read_yaml, write_json


LATEX_INCLUDE_PATTERN = re.compile(r"\\(?P<command>input|include)\s*\{(?P<target>[^{}]+)\}")
LATEX_HEADING_PATTERN = re.compile(
    r"\\(?P<level>part|chapter|section|subsection|subsubsection)\*?\s*\{(?P<title>[^{}]+)\}"
)
MARKDOWN_HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s+(?P<title>.+?)\s*$")


def _strip_latex_comment(line: str) -> str:
    """Remove an unescaped LaTeX comment while preserving the source line."""

    for index, character in enumerate(line):
        if character != "%":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return line[:index]
    return line


def _within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _source_location(path: Path, line: int, *, match: str | None = None) -> dict[str, Any]:
    location: dict[str, Any] = {"file": str(path), "line": int(line)}
    if match is not None:
        location["match"] = match
    return location


def _read_document_graph(paper_path: Path, artifact_root: Path | None = None) -> dict[str, Any]:
    """Read one paper in logical order and recursively expand confined LaTeX includes."""

    entry = paper_path.resolve()
    paper_root = entry.parent.resolve()
    root = Path(artifact_root).resolve() if artifact_root is not None else paper_root
    if not _within_root(entry, root):
        raise ValueError(f"论文入口不在 artifact_root 内：{entry}")
    fragments: list[dict[str, Any]] = []
    files: list[str] = []
    issues: list[dict[str, Any]] = []
    duplicate_skips: list[dict[str, Any]] = []
    visited: set[Path] = set()
    active: list[Path] = []

    def append_fragment(text: str, source: Path, line: int) -> None:
        if text:
            fragments.append({"text": text, "file": str(source), "line": line})

    def resolve_include(source: Path, target_text: str) -> tuple[Path | None, str | None]:
        target = Path(target_text.strip())
        if not target.suffix:
            target = target.with_suffix(".tex")
        candidates = [(source.parent / target).resolve(), (paper_root / target).resolve()]
        confined: list[Path] = []
        for candidate in candidates:
            if candidate not in confined and _within_root(candidate, root):
                confined.append(candidate)
        if not confined:
            return None, "outside_root"
        for candidate in confined:
            if candidate.is_file():
                return candidate, None
        return confined[0], "missing"

    def expand(path: Path) -> None:
        resolved = path.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        active.append(resolved)
        files.append(str(resolved))
        raw = resolved.read_text(encoding="utf-8-sig", errors="replace")
        is_latex = resolved.suffix.casefold() == ".tex"
        for line_number, raw_line in enumerate(raw.splitlines(), start=1):
            line = _strip_latex_comment(raw_line) if is_latex else raw_line
            if not is_latex:
                append_fragment(line, resolved, line_number)
                continue
            cursor = 0
            for match in LATEX_INCLUDE_PATTERN.finditer(line):
                append_fragment(line[cursor : match.start()], resolved, line_number)
                target_text = match.group("target").strip()
                child, error = resolve_include(resolved, target_text)
                if error == "outside_root":
                    issues.append(
                        {
                            "kind": "outside_root",
                            "status": "fail",
                            "target": target_text,
                            "source": _source_location(resolved, line_number),
                        }
                    )
                elif error == "missing":
                    issues.append(
                        {
                            "kind": "missing_include",
                            "status": "fail",
                            "target": target_text,
                            "resolved": str(child),
                            "source": _source_location(resolved, line_number),
                        }
                    )
                elif child in active:
                    issues.append(
                        {
                            "kind": "include_cycle",
                            "status": "fail",
                            "target": target_text,
                            "resolved": str(child),
                            "source": _source_location(resolved, line_number),
                            "cycle": [str(item) for item in active + [child]],
                        }
                    )
                elif child in visited:
                    duplicate_skips.append(
                        {
                            "target": target_text,
                            "resolved": str(child),
                            "source": _source_location(resolved, line_number),
                        }
                    )
                else:
                    expand(child)
                cursor = match.end()
            append_fragment(line[cursor:], resolved, line_number)
        active.pop()

    expand(entry)
    return {
        "text": "\n".join(fragment["text"] for fragment in fragments),
        "fragments": fragments,
        "root": str(root),
        "paper_root": str(paper_root),
        "entry": str(entry),
        "files": files,
        "issues": issues,
        "duplicate_skips": duplicate_skips,
    }


def _normalize_heading(value: str) -> str:
    value = re.sub(r"\\[A-Za-z@]+\*?", "", value)
    return re.sub(r"[\s，,、；;：:（）()《》【】\[\]—–-]+", "", value).casefold()


def _heading_entries(document: dict[str, Any], suffix: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    pattern = LATEX_HEADING_PATTERN if suffix.casefold() == ".tex" else MARKDOWN_HEADING_PATTERN
    for fragment in document["fragments"]:
        for match in pattern.finditer(fragment["text"]):
            title = match.group("title").strip()
            entries.append(
                {
                    "title": title,
                    "normalized": _normalize_heading(title),
                    "file": fragment["file"],
                    "line": fragment["line"],
                }
            )
    return entries


def _unique_locations(locations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for item in locations:
        key = (str(item.get("file")), int(item.get("line") or 0), str(item.get("match") or ""))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _locations_for_patterns(document: dict[str, Any], patterns: tuple[str, ...]) -> list[dict[str, Any]]:
    lowered_patterns = tuple(pattern.casefold() for pattern in patterns)
    locations: list[dict[str, Any]] = []
    for fragment in document["fragments"]:
        lowered = fragment["text"].casefold()
        for original, pattern in zip(patterns, lowered_patterns):
            if pattern in lowered:
                locations.append(_source_location(Path(fragment["file"]), fragment["line"], match=original))
    return _unique_locations(locations)


def _extract_abstract_text(text: str, suffix: str) -> str:
    if suffix.casefold() == ".tex":
        match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, flags=re.IGNORECASE | re.DOTALL)
        return match.group(1) if match else ""
    match = re.search(
        r"^\s*#{1,6}\s*摘要\s*$\n(?P<body>.*?)(?=^\s*#{1,6}\s+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else ""


def _section_matches(
    section: str,
    aliases: dict[str, Any],
    headings: list[dict[str, Any]],
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    configured = aliases.get(section) or [section]
    if isinstance(configured, str):
        configured = [configured]
    normalized_aliases = [_normalize_heading(str(item)) for item in configured if str(item).strip()]
    matches = [
        _source_location(Path(heading["file"]), heading["line"], match=heading["title"])
        for heading in headings
        if any(alias and alias in heading["normalized"] for alias in normalized_aliases)
    ]
    structural_patterns: dict[str, tuple[str, ...]] = {
        "摘要": ("\\begin{abstract}",),
        "关键词": ("\\keywords", "关键词", "keywords", "key words"),
        "参考文献": ("\\begin{thebibliography}", "\\bibliography", "\\printbibliography"),
        "附录": ("\\appendix",),
    }
    matches.extend(_locations_for_patterns(document, structural_patterns.get(section, ())))
    return _unique_locations(matches)


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
    document = _read_document_graph(paper_path, artifact_root)
    text = document["text"]
    headings = _heading_entries(document, paper_path.suffix)
    checks: list[dict[str, Any]] = []

    def add(check_id: str, status: str, evidence: str, locations: list[dict[str, Any]] | None = None) -> None:
        checks.append(
            {
                "id": check_id,
                "status": status,
                "evidence": evidence,
                "locations": _unique_locations(locations or []),
            }
        )

    graph_status = "fail" if any(issue["status"] == "fail" for issue in document["issues"]) else "pass"
    add(
        "latex_document_graph",
        graph_status,
        (
            f"扫描 {len(document['files'])} 个文件；问题 {len(document['issues'])}；"
            f"重复引用跳过 {len(document['duplicate_skips'])}"
        ),
        [issue["source"] for issue in document["issues"]],
    )

    lowered = text.casefold()
    has_abstract = "摘要" in text or "\\begin{abstract}" in lowered
    abstract_locations = _locations_for_patterns(document, ("摘要", "\\begin{abstract}"))
    add("abstract_exists", "pass" if has_abstract else "fail", "检测摘要标题或 abstract 环境", abstract_locations)
    abstract_text = _extract_abstract_text(text, paper_path.suffix)
    model_terms = ("模型", "回归", "方程", "优化", "仿真", "baseline", "基线")
    add(
        "abstract_model",
        "pass" if any(term in abstract_text for term in model_terms) else "fail",
        "摘要区段中的模型词",
        _locations_for_patterns(document, tuple(term for term in model_terms if term.casefold() in abstract_text.casefold())),
    )
    numeric_locations = [
        _source_location(Path(fragment["file"]), fragment["line"], match="定量结果")
        for fragment in document["fragments"]
        if re.search(r"\d+(?:\.\d+)?\s*(?:%|个|次|秒|元|米|kg|h)?", fragment["text"])
    ]
    add(
        "abstract_result",
        "pass" if re.search(r"\d+(?:\.\d+)?\s*(?:%|个|次|秒|元|米|kg|h)?", abstract_text) else "fail",
        "摘要中的定量结果",
        numeric_locations[:5],
    )

    required_sections = rules.get("required_sections") or []
    section_aliases = rules.get("section_aliases") or {}
    section_match_map = {
        str(section): _section_matches(str(section), section_aliases, headings, document) for section in required_sections
    }
    missing_sections = [section for section in required_sections if not section_match_map[str(section)]]
    section_locations = [location for section in required_sections for location in section_match_map[str(section)]]
    add(
        "required_sections",
        "pass" if not missing_sections else "fail",
        f"缺少：{missing_sections}" if missing_sections else "章节齐全",
        section_locations,
    )
    conclusion_locations = _locations_for_patterns(document, ("小结", "结论"))
    add(
        "question_conclusions",
        "pass" if conclusion_locations else "needs_review",
        "需人工核对每一小问是否均有结论",
        conclusion_locations,
    )

    figures = re.findall(r"!\[([^\]]*)\]\([^)]*\)|\\caption\{([^}]*)\}", text)
    if figures:
        missing_captions = [item for item in figures if not any(part.strip() for part in item)]
        figure_locations = _locations_for_patterns(document, ("![", "\\caption{"))
        add("figure_captions", "pass" if not missing_captions else "fail", f"检测到 {len(figures)} 个图表标题", figure_locations)
        referenced = bool(re.search(r"图\s*\d|表\s*\d|\\ref\{|如图|见表", text))
        add(
            "figure_references",
            "pass" if referenced else "needs_review",
            "正文图表引用模式",
            _locations_for_patterns(document, ("如图", "见表", "\\ref{")),
        )
    else:
        add("figure_captions", "needs_review", "未检测到图表，需确认题目是否需要")
        add("figure_references", "needs_review", "无图表可检查")
    add("table_units", "needs_review", "单位语义无法可靠自动判断")

    placeholders = [pattern for pattern in (rules.get("placeholder_patterns") or []) if str(pattern).casefold() in lowered]
    add(
        "placeholders",
        "fail" if placeholders else "pass",
        f"发现：{placeholders}" if placeholders else "未发现配置占位符",
        _locations_for_patterns(document, tuple(str(item) for item in placeholders)),
    )
    identities = [pattern for pattern in (rules.get("identity_patterns") or []) if str(pattern).casefold() in lowered]
    add(
        "identity",
        "fail" if identities and rules.get("anonymous_required", True) else "pass",
        f"发现：{identities}" if identities else "未发现配置身份模式",
        _locations_for_patterns(document, tuple(str(item) for item in identities)),
    )
    add("undefined_symbols", "needs_review", "明显符号定义仍需人工核查")
    reference_locations = _section_matches("参考文献", section_aliases, headings, document)
    add("references", "pass" if reference_locations else "fail", "参考文献章节/命令", reference_locations)
    support_locations = _section_matches("支撑材料说明", section_aliases, headings, document)
    add("supporting_materials", "pass" if support_locations else "fail", "支撑材料说明", support_locations)

    if rules.get("page_limit") is None:
        add("page_limit", "needs_review", "competition-rules.yaml 未填写当年官方页数")
    else:
        add("page_limit", "needs_review", "文本源文件无法可靠判断最终 PDF 页数")

    if artifact_root:
        supported_code_suffixes = {".py", ".ps1", ".cmd", ".bat"}
        has_code = (artifact_root / "code").exists() and any(
            path.is_file() and path.suffix.casefold() in supported_code_suffixes
            for path in (artifact_root / "code").rglob("*")
        )
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
        "document_graph": {
            "entry": document["entry"],
            "root": document["root"],
            "paper_root": document["paper_root"],
            "files": document["files"],
            "issues": document["issues"],
            "duplicate_skips": document["duplicate_skips"],
        },
        "section_matches": section_match_map,
        "limitation": "结构检查不能替代数学正确性、论证质量或人工排版评审。",
    }
    if report_path:
        write_json(report_path, report)
    return report
