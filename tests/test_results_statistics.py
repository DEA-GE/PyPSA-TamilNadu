"""Results metrics built on PyPSA ``n.statistics``."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pypsa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from results_analysis.loaders import AnalysisRun  # noqa: E402
from results_analysis.metrics import (  # noqa: E402
    balancing_area_metrics,
    capacity_comparison,
    corridor_metrics,
    generation_by_carrier,
    generation_by_carrier_timeseries,
    installed_capacity_by_area,
    renewable_generation_by_area,
    storage_metrics,
    store_metrics,
)


def _window(start: str, coal: float) -> pypsa.Network:
    """Build a two-hour, balanced, solved-looking network with a water layer.

    Erode has coal and a hydro turbine fed from a reservoir; it exports over
    an AC tie link and a line to Chennai, which has solar, a pumped-storage
    unit and the load.
    """
    network = pypsa.Network()
    network.set_snapshots(pd.date_range(start, periods=2, freq="h"))
    network.add("Carrier", ["AC", "water_energy"])
    network.add("Bus", ["Erode", "Chennai"], carrier="AC")
    network.add("Bus", "water_mettur", carrier="water_energy")
    network.add("Generator", "coal_1", bus="Erode", carrier="coal", p_nom=100.0)
    network.add("Generator", "solar_1", bus="Chennai", carrier="solar", p_nom=50.0)
    network.add(
        "Generator", "ue_Erode", bus="Erode", carrier="unserved_energy", p_nom=1_000.0
    )
    network.add(
        "Generator", "inflow", bus="water_mettur", carrier="water_inflow", p_nom=40.0
    )
    network.add("Store", "mettur", bus="water_mettur", carrier="water_energy", e_nom=500.0)
    network.add(
        "StorageUnit",
        "psp",
        bus="Chennai",
        carrier="pumped_hydro",
        p_nom=10.0,
        max_hours=4.0,
    )
    network.add(
        "Link",
        "turbine",
        bus0="water_mettur",
        bus1="Erode",
        carrier="hydro_turbine",
        p_nom=20.0,
        efficiency=0.9,
    )
    network.add(
        "Link", "tie", bus0="Erode", bus1="Chennai", carrier="AC", p_nom=80.0
    )
    network.add(
        "Line",
        "Erode-Chennai",
        bus0="Erode",
        bus1="Chennai",
        carrier="AC",
        s_nom=100.0,
        s_max_pu=0.7,
        x=0.1,
    )
    network.add("Load", "load_Chennai", bus="Chennai", carrier="electricity")

    index = network.snapshots
    solar = pd.Series([10.0, 0.0], index=index)
    storage = pd.Series([5.0, -5.0], index=index)
    line = coal + 9.0 - 60.0  # Erode: coal + turbine - tie - line = 0
    network.generators_t.p = pd.DataFrame(
        {"coal_1": coal, "solar_1": solar, "ue_Erode": 0.0, "inflow": 30.0}, index=index
    )
    network.links_t.p0 = pd.DataFrame({"turbine": 10.0, "tie": 60.0}, index=index)
    network.links_t.p1 = pd.DataFrame({"turbine": -9.0, "tie": -60.0}, index=index)
    network.lines_t.p0 = pd.DataFrame({"Erode-Chennai": line}, index=index)
    network.lines_t.p1 = pd.DataFrame({"Erode-Chennai": -line}, index=index)
    network.storage_units_t.p = pd.DataFrame({"psp": storage}, index=index)
    network.storage_units_t.state_of_charge = pd.DataFrame(
        {"psp": [20.0, 25.0]}, index=index
    )
    # Water bus: inflow - turbine intake + store dispatch = 0.
    network.stores_t.p = pd.DataFrame({"mettur": -20.0}, index=index)
    network.stores_t.e = pd.DataFrame({"mettur": [120.0, 140.0]}, index=index)
    network.loads_t.p = pd.DataFrame(
        {"load_Chennai": solar + 60.0 + line + storage}, index=index
    )
    return network


@pytest.fixture
def rolling_run() -> AnalysisRun:
    networks = (
        _window("2025-04-01 00:00", coal=60.0),
        _window("2025-04-01 02:00", coal=40.0),
    )
    return AnalysisRun(
        result_dir=Path("."),
        run_type="rolling",
        networks=networks,
        source_files=(),
        metadata={},
    )


def test_generation_joins_windows_and_counts_electricity_only(rolling_run):
    hourly = generation_by_carrier_timeseries(rolling_run)

    assert len(hourly) == 4
    assert hourly["coal"].tolist() == [60.0, 60.0, 40.0, 40.0]
    # Turbine output counts at its grid side, pumped-storage discharge only
    # when positive; the water inflow and transmission are not generation.
    assert hourly["hydro"].tolist() == pytest.approx([14.0, 9.0, 14.0, 9.0])
    assert "water_inflow" not in hourly
    assert "AC" not in hourly

    energy = generation_by_carrier(rolling_run)
    assert energy.at["coal", "generation_gwh"] == pytest.approx(0.2)
    assert energy.at["hydro", "capacity_mw"] == pytest.approx(28.0)


def test_capacity_by_area_uses_grid_side_turbine_rating(rolling_run):
    capacity = installed_capacity_by_area(rolling_run).capacity_mw

    assert capacity[("Erode", "hydro")] == pytest.approx(18.0)
    assert capacity[("Chennai", "hydro")] == pytest.approx(10.0)
    assert capacity[("Chennai", "solar")] == pytest.approx(50.0)
    assert not capacity.index.get_level_values("balancing_area").str.startswith(
        "water"
    ).any()


def test_renewable_generation_by_area(rolling_run):
    energy = renewable_generation_by_area(rolling_run).generation_gwh

    assert energy.to_dict() == pytest.approx({("Chennai", "solar"): 0.02})


def test_capacity_comparison_total_and_shares_exclude_slack(rolling_run):
    observed = pd.DataFrame(
        {"installed_capacity_mw": [100.0, 30.0, 50.0, 180.0], "source": "test"},
        index=pd.Index(["coal", "hydro", "solar", "total"], name="carrier"),
    )

    result = capacity_comparison(rolling_run.base, observed)

    assert result.at["total", "modeled_capacity_mw"] == pytest.approx(178.0)
    assert result.at["total", "modeled_share_pct"] == pytest.approx(100.0)
    assert result.at["coal", "modeled_share_pct"] == pytest.approx(100.0 * 100.0 / 178.0)
    assert result.at["hydro", "difference_mw"] == pytest.approx(-2.0)


def test_storage_and_store_energy(rolling_run):
    storage = storage_metrics(rolling_run).loc["psp"]
    assert storage.power_capacity_mw == pytest.approx(10.0)
    assert storage.discharge_gwh == pytest.approx(0.010)
    assert storage.charge_gwh == pytest.approx(0.010)
    assert storage.equivalent_full_cycles == pytest.approx(0.25)

    store = store_metrics(rolling_run).loc["mettur"]
    assert store.energy_capacity_mwh == pytest.approx(500.0)
    assert store.charge_gwh == pytest.approx(0.080)
    assert store.discharge_gwh == pytest.approx(0.0)
    assert store.maximum_stored_energy_mwh == pytest.approx(140.0)


def test_corridors_cover_lines_and_links_against_operating_limit(rolling_run):
    corridors = corridor_metrics(rolling_run)

    assert set(corridors.index) == {"Erode-Chennai", "tie"}
    line = corridors.loc["Erode-Chennai"]
    assert line.component == "Line"
    assert line.limit_mw == pytest.approx(70.0)
    assert line.maximum_absolute_flow_mw == pytest.approx(11.0)
    assert line.maximum_loading_pct == pytest.approx(100.0 * 11.0 / 70.0)
    assert line.net_energy_bus0_to_bus1_gwh == pytest.approx(-0.004)
    assert corridors.at["tie", "maximum_loading_pct"] == pytest.approx(75.0)


def test_area_balance_closes_with_lines_and_ties(rolling_run):
    areas = balancing_area_metrics(rolling_run)

    assert areas.balance_residual_gwh.abs().max() == pytest.approx(0.0, abs=1e-9)
    # Erode: 200 MWh coal + 36 MWh turbine output, all exported.
    assert areas.at["Erode", "generation_gwh"] == pytest.approx(0.236)
    assert areas.at["Erode", "net_import_gwh"] == pytest.approx(-0.236)
    assert areas.at["Chennai", "net_import_gwh"] == pytest.approx(0.236)
    assert areas.at["Chennai", "storage_gwh"] == pytest.approx(0.0)
    assert "water_mettur" not in areas.index
