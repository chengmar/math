#!/usr/bin/env python3
"""Independent formula and ball-node checks for the revised solver."""

import json
import math

import solve


def main():
    dry_mass = 4800.0
    volume = dry_mass / solve.RHO_STEEL
    diameter = (6.0 * volume / math.pi) ** (1.0 / 3.0)
    area = math.pi * diameter ** 2 / 4.0
    drag = solve.CURRENT_COEFF * area * 1.5 ** 2

    ball = solve.solid_sphere_ball_from_dry_mass(dry_mass, solve.RHO_STEEL, 1.0)
    design = solve.design_with_ball("V", 117 * solve.CHAIN_TYPES["V"]["link_length_m"], ball)
    scenario = {
        "id": "UNIT-V117-D20-W36-C15",
        "depth_m": 20.0,
        "wind_m_s": 36.0,
        "current_m_s": 1.5,
    }
    case = solve.solve_case(scenario, design, vertical_iterations=70)
    checks = [
        ("volume", ball["ball_displaced_volume_m3"], volume, 1.0e-12),
        ("diameter", ball["ball_hydrodynamic_diameter_m"], diameter, 1.0e-12),
        ("projected_area", ball["ball_projected_area_m2"], area, 1.0e-12),
        ("current_drag", case["ball_current_force_n"], drag, 1.0e-9),
        (
            "ball_node_horizontal_residual",
            case["residuals"]["ball_node_horizontal_force_n"],
            0.0,
            1.0e-9,
        ),
        (
            "drum_interface_identity",
            case["chain_horizontal_tension_n"] - case["ball_current_force_n"],
            case["drum_interface_horizontal_force_n"],
            1.0e-9,
        ),
    ]
    rows = []
    for name, computed, expected, tolerance in checks:
        error = abs(computed - expected)
        rows.append({
            "check": name,
            "computed": computed,
            "expected": expected,
            "absolute_error": error,
            "tolerance": tolerance,
            "status": "pass" if error <= tolerance else "fail",
        })
    status = "pass" if all(row["status"] == "pass" for row in rows) else "fail"
    print(json.dumps({
        "status": status,
        "checks": rows,
        "legacy_nominal_anchor_angle_deg": case["chain"]["anchor_angle_deg"],
        "legacy_nominal_anchor_constraint": case["constraints"]["anchor_angle_le_16"],
    }, ensure_ascii=False, sort_keys=True))
    if status != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
