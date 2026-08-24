from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def lab_factory(tmp_path):
    def create() -> tuple[Path, Path, Path]:
        lab_root = tmp_path / "CUMCM-A-Lab"
        trainer = lab_root / "trainer"
        vault_root = tmp_path / "CUMCM-A-Vaults"
        for rel in (
            "cases/train",
            "cases/dev",
            "cases/exam-stubs",
            "cases/dummy",
            "knowledge/method-cards",
            "knowledge/failure-modes",
            "knowledge/validation-patterns",
            "knowledge/paper-writing",
            "knowledge/problem-taxonomy",
            "knowledge/candidates",
            "knowledge/promotion-proposals",
            "knowledge/deprecated",
            "reports",
            "benchmarks",
            "config",
            "templates/paper",
        ):
            (trainer / rel).mkdir(parents=True, exist_ok=True)
        (vault_root / "reference-vault").mkdir(parents=True)
        (vault_root / "exam-vault").mkdir(parents=True)
        (trainer / "pyproject.toml").write_text("[project]\nname='test-lab'\n", encoding="utf-8")
        (trainer / "cases" / "registry.yaml").write_text("cases: []\n", encoding="utf-8")
        shutil.copy2(PROJECT_ROOT / "config" / "competition-rules.yaml", trainer / "config" / "competition-rules.yaml")
        shutil.copy2(PROJECT_ROOT / "config" / "scoring-rubric.yaml", trainer / "config" / "scoring-rubric.yaml")
        shutil.copytree(PROJECT_ROOT / "templates" / "paper", trainer / "templates" / "paper", dirs_exist_ok=True)
        shutil.copytree(PROJECT_ROOT / ".agents" / "skills", trainer / ".agents" / "skills", dirs_exist_ok=True)
        codex_home = lab_root / "codex-home"
        codex_home.mkdir()
        (codex_home / "lab-paths.toml").write_text(
            "[paths]\n"
            f'lab_root = "{lab_root.as_posix()}"\n'
            f'vault_root = "{vault_root.as_posix()}"\n'
            f'trainer = "{trainer.as_posix()}"\n'
            f'codex_home = "{codex_home.as_posix()}"\n'
            f'baseline_codex_home = "{(lab_root / "baseline-codex-home").as_posix()}"\n'
            f'reference_vault = "{(vault_root / "reference-vault").as_posix()}"\n'
            f'exam_vault = "{(vault_root / "exam-vault").as_posix()}"\n',
            encoding="utf-8",
        )
        return trainer, lab_root, vault_root

    return create


def write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def write_solution(workspace: Path, case_id: str = "dummy-a", offset: int = 0) -> None:
    for rel in ("code", "results", "figures", "paper"):
        (workspace / rel).mkdir(parents=True, exist_ok=True)
    (workspace / "code" / "run.py").write_text(
        "from pathlib import Path\n"
        "import json\n"
        "SEED = 20260816\n"
        "values = [1, 2, 3, 4, 5]\n"
        "result = {'mean': sum(values)/len(values), 'count': len(values)}\n"
        "Path('results').mkdir(exist_ok=True)\n"
        "Path('results/summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')\n"
        "print(json.dumps(result))\n",
        encoding="utf-8",
    )
    (workspace / "results" / "summary.json").write_text(json.dumps({"mean": 3 + offset, "count": 5}), encoding="utf-8")
    (workspace / "figures" / "trend.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><path d="M0 40 L100 10" stroke="black"/></svg>',
        encoding="utf-8",
    )
    write_yaml(
        workspace / "solution-report.yaml",
        {
            "case_id": case_id,
            "questions": [{"id": "Q1", "result": 3 + offset, "unit": "unit"}],
            "baseline": {"name": "arithmetic mean", "result": 3},
            "main_model": {"name": "deterministic mean model"},
            "validation": [{"type": "hand_check", "status": "pass"}, {"type": "sensitivity", "status": "pass"}],
            "limitations": ["仅用于 Dummy 流程验证"],
            "reproducibility": {"result_file": "results/summary.json"},
        },
    )
    write_yaml(
        workspace / "reproducibility.yaml",
        {"random_seed": 20260816, "run_command": "python code/run.py", "dependencies": [], "knowledge_cards": []},
    )
    for name in ("problem-analysis.md", "data-audit.md", "model-selection.md"):
        (workspace / name).write_text("# Demo\n\n人工生成的流程验证内容。\n", encoding="utf-8")
    write_yaml(workspace / "assumptions.yaml", {"assumptions": ["人工数据无缺失"]})
    write_yaml(workspace / "variables.yaml", {"variables": [{"name": "x", "unit": "unit"}]})
    paper = """# Dummy 流程验证论文

## 摘要
本文采用确定性均值模型处理 5 个完全人工生成数据，得到均值 3.0；手算验证误差为 0%，结论仅用于流程测试。

关键词：均值模型；流程验证；敏感性

## 问题重述
计算人工数据均值。
## 问题分析
使用简单基线即可。
## 模型假设
数据无缺失。
## 符号说明
x 的单位为 unit。
## 数据处理
保留全部 5 个样本。
## 模型建立
基线和主模型均为算术均值。
## 模型求解
运行固定种子代码得到 3.0 unit。
## 模型验证
手算结果与代码一致。
## 敏感性分析
单个输入增加 1 时均值增加 0.2。
## 模型评价
方法简单可解释，但不推广到真实竞赛。
## 结论
Q1 的 Dummy 结果为 3.0 unit。

如图 1 所示为演示趋势。
![图 1：人工趋势，横纵轴单位均为 unit](../figures/trend.svg)

## 参考文献
本 Dummy 不使用外部文献。
## 附录
运行命令见复现文件。
## 支撑材料说明
代码为 code/run.py，主要输出为 results/summary.json。
"""
    (workspace / "paper" / "paper.md").write_text(paper, encoding="utf-8")
    (workspace / "paper" / "main.tex").write_text("\\documentclass{article}\\begin{document}Dummy\\end{document}\n", encoding="utf-8")


@pytest.fixture
def solution_writer():
    return write_solution
