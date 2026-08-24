from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

trainer = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(trainer / "src"))

from cumcm_lab.autopilot import CodexPhaseExecutor, resume_autopilot, run_autopilot
from cumcm_lab.training_queue import (
    TRAIN_PHASES,
    create_training_queue,
    load_training_queue,
    next_runnable_item,
    queue_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 CUMCM A 题自动训练队列")
    parser.add_argument("--queue", type=Path, default=trainer / "runtime" / "training-queue-state.json")
    parser.add_argument("--runtime-dir", type=Path, default=trainer / "runtime")
    parser.add_argument("--trainer-root", type=Path, default=trainer)
    parser.add_argument("--codex-home", type=Path, default=trainer.parent / "codex-home")
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning", "--reasoning-effort", dest="reasoning_effort", choices=["low", "medium", "high", "xhigh", "max"], default="max")
    parser.add_argument("--case-id", action="append", default=[], help="队列不存在时据此创建；可重复")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--max-retries", type=int, choices=[1], default=1)
    parser.add_argument("--stop-after-phase", choices=TRAIN_PHASES)
    parser.add_argument("--all", action="store_true", help="显式运行队列中全部可运行案例（默认行为）")
    parser.add_argument("--dry-run", action="store_true", help="只验证并显示队列，不启动任何阶段")
    parser.add_argument("--resume", action="store_true", help="从持久断点恢复并清理失效锁")
    parser.add_argument("--detach", action="store_true", help="已暂停：正式训练只允许前台执行")
    args = parser.parse_args()
    try:
        if args.detach:
            raise RuntimeError("后台 Autopilot 已暂停；请使用前台训练队列。")
        if args.all and args.case_id:
            raise ValueError("--all 与 --case-id 不能同时使用。")
        if not args.queue.exists():
            if not args.case_id:
                raise FileNotFoundError(f"队列不存在且未提供 --case-id：{args.queue}")
            create_training_queue(args.case_id, args.queue)
        elif args.case_id:
            if len(args.case_id) != 1:
                raise ValueError("已有队列上 --case-id 只能指定当前一个案例。")
            current = next_runnable_item(load_training_queue(args.queue))
            requested = args.case_id[0].strip().upper()
            if current is None or current["case_id"] != requested:
                actual = None if current is None else current["case_id"]
                raise ValueError(f"不能跳过队列顺序执行 {requested}；当前应为 {actual}。")
            if args.max_cases not in {None, 1}:
                raise ValueError("已有队列上 --case-id 与 --max-cases 仅允许 1。")
            args.max_cases = 1
        if args.dry_run:
            print(json.dumps({"status": "pass", "dry_run": True, "queue": queue_summary(load_training_queue(args.queue))}, ensure_ascii=False, indent=2))
            return 0
        executor = CodexPhaseExecutor(
            args.trainer_root,
            args.codex_home,
            codex_command=args.codex_command,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        runner = resume_autopilot if args.resume else run_autopilot
        result = runner(args.queue, args.runtime_dir, executor, max_cases=args.max_cases, stop_after_phase=args.stop_after_phase)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {
            "completed",
            "completed_with_blocks",
            "checkpointed",
            "stopped",
            "resumable_after_quota_reset",
        } else 1
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
