from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

trainer = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(trainer / "src"))

from cumcm_lab.cases import init_case
from cumcm_lab.freeze import freeze_solution, verify_frozen
from cumcm_lab.phases import complete_phase, prepare_phase
from cumcm_lab.regression import run_regression
from cumcm_lab.scoring import score_case
from cumcm_lab.state import transition
from cumcm_lab.util import load_lab_paths, write_json, write_yaml
from cumcm_lab.verify import verify_case


def write_solution(workspace: Path) -> None:
    for rel in ("code", "results", "figures", "paper"):
        (workspace / rel).mkdir(parents=True, exist_ok=True)
    (workspace / "code" / "run.py").write_text(
        "from pathlib import Path\n"
        "import csv, json\n"
        "SEED = 20260816\n"
        "with Path('input/data/values.csv').open(encoding='utf-8-sig') as f:\n"
        "    values = [float(row['value']) for row in csv.DictReader(f)]\n"
        "result = {'mean': sum(values)/len(values), 'count': len(values), 'seed': SEED}\n"
        "Path('results').mkdir(exist_ok=True)\n"
        "Path('results/summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')\n"
        "print(json.dumps(result, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    write_json(workspace / "results" / "summary.json", {"mean": 3.0, "count": 5, "seed": 20260816})
    (workspace / "figures" / "trend.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="100"><path d="M10 90 L230 10" stroke="black" fill="none"/><text x="15" y="20">Dummy</text></svg>',
        encoding="utf-8",
    )
    write_yaml(
        workspace / "solution-report.yaml",
        {
            "case_id": "dummy-a",
            "questions": [{"id": "Q1", "result": 3.0, "unit": "无量纲"}],
            "baseline": {"name": "hand-computed arithmetic mean", "result": 3.0},
            "main_model": {"name": "deterministic arithmetic mean"},
            "validation": [
                {"type": "independent hand check", "status": "pass", "error": 0.0},
                {"type": "sensitivity", "status": "pass", "delta": 0.2},
            ],
            "limitations": ["仅验证训练系统生命周期，不代表真实建模能力"],
            "reproducibility": {"run_command": "python code/run.py", "result": "results/summary.json"},
        },
    )
    write_yaml(
        workspace / "reproducibility.yaml",
        {"random_seed": 20260816, "run_command": "python code/run.py", "dependencies": [], "knowledge_cards": []},
    )
    (workspace / "problem-analysis.md").write_text("# 问题分析\n\n唯一任务为计算人工序列均值。\n", encoding="utf-8")
    (workspace / "data-audit.md").write_text("# 数据审计\n\n5 条数据，无缺失、重复或单位冲突。\n", encoding="utf-8")
    (workspace / "model-selection.md").write_text("# 模型选择\n\n选择算术均值基线；复杂模型没有收益。\n", encoding="utf-8")
    write_yaml(workspace / "assumptions.yaml", {"assumptions": [{"id": "A1", "text": "输入数据真实等于人工值"}]})
    write_yaml(workspace / "variables.yaml", {"variables": [{"name": "x_i", "meaning": "第 i 个值", "unit": "无量纲"}]})
    paper = """# Dummy A 题训练流程验证

## 摘要
本文采用确定性算术均值模型处理 5 个完全人工生成数据，得到均值 3.0；独立手算误差为 0%，输入单点增加 1 时均值增加 0.2。本结论仅用于验证训练系统。

关键词：均值模型；流程验证；敏感性

## 问题重述
计算人工序列 1、2、3、4、5 的均值。
## 问题分析
任务没有预测或优化要求，简单基线已充分。
## 模型假设
输入文件完整且每个样本权重相同。
## 符号说明
$x_i$ 表示第 i 个无量纲观测，$n=5$。
## 数据处理
CSV 共 5 行，无缺失与异常，不删除数据。
## 模型建立
基线与主模型均为 $\bar{x}=\sum_i x_i/n$；复杂方法不带来收益。
## 模型求解
固定随机种子 20260816，代码计算结果为 3.0。
## 模型验证
独立手算同为 3.0，绝对误差 0。
## 敏感性分析
任一输入增加 1，均值精确增加 0.2。
## 模型评价
方法可解释、零额外参数；空输入时失效。
## 结论
Q1 的人工结果为 3.0（无量纲）。

如图 1 所示为人工趋势。
![图 1：人工数据趋势，横纵轴均为无量纲](../figures/trend.svg)

## 参考文献
Dummy 盲解不使用外部文献。
## 附录
核心代码位于 code/run.py。
## 支撑材料说明
输入为 input/data/values.csv；输出为 results/summary.json；运行命令为 python code/run.py。
"""
    (workspace / "paper" / "paper.md").write_text(paper, encoding="utf-8")
    (workspace / "paper" / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nDummy workflow only. Mean is 3.0.\n\\end{document}\n",
        encoding="utf-8",
    )


def main() -> int:
    case_dir = init_case(trainer, "dummy-a", "dummy", title="人工 Dummy 生命周期", problem_family="synthetic-mean")
    (case_dir / "input" / "problem" / "problem.md").write_text("# 人工 Dummy\n\n计算序列 1、2、3、4、5 的算术均值。\n", encoding="utf-8")
    (case_dir / "input" / "data" / "values.csv").write_text("value\n1\n2\n3\n4\n5\n", encoding="utf-8")

    solve = prepare_phase(trainer, "dummy-a", "solve")
    write_solution(solve)
    freeze_solution(case_dir, "blind-v1", random_seed=20260816, run_command="python code/run.py")

    audit = prepare_phase(trainer, "dummy-a", "audit")
    (audit / "audit-report.md").write_text("# Dummy 独立审计\n\n哈希有效，干净环境重跑成功；未发现阻断性问题。\n", encoding="utf-8")
    write_yaml(audit / "audit-findings.yaml", {"findings": []})
    write_json(audit / "reproduction-report.json", {"status": "pass", "command": "python code/run.py", "return_code": 0})
    (audit / "counterexamples.md").write_text("# 反例\n\n空输入会使均值未定义。\n", encoding="utf-8")
    (audit / "revision-plan.md").write_text("# 修订计划\n\n在局限性中明确空输入失效。\n", encoding="utf-8")
    complete_phase(trainer, "dummy-a", "audit")

    revision = prepare_phase(trainer, "dummy-a", "blind-revision")
    write_solution(revision)
    freeze_solution(case_dir, "blind-final", random_seed=20260816, run_command="python code/run.py")

    paths = load_lab_paths(trainer)
    reference = Path(paths["reference_vault"]) / "dummy-a" / "demo-reference.md"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_text("# 人工 Demo 参考\n\n同一人工序列的手算均值为 3.0。该文件不是竞赛论文。\n", encoding="utf-8")
    case_meta = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    case_meta["reference_ids"] = ["dummy-a/demo-reference.md"]
    write_yaml(case_dir / "case.yaml", case_meta)

    reflection = prepare_phase(trainer, "dummy-a", "reflection")
    (reflection / "comparison-matrix.md").write_text("# Demo 比较矩阵\n\n盲解与手算参考均为 3.0；只验证流程。\n", encoding="utf-8")
    write_yaml(reflection / "comparison-matrix.yaml", {"status": "demo", "blind": 3.0, "reference": 3.0})
    (reflection / "reference-validation.md").write_text("# 参考验证\n\n参考由人工手算，非获奖论文。\n", encoding="utf-8")
    (reflection / "self-gap-analysis.md").write_text("# 自身差距\n\n无真实建模结论可推广。\n", encoding="utf-8")
    (reflection / "innovation-analysis.md").write_text("# 创新分析\n\n不声称创新。\n", encoding="utf-8")
    lessons = reflection / "lessons-proposed"
    lessons.mkdir()
    write_yaml(lessons / "dummy-demo-lesson.yaml", {"id": "dummy-demo-lesson", "status": "demo", "provenance": "demo", "note": "不得升级 verified"})
    complete_phase(trainer, "dummy-a", "reflection")
    transition(case_dir, "knowledge_proposed", command="build_dummy", reason="生成明确标记 demo 的知识提案")

    verification = verify_case(case_dir, report_path=case_dir / "reports" / "verify-case.json")
    rubric = yaml.safe_load((trainer / "config" / "scoring-rubric.yaml").read_text(encoding="utf-8"))
    weights = {key: item["weight"] for category in rubric["categories"].values() for key, item in category["criteria"].items()}
    human = ["mathematical_correctness", "uncertainty_limitations", "fit_to_problem", "simplicity_cost", "innovation_benefit", "interpretability", "abstract_quality", "structure_logic", "notation_explanation", "figure_tables"]
    write_yaml(
        case_dir / "reports" / "judge-scores.yaml",
        {
            "scores": {
                key: {
                    "score": 0 if key == "innovation_benefit" else weights[key],
                    "evidence": "Dummy 没有创新收益" if key == "innovation_benefit" else "人工 Dummy 验收，仅评价流程",
                }
                for key in human
            }
        },
    )
    score = score_case(case_dir, trainer)
    write_yaml(
        trainer / "benchmarks" / "baseline-scores.yaml",
        {"cases": {"dummy-a": {"categories": {key: {"score": max(0, item["score"] - 1)} for key, item in score["categories"].items()}}}},
    )
    regression = run_regression(trainer)
    final = {
        "status": "pass" if verification["status"] == "pass" and regression["status"] == "pass" else "fail",
        "case": str(case_dir),
        "blind_v1": verify_frozen(case_dir, "blind-v1")["status"],
        "blind_final": verify_frozen(case_dir, "blind-final")["status"],
        "reproduction": verification["status"],
        "score": score["total"],
        "regression": regression["status"],
    }
    write_json(case_dir / "reports" / "dummy-lifecycle.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0 if final["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
