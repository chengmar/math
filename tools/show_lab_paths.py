from __future__ import annotations

import json
import sys
from pathlib import Path


TRAINER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TRAINER_ROOT / "src"))

from cumcm_lab.util import load_lab_paths  # noqa: E402


if __name__ == "__main__":
    try:
        print(json.dumps(load_lab_paths(TRAINER_ROOT), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        print(f"path loading failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
