from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TRAINER_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cumcm_lab.session_runner import run_stage_session  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one isolated Codex stage session with auditable outputs.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, help="UTF-8 prompt file; omit to read the prompt from stdin.")
    parser.add_argument("--codex-home", type=Path, default=os.environ.get("CODEX_HOME"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"), default="max")
    parser.add_argument("--input-file", type=Path, action="append", default=[])
    parser.add_argument("--executable", default="codex")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.codex_home is None:
        parser.error("必须通过 --codex-home 或 CODEX_HOME 指定隔离配置目录。")
    if args.prompt_file:
        try:
            prompt = args.prompt_file.read_text(encoding="utf-8-sig")
        except OSError as exc:
            print(f"prompt read failed: {exc}", file=sys.stderr)
            return 2
        input_files = [args.prompt_file, *args.input_file]
    else:
        if sys.stdin.isatty():
            parser.error("未提供 --prompt-file 时必须从 stdin 传入提示词。")
        prompt = sys.stdin.read()
        input_files = list(args.input_file)

    try:
        result = run_stage_session(
            workspace=args.workspace,
            run_root=args.run_root,
            prompt=prompt,
            codex_home=args.codex_home,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            input_files=input_files,
            executable=args.executable,
        )
    except (OSError, ValueError) as exc:
        print(f"session setup failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "completed":
        return 0
    if result["status"] == "blocked":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
