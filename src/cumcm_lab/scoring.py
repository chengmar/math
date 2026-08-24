from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .paper import lint_paper
from .util import now_iso, read_json, read_yaml, write_yaml
from .verify import artifact_root, verify_case


def _objective_statuses(case_dir: Path, trainer_root: Path) -> dict[str, tuple[str, str]]:
    root = artifact_root(case_dir)
    solution = read_yaml(root / "solution-report.yaml", {})
    reproducibility = read_yaml(root / "reproducibility.yaml", {})
    reproduction_report = verify_case(case_dir, report_path=case_dir / "reports" / "reproduction-report.json")
    paper_path = root / "paper" / "paper.md"
    lint = (
        lint_paper(
            paper_path,
            trainer_root / "config" / "competition-rules.yaml",
            artifact_root=root,
            report_path=case_dir / "reports" / "paper-lint.json",
        )
        if paper_path.exists()
        else {"status": "fail", "checks": []}
    )
    results_exist = (root / "results").exists() and any(path.is_file() for path in (root / "results").rglob("*"))
    questions = solution.get("questions") or []
    validation = solution.get("validation") or []
    baseline = solution.get("baseline") or {}
    traceable = bool(reproducibility.get("run_command") and results_exist and solution)
    reference_check = next((item for item in lint.get("checks", []) if item.get("id") == "references"), {"status": "fail"})
    placeholder_check = next((item for item in lint.get("checks", []) if item.get("id") == "placeholders"), {"status": "fail"})
    return {
        "question_coverage": ("pass" if questions else "fail", f"questions={len(questions)}"),
        "numeric_results": ("pass" if results_exist else "fail", "结构化 results 目录"),
        "reproducibility": (reproduction_report["status"], "干净目录重跑"),
        "validation_sensitivity": ("pass" if validation else "fail", f"validation={len(validation)}"),
        "baseline_comparison": ("pass" if baseline else "fail", "solution-report.baseline"),
        "traceability": ("pass" if traceable else "fail", "运行命令、结果与报告") ,
        "format_references": (
            "pass" if reference_check.get("status") == "pass" and placeholder_check.get("status") == "pass" else "fail",
            "参考文献与占位符检查",
        ),
    }


def score_case(case_dir: Path, trainer_root: Path, *, judge_scores_path: Path | None = None) -> dict[str, Any]:
    rubric = read_yaml(trainer_root / "config" / "scoring-rubric.yaml")
    judge = read_yaml(judge_scores_path or case_dir / "reports" / "judge-scores.yaml", {"scores": {}})
    judge_scores = judge.get("scores", {}) if isinstance(judge, dict) else {}
    objective = _objective_statuses(case_dir, trainer_root)
    categories: dict[str, Any] = {}
    review_needed: list[str] = []
    total = 0.0
    for category_id, category in rubric["categories"].items():
        entries: dict[str, Any] = {}
        category_score = 0.0
        for criterion_id, criterion in category["criteria"].items():
            weight = float(criterion["weight"])
            if criterion["mode"] == "objective":
                check_status, evidence = objective[criterion_id]
                score = weight if check_status == "pass" else 0.0
                status = check_status
            else:
                supplied = judge_scores.get(criterion_id, {})
                evidence = supplied.get("evidence") if isinstance(supplied, dict) else None
                raw = supplied.get("score") if isinstance(supplied, dict) else None
                if raw is None or not evidence:
                    score = 0.0
                    status = "needs_review"
                    review_needed.append(criterion_id)
                    evidence = evidence or "缺少人工评分或证据"
                else:
                    score = min(max(float(raw), 0.0), weight)
                    status = "pass"
            entries[criterion_id] = {"weight": weight, "score": score, "status": status, "evidence": evidence}
            category_score += score
        categories[category_id] = {
            "label": category["label"],
            "weight": category["weight"],
            "score": round(category_score, 2),
            "criteria": entries,
        }
        total += category_score
    report = {
        "status": "needs_review" if review_needed else "pass",
        "scored_at": now_iso(),
        "case_id": read_yaml(case_dir / "case.yaml").get("case_id"),
        "total": round(total, 2),
        "maximum": 100,
        "categories": categories,
        "human_review_needed": review_needed,
        "boundary": "自动评分只覆盖客观结构与复现项；数学正确性、创新收益和表达质量必须有人工作证。",
    }
    yaml_path = case_dir / "reports" / "score-report.yaml"
    write_yaml(yaml_path, report)
    lines = [f"# 评分报告：{report['case_id']}", "", f"- 状态：`{report['status']}`", f"- 总分：{report['total']}/100", ""]
    for category in categories.values():
        lines.append(f"## {category['label']}：{category['score']}/{category['weight']}")
        lines.append("")
        for criterion_id, item in category["criteria"].items():
            lines.append(f"- `{criterion_id}`：{item['score']}/{item['weight']}（{item['status']}）— {item['evidence']}")
        lines.append("")
    (case_dir / "reports" / "score-report.md").write_text("\n".join(lines), encoding="utf-8")
    _update_leaderboard(trainer_root / "reports" / "leaderboard.csv", report)
    return report


def _update_leaderboard(path: Path, report: dict[str, Any]) -> None:
    rows: list[dict[str, str]] = []
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    row = {
        "case_id": str(report["case_id"]),
        "total": str(report["total"]),
        "result_reliability": str(report["categories"]["result_reliability"]["score"]),
        "method_quality": str(report["categories"]["method_quality"]["score"]),
        "paper_quality": str(report["categories"]["paper_quality"]["score"]),
        "status": str(report["status"]),
        "scored_at": str(report["scored_at"]),
    }
    rows = [item for item in rows if item.get("case_id") != row["case_id"]]
    rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row), lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda item: item["case_id"]))
