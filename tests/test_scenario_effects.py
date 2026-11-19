"""Scenario effects: what offshore wind changes against the baseline."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from results_analysis.loaders import AnalysisRun  # noqa: E402
from results_analysis.scenario_effects import compare_scenario  # noqa: E402
from results_analysis.stitching import stitch_networks  # noqa: E402
from test_results_statistics import _frame, _window  # noqa: E402

FACTORS = {"carriers": {"coal": 1.0}, "net_imports": 0.5}


def _windows(offshore: bool) -> list:
    """Two windows; with offshore, 10 MW at Erode producing 8 MW of 10 MW available."""
    networks = []
    for start, coal in (("2025-04-01 00:00", 60.0), ("2025-04-01 02:00", 40.0)):
        network = _window(start, coal=coal)
        network.buses["kind"] = "TN balancing area"
        if offshore:
            index = network.snapshots
            network.add("Carrier", "offshore_wind")
            network.add(
                "Generator", "offshore_wind block2", bus="Erode", carrier="offshore_wind",
                p_nom=10.0, capital_cost=1_000.0,
                p_max_pu=pd.Series(1.0, index=index),
            )
            p = network.generators_t.p.copy()
            p["coal_1"] -= 8.0  # offshore replaces coal one for one
            p["offshore_wind block2"] = 8.0
            network.generators_t.p = _frame(p.to_dict("series"), index)
        networks.append(network)
    return networks


def _run(offshore: bool) -> AnalysisRun:
    return AnalysisRun(Path("."), "rolling", stitch_networks(_windows(offshore)), (), {})


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
