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

from cumcm_lab.autopilot import CodexPhaseExecutor, resume_autopilot
from cumcm_lab.training_queue import TRAIN_PHASES


def main() -> int:
    parser = argparse.ArgumentParser(description="从安全断点恢复 CUMCM A 题自动训练队列")
    parser.add_argument("--queue", type=Path, default=trainer / "runtime" / "training-queue-state.json")
    parser.add_argument("--runtime-dir", type=Path, default=trainer / "runtime")
    parser.add_argument("--trainer-root", type=Path, default=trainer)
    parser.add_argument("--codex-home", type=Path, default=trainer.parent / "codex-home")
    parser.add_argument("--codex-command", default="codex")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning", "--reasoning-effort", dest="reasoning_effort", choices=["low", "medium", "high", "xhigh", "max"], default="max")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--max-retries", type=int, choices=[1], default=1)
    parser.add_argument("--stop-after-phase", choices=TRAIN_PHASES)
    args = parser.parse_args()
    try:
        executor = CodexPhaseExecutor(
            args.trainer_root,
            args.codex_home,
            codex_command=args.codex_command,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        result = resume_autopilot(
            args.queue,
            args.runtime_dir,
            executor,
            max_cases=args.max_cases,
            stop_after_phase=args.stop_after_phase,
        )
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
