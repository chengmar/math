from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .util import now_iso, read_yaml


def compare_runs(input_path: Path, output_dir: Path) -> dict[str, Any]:
    payload = read_yaml(input_path, {"runs": []})
    runs = payload.get("runs", [])
    required = {"case_id", "variant", "codex_version", "model", "reasoning_setting", "time_budget", "allowed_tools", "run_id", "scores"}
    for run in runs:
        missing = required - set(run)
        if missing:
            raise ValueError(f"A/B 运行记录缺少字段：{sorted(missing)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "ab-comparison.csv"
    fields = ["case_id", "variant", "codex_version", "model", "reasoning_setting", "time_budget", "run_id", "total"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            writer.writerow({**{key: run.get(key) for key in fields if key != "total"}, "total": run.get("scores", {}).get("total")})
    counts: dict[str, int] = {}
    for run in runs:
        counts[str(run["variant"])] = counts.get(str(run["variant"]), 0) + 1
    conclusion = "needs_review" if any(count < 2 for count in counts.values()) else "descriptive_only"
    md = ["# A/B 对照记录", "", f"生成时间：{now_iso()}", "", f"结论状态：`{conclusion}`", "", "单次运行不得得出确定性结论；应重复运行并报告波动。", ""]
    for run in runs:
        md.append(f"- {run['case_id']} / {run['variant']} / {run['run_id']}：{run.get('scores', {}).get('total', 'NA')}")
    (output_dir / "ab-comparison.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return {"status": conclusion, "runs": len(runs), "variant_counts": counts, "csv": str(csv_path)}
