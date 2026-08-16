import json
from pathlib import Path

import yaml

from cumcm_lab.cases import init_case
from cumcm_lab.freeze import freeze_solution, verify_frozen
from cumcm_lab.phases import complete_phase, prepare_phase
from cumcm_lab.regression import run_regression
from cumcm_lab.scoring import score_case
from cumcm_lab.state import load_state, transition
from cumcm_lab.verify import verify_case


def test_dummy_case_complete_lifecycle(lab_factory, solution_writer):
    trainer, _, vault_root = lab_factory()
    case_dir = init_case(trainer, "dummy-a", "dummy", title="人工 Dummy", problem_family="synthetic")
    (case_dir / "input" / "problem" / "problem.md").write_text("计算人工序列 1,2,3,4,5 的均值。", encoding="utf-8")
    (case_dir / "input" / "data" / "values.csv").write_text("value\n1\n2\n3\n4\n5\n", encoding="utf-8")

    solve = prepare_phase(trainer, "dummy-a", "solve")
    solution_writer(solve)
    freeze_solution(case_dir, "blind-v1")
    assert verify_frozen(case_dir, "blind-v1")["status"] == "pass"

    audit = prepare_phase(trainer, "dummy-a", "audit")
    (audit / "audit-report.md").write_text("# 独立审计\n\n未发现阻断性错误。\n", encoding="utf-8")
    (audit / "audit-findings.yaml").write_text("findings: []\n", encoding="utf-8")
    (audit / "reproduction-report.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    (audit / "counterexamples.md").write_text("# 反例\n\n空数据会失效。\n", encoding="utf-8")
    (audit / "revision-plan.md").write_text("# 修订计划\n\n补充边界说明。\n", encoding="utf-8")
    complete_phase(trainer, "dummy-a", "audit")

    revision = prepare_phase(trainer, "dummy-a", "blind-revision")
    solution_writer(revision)
    freeze_solution(case_dir, "blind-final")
    assert verify_frozen(case_dir, "blind-final")["status"] == "pass"

    reference = vault_root / "reference-vault" / "dummy-a" / "demo-reference.md"
    reference.parent.mkdir(parents=True)
    reference.write_text("# 人工参考\n\n仅用于验证受控导入，不含真实竞赛内容。\n", encoding="utf-8")
    meta = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    meta["reference_ids"] = ["dummy-a/demo-reference.md"]
    (case_dir / "case.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")

    reflection = prepare_phase(trainer, "dummy-a", "reflection")
    (reflection / "comparison-matrix.md").write_text("# Demo 比较\n", encoding="utf-8")
    (reflection / "comparison-matrix.yaml").write_text("status: demo\n", encoding="utf-8")
    (reflection / "reference-validation.md").write_text("# 参考验证\n", encoding="utf-8")
    (reflection / "self-gap-analysis.md").write_text("# 差距\n", encoding="utf-8")
    (reflection / "innovation-analysis.md").write_text("# 创新\n\n不声称真实创新。\n", encoding="utf-8")
    lesson_dir = reflection / "lessons-proposed"
    lesson_dir.mkdir()
    (lesson_dir / "demo.yaml").write_text("id: dummy-lesson\nstatus: demo\nprovenance: demo\n", encoding="utf-8")
    complete_phase(trainer, "dummy-a", "reflection")
    transition(case_dir, "knowledge_proposed", command="test dummy proposal", reason="生成 demo 提案")
    assert load_state(case_dir)["state"] == "knowledge_proposed"

    verify_report = verify_case(case_dir)
    assert verify_report["status"] == "pass"
    human_ids = [
        "mathematical_correctness",
        "uncertainty_limitations",
        "fit_to_problem",
        "simplicity_cost",
        "innovation_benefit",
        "interpretability",
        "abstract_quality",
        "structure_logic",
        "notation_explanation",
        "figure_tables",
    ]
    rubric = yaml.safe_load((trainer / "config" / "scoring-rubric.yaml").read_text(encoding="utf-8"))
    weights = {key: item["weight"] for category in rubric["categories"].values() for key, item in category["criteria"].items()}
    judge = {"scores": {criterion: {"score": weights[criterion], "evidence": "Dummy 人工核查证据"} for criterion in human_ids}}
    judge_path = case_dir / "reports" / "judge-scores.yaml"
    judge_path.write_text(yaml.safe_dump(judge, allow_unicode=True), encoding="utf-8")
    scored = score_case(case_dir, trainer)
    assert scored["status"] == "pass"

    baseline = {"cases": {"dummy-a": {"categories": {key: {"score": max(0, value["score"] - 1)} for key, value in scored["categories"].items()}}}}
    (trainer / "benchmarks" / "baseline-scores.yaml").write_text(yaml.safe_dump(baseline), encoding="utf-8")
    assert run_regression(trainer)["status"] == "pass"
