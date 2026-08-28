from __future__ import annotations

from cumcm_lab import corpus_validate
from cumcm_lab.autopilot import run_autopilot
from cumcm_lab.corpus_import import apply_import, plan_import, write_dry_run_lock
from cumcm_lab.corpus_inventory import inventory_corpus
from cumcm_lab.corpus_validate import validate_corpus
from cumcm_lab.training_queue import create_training_queue, load_training_queue, seal_final_test


def test_dummy_corpus_inventory_apply_validate_and_queue(tmp_path, monkeypatch):
    problems = tmp_path / "intake" / "problems-raw"
    papers = tmp_path / "intake" / "papers-raw"
    for path, body in (
        (problems / "2003年A题" / "题面.pdf", b"dummy-train-problem"),
        (problems / "2003年A题" / "附件.csv", b"x,y\n1,2\n"),
        (papers / "2003" / "论文.pdf", b"dummy-train-reference"),
        (problems / "2023年A题" / "题面.pdf", b"dummy-sealed-problem"),
        (papers / "2023" / "保密.pdf", b"dummy-sealed-reference"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    config = {
        "schema_version": 1,
        "train": {"years": [2003], "problem_letter": "A"},
        "dev": {"years": [], "problem_letter": "A"},
        "test": {"years": [2023], "problem_letter": "A"},
        "excluded": {"years": [2022]},
        "out_of_scope": {"before_year": 2003, "after_year": 2023},
        "expected_counts": {"train": 1, "dev": 0, "test": 1, "total": 2},
    }
    paths = {
        "problems_intake": str(problems),
        "papers_intake": str(papers),
        "question_bank": str(tmp_path / "vault" / "question-bank"),
        "reference_vault": str(tmp_path / "vault" / "reference-vault"),
        "exam_vault": str(tmp_path / "vault" / "exam-vault"),
        "vault_hash_index": str(tmp_path / "vault" / "vault-hashes.json"),
    }
    inventory = inventory_corpus(problems, papers, config)
    plan = plan_import(inventory, paths)
    assert plan["status"] == "pass"
    lock = tmp_path / "reports" / "import-lock.json"
    write_dry_run_lock(plan, lock)
    result = apply_import(inventory, plan, paths, lock_path=lock)
    seal_final_test(
        tmp_path / "vault" / "exam-vault" / "2023A" / "SEALED.json",
        [item for item in inventory["files"] if item.get("split") == "test"],
    )
    trainer = tmp_path / "trainer"
    trainer.mkdir()
    monkeypatch.setattr(corpus_validate, "_git_files", lambda _root: [])
    validation = validate_corpus(inventory, paths, config, trainer_root=trainer, import_result=result)
    assert validation["status"] == "pass"

    queue_path = tmp_path / "runtime" / "queue.json"
    create_training_queue(["2003A"], queue_path)
    calls = []

    def fake(case_id, phase, attempt, run_dir):
        calls.append((case_id, phase, attempt))
        return {"status": "pass"}

    assert run_autopilot(queue_path, tmp_path / "runtime", fake)["status"] == "completed"
    assert {case_id for case_id, _, _ in calls} == {"2003A"}
    assert load_training_queue(queue_path)["items"][0]["status"] == "completed"
