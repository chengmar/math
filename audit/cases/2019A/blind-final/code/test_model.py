"""Fast regression tests for the audit-driven mass-conservation revision."""

from __future__ import annotations

import math
from pathlib import Path
import unittest

from scipy.optimize import brentq

from model import (
    CONSTS,
    ReliefControl,
    _flow_terms,
    load_models,
    orifice_flow,
    rail_mass_balance_residual,
    relief_dwell_statistics,
    simulate_problem1,
    simulate_pump_system,
)
from run_all import mean_problem1_tau


class MassConservationRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = Path(__file__).resolve().parents[1]
        cls.models = load_models(cls.workspace)

    def test_problem1_exact_mass_balance_regression(self) -> None:
        fuel = self.models.fuel
        average_injection = 44.0 / CONSTS.injection_period
        qin_100 = orifice_flow(
            CONSTS.pump_supply_pressure, 100.0, CONSTS.inlet_area, fuel
        )
        legacy_tau = (
            CONSTS.valve_closed_time
            * average_injection
            / (qin_100 - average_injection)
        )
        corrected_tau = mean_problem1_tau(self.models, 100.0)
        self.assertAlmostEqual(corrected_tau, 0.28760116056100204, places=11)
        self.assertGreater(legacy_tau / corrected_tau - 1.0, 0.025)

        rho_supply = fuel.rho(CONSTS.pump_supply_pressure)

        def legacy_command_mass_residual(pressure: float) -> float:
            qin = orifice_flow(
                CONSTS.pump_supply_pressure, pressure, CONSTS.inlet_area, fuel
            )
            duty = legacy_tau / (legacy_tau + CONSTS.valve_closed_time)
            return rho_supply * qin * duty - fuel.rho(pressure) * average_injection

        equilibrium = brentq(legacy_command_mass_residual, 100.0, 110.0)
        self.assertGreater(equilibrium, 102.5)

    def test_problem1_physical_mass_residual(self) -> None:
        tau = mean_problem1_tau(self.models, 100.0)
        simulation = simulate_problem1(
            self.models.fuel, 1000.0, tau, initial_pressure=100.0, dt=0.02
        )
        residual = rail_mass_balance_residual(simulation, self.models.fuel)
        self.assertLess(residual["max_relative_to_throughput"], 1e-4)

    def test_chamber_to_rail_density_conversion(self) -> None:
        rail_pressure = 100.0
        chamber_pressure = 120.0
        volume = 60.0
        dvdt = -0.5
        values = _flow_terms(
            rail_pressure,
            chamber_pressure,
            volume,
            dvdt,
            0.0,
            False,
            self.models,
        )
        rail_derivative, _, pump_flow, _, _, pump_mass, _, _ = values
        fuel = self.models.fuel
        expected = (
            fuel.e(rail_pressure)
            / CONSTS.rail_volume
            * fuel.rho(chamber_pressure)
            / fuel.rho(rail_pressure)
            * pump_flow
        )
        self.assertAlmostEqual(rail_derivative, expected, places=12)
        self.assertAlmostEqual(pump_mass, fuel.rho(chamber_pressure) * pump_flow, places=12)

    def test_relief_minimum_dwell_is_enforced(self) -> None:
        control = ReliefControl(
            close_pressure=99.8,
            open_pressure=100.4,
            min_open_ms=1.0,
            min_closed_ms=1.0,
            max_switches_per_100ms=60,
        )
        simulation = simulate_pump_system(
            self.models,
            2.0 * math.pi / 50.0,
            300.0,
            dt=0.02,
            injector_offsets=(0.0, 50.0),
            cam_phase0=4.2,
            relief_control=control,
        )
        dwell = relief_dwell_statistics(simulation, 100.0, 300.0)
        self.assertGreaterEqual(dwell["minimum_dwell_ms"] + 1e-12, 1.0)
        self.assertLessEqual(dwell["state_changes_per_100ms"], 60.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
