from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

trainer = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(trainer / "src"))

from cumcm_lab.training_queue import (
    CONSUME_CONFIRMATION,
    consume_final_test,
    read_final_test_seal,
    seal_final_test,
)
from cumcm_lab.util import load_lab_paths


def _records(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        records = payload.get("files", payload.get("records"))
        if isinstance(records, list):
            return records
    raise ValueError("records JSON 必须是数组，或含 files/records 数组的对象。")


def main() -> int:
    parser = argparse.ArgumentParser(description="封存或显式消费 2023A 最终测试")
    parser.add_argument("--seal-file", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--records", type=Path, required=True)
    sub.add_parser("status")
    consume = sub.add_parser("consume")
    consume.add_argument(
        "--confirmation",
        required=True,
        help=f"不可逆消费必须精确传入：{CONSUME_CONFIRMATION}",
    )
    args = parser.parse_args()
    if args.seal_file is None:
        args.seal_file = Path(load_lab_paths(trainer)["exam_vault"]) / "2023A" / "SEALED.json"
    try:
        if args.command == "seal":
            result = seal_final_test(args.seal_file, _records(args.records))
        elif args.command == "consume":
            result = consume_final_test(args.seal_file, args.confirmation)
        else:
            result = read_final_test_seal(args.seal_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
