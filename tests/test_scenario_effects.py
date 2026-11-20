"""Scenario effects: what offshore wind and batteries change against the baseline."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import pandas as pd
import pytest

matplotlib.use("Agg")  # figures are built off-screen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from results_analysis.loaders import AnalysisRun  # noqa: E402
from results_analysis.scenario_effects import compare_scenario  # noqa: E402
from results_analysis.stitching import stitch_networks  # noqa: E402
from test_results_statistics import _frame, _window  # noqa: E402

FACTORS = {"carriers": {"coal": 1.0}, "net_imports": 0.5}


def _windows(offshore: bool, battery: bool = False) -> list:
    """Two windows.

    With offshore, 10 MW at Erode producing 8 MW of 10 MW available. With a
    battery, 10 MW / 20 MWh at Erode charging 10 MW at 2,000 INR/MWh in the
    first hour of each window and discharging 8 MW at 6,000 INR/MWh in the
    second; coal at Erode covers the charging and backs off for the discharge.
    """
    networks = []
    for start, coal in (("2025-04-01 00:00", 60.0), ("2025-04-01 02:00", 40.0)):
        network = _window(start, coal=coal)
        network.buses["kind"] = "TN balancing area"
        index = network.snapshots
        p = network.generators_t.p.copy()
        if offshore:
            network.add("Carrier", "offshore_wind")
            network.add(
                "Generator", "offshore_wind block2", bus="Erode", carrier="offshore_wind",
                p_nom=10.0, capital_cost=1_000.0,
                p_max_pu=pd.Series(1.0, index=index),
            )
            p["coal_1"] -= 8.0  # offshore replaces coal one for one
            p["offshore_wind block2"] = 8.0
        if battery:
            network.add("Carrier", "battery")
            network.add(
                "StorageUnit", "battery Erode", bus="Erode", carrier="battery",
                p_nom=10.0, max_hours=2.0, capital_cost=1_000.0,
            )
            p["coal_1"] += pd.Series([10.0, -8.0], index)
            storage = network.storage_units_t.p.copy()
            storage["battery Erode"] = [-10.0, 8.0]
            network.storage_units_t.p = _frame(storage.to_dict("series"), index)
            soc = network.storage_units_t.state_of_charge.copy()
            soc["battery Erode"] = [10.0, 2.0]
            network.storage_units_t.state_of_charge = _frame(soc.to_dict("series"), index)
            prices = network.buses_t.marginal_price.copy()
            prices["Erode"] = [2_000.0, 6_000.0]
            network.buses_t.marginal_price = _frame(prices.to_dict("series"), index)
        network.generators_t.p = _frame(p.to_dict("series"), index)
        networks.append(network)
    return networks


def _run(offshore: bool, battery: bool = False) -> AnalysisRun:
    return AnalysisRun(Path("."), "rolling", stitch_networks(_windows(offshore, battery)), (), {})


@pytest.fixture(scope="module")
def bundle():
    return compare_scenario(_run(False), _run(True), emission_factors=FACTORS)


def test_offshore_output_and_curtailment(bundle):
    summary = bundle.tables["offshore_summary"].value
    assert summary["capacity"] == pytest.approx(10.0)
    assert summary["available_energy"] == pytest.approx(0.040)
    assert summary["generated_energy"] == pytest.approx(0.032)
    assert summary["curtailment_share"] == pytest.approx(20.0)
    assert summary["capacity_factor_achieved"] == pytest.approx(0.8)
    assert summary["market_value"] == pytest.approx(3_000.0)  # Erode price


def test_offshore_displaces_coal_one_for_one(bundle):
    displacement = bundle.tables["offshore_displacement"]
    assert displacement.at["coal", "per_mwh_offshore"] == pytest.approx(-1.0)
    assert displacement.at["coal", "change_gwh"] == pytest.approx(-0.032)
    assert "offshore_wind" not in displacement.index


def test_offshore_value_cost_and_emissions(bundle):
    value = bundle.tables["offshore_value"].value
    assert value["operating_cost_saving"] == pytest.approx(0.096)  # 32 MWh x 3,000 INR
    assert value["operating_cost_saving_per_mwh"] == pytest.approx(3_000.0)
    assert value["offshore_annualised_cost"] == pytest.approx(0.01)  # 10 MW x 1,000 INR
    assert value["net_benefit"] == pytest.approx(0.086)
    assert value["co2_avoided"] == pytest.approx(32e-6)  # 32 t of coal at 1 t/MWh
    assert bundle.headline["co2_avoided_mt"] == pytest.approx(32e-6)


def test_general_tables_and_figures(bundle):
    generation = bundle.tables["generation_change"]
    assert generation.at["offshore_wind", "change_gwh"] == pytest.approx(0.032)
    system = bundle.tables["system_change"]
    assert system.at["co2_generation", "change"] == pytest.approx(-32e-6)
    assert set(bundle.tables["offshore_corridors"].index) == {"Erode-Chennai", "tie"}
    assert {"offshore_displacement", "offshore_monthly", "offshore_corridors"} <= set(bundle.figures)


def test_a_scenario_without_offshore_has_no_offshore_section():
    bundle = compare_scenario(_run(False), _run(False), emission_factors=FACTORS)
    assert "offshore_summary" not in bundle.tables
    assert bundle.tables["generation_change"].change_gwh.abs().max() == pytest.approx(0.0)


@pytest.fixture(scope="module")
def battery_bundle():
    return compare_scenario(_run(False), _run(False, battery=True), emission_factors=FACTORS)


def test_battery_operation_and_arbitrage(battery_bundle):
    summary = battery_bundle.tables["battery_summary"].value
    assert summary["power_capacity"] == pytest.approx(10.0)
    assert summary["energy_capacity"] == pytest.approx(20.0)
    assert summary["charged_energy"] == pytest.approx(0.020)
    assert summary["discharged_energy"] == pytest.approx(0.016)
    assert summary["conversion_losses"] == pytest.approx(0.004)
    assert summary["equivalent_full_cycles"] == pytest.approx(0.8)
    assert summary["mean_charge_price"] == pytest.approx(2_000.0)
    assert summary["mean_discharge_price"] == pytest.approx(6_000.0)
    assert summary["price_spread_captured"] == pytest.approx(4_000.0)
    assert summary["arbitrage_revenue"] == pytest.approx(0.056)  # 2 x (8 x 6,000 - 10 x 2,000)
    sites = battery_bundle.tables["battery_sites"]
    assert sites.at["battery Erode", "bus"] == "Erode"
    assert sites.at["battery Erode", "discharged_gwh"] == pytest.approx(0.016)


def test_battery_displacement_value_and_emissions(battery_bundle):
    displacement = battery_bundle.tables["battery_displacement"]
    assert displacement.at["coal", "change_gwh"] == pytest.approx(0.004)  # covers the losses
    assert displacement.at["coal", "per_mwh_discharged"] == pytest.approx(0.25)
    assert "battery" not in displacement.index
    value = battery_bundle.tables["battery_value"].value
    assert value["operating_cost_saving"] == pytest.approx(-0.012)  # 4 MWh more coal at 3,000
    assert value["battery_annualised_cost"] == pytest.approx(0.01)
    assert value["net_benefit"] == pytest.approx(-0.022)
    assert value["co2_avoided"] == pytest.approx(-4e-6)
    assert battery_bundle.headline["battery_power_mw"] == pytest.approx(10.0)


def test_battery_average_day_and_figures(battery_bundle):
    profile = battery_bundle.tables["battery_daily_profile"]
    assert profile.loc[[0, 2], "charge_mw"].tolist() == pytest.approx([10.0, 10.0])
    assert profile.loc[[1, 3], "discharge_mw"].tolist() == pytest.approx([8.0, 8.0])
    assert {"baseline_price", "scenario_price"} <= set(profile.columns)
    monthly = battery_bundle.tables["battery_monthly"]
    assert monthly.battery_discharge_gwh.sum() == pytest.approx(0.016)
    assert {"battery_profile", "battery_displacement", "battery_monthly"} <= set(battery_bundle.figures)
    assert "offshore_summary" not in battery_bundle.tables
    assert "scenario_value" not in battery_bundle.tables


def test_combined_scenario_has_joint_value_and_a_note():
    bundle = compare_scenario(_run(False), _run(True, battery=True), emission_factors=FACTORS)
    value = bundle.tables["scenario_value"].value
    assert value["offshore_wind_annualised_cost"] == pytest.approx(0.01)
    assert value["battery_annualised_cost"] == pytest.approx(0.01)
    assert value["total_annualised_cost"] == pytest.approx(0.02)
    assert {"offshore_summary", "battery_summary"} <= set(bundle.tables)
    assert bundle.notes
