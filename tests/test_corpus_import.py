from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from cumcm_lab.corpus_import import apply_import, plan_import, write_dry_run_lock
from cumcm_lab.corpus_inventory import inventory_corpus


SPLIT = {
    "schema_version": 1,
    "train": {"years": list(range(2003, 2022)), "problem_letter": "A"},
    "dev": {"years": [], "problem_letter": "A"},
    "test": {"years": [2023], "problem_letter": "A"},
    "excluded": {"years": [2022]},
    "out_of_scope": {"before_year": 2003, "after_year": 2023},
    "expected_counts": {"train": 19, "dev": 0, "test": 1, "total": 20},
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layout(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    problems = tmp_path / "intake" / "problems-raw"
    papers = tmp_path / "intake" / "papers-raw"
    problems.mkdir(parents=True)
    papers.mkdir(parents=True)
    paths = {
        "problems_intake": str(problems),
        "papers_intake": str(papers),
        "question_bank": str(tmp_path / "vaults" / "question-bank"),
        "reference_vault": str(tmp_path / "vaults" / "reference-vault"),
        "exam_vault": str(tmp_path / "vaults" / "exam-vault"),
    }
    return problems, papers, paths


def _write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_dry_run_does_not_modify_source(tmp_path: Path) -> None:
    problems, papers, paths = _layout(tmp_path)
    source = _write(problems / "2003年A题" / "题面.pdf", b"synthetic-problem")
    before = (_sha(source), source.stat().st_mtime_ns)
    inventory = inventory_corpus(problems, papers, SPLIT)
    plan = plan_import(inventory, paths)
    lock_path = tmp_path / "reports" / "import-lock.json"
    write_dry_run_lock(plan, lock_path)
    assert (_sha(source), source.stat().st_mtime_ns) == before
    assert plan["status"] == "pass"
    assert not Path(paths["question_bank"]).exists()


def test_apply_preserves_source_and_matches_destination_hash_then_resume(tmp_path: Path) -> None:
    problems, papers, paths = _layout(tmp_path)
    source = _write(problems / "2003年A题" / "题面.pdf", b"synthetic-problem")
    source_hash = _sha(source)
    source_time = source.stat().st_mtime_ns
    inventory = inventory_corpus(problems, papers, SPLIT)
    plan = plan_import(inventory, paths)
    lock_path = tmp_path / "reports" / "import-lock.json"
    write_dry_run_lock(plan, lock_path)

    first = apply_import(inventory, plan, paths, lock_path=lock_path)
    copied = next(item for item in first["results"] if item["action"] == "copy")
    destination = Path(copied["destination"])
    assert copied["result"] == "copied"
    assert _sha(source) == source_hash == _sha(destination)
    assert source.stat().st_mtime_ns == source_time

    resumed = apply_import(inventory, plan, paths, lock_path=lock_path, resume=True)
    reused = next(item for item in resumed["results"] if item["action"] == "copy")
    assert reused["result"] == "reused"
    assert _sha(source) == source_hash == _sha(destination)


def test_duplicate_same_route_is_skipped(tmp_path: Path) -> None:
    problems, papers, paths = _layout(tmp_path)
    _write(problems / "2004年A题" / "附件一.csv", b"same")
    _write(problems / "2004年A题" / "附件二.csv", b"same")
    inventory = inventory_corpus(problems, papers, SPLIT)
    plan = plan_import(inventory, paths)
    actions = [item for item in plan["actions"] if item["case_id"] == "2004A"]
    assert sum(item["action"] == "copy" for item in actions) == 1
    assert sum(item["reason"] == "duplicate_file" for item in actions) == 1


def test_cross_split_duplicate_blocks_apply(tmp_path: Path) -> None:
    problems, papers, paths = _layout(tmp_path)
    _write(problems / "2003年A题" / "题面.pdf", b"same-across-splits")
    _write(problems / "2023年A题" / "题面.pdf", b"same-across-splits")
    inventory = inventory_corpus(problems, papers, SPLIT)
    plan = plan_import(inventory, paths)
    assert plan["status"] == "fail"
    assert len(plan["cross_split_hash_conflicts"]) == 1
    lock_path = tmp_path / "reports" / "import-lock.json"
    write_dry_run_lock(plan, lock_path)
    with pytest.raises(ValueError, match="系统级冲突"):
        apply_import(inventory, plan, paths, lock_path=lock_path)


def test_low_confidence_paper_is_not_copied_or_routed_to_solve(tmp_path: Path) -> None:
    problems, papers, paths = _layout(tmp_path)
    _write(problems / "2005年A题" / "题面.pdf", b"problem")
    _write(papers / "2005" / "ambiguous.bin", b"uncertain-reference")
    inventory = inventory_corpus(problems, papers, SPLIT)
    plan = plan_import(inventory, paths)
    uncertain = next(item for item in inventory["files"] if item["extension"] == ".bin")
    action = next(item for item in plan["actions"] if item["file_id"] == uncertain["file_id"])
    assert uncertain["classification_confidence"] == "low"
    assert action["action"] == "skip"
    assert action["destination"] is None
    assert all("question-bank" not in str(item.get("destination") or "") for item in plan["actions"] if item["source_kind"] == "papers")


def test_2023_title_is_not_in_plan_and_importer_never_writes_knowledge(tmp_path: Path) -> None:
    problems, papers, paths = _layout(tmp_path)
    secret = "人工2023保密论文标题"
    _write(problems / "2023年A题" / "题面.pdf", b"sealed-problem")
    _write(papers / "2023" / f"{secret}.pdf", b"sealed-paper")
    paths["vault_hash_index"] = str(tmp_path / "vaults" / "vault-hashes.json")
    inventory = inventory_corpus(problems, papers, SPLIT)
    plan = plan_import(inventory, paths)
    assert secret not in json.dumps(plan, ensure_ascii=False)
    lock_path = tmp_path / "reports" / "import-lock.json"
    write_dry_run_lock(plan, lock_path)
    result = apply_import(inventory, plan, paths, lock_path=lock_path)
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert not (tmp_path / "knowledge").exists()
    assert all("knowledge" not in str(item.get("destination") or "").casefold() for item in result["results"])
    index = json.loads(Path(paths["vault_hash_index"]).read_text(encoding="utf-8"))
    assert secret not in json.dumps(index, ensure_ascii=False)
    assert len(index["hashes"]) == 1
    assert set(index["hashes"][0]) == {"sha256", "file_id", "split"}
    assert index["hashes"][0]["sha256"] == hashlib.sha256(b"sealed-paper").hexdigest()
    assert index["hashes"][0]["split"] == "test"


def test_apply_rejects_tampered_destination_outside_deterministic_route(tmp_path: Path) -> None:
    problems, papers, paths = _layout(tmp_path)
    _write(problems / "2006年A题" / "题面.pdf", b"problem")
    inventory = inventory_corpus(problems, papers, SPLIT)
    plan = plan_import(inventory, paths)
    lock_path = tmp_path / "reports" / "import-lock.json"
    write_dry_run_lock(plan, lock_path)
    tampered = deepcopy(plan)
    action = next(item for item in tampered["actions"] if item["action"] == "copy")
    forbidden = tmp_path / "trainer" / "knowledge" / "leak.pdf"
    action["destination"] = str(forbidden)
    with pytest.raises(ValueError, match="确定性路由"):
        apply_import(inventory, tampered, paths, lock_path=lock_path)
    assert not forbidden.exists()
