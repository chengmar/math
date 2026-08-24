from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.training_queue import (  # noqa: E402
    create_training_queue,
    load_training_queue,
    read_final_test_seal,
    write_public_queue_plan,
)
from cumcm_lab.util import read_json  # noqa: E402
from cumcm_lab.util import load_lab_paths  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the fixed 2003A-2021A queue from corpus validation.")
    parser.add_argument("--validation", type=Path, default=TRAINER_ROOT / "corpus" / "corpus-validation.json")
    parser.add_argument("--queue", type=Path, default=TRAINER_ROOT / "runtime" / "training-queue-state.json")
    parser.add_argument("--public-plan", type=Path, default=TRAINER_ROOT / "corpus" / "training-queue.yaml")
    parser.add_argument("--seal-file", type=Path)
    args = parser.parse_args(argv)
    try:
        seal_file = args.seal_file or (Path(load_lab_paths(TRAINER_ROOT)["exam_vault"]) / "2023A" / "SEALED.json")
        report = read_json(args.validation)
        if report.get("status") == "fail":
            raise ValueError("corpus validation 含系统级 fail，拒绝创建训练队列。")
        statuses = report.get("case_statuses") or []
        case_ids = [str(item["case_id"]) for item in statuses]
        expected = [f"{year}A" for year in range(2003, 2022)]
        if case_ids != expected:
            raise ValueError("validation 未提供严格升序的 19 个训练案例。")
        blocked = {
            str(item["case_id"]): "import_blocked"
            for item in statuses
            if item.get("status") != "ready"
        }
        if args.queue.exists():
            queue = load_training_queue(args.queue)
            if [item["case_id"] for item in queue["items"]] != expected:
                raise ValueError("已有本机队列与固定 19 年划分不一致，拒绝覆盖。")
        else:
            queue = create_training_queue(case_ids, args.queue, blocked=blocked)
        seal = read_final_test_seal(seal_file)
        plan = write_public_queue_plan(queue, args.public_plan, final_test_status=str(seal["status"]))
    except (OSError, ValueError) as exc:
        print(f"queue initialization failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "pass",
                "runtime_queue": str(args.queue),
                "public_plan": str(args.public_plan),
                "train_cases": len(plan["items"]),
                "blocked_cases": sorted(blocked),
                "final_test": plan["final_test"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
