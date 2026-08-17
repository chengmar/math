from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.git_guard import inspect_git_tree  # noqa: E402


def _hashes(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    records = payload.get("files", payload.get("hashes", [])) if isinstance(payload, dict) else []
    return [str(item.get("sha256")) for item in records if isinstance(item, dict) and item.get("sha256")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail closed if Git candidates contain real corpus or binary material.")
    parser.add_argument("--trainer-root", type=Path, default=TRAINER_ROOT)
    parser.add_argument("--manifest", type=Path, default=TRAINER_ROOT / "corpus" / "corpus-manifest.json")
    parser.add_argument("--tracked-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inspect_git_tree(
            args.trainer_root,
            real_hashes=_hashes(args.manifest),
            include_untracked=not args.tracked_only,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"git corpus guard failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
