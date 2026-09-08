from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


ARMS = {"A": "arm-a-policy.md", "B": "arm-b-policy.md", "C": "arm-c-policy.md"}
STAGES = {
    "solve": "stage-solve.md",
    "audit": "stage-audit.md",
    "revision-correctness": "stage-revision-correctness.md",
    "revision-paper-verification": "stage-revision-paper-verification.md",
    "continuation": "stage-continuation.md",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_prompt(prompt_root: Path, arm: str, stage: str) -> str:
    parts = [
        prompt_root / "common-contract.md",
        prompt_root / ARMS[arm],
        prompt_root / STAGES[stage],
    ]
    resource_note = {
        "A": "本臂没有项目训练资源；不要尝试定位或读取。",
        "B": "开始前读取 resources/AGENTS.md、resources/skills/cumcm-a-solve/SKILL.md、resources/skills/cumcm-a-audit/SKILL.md 和 resources/paper-template/；这些均为只读冻结副本。",
        "C": "开始前读取 B 组相同冻结资源；仅在 Solve 中额外读取 resources/training-memory/index.yaml，并按索引最多读取 5 张卡。",
    }[arm]
    return "\n\n".join(path.read_text(encoding="utf-8") for path in parts) + "\n\n" + resource_note + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve(strict=True)
    manifest_path = args.manifest.resolve(strict=True)
    runtime_root = args.runtime_root.resolve(strict=False)
    if runtime_root.exists():
        raise SystemExit("runtime root already exists; refusing to overwrite")
    preregistration = repo / "preregistration.yaml"
    prereg_hash_file = repo / "PREREGISTRATION.sha256"
    expected_prereg = prereg_hash_file.read_text(encoding="utf-8-sig").split()[0]
    actual_prereg = sha256_file(preregistration)
    if actual_prereg != expected_prereg:
        raise SystemExit("preregistration hash mismatch")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [
        row
        for row in manifest["files"]
        if row.get("detected_year") == 2022 and row.get("problem_letter") == "A"
    ]
    official_rows = [row for row in rows if row["document_type"] != "reference_paper"]
    reference_rows = [row for row in rows if row["document_type"] == "reference_paper"]
    if len(official_rows) != 8 or len(reference_rows) not in range(2, 5):
        raise SystemExit("protected material count does not match eligibility decision")

    for row in rows:
        source = Path(row["original_path"])
        if not source.is_file() or source.stat().st_size != row["size"] or sha256_file(source) != row["sha256"]:
            raise SystemExit("protected source identity mismatch")

    runtime_root.mkdir(parents=True)
    prompt_root = repo / "evaluation-2022" / "prompts"
    frozen = repo / "final-evaluation" / "frozen-config"
    sorted_official = sorted(official_rows, key=lambda row: (row["document_type"], row["sha256"]))
    role_counts: dict[str, int] = {}
    source_plan = []
    for row in sorted_official:
        role = row["document_type"]
        role_counts[role] = role_counts.get(role, 0) + 1
        extension = row["extension"].lower().lstrip(".")
        if role == "problem_statement":
            generic_name = f"problem-statement.{extension}"
        else:
            generic_name = f"{role.replace('_', '-')}-{role_counts[role]:02d}.{extension}"
        source_plan.append((row, generic_name))

    arm_input_hashes: dict[str, dict[str, str]] = {}
    for arm in ARMS:
        workspace = runtime_root / arm
        input_dir = workspace / "input"
        input_dir.mkdir(parents=True)
        for row, generic_name in source_plan:
            shutil.copy2(Path(row["original_path"]), input_dir / generic_name)
        input_manifest = {
            "case_id": "2022A",
            "files": [
                {
                    "generic_name": generic_name,
                    "document_type": row["document_type"],
                    "size": row["size"],
                    "sha256": row["sha256"],
                }
                for row, generic_name in source_plan
            ],
        }
        write_json(input_dir / "INPUT_MANIFEST.json", input_manifest)
        arm_input_hashes[arm] = {item.name: sha256_file(item) for item in sorted(input_dir.iterdir()) if item.is_file()}

        resources = workspace / "resources"
        resources.mkdir()
        copy_tree(frozen / "paper-template", resources / "paper-template")
        if arm in {"B", "C"}:
            shutil.copy2(frozen / "AGENTS.md", resources / "AGENTS.md")
            (resources / "skills").mkdir()
            copy_tree(frozen / "skills" / "cumcm-a-solve", resources / "skills" / "cumcm-a-solve")
            copy_tree(frozen / "skills" / "cumcm-a-audit", resources / "skills" / "cumcm-a-audit")
        if arm == "C":
            copy_tree(frozen / "training-memory", resources / "training-memory")

        stage_prompts = workspace / "stage-prompts"
        stage_prompts.mkdir()
        for stage in STAGES:
            (stage_prompts / f"{stage}.md").write_text(build_prompt(prompt_root, arm, stage), encoding="utf-8")
        write_json(
            workspace / "arm-state.json",
            {
                "schema_version": 1,
                "arm": arm,
                "status": "prepared_sealed",
                "formal_stages": list(STAGES)[:4],
                "formal_calls_used": 0,
                "technical_retries_used": {},
                "continuations_used": 0,
                "reference_accessed": False,
            },
        )

    if not (arm_input_hashes["A"] == arm_input_hashes["B"] == arm_input_hashes["C"]):
        raise SystemExit("arm input copies are not byte-identical")

    write_json(
        runtime_root / "REFERENCE_SEAL.json",
        {
            "case_id": "2022A",
            "reference_accessed": False,
            "reference_files_copied": 0,
            "reference_count": len(reference_rows),
            "identities": [
                {"material_id": f"R{index:02d}", "size": row["size"], "sha256": row["sha256"]}
                for index, row in enumerate(sorted(reference_rows, key=lambda row: row["sha256"]), 1)
            ],
            "access_rule": "blind_scores_mapping_and_effect_conclusion_must_be_frozen_first",
        },
    )
    write_json(
        runtime_root / "EVALUATION_STATE.json",
        {
            "schema_version": 1,
            "evaluation_case": "2022A",
            "status": "prepared_sealed",
            "eligibility": "eligible_with_metadata_exposure",
            "consumed": True,
            "problem_body_available_only_inside_arms": True,
            "reference_accessed": False,
            "preregistration_sha256": actual_prereg,
            "round_1_order": ["B", "C", "A"],
            "active_arm": None,
        },
    )
    write_json(
        runtime_root / "PREPARATION_REPORT.json",
        {
            "status": "pass",
            "official_input_count": len(official_rows),
            "reference_count_sealed": len(reference_rows),
            "arm_input_hashes_identical": True,
            "arms": list(ARMS),
            "round_1_order": ["B", "C", "A"],
            "preregistration_sha256": actual_prereg,
        },
    )
    print(json.dumps({"status": "pass", "runtime_root": str(runtime_root), "official_inputs": 8}, indent=2))


if __name__ == "__main__":
    main()
