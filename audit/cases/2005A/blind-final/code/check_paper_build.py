"""Record evidence for the PDF produced by the immediately preceding TeX build."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    root = args.workspace.resolve()
    paper = root / "paper"
    results = root / "results"
    pdf = paper / "<SOURCE_FILE_REDACTED>"
    log = paper / "main.log"

    log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    page_matches = re.findall(r"Output written on main\.pdf \((\d+) pages?\)\.", log_text)
    fatal_markers = ["! LaTeX Error", "Undefined control sequence", "Emergency stop", "Fatal error occurred"]
    checks = {
        "pdf_exists_and_signature": "pass" if pdf.is_file() and pdf.read_bytes()[:5] == b"%PDF-" else "fail",
        "log_exists": "pass" if log.is_file() else "fail",
        "page_count_recorded": "pass" if page_matches else "fail",
        "no_fatal_latex_marker": "pass" if not any(marker in log_text for marker in fatal_markers) else "fail",
        "no_overfull_box": "pass" if "Overfull \\hbox" not in log_text and "Overfull \\vbox" not in log_text else "fail",
        "no_undefined_reference": "pass" if "undefined references" not in log_text.lower() and "Citation `" not in log_text else "fail",
        "no_missing_character": "pass" if "Missing character:" not in log_text else "fail",
    }
    build_status = "fail" if "fail" in checks.values() else "pass"
    payload = {
        "schema_version": 2,
        "status": build_status,
        "path": "paper/<SOURCE_FILE_REDACTED>",
        "pages": int(page_matches[-1]) if page_matches else None,
        "length_bytes": pdf.stat().st_size if pdf.is_file() else None,
        "pdf_sha256": sha256(pdf) if pdf.is_file() else None,
        "log_sha256": sha256(log) if log.is_file() else None,
        "compiler": "XeLaTeX + BibTeX + XeLaTeX + XeLaTeX",
        "checks": checks,
        "diagnostic_counts": {
            "latex_warning": log_text.count("LaTeX Warning:"),
            "underfull": log_text.count("Underfull"),
            "overfull": log_text.count("Overfull"),
            "missing_character": log_text.count("Missing character:"),
        },
        "binary_rebuild_hash_stability": "needs_review",
        "note": "当前PDF与日志由本次编译后立即取哈希；TeX元数据可能使跨次二进制哈希变化。",
    }
    results.mkdir(parents=True, exist_ok=True)
    (results / "paper_build.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": build_status, "pages": payload["pages"], "pdf_sha256": payload["pdf_sha256"]}, ensure_ascii=False))
    if build_status == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
