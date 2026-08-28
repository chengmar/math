from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"

TRACKED = [
    RESULTS / "summary.json",
    RESULTS / "paper_numbers.json",
    RESULTS / "<SOURCE_FILE_REDACTED>",
    RESULTS / "<SOURCE_FILE_REDACTED>",
    RESULTS / "<SOURCE_FILE_REDACTED>",
    RESULTS / "<SOURCE_FILE_REDACTED>",
    RESULTS / "<SOURCE_FILE_REDACTED>",
    FIGURES / "<SOURCE_FILE_REDACTED>",
    FIGURES / "<SOURCE_FILE_REDACTED>",
    FIGURES / "<SOURCE_FILE_REDACTED>",
    FIGURES / "<SOURCE_FILE_REDACTED>",
]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def snapshot() -> dict[str, str]:
    missing = [str(path) for path in TRACKED if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing deterministic outputs: " + ", ".join(missing))
    return {str(path.relative_to(ROOT)): digest(path) for path in TRACKED}


def main() -> None:
    before = snapshot()
    completed = subprocess.run(
        [sys.executable, str(ROOT / "code" / "solve.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    after = snapshot()
    comparisons = {
        path: {"before": before[path], "after": after[path], "status": "pass" if before[path] == after[path] else "fail"}
        for path in before
    }
    overall = "pass" if completed.returncode == 0 and all(item["status"] == "pass" for item in comparisons.values()) else "fail"
    payload = {
        "status": overall,
        "rerun_return_code": completed.returncode,
        "tracked_file_count": len(TRACKED),
        "comparisons": comparisons,
        "stderr": completed.stderr,
    }
    (RESULTS / "reproducibility_check.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if overall == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
