from __future__ import annotations

from pathlib import Path
from typing import Any

from .cases import find_case
from .util import now_iso, read_yaml, write_yaml


def compare_score_reports(before: dict[str, Any], after: dict[str, Any], *, tolerance: float = 2.0) -> dict[str, Any]:
    dimensions: dict[str, Any] = {}
    failed = False
    for category in ("result_reliability", "method_quality", "paper_quality"):
        old = float(before["categories"][category]["score"])
        new = float(after["categories"][category]["score"])
        delta = new - old
        status = "fail" if delta < -abs(tolerance) else "pass"
        failed = failed or status == "fail"
        dimensions[category] = {"before": old, "after": new, "delta": round(delta, 2), "status": status}
    return {"status": "fail" if failed else "pass", "dimensions": dimensions, "tolerance": tolerance}


def run_regression(trainer_root: Path, *, report_path: Path | None = None) -> dict[str, Any]:
    benchmark = read_yaml(trainer_root / "benchmarks" / "baseline-scores.yaml", {"cases": {}})
    results: list[dict[str, Any]] = []
    for case_id, before in benchmark.get("cases", {}).items():
        try:
            case_dir = find_case(trainer_root, case_id)
            after = read_yaml(case_dir / "reports" / "score-report.yaml")
            if not after:
                results.append({"case_id": case_id, "status": "needs_review", "reason": "缺少当前评分"})
                continue
            comparison = compare_score_reports(before, after)
            comparison["case_id"] = case_id
            results.append(comparison)
        except Exception as exc:
            results.append({"case_id": case_id, "status": "needs_review", "reason": str(exc)})
    if any(item["status"] == "fail" for item in results):
        status = "fail"
    elif any(item["status"] == "needs_review" for item in results) or not results:
        status = "needs_review"
    else:
        status = "pass"
    report = {"status": status, "generated_at": now_iso(), "cases": results}
    write_yaml(report_path or trainer_root / "reports" / "regression-report.yaml", report)
    return report
