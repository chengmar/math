"""Parse the three blind input attachments into auditable tidy tables.

The script never edits the input workbooks.  It consumes the UTF-8 TSV
snapshots created by ``extract_xls.ps1`` and writes only below ``results``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


SEED = 20240824
SCORE_NAMES = [
    "appearance_clarity",
    "appearance_tone",
    "aroma_purity",
    "aroma_intensity",
    "aroma_quality",
    "taste_purity",
    "taste_intensity",
    "taste_persistence",
    "taste_quality",
    "balance_overall",
]
SCORE_MAXIMA = np.array([5, 10, 6, 8, 16, 6, 8, 8, 22, 11], dtype=float)
SAMPLE_RE = re.compile(r"(?:葡萄酒|葡酒萄|酒)样品\s*(\d+)")
GRAPE_SAMPLE_RE = re.compile(r"葡萄样品\s*(\d+)")
WINE_SAMPLE_RE = re.compile(r"酒样品\s*(\d+)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u3000", " ").strip()
    return re.sub(r"\s+", " ", text)


def read_tsv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = [list(row) for row in csv.reader(handle, delimiter="\t")]
    width = max(len(row) for row in rows)
    return [row + [""] * (width - len(row)) for row in rows]


def numeric(value: str) -> float:
    text = normalize_text(value)
    if not text:
        return np.nan
    try:
        return float(text)
    except ValueError:
        return np.nan


def locate_sheets(extracted_dir: Path) -> tuple[dict[str, Path], list[dict[str, Any]]]:
    manifest_path = extracted_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_sheet: dict[str, Path] = {}
    for item in manifest:
        path = extracted_dir / item["extracted_file"]
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256(path)
        if actual != item["extracted_sha256"]:
            raise RuntimeError(f"Extracted hash mismatch for {path.name}")
        by_sheet[item["sheet"]] = path
    return by_sheet, manifest


def parse_tasting_sheet(path: Path, sheet_name: str) -> tuple[pd.DataFrame, list[str]]:
    rows = read_tsv(path)
    color = "red" if "红" in sheet_name else "white"
    panel = 1 if "第一组" in sheet_name else 2

    global_rater_start: int | None = None
    anchors: list[tuple[int, int, int]] = []
    for row_index, row in enumerate(rows):
        rater_columns = [
            column_index
            for column_index, cell in enumerate(row)
            if re.search(r"品酒员\s*\d+", normalize_text(cell))
        ]
        if len(rater_columns) >= 8:
            global_rater_start = min(rater_columns)
        for cell in row:
            match = SAMPLE_RE.search(normalize_text(cell))
            if match:
                anchors.append((row_index, int(match.group(1)), min(rater_columns) if len(rater_columns) >= 8 else -1))
                break

    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for anchor_pos, (anchor_row, sample_id, local_start) in enumerate(anchors):
        rater_start = local_start if local_start >= 0 else global_rater_start
        if rater_start is None:
            raise RuntimeError(f"No rater columns found in {sheet_name}")
        next_anchor = anchors[anchor_pos + 1][0] if anchor_pos + 1 < len(anchors) else len(rows)
        scored_rows: list[tuple[int, np.ndarray]] = []
        for row_index in range(anchor_row, next_anchor):
            values = np.array(
                [numeric(rows[row_index][column]) for column in range(rater_start, rater_start + 10)],
                dtype=float,
            )
            if np.isfinite(values).sum() >= 8:
                scored_rows.append((row_index, values))
            if len(scored_rows) == 10:
                break
        if len(scored_rows) != 10:
            raise RuntimeError(
                f"{sheet_name} sample {sample_id}: expected 10 scoring rows, found {len(scored_rows)}"
            )
        score_matrix = np.vstack([item[1] for item in scored_rows])
        invalid_mask = (~np.isfinite(score_matrix)) | (score_matrix < 0) | (
            score_matrix > SCORE_MAXIMA[:, None]
        )
        if invalid_mask.any():
            for item_index, rater_index in np.argwhere(invalid_mask):
                original = score_matrix[item_index, rater_index]
                valid_row = (
                    np.isfinite(score_matrix[item_index])
                    & (score_matrix[item_index] >= 0)
                    & (score_matrix[item_index] <= SCORE_MAXIMA[item_index])
                )
                observed = score_matrix[item_index, valid_row]
                if observed.size < 8:
                    raise RuntimeError(
                        f"{sheet_name} sample {sample_id}: too few valid values to repair item {item_index + 1}"
                    )
                replacement = float(np.median(observed))
                score_matrix[item_index, rater_index] = replacement
                reason = "missing" if not np.isfinite(original) else f"outside [0,{SCORE_MAXIMA[item_index]:g}] (raw={original:g})"
                warnings.append(
                    f"{sheet_name}: sample {sample_id}, rater {rater_index + 1}, "
                    f"item {SCORE_NAMES[item_index]} {reason}; repaired by within-sample item median {replacement:g}"
                )
        if np.any(score_matrix < 0) or np.any(score_matrix > SCORE_MAXIMA[:, None]):
            raise RuntimeError(f"{sheet_name} sample {sample_id}: score outside item bounds")
        for rater in range(10):
            record: dict[str, Any] = {
                "color": color,
                "panel": panel,
                "sample_id": sample_id,
                "rater": rater + 1,
                "repaired_item_count": int(invalid_mask[:, rater].sum()),
            }
            for score_name, value in zip(SCORE_NAMES, score_matrix[:, rater], strict=True):
                record[score_name] = float(value)
            record["total"] = float(score_matrix[:, rater].sum())
            records.append(record)
    frame = pd.DataFrame.from_records(records)
    duplicates = frame.duplicated(["color", "panel", "sample_id", "rater"]).sum()
    if duplicates:
        warnings.append(f"{sheet_name}: {duplicates} duplicate sample-rater records")
    expected_samples = 27 if color == "red" else 28
    observed_samples = frame["sample_id"].nunique()
    if observed_samples != expected_samples:
        warnings.append(
            f"{sheet_name}: expected {expected_samples} samples, found {observed_samples}"
        )
    return frame.sort_values(["sample_id", "rater"]).reset_index(drop=True), warnings


def forward_fill_headers(row: list[str]) -> list[str]:
    filled: list[str] = []
    current = ""
    for cell in row:
        text = normalize_text(cell)
        if text:
            current = text
        filled.append(current)
    return filled


def color_subfeature(subheader: str, previous: str) -> tuple[str, str]:
    text = normalize_text(subheader)
    descriptor = text.replace("（", "(").replace("）", ")")
    descriptor = re.sub(r"\(D65\)", "", descriptor, flags=re.IGNORECASE)
    descriptor = descriptor.replace("(+红；-绿)", "").replace("(+黄;-蓝)", "")
    descriptor = descriptor.replace("(+黄；-蓝)", "")
    descriptor = re.sub(r"(?:\s|[0-9])+", "", descriptor)
    descriptor = descriptor.strip(" _-;")
    if descriptor:
        previous = descriptor
    return previous or "component", previous


def parse_grouped_physchem(
    path: Path,
    table_kind: str,
    color: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = read_tsv(path)
    if table_kind == "grape":
        header_index, subheader_index = (0, 1) if color == "red" else (31, 32)
        sample_regex = GRAPE_SAMPLE_RE
    else:
        header_index, subheader_index = (0, 1) if color == "red" else (30, 31)
        sample_regex = WINE_SAMPLE_RE

    sample_rows: list[tuple[int, int]] = []
    for row_index in range(subheader_index + 1, len(rows)):
        found = None
        for cell in rows[row_index][:3]:
            match = sample_regex.search(normalize_text(cell))
            if match:
                found = int(match.group(1))
                break
        if found is not None:
            sample_rows.append((row_index, found))
            if len(sample_rows) == (27 if color == "red" else 28):
                break
    expected = 27 if color == "red" else 28
    if len(sample_rows) != expected:
        raise RuntimeError(
            f"{table_kind}-{color}: expected {expected} sample rows, found {len(sample_rows)}"
        )

    high_headers = forward_fill_headers(rows[header_index])
    subheaders = [normalize_text(value) for value in rows[subheader_index]]
    sample_ids = [sample_id for _, sample_id in sample_rows]
    raw_columns: defaultdict[str, list[np.ndarray]] = defaultdict(list)
    raw_column_labels: defaultdict[str, list[str]] = defaultdict(list)
    color_state: defaultdict[str, str] = defaultdict(str)

    width = len(rows[0])
    for column in range(width):
        values = np.array([numeric(rows[row_index][column]) for row_index, _ in sample_rows])
        if not np.isfinite(values).any():
            continue
        high = normalize_text(high_headers[column])
        # In the source grape sheet the first L* replicate precedes the merged
        # ``果皮颜色`` header by one column.  The secondary header is the only
        # unambiguous boundary marker, so repair that header assignment here.
        if table_kind == "grape" and re.search(r"L\*\s*1", subheaders[column], re.IGNORECASE):
            high = "果皮颜色"
        if not high or "样品编号" in high or high in {"红葡萄", "白葡萄", "红葡萄酒", "白葡萄酒", "品种编号"}:
            continue
        feature = high
        if "颜色" in high or "色泽" in high:
            descriptor, color_state[high] = color_subfeature(subheaders[column], color_state[high])
            feature = f"{high}|{descriptor}"
        raw_columns[feature].append(values)
        raw_column_labels[feature].append(subheaders[column])

    data: dict[str, Any] = {"sample_id": sample_ids}
    replicate_flags: list[dict[str, Any]] = []
    replicate_counts: dict[str, int] = {}
    for feature, columns in raw_columns.items():
        matrix = np.column_stack(columns)
        replicate_counts[feature] = matrix.shape[1]
        # Technical repeats are collapsed by the row median; this leaves genuine
        # between-sample extremes intact while resisting one transcription error.
        with np.errstate(all="ignore"):
            data[feature] = np.nanmedian(matrix, axis=1)
        if matrix.shape[1] >= 3:
            for row_position, sample_id in enumerate(sample_ids):
                finite = matrix[row_position, np.isfinite(matrix[row_position])]
                if finite.size >= 3:
                    abs_values = np.abs(finite)
                    positive = abs_values[abs_values > 0]
                    ratio = float(abs_values.max() / positive.min()) if positive.size else 1.0
                    median = float(np.median(finite))
                    mad = float(np.median(np.abs(finite - median)))
                    robust_distance = (
                        float(np.max(np.abs(finite - median)) / max(mad, 1e-12))
                        if mad > 0
                        else (float("inf") if np.max(np.abs(finite - median)) > 0 else 0.0)
                    )
                    if ratio >= 5 and robust_distance >= 5:
                        replicate_flags.append(
                            {
                                "table": table_kind,
                                "color": color,
                                "sample_id": int(sample_id),
                                "feature": feature,
                                "values": [float(value) for value in finite],
                                "aggregation": "median",
                            }
                        )

    frame = pd.DataFrame(data).sort_values("sample_id").reset_index(drop=True)
    metadata = {
        "table": table_kind,
        "color": color,
        "samples": int(frame.shape[0]),
        "features": int(frame.shape[1] - 1),
        "missing_cells": int(frame.drop(columns="sample_id").isna().sum().sum()),
        "missing_rate": float(frame.drop(columns="sample_id").isna().mean().mean()),
        "replicate_counts": replicate_counts,
        "replicate_flags": replicate_flags,
    }
    return frame, metadata


def chemical_key(row: list[str], row_number: int) -> tuple[str, str]:
    english = normalize_text(row[0])
    chinese = normalize_text(row[1])
    formula = normalize_text(row[3])
    if not english and not chinese:
        return "", ""
    base = english or chinese
    normalized = re.sub(r"\s+", " ", base.lower()).strip()
    key = f"{normalized}|{formula.lower()}"
    display = f"{base} [{formula}]" if formula else base
    return key or f"row-{row_number}", display


def parse_aroma(path: Path, matrix_kind: str, color: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = read_tsv(path)
    sample_columns: list[tuple[int, int]] = []
    for column, cell in enumerate(rows[0]):
        match = GRAPE_SAMPLE_RE.fullmatch(normalize_text(cell))
        if match:
            sample_columns.append((column, int(match.group(1))))
    expected = 27 if color == "red" else 28
    if len(sample_columns) != expected:
        raise RuntimeError(
            f"aroma-{matrix_kind}-{color}: expected {expected} sample columns, found {len(sample_columns)}"
        )

    by_key: defaultdict[str, list[np.ndarray]] = defaultdict(list)
    displays: dict[str, str] = {}
    for row_number, row in enumerate(rows[1:], start=2):
        key, display = chemical_key(row, row_number)
        if not key:
            continue
        values = np.array([numeric(row[column]) for column, _ in sample_columns], dtype=float)
        if not np.isfinite(values).any():
            continue
        by_key[key].append(values)
        displays[key] = display

    sample_ids = [sample_id for _, sample_id in sample_columns]
    data: dict[str, Any] = {"sample_id": sample_ids}
    duplicate_keys: dict[str, int] = {}
    for key in sorted(by_key):
        matrices = np.vstack(by_key[key])
        if matrices.shape[0] > 1:
            duplicate_keys[displays[key]] = matrices.shape[0]
        # Repeated identification rows denote the same compound; sum detected
        # abundances while leaving a fully undetected sample as missing.
        detected = np.isfinite(matrices).any(axis=0)
        values = np.nansum(matrices, axis=0)
        values[~detected] = np.nan
        data[displays[key]] = values
    frame = pd.DataFrame(data).sort_values("sample_id").reset_index(drop=True)
    values = frame.drop(columns="sample_id")
    metadata = {
        "matrix": matrix_kind,
        "color": color,
        "samples": int(frame.shape[0]),
        "compounds": int(values.shape[1]),
        "missing_cells": int(values.isna().sum().sum()),
        "missing_rate": float(values.isna().mean().mean()),
        "duplicate_keys": duplicate_keys,
    }
    return frame, metadata


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    extracted_dir = workspace / "results" / "extracted"
    clean_dir = workspace / "results" / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)

    sheets, manifest = locate_sheets(extracted_dir)
    score_frames: list[pd.DataFrame] = []
    score_warnings: list[str] = []
    for sheet_name in [
        "第一组红葡萄酒品尝评分",
        "第一组白葡萄酒品尝评分",
        "第二组红葡萄酒品尝评分",
        "第二组白葡萄酒品尝评分",
    ]:
        frame, warnings = parse_tasting_sheet(sheets[sheet_name], sheet_name)
        score_frames.append(frame)
        score_warnings.extend(warnings)
    tasting = pd.concat(score_frames, ignore_index=True)
    tasting.to_csv(clean_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8")

    conventional_metadata: list[dict[str, Any]] = []
    conventional: dict[tuple[str, str], pd.DataFrame] = {}
    for kind, sheet_name in [("grape", "酿酒葡萄"), ("wine", "葡萄酒")]:
        for color in ["red", "white"]:
            frame, metadata = parse_grouped_physchem(sheets[sheet_name], kind, color)
            conventional[(kind, color)] = frame
            conventional_metadata.append(metadata)
            frame.to_csv(
                clean_dir / f"{kind}_conventional_{color}.csv",
                index=False,
                encoding="utf-8",
            )

    aroma_metadata: list[dict[str, Any]] = []
    aroma: dict[tuple[str, str], pd.DataFrame] = {}
    for matrix_kind, prefix in [("grape", ""), ("wine", "")]:
        for color, color_cn in [("red", "红"), ("white", "白")]:
            sheet_name = f"{color_cn}葡萄" if matrix_kind == "grape" else f"{color_cn}葡萄酒"
            frame, metadata = parse_aroma(sheets[sheet_name], matrix_kind, color)
            aroma[(matrix_kind, color)] = frame
            aroma_metadata.append(metadata)
            frame.to_csv(
                clean_dir / f"{matrix_kind}_aroma_{color}.csv",
                index=False,
                encoding="utf-8",
            )

    sample_checks: list[dict[str, Any]] = []
    expected_sets = {"red": set(range(1, 28)), "white": set(range(1, 29))}
    for color in ["red", "white"]:
        sets: dict[str, set[int]] = {
            "tasting_panel_1": set(
                tasting.loc[(tasting.color == color) & (tasting.panel == 1), "sample_id"].astype(int)
            ),
            "tasting_panel_2": set(
                tasting.loc[(tasting.color == color) & (tasting.panel == 2), "sample_id"].astype(int)
            ),
            "grape_conventional": set(conventional[("grape", color)].sample_id.astype(int)),
            "wine_conventional": set(conventional[("wine", color)].sample_id.astype(int)),
            "grape_aroma": set(aroma[("grape", color)].sample_id.astype(int)),
            "wine_aroma": set(aroma[("wine", color)].sample_id.astype(int)),
        }
        aligned = all(sample_set == expected_sets[color] for sample_set in sets.values())
        sample_checks.append(
            {
                "color": color,
                "status": "pass" if aligned else "fail",
                "counts": {name: len(sample_set) for name, sample_set in sets.items()},
                "missing": {
                    name: sorted(expected_sets[color] - sample_set)
                    for name, sample_set in sets.items()
                    if expected_sets[color] - sample_set
                },
            }
        )

    score_bounds_ok = bool(
        (tasting["total"] >= 0).all()
        and (tasting["total"] <= 100).all()
        and not tasting[SCORE_NAMES].isna().any().any()
    )
    replicate_flags = [
        flag
        for metadata in conventional_metadata
        for flag in metadata["replicate_flags"]
    ]
    input_hashes = {
        item["workbook"]: item["workbook_sha256"]
        for item in manifest
    }
    audit = {
        "seed": SEED,
        "source_files": input_hashes,
        "extracted_sheets": len(manifest),
        "tasting": {
            "records": int(tasting.shape[0]),
            "samples_by_color_panel": tasting.groupby(["color", "panel"])["sample_id"]
            .nunique()
            .rename("samples")
            .reset_index()
            .to_dict(orient="records"),
            "score_range": [float(tasting.total.min()), float(tasting.total.max())],
            "warnings": score_warnings,
        },
        "conventional": conventional_metadata,
        "aroma": aroma_metadata,
        "sample_alignment": sample_checks,
        "replicate_anomalies": replicate_flags,
        "checks": {
            "extracted_hashes": {"status": "pass"},
            "sample_alignment": {
                "status": "pass" if all(item["status"] == "pass" for item in sample_checks) else "fail"
            },
            "score_completeness_and_bounds": {"status": "pass" if score_bounds_ok else "fail"},
            "replicate_anomalies": {
                "status": "needs_review" if replicate_flags else "pass",
                "handling": "technical-repeat median; original values retained in extracted snapshot",
            },
            "aroma_missingness": {
                "status": "needs_review",
                "handling": "primary analysis treats blank as non-detection zero after filtering; half-minimum sensitivity is required",
            },
            "pre_extraction_input_hash_observation": {
                "status": "needs_review",
                "note": "An earlier console observation showed different XLS hash prefixes before Office COM extraction; full prior hashes were not captured, so no identity claim is made.",
            },
        },
    }
    (clean_dir / "data_audit.json").write_text(
        json.dumps(json_ready(audit), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"[PASS] prepared {len(tasting)} tasting records, "
        f"{sum(item['features'] for item in conventional_metadata)} conventional features, "
        f"{sum(item['compounds'] for item in aroma_metadata)} aroma matrices/features"
    )


if __name__ == "__main__":
    main()
