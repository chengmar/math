#!/usr/bin/env python3
"""Execute explicit rejection tests for extreme images and degenerate geometry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

import solve


ROOT = Path(__file__).resolve().parents[1]


def expect_value_error(action, expected_fragment: str) -> dict[str, str]:
    try:
        action()
    except (ValueError, argparse.ArgumentTypeError) as error:
        matched = expected_fragment.lower() in str(error).lower()
        return {
            "status": "pass" if matched else "fail",
            "observed": type(error).__name__,
            "message": str(error),
        }
    except Exception as error:  # pragma: no cover - recorded as an unexpected failure
        return {
            "status": "fail",
            "observed": type(error).__name__,
            "message": str(error),
        }
    return {"status": "fail", "observed": "accepted", "message": "input was not rejected"}


def main() -> int:
    image_path = ROOT / "working/source-extract/docx-unpacked/word/media/<SOURCE_FILE_REDACTED>"
    image = np.asarray(Image.open(image_path).convert("L"))
    dark, bright, threshold = solve.dominant_levels(image)

    four_targets = np.full((768, 1024), 220, dtype=np.uint8)
    yy, xx = np.ogrid[:768, :1024]
    for center_x, center_y in ((200, 200), (400, 200), (200, 450), (400, 450)):
        four_targets[(xx - center_x) ** 2 + (yy - center_y) ** 2 <= 30**2] = 25

    collinear = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])
    rectangle = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])

    checks = {
        "nominal_image_has_five_components": {
            "status": "pass" if len(solve.find_components(image, threshold)[1]) == 5 else "fail",
            "dark_mode": int(dark),
            "bright_mode": int(bright),
            "threshold": float(threshold),
        },
        "all_white_image_rejected": expect_value_error(
            lambda: solve.prepare_observation(np.full((768, 1024), 255, dtype=np.uint8), 127.5),
            "expected five target components",
        ),
        "all_black_image_rejected": expect_value_error(
            lambda: solve.prepare_observation(np.zeros((768, 1024), dtype=np.uint8), 127.5),
            "expected five target components",
        ),
        "four_target_image_rejected": expect_value_error(
            lambda: solve.prepare_observation(four_targets, 127.5),
            "expected five target components",
        ),
        "threshold_below_range_rejected": expect_value_error(
            lambda: solve.prepare_observation(image, -1.0),
            "expected five target components",
        ),
        "threshold_above_range_rejected": expect_value_error(
            lambda: solve.prepare_observation(image, 256.0),
            "expected five target components",
        ),
        "collinear_source_points_rejected": expect_value_error(
            lambda: solve.dlt_homography(collinear, rectangle),
            "source control points are collinear",
        ),
        "invalid_repetition_count_rejected": expect_value_error(
            lambda: solve.monte_carlo_repetitions("1"),
            "--monte-carlo must be at least 2",
        ),
    }
    status = "pass" if all(item["status"] == "pass" for item in checks.values()) else "fail"
    report = {
        "status": status,
        "executed": "pass",
        "checks": checks,
        "formal_mathematical_proof_status": "needs_review",
        "scope": "Input rejection and degenerate-geometry guards; not a proof of model correctness.",
    }
    output = ROOT / "results/extreme_checks.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
