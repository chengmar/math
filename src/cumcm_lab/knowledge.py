from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import now_iso, read_yaml, write_json, write_yaml


SEARCH_DIRS = ("method-cards", "failure-modes", "validation-patterns", "paper-writing", "problem-taxonomy")
TRAINING_MEMORY_REQUIRED_FIELDS = (
    "id",
    "title",
    "type",
    "status",
    "source_cases",
    "source_kind",
    "applicable_conditions",
    "inapplicable_conditions",
    "minimum_baseline",
    "recommended_action",
    "why_it_may_help",
    "required_validation",
    "failure_modes",
    "counterexamples",
    "complexity_cost",
    "evidence_summary",
    "last_updated",
)
QUERY_FIELDS = (
    "problem_family",
    "task_type",
    "data_type",
    "model_family",
    "objective",
    "constraints",
    "validation_needed",
    "failure_modes",
)


def _tokens(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value)
    return {item.casefold() for item in re.findall(r"[\w\-\u4e00-\u9fff]+", text)}


def retrieve_knowledge(
    knowledge_root: Path,
    query: dict[str, Any],
    *,
    phase: str = "solve",
    limit: int = 5,
    log_path: Path | None = None,
) -> list[dict[str, Any]]:
    limit = min(max(int(limit), 1), 5)
    allowed_statuses = {"machine_verified", "verified"} if phase in {"solve", "evaluation"} else {"machine_verified", "verified", "candidate", "demo"}
    query_tokens = set().union(*(_tokens(query.get(field)) for field in QUERY_FIELDS))
    scored: list[dict[str, Any]] = []
    for directory in SEARCH_DIRS:
        base = knowledge_root / directory
        if not base.exists():
            continue
        for path in sorted([*base.rglob("*.yaml"), *base.rglob("*.yml")]):
            card = read_yaml(path)
            if not isinstance(card, dict) or card.get("status") not in allowed_statuses:
                continue
            searchable = {
                "title": card.get("title"),
                "tags": card.get("tags"),
                "problem_families": card.get("problem_families"),
                "recommended_method": card.get("recommended_method"),
                "required_validation": card.get("required_validation"),
                "common_failures": card.get("common_failures"),
            }
            card_tokens = set().union(*(_tokens(value) for value in searchable.values()))
            matches = sorted(query_tokens & card_tokens)
            score = len(matches)
            if query_tokens and score == 0:
                continue
            scored.append(
                {
                    "id": card.get("id"),
                    "title": card.get("title"),
                    "status": card.get("status"),
                    "score": score,
                    "matched_terms": matches,
                    "match_reason": "匹配标签/任务/模型/验证关键词" if matches else "空查询下的合格卡片",
                    "path": str(path),
                }
            )
    results = sorted(scored, key=lambda item: (-item["score"], str(item["id"])))[:limit]
    if log_path:
        write_json(
            log_path,
            {
                "retrieved_at": now_iso(),
                "phase": phase,
                "query": query,
                "limit": limit,
                "cards": results,
            },
        )
    return results


def _case_year(value: Any) -> int | None:
    match = re.fullmatch(r"(\d{4})A", str(value or "").strip(), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def retrieve_training_memory(
    knowledge_root: Path,
    query: dict[str, Any],
    *,
    case_id: str,
    phase: str = "solve",
    limit: int = 5,
    log_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Retrieve provisional cross-case memory for later *training* solves only.

    This is deliberately separate from verified knowledge retrieval.  A
    provisional card never becomes verified or machine_verified merely because
    it is copied into a later training workspace.
    """

    year = _case_year(case_id)
    policy_path = knowledge_root.parent / "config" / "training-memory-policy.yaml"
    policy = read_yaml(policy_path) if policy_path.is_file() else {}
    first_year = int(policy.get("first_allowed_year", 2011))
    final_year = int(policy.get("final_allowed_year", 2021))
    configured_limit = int(policy.get("max_cards_per_solve", 5))
    limit = min(max(int(limit), 0), configured_limit, 5)
    eligible_phase = phase == "solve"
    eligible_case = year is not None and first_year <= year <= final_year
    query_tokens = set().union(*(_tokens(query.get(field)) for field in QUERY_FIELDS))
    query_tokens |= _tokens(query.get("profile_tags"))
    scored: list[dict[str, Any]] = []
    invalid_cards: list[dict[str, Any]] = []

    card_root = knowledge_root / "training-memory" / "cards"
    if eligible_phase and eligible_case and limit and card_root.is_dir():
        for path in sorted([*card_root.glob("*.yaml"), *card_root.glob("*.yml")]):
            card = read_yaml(path)
            missing = [field for field in TRAINING_MEMORY_REQUIRED_FIELDS if not card.get(field)] if isinstance(card, dict) else list(TRAINING_MEMORY_REQUIRED_FIELDS)
            if missing:
                invalid_cards.append({"path": path.name, "reason": "missing_fields", "fields": missing})
                continue
            if card.get("status") != "provisional_training":
                continue
            source_years = [_case_year(item) for item in card.get("source_cases", [])]
            if not source_years or any(item is None or item >= year for item in source_years):
                invalid_cards.append({"path": path.name, "reason": "source_not_earlier"})
                continue
            searchable = {
                "title": card.get("title"),
                "tags": card.get("tags"),
                "type": card.get("type"),
                "applicable_conditions": card.get("applicable_conditions"),
                "recommended_action": card.get("recommended_action"),
                "required_validation": card.get("required_validation"),
                "failure_modes": card.get("failure_modes"),
            }
            card_tokens = set().union(*(_tokens(value) for value in searchable.values()))
            matches = sorted(query_tokens & card_tokens)
            score = len(matches)
            if query_tokens and score == 0:
                continue
            scored.append(
                {
                    "id": card["id"],
                    "title": card["title"],
                    "status": card["status"],
                    "score": score,
                    "matched_terms": matches,
                    "match_reason": "匹配问题类型/模型/约束/验证标签" if matches else "无标签时的通用卡片",
                    "source_cases": list(card.get("source_cases", [])),
                    "path": str(path),
                }
            )

    results = sorted(scored, key=lambda item: (-item["score"], str(item["id"])))[:limit]
    if log_path:
        write_json(
            log_path,
            {
                "retrieved_at": now_iso(),
                "case_id": case_id,
                "phase": phase,
                "status": "provisional_training",
                "query": query,
                "limit": limit,
                "eligible": eligible_phase and eligible_case,
                "cards": results,
                "invalid_cards": invalid_cards,
                "policy": {
                    "first_allowed_year": first_year,
                    "final_allowed_year": final_year,
                    "only_earlier_source_years": True,
                    "not_verified": True,
                },
            },
        )
    return results


def promote_lesson(
    knowledge_root: Path,
    candidate_path: Path,
    *,
    human_approved: bool = False,
    approved_by: str | None = None,
) -> dict[str, Any]:
    card = read_yaml(candidate_path)
    if not isinstance(card, dict) or card.get("status") != "candidate":
        raise ValueError("只能升级 status=candidate 的知识卡。")
    if card.get("provenance") == "demo":
        raise ValueError("demo 卡片不得升级为真实 verified 知识。")
    cases = card.get("source_cases") or []
    positive = [case for case in cases if isinstance(case, dict) and case.get("outcome") == "positive"]
    case_ids = {case.get("case_id") for case in positive if case.get("case_id")}
    variant_groups = {case.get("variant_group") for case in positive if case.get("variant_group")}
    checks = {
        "two_independent_cases": len(case_ids) >= 2 and len(variant_groups) >= 2,
        "positive_evidence": len(positive) >= 2 and bool(card.get("evidence")),
        "applicable_conditions": bool(card.get("applicable_conditions")),
        "inapplicable_conditions": bool(card.get("inapplicable_conditions")),
        "counterexamples": bool(card.get("counterexamples")),
        "regression_passed": card.get("regression_status") == "pass",
        "human_approval": bool(human_approved and approved_by),
    }
    proposal = {
        "card_id": card.get("id"),
        "created_at": now_iso(),
        "source": str(candidate_path),
        "checks": checks,
        "status": "approved" if all(checks.values()) else "needs_review",
    }
    proposal_path = knowledge_root / "promotion-proposals" / f"{card.get('id')}-proposal.yaml"
    write_yaml(proposal_path, proposal)
    if not all(value for key, value in checks.items() if key != "human_approval"):
        raise ValueError(f"升级条件不足，已生成提案：{proposal_path}")
    if not checks["human_approval"]:
        return proposal
    promoted = dict(card)
    promoted["status"] = "verified"
    promoted["approved_by"] = approved_by
    promoted["updated_at"] = now_iso()
    destination = knowledge_root / "method-cards" / f"{card['id']}.yaml"
    if destination.exists():
        raise FileExistsError(f"verified 卡片已存在，拒绝覆盖：{destination}")
    write_yaml(destination, promoted)
    proposal["verified_path"] = str(destination)
    write_yaml(proposal_path, proposal)
    return proposal


def machine_verify_lesson(knowledge_root: Path, candidate_path: Path) -> dict[str, Any]:
    """Promote a candidate only after strict cross-year, leakage-free evidence."""
    card = read_yaml(candidate_path)
    if not isinstance(card, dict) or card.get("status") != "candidate":
        raise ValueError("只能机器验证 status=candidate 的知识卡。")
    if card.get("provenance") == "demo":
        raise ValueError("demo 卡片不得升级为 machine_verified。")

    source_cases = [item for item in card.get("source_cases", []) if isinstance(item, dict)]
    origin_year = card.get("origin_year")
    validations = [
        item
        for item in source_cases
        if item.get("role") == "shadow_validation"
        and item.get("outcome") == "positive"
        and isinstance(item.get("year"), int)
        and (not isinstance(origin_year, int) or item["year"] > origin_year)
    ]
    case_ids = {item.get("case_id") for item in validations if item.get("case_id")}
    families = {item.get("problem_family") for item in validations if item.get("problem_family")}
    checks = {
        "two_later_cases": len(case_ids) >= 2,
        "different_problem_families": len(families) >= 2,
        "core_metric_improved": all(item.get("core_metric_improved") is True for item in validations) and bool(validations),
        "accuracy_not_degraded": all(item.get("accuracy_not_degraded") is True for item in validations) and bool(validations),
        "paper_not_degraded": all(item.get("paper_not_degraded") is True for item in validations) and bool(validations),
        "complexity_justified": all(item.get("complexity_justified") is True for item in validations) and bool(validations),
        "leakage_free": all(item.get("leakage_status") == "pass" for item in validations) and bool(validations),
        "independent_review": card.get("independent_review_status") == "pass",
        "regression_passed": card.get("regression_status") == "pass",
        "applicable_conditions": bool(card.get("applicable_conditions")),
        "inapplicable_conditions": bool(card.get("inapplicable_conditions")),
        "failure_cases": bool(card.get("failure_cases")),
        "counterexamples": bool(card.get("counterexamples")),
    }
    proposal = {
        "card_id": card.get("id"),
        "created_at": now_iso(),
        "source": str(candidate_path),
        "verification_type": "machine",
        "label": "机器验证",
        "checks": checks,
        "status": "approved" if all(checks.values()) else "needs_review",
    }
    proposal_path = knowledge_root / "promotion-proposals" / f"{card.get('id')}-machine-proposal.yaml"
    write_yaml(proposal_path, proposal)
    if not all(checks.values()):
        raise ValueError(f"机器验证条件不足，已生成提案：{proposal_path}")

    promoted = dict(card)
    promoted["status"] = "machine_verified"
    promoted["verification_kind"] = "machine"
    promoted["verification_label"] = "机器验证"
    promoted["updated_at"] = now_iso()
    destination = knowledge_root / "method-cards" / f"{card['id']}.yaml"
    if destination.exists():
        raise FileExistsError(f"知识卡已存在，拒绝覆盖：{destination}")
    write_yaml(destination, promoted)
    proposal["machine_verified_path"] = str(destination)
    write_yaml(proposal_path, proposal)
    return proposal
