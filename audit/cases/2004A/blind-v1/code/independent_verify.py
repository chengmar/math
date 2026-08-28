from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
ATTENDANCE = 200_000
STAND_CAPACITY = 10_000
SMALL_CAPACITY = 1_500
LARGE_CAPACITY = 3_000
MIN_UTILIZATION = 0.60


def main() -> int:
    routes = pd.read_csv(ROOT / "results" / "<SOURCE_FILE_REDACTED>", encoding="utf-8-sig")
    flow = pd.read_csv(ROOT / "results" / "<SOURCE_FILE_REDACTED>", encoding="utf-8-sig")
    plan = pd.read_csv(ROOT / "results" / "<SOURCE_FILE_REDACTED>", encoding="utf-8-sig")
    uncertainty = pd.read_csv(
        ROOT / "results" / "<SOURCE_FILE_REDACTED>", encoding="utf-8-sig"
    )
    summary = json.loads((ROOT / "results" / "summary.json").read_text(encoding="utf-8"))

    checks: list[dict[str, str]] = []

    def add(item: str, condition: bool, evidence: str) -> None:
        checks.append({"item": item, "status": "pass" if condition else "fail", "evidence": evidence})

    route_mass = float(routes["path_mass"].sum())
    add("ledger_route_mass", abs(route_mass - 2 * ATTENDANCE) < 1e-6, f"sum={route_mass:.9f}")

    grouped = routes.groupby(["origin", "trip"])["path_mass"].sum()
    maximum_origin_error = float((grouped - STAND_CAPACITY).abs().max())
    add("origin_trip_mass", maximum_origin_error < 1e-6, f"maximum_error={maximum_origin_error:.9f}")

    passage = defaultdict(float)
    spend_exposure = defaultdict(float)
    for row in routes.itertuples(index=False):
        nodes = str(row.path).split("-")
        for node in nodes:
            if node and node[0] in {"A", "B", "C"} and node[1:].isdigit():
                passage[node] += float(row.path_mass)
                spend_exposure[node] += float(row.path_mass) * float(row.spend)
    flow_index = flow.set_index("sector")
    passage_error = max(abs(passage[sector] - float(flow_index.loc[sector, "passages"])) for sector in flow_index.index)
    add("ledger_passage_reaggregation", passage_error < 1e-6, f"maximum_error={passage_error:.9f}")

    total_passage = sum(passage.values())
    share_error = max(
        abs(passage[sector] / total_passage - float(flow_index.loc[sector, "flow_share"]))
        for sector in flow_index.index
    )
    add("independent_flow_share", share_error < 1e-12, f"maximum_error={share_error:.3e}")

    total_spend_exposure = sum(spend_exposure.values())
    spend_share_error = max(
        abs(spend_exposure[sector] / total_spend_exposure - float(flow_index.loc[sector, "spend_share"]))
        for sector in flow_index.index
    )
    add("independent_spend_share", spend_share_error < 1e-12, f"maximum_error={spend_share_error:.3e}")

    capacity_recomputed = SMALL_CAPACITY * plan["small"] + LARGE_CAPACITY * plan["large"]
    capacity_error = float(np.max(np.abs(capacity_recomputed - plan["capacity"])))
    add("capacity_formula", capacity_error < 1e-9, f"maximum_error={capacity_error:.9f}")
    robust_error = float(np.max(np.abs(ATTENDANCE * plan["q95"] - plan["robust_demand"])))
    add("robust_demand_formula", robust_error < 1e-6, f"maximum_error={robust_error:.9f}")
    add(
        "plan_constraints",
        bool(
            (plan["capacity"] + 1e-9 >= plan["robust_demand"]).all()
            and (plan["nominal_demand"] / plan["capacity"] + 1e-12 >= MIN_UTILIZATION).all()
            and ((plan["small"] + plan["large"]) >= 1).all()
        ),
        f"minimum_slack={(plan['capacity'] - plan['robust_demand']).min():.6f}",
    )

    uncertainty_error = float((uncertainty.sum(axis=1) - 1.0).abs().max())
    add("uncertainty_sample_conservation", uncertainty_error < 1e-12, f"maximum_error={uncertainty_error:.3e}")
    add(
        "summary_totals",
        int(plan["small"].sum()) == summary["allocation"]["small_total"]
        and int(plan["large"].sum()) == summary["allocation"]["large_total"]
        and abs(total_passage - summary["flow"]["total_sector_passages"]) < 1e-6,
        f"small={int(plan['small'].sum())}; large={int(plan['large'].sum())}; passages={total_passage:.6f}",
    )

    status = "fail" if any(check["status"] == "fail" for check in checks) else "pass"
    report = {
        "status": status,
        "scope": "独立从路线台账重聚合人流/消费暴露，并复算容量与稳健约束；不声称构成外部数学正确性证明。",
        "checks": checks,
    }
    (ROOT / "results" / "independent_verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": status, "report": str(ROOT / 'results' / 'independent_verification.json')}, ensure_ascii=False))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
