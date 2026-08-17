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
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--reasoning", "--reasoning-effort", dest="reasoning_effort", choices=["low", "medium", "high", "xhigh"], default="xhigh")
    parser.add_argument("--case-id", action="append", default=[], help="队列不存在时据此创建；可重复")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--max-retries", type=int, choices=[1], default=1)
    parser.add_argument("--stop-after-phase", choices=TRAIN_PHASES)
    parser.add_argument("--all", action="store_true", help="显式运行队列中全部可运行案例（默认行为）")
    parser.add_argument("--dry-run", action="store_true", help="只验证并显示队列，不启动任何阶段")
    parser.add_argument("--resume", action="store_true", help="从持久断点恢复并清理失效锁")
    parser.add_argument("--detach", action="store_true", help="以可核验的独立后台进程启动")
    args = parser.parse_args()
    try:
        if args.detach:
            child_args = [value for value in sys.argv[1:] if value != "--detach"]
            log_dir = args.runtime_dir
            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_path = log_dir / "autopilot-stdout.log"
            stderr_path = log_dir / "autopilot-stderr.log"
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
                process = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), *child_args],
                    cwd=str(trainer),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=creationflags,
                )
            time.sleep(1.0)
            if process.poll() is not None:
                raise RuntimeError(f"后台进程提前退出：{process.returncode}")
            print(json.dumps({"status": "running", "pid": process.pid, "stdout": str(stdout_path), "stderr": str(stderr_path)}, ensure_ascii=False, indent=2))
            return 0
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
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        runner = resume_autopilot if args.resume else run_autopilot
        result = runner(args.queue, args.runtime_dir, executor, max_cases=args.max_cases, stop_after_phase=args.stop_after_phase)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {"completed", "completed_with_blocks", "checkpointed", "stopped"} else 1
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
