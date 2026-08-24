from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml


DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt"}
DATA_EXTENSIONS = {".xls", ".xlsx", ".csv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz"}
REFERENCE_CODE_EXTENSIONS = {
    ".py",
    ".ipynb",
    ".m",
    ".r",
    ".jl",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
}
SUPPORTED_EXTENSIONS = (
    DOCUMENT_EXTENSIONS | DATA_EXTENSIONS | IMAGE_EXTENSIONS | ARCHIVE_EXTENSIONS | REFERENCE_CODE_EXTENSIONS
)

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_LETTER_PATTERNS = (
    re.compile(r"(?<![A-Z])([A-E])\s*题", re.IGNORECASE),
    re.compile(r"(?:19|20)\d{2}\s*[-_. ]*([A-E])(?=$|[-_. ()（）]|题)", re.IGNORECASE),
    re.compile(r"(?:problem|question|赛题|题目)\s*[-_:： ]*([A-E])(?=$|[-_. ()（）]|题)", re.IGNORECASE),
)
_INSTRUCTION_WORDS = ("说明", "须知", "要求", "readme", "instruction")
_ATTACHMENT_WORDS = ("附件", "附录", "attachment", "appendix")
_DATA_WORDS = ("数据", "data", "dataset")
_COMMENTARY_WORDS = ("讲评", "评述", "点评", "commentary", "review")
_CODE_WORDS = ("代码", "程序", "源码", "source", "code")


def load_split_config(path: Path | str) -> dict[str, Any]:
    """Load and minimally validate the corpus split configuration."""

    config_path = Path(path)
    with config_path.open("r", encoding="utf-8-sig") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"数据划分配置必须是映射：{config_path}")
    _validate_split_config(payload)
    return payload


def _coerce_config(split_config: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(split_config, (str, Path)):
        return load_split_config(split_config)
    payload = dict(split_config)
    _validate_split_config(payload)
    return payload


def _years(config: Mapping[str, Any], section: str) -> set[int]:
    value = config.get(section, {})
    if not isinstance(value, Mapping):
        return set()
    raw_years = value.get("years", [])
    if raw_years is None:
        return set()
    return {int(year) for year in raw_years}


def _validate_split_config(config: Mapping[str, Any]) -> None:
    assignments: dict[int, str] = {}
    for section in ("train", "dev", "test", "excluded"):
        for year in _years(config, section):
            previous = assignments.get(year)
            if previous is not None:
                raise ValueError(f"年份 {year} 同时出现在 {previous} 和 {section}")
            assignments[year] = section
    scope = config.get("out_of_scope", {})
    if scope and not isinstance(scope, Mapping):
        raise ValueError("out_of_scope 必须是映射")


def split_for_year(
    year: int | None,
    split_config: Path | str | Mapping[str, Any],
    problem_letter: str | None = "A",
) -> str:
    """Return the deterministic split for a year and problem letter."""

    config = _coerce_config(split_config)
    if problem_letter is not None and problem_letter.upper() != "A":
        return "quarantine"
    if year is None:
        return "unassigned"
    year = int(year)
    for section in ("train", "dev", "test", "excluded"):
        if year in _years(config, section):
            return section
    scope = config.get("out_of_scope", {})
    before = scope.get("before_year") if isinstance(scope, Mapping) else None
    after = scope.get("after_year") if isinstance(scope, Mapping) else None
    if before is not None and year < int(before):
        return "out_of_scope"
    if after is not None and year > int(after):
        return "out_of_scope"
    return "unassigned"


def _path_label(path: Path, source_root: Path | None) -> str:
    if source_root is not None:
        try:
            return path.relative_to(source_root).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _detect_years(label: str) -> list[int]:
    return sorted({int(match.group(1)) for match in _YEAR_RE.finditer(label)})


def _detect_letters(label: str) -> list[str]:
    upper = label.upper()
    found: set[str] = set()
    for pattern in _LETTER_PATTERNS:
        found.update(match.group(1).upper() for match in pattern.finditer(upper))
    for part in re.split(r"[\\/_. ()（）-]+", upper):
        if part in {"A", "B", "C", "D", "E"}:
            found.add(part)
    return sorted(found)


def _document_type(source_kind: str, label: str, extension: str) -> tuple[str, str, str | None]:
    folded = label.casefold()
    if source_kind == "problems":
        if extension in ARCHIVE_EXTENSIONS:
            return "archive_container", "high", None
        if extension in DATA_EXTENSIONS:
            return "official_data", "high", None
        if extension in IMAGE_EXTENSIONS:
            return "official_attachment", "high", None
        if any(word in folded for word in _INSTRUCTION_WORDS):
            return "problem_instruction", "high", None
        if any(word in folded for word in _DATA_WORDS):
            return "official_data", "high", None
        if any(word in folded for word in _ATTACHMENT_WORDS):
            return "official_attachment", "high", None
        if extension in DOCUMENT_EXTENSIONS:
            return "problem_statement", "medium", None
        return "unknown_problem_file", "low", "unsupported_or_ambiguous_problem_file"

    if extension in ARCHIVE_EXTENSIONS:
        return "archive_container", "high", None
    if extension in REFERENCE_CODE_EXTENSIONS or any(word in folded for word in _CODE_WORDS):
        return "reference_code", "high", None
    if any(word in folded for word in _COMMENTARY_WORDS):
        return "commentary", "high", None
    if extension in DOCUMENT_EXTENSIONS:
        return "reference_paper", "medium", None
    if extension in DATA_EXTENSIONS or extension in IMAGE_EXTENSIONS:
        return "reference_attachment", "high", None
    return "unknown_reference_file", "low", "unsupported_or_ambiguous_reference_file"


def classify_path(
    path: Path | str,
    source_kind: str,
    split_config: Path | str | Mapping[str, Any],
    *,
    source_root: Path | str | None = None,
) -> dict[str, Any]:
    """Classify a path using only its source root, relative path, name and extension."""

    item = Path(path)
    root = Path(source_root) if source_root is not None else None
    normalized_source = source_kind.casefold().strip()
    if normalized_source in {"problem", "problems", "problem_source"}:
        normalized_source = "problems"
    elif normalized_source in {"paper", "papers", "reference", "references", "paper_source"}:
        normalized_source = "papers"
    else:
        raise ValueError(f"未知语料来源类型：{source_kind}")

    label = _path_label(item, root)
    years = _detect_years(label)
    letters = _detect_letters(label)
    reasons: list[str] = []

    detected_year = years[0] if len(years) == 1 else None
    if not years:
        reasons.append("missing_year")
    elif len(years) > 1:
        reasons.append("ambiguous_year")

    if len(letters) == 1:
        problem_letter = letters[0]
        letter_method = "path_or_filename"
    elif len(letters) > 1:
        problem_letter = None
        letter_method = "ambiguous"
        reasons.append("ambiguous_problem_letter")
    else:
        # The two configured source roots are A-problem-only corpora.  An explicit
        # non-A marker always overrides this conservative source-root inference.
        problem_letter = "A"
        letter_method = "source_root_scope"

    extension = item.suffix.casefold()
    document_type, confidence, type_reason = _document_type(normalized_source, label, extension)
    if extension not in SUPPORTED_EXTENSIONS:
        confidence = "low"
        type_reason = type_reason or "unsupported_extension"
    if type_reason:
        reasons.append(type_reason)

    split = split_for_year(detected_year, split_config, problem_letter)
    if problem_letter not in {None, "A"}:
        reasons.append("non_a_problem")
        split = "quarantine"
    if detected_year is None or problem_letter is None:
        split = "quarantine"
        confidence = "low"
    if confidence == "low" and not reasons:
        reasons.append("low_confidence")

    matched_case_id = None
    if detected_year is not None and problem_letter == "A" and split in {"train", "dev", "test"}:
        matched_case_id = f"{detected_year}A"

    return {
        "detected_year": detected_year,
        "problem_letter": problem_letter,
        "document_type": document_type,
        "classification_method": f"source_root+relative_path+filename+extension;letter={letter_method}",
        "classification_confidence": confidence,
        "matched_case_id": matched_case_id,
        "split": split,
        "requires_review": bool(reasons),
        "review_reason": ";".join(dict.fromkeys(reasons)) if reasons else None,
        "quarantine_reason": "non_a_problem" if "non_a_problem" in reasons else None,
    }
