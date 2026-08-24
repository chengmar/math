from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .util import iter_regular_files, now_iso, write_json


FORBIDDEN_TOKENS = {"reference-vault", "exam-vault", "problems-raw", "papers-raw"}


def inspect_stage_workspace(workspace: Path, *, phase: str, case_id: str) -> dict[str, Any]:
    """Verify the copy-only stage boundary without overstating OS-level denial."""

    workspace = Path(workspace).resolve()
    violations: list[str] = []
    for path in iter_regular_files(workspace):
        normalized = path.relative_to(workspace).as_posix().casefold()
        if any(token in normalized for token in FORBIDDEN_TOKENS):
            violations.append(normalized)
        if "2023a" in normalized and case_id.upper() != "2023A":
            violations.append(normalized)
        if phase != "reflection" and "approved-references" in normalized:
            violations.append(normalized)
    return {
        "status": "pass" if not violations else "fail",
        "phase": phase,
        "case_id": case_id,
        "workspace": str(workspace),
        "violations": sorted(set(violations)),
    }


def sentinel_report(
    probes: Iterable[dict[str, Any]],
    *,
    codex_probe_executed: bool,
    codex_probe_results: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    workspace_probes = list(probes)
    codex_results = list(codex_probe_results)
    physical_fail = any(item.get("status") == "fail" for item in workspace_probes)
    if physical_fail:
        overall = "fail"
    elif not codex_probe_executed:
        overall = "needs_review"
    elif any(item.get("status") != "pass" for item in codex_results):
        overall = "fail"
    else:
        overall = "pass"
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "status": overall,
        "workspace_copy_isolation": workspace_probes,
        "codex_absolute_path_denial": {
            "status": (
                "needs_review"
                if not codex_probe_executed
                else ("pass" if codex_results and all(item.get("status") == "pass" for item in codex_results) else "fail")
            ),
            "executed": codex_probe_executed,
            "results": codex_results,
            "reason": None if codex_probe_executed else "isolated_codex_auth_missing_or_probe_not_run",
        },
    }


def write_sentinel_report(report: dict[str, Any], path: Path) -> Path:
    write_json(path, report)
    return path
