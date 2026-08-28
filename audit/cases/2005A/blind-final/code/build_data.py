"""Build tidy CSV inputs from the blind workspace's extracted DOCX JSON files."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


ROMAN_MAP = {
    "Ⅰ": "I",
    "Ⅱ": "II",
    "Ⅲ": "III",
    "Ⅳ": "IV",
    "Ⅴ": "V",
    "I": "I",
    "II": "II",
    "III": "III",
    "IV": "IV",
    "V": "V",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(value: str) -> str:
    return re.sub(r"[\s　]+", "", str(value))


def normalize_class(value: str) -> str:
    value = compact(value).upper().replace("类", "")
    if "劣" in value:
        return "inferior_V"
    return ROMAN_MAP.get(value, value)


def normalize_scope(value: str) -> str:
    value = compact(value)
    return {"全流域": "whole_basin", "干流": "mainstream", "支流": "tributary"}.get(
        value, value
    )


def normalize_period(value: str) -> str:
    value = compact(value)
    return {"枯水期": "dry", "丰水期": "wet", "水文年": "hydrological_year"}.get(
        value, value
    )


def build_monthly(doc: dict) -> pd.DataFrame:
    tables = [b for b in doc["blocks"] if b["type"] == "table" and b["index"] < 28]
    dates = pd.period_range("2003-06", "2005-09", freq="M")
    if len(tables) != len(dates):
        raise ValueError(f"Expected 28 monthly tables, found {len(tables)}")

    records = []
    for date, table in zip(dates, tables, strict=True):
        data_rows = table["rows"][2:]
        if len(data_rows) != 17:
            raise ValueError(f"{date}: expected 17 stations, found {len(data_rows)}")
        for row in data_rows:
            if len(row) != 10:
                raise ValueError(f"{date}: expected 10 columns, found {len(row)}")
            station = compact(row[1])
            if station == "四川攀枝花":
                station = "四川攀枝花龙洞"
            records.append(
                {
                    "date": str(date),
                    "sequence": int(float(row[0])),
                    "station": station,
                    "section": compact(row[2]),
                    "ph": pd.to_numeric(row[3], errors="coerce"),
                    "do_mg_l": pd.to_numeric(row[4], errors="coerce"),
                    "codmn_mg_l": pd.to_numeric(row[5], errors="coerce"),
                    "nh3n_mg_l": pd.to_numeric(row[6], errors="coerce"),
                    "reported_class": normalize_class(row[7]),
                    "previous_class": normalize_class(row[8]),
                    "reported_pollutant": compact(row[9]),
                    "is_mainstream": "干流" in row[2],
                }
            )
    return pd.DataFrame.from_records(records)


def build_hydrology(doc: dict) -> pd.DataFrame:
    table = next(
        b for b in doc["blocks"] if b["type"] == "table" and b["index"] == 28
    )
    rows = table["rows"]
    stations = [compact(v) for v in rows[0][2:]]
    distances = [float(v) for v in rows[1][2:]]
    records = []
    for row in rows[2:]:
        date = row[0].replace(".", "-")
        metric = compact(row[1])
        field = {"水流量": "flow_m3_s", "水流速": "velocity_m_s"}[metric]
        for station, distance, value in zip(stations, distances, row[2:], strict=True):
            records.append(
                {
                    "date": date,
                    "station_short": station,
                    "distance_km": distance,
                    "metric": field,
                    "value": float(value),
                }
            )
    tidy = (
        pd.DataFrame.from_records(records)
        .pivot(index=["date", "station_short", "distance_km"], columns="metric", values="value")
        .reset_index()
        .rename_axis(columns=None)
    )
    return tidy


def build_annual(doc: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    tables = [b for b in doc["blocks"] if b["type"] == "table"]
    classes = ["I", "II", "III", "IV", "V", "inferior_V"]
    water_quality = []
    for year, table in zip(range(1995, 2005), tables, strict=True):
        seen = set()
        for row in table["rows"][2:]:
            # The 1995 table contains one duplicated period column. Parsing from
            # the right makes both layouts consistent.
            period = normalize_period(row[0])
            scope = normalize_scope(row[-14])
            evaluated_length = float(row[-13])
            pairs = row[-12:]
            key = (period, scope, tuple(pairs))
            if key in seen:
                continue
            seen.add(key)
            record = {
                "year": year,
                "period": period,
                "scope": scope,
                "evaluated_length_km": evaluated_length,
            }
            for i, water_class in enumerate(classes):
                record[f"length_{water_class}_km"] = float(pairs[2 * i])
                record[f"share_{water_class}_pct"] = float(pairs[2 * i + 1])
            water_quality.append(record)

    paragraphs = [b["text"] for b in doc["blocks"] if b["type"] == "paragraph"]
    annual_totals = []
    pattern = re.compile(
        r"(?P<year>19\d{2}|20\d{2})年长江总流量(?P<flow>[\d.]+)亿(?:立方米|吨)，"
        r"废水排放总量(?P<wastewater>[\d.]+)亿吨"
    )
    for paragraph in paragraphs:
        match = pattern.search(compact(paragraph))
        if match:
            annual_totals.append(
                {
                    "year": int(match.group("year")),
                    "river_flow_1e8_m3": float(match.group("flow")),
                    "wastewater_1e8_t": float(match.group("wastewater")),
                    "source_flow_unit_typo": int(
                        match.group("year") == "1999" and "亿吨" in compact(paragraph)
                    ),
                }
            )
    return pd.DataFrame(water_quality), pd.DataFrame(annual_totals)


def audit(
    monthly: pd.DataFrame,
    hydrology: pd.DataFrame,
    annual_wq: pd.DataFrame,
    annual_totals: pd.DataFrame,
) -> dict:
    share_cols = [c for c in annual_wq if c.startswith("share_")]
    length_cols = [c for c in annual_wq if c.startswith("length_")]
    share_sum = annual_wq[share_cols].sum(axis=1)
    length_sum = annual_wq[length_cols].sum(axis=1)
    length_pct_error = []
    for water_class in ["I", "II", "III", "IV", "V", "inferior_V"]:
        implied = (
            annual_wq[f"length_{water_class}_km"]
            / annual_wq["evaluated_length_km"]
            * 100
        )
        length_pct_error.append(
            (implied - annual_wq[f"share_{water_class}_pct"]).abs()
        )
    max_pair_error = pd.concat(length_pct_error, axis=1).max(axis=1)

    numeric_monthly = ["ph", "do_mg_l", "codmn_mg_l", "nh3n_mg_l"]
    return {
        "monthly": {
            "rows": int(len(monthly)),
            "months": int(monthly["date"].nunique()),
            "stations": int(monthly["station"].nunique()),
            "duplicate_station_month_rows": int(
                monthly.duplicated(["date", "station"]).sum()
            ),
            "missing_numeric_values": {
                col: int(monthly[col].isna().sum()) for col in numeric_monthly
            },
            "ranges": {
                col: [float(monthly[col].min()), float(monthly[col].max())]
                for col in numeric_monthly
            },
        },
        "hydrology": {
            "rows": int(len(hydrology)),
            "months": int(hydrology["date"].nunique()),
            "stations": int(hydrology["station_short"].nunique()),
            "missing_values": int(hydrology.isna().sum().sum()),
            "monotone_station_distances": bool(
                np.all(np.diff(sorted(hydrology["distance_km"].unique())) > 0)
            ),
        },
        "annual": {
            "water_quality_rows": int(len(annual_wq)),
            "total_rows": int(len(annual_totals)),
            "share_sum_outside_99_101": int(((share_sum < 99) | (share_sum > 101)).sum()),
            "max_share_sum_abs_error_pct_point": float((share_sum - 100).abs().max()),
            "length_sum_mismatch_over_1km": int(
                ((length_sum - annual_wq["evaluated_length_km"]).abs() > 1).sum()
            ),
            "length_share_pair_error_over_1pct_point": int((max_pair_error > 1).sum()),
            "coverage_break": {
                "whole_basin_hydrological_year_1998_km": float(
                    annual_wq.query(
                        "year == 1998 and period == 'hydrological_year' and scope == 'whole_basin'"
                    )["evaluated_length_km"].iloc[0]
                ),
                "whole_basin_hydrological_year_1999_km": float(
                    annual_wq.query(
                        "year == 1999 and period == 'hydrological_year' and scope == 'whole_basin'"
                    )["evaluated_length_km"].iloc[0]
                ),
            },
            "source_flow_unit_typo_rows": int(annual_totals["source_flow_unit_typo"].sum()),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    attachment3 = load_json(args.extracted_dir / "attachment3.json")
    attachment4 = load_json(args.extracted_dir / "attachment4.json")
    monthly = build_monthly(attachment3)
    hydrology = build_hydrology(attachment3)
    annual_wq, annual_totals = build_annual(attachment4)

    monthly.to_csv(args.output_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    hydrology.to_csv(args.output_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    annual_wq.to_csv(args.output_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    annual_totals.to_csv(args.output_dir / "<SOURCE_FILE_REDACTED>", index=False, encoding="utf-8-sig")
    report = audit(monthly, hydrology, annual_wq, annual_totals)
    (args.output_dir / "input_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
