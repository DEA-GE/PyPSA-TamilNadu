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
    capacity_comparison,
    generation_by_carrier,
    generation_by_carrier_timeseries,
    installed_capacity_by_area,
    renewable_generation_by_area,
)


def _window(start: str, coal: float) -> pypsa.Network:
    """Build a two-hour solved-looking network with a water layer."""
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
        "Link",
        "turbine",
        bus0="water_mettur",
        bus1="Erode",
        carrier="hydro_turbine",
        p_nom=20.0,
        efficiency=0.9,
    )
    network.add(
        "Link", "corridor", bus0="Erode", bus1="Chennai", carrier="AC", p_nom=80.0
    )
    network.add("Load", "load_Chennai", bus="Chennai", carrier="electricity")

    snapshots = network.snapshots
    network.generators_t.p = pd.DataFrame(
        {"coal_1": coal, "solar_1": [10.0, 0.0], "ue_Erode": 0.0, "inflow": 30.0},
        index=snapshots,
    )
    network.links_t.p0 = pd.DataFrame(
        {"turbine": 10.0, "corridor": 60.0}, index=snapshots
    )
    network.links_t.p1 = pd.DataFrame(
        {"turbine": -9.0, "corridor": -60.0}, index=snapshots
    )
    network.loads_t.p = pd.DataFrame(
        {"load_Chennai": [70.0, 60.0]}, index=snapshots
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
    # Turbine output counts at its grid side; the water inflow feeding it and
    # the transmission corridor are not generation.
    assert hourly["hydro"].tolist() == pytest.approx([9.0] * 4)
    assert "water_inflow" not in hourly
    assert "AC" not in hourly

    energy = generation_by_carrier(rolling_run)
    assert energy.at["coal", "generation_gwh"] == pytest.approx(0.2)
    assert energy.at["hydro", "capacity_mw"] == pytest.approx(18.0)


def test_capacity_by_area_uses_grid_side_turbine_rating(rolling_run):
    capacity = installed_capacity_by_area(rolling_run).capacity_mw

    assert capacity[("Erode", "hydro")] == pytest.approx(18.0)
    assert capacity[("Chennai", "solar")] == pytest.approx(50.0)
    assert not capacity.index.get_level_values("balancing_area").str.startswith(
        "water"
    ).any()


def test_renewable_generation_by_area(rolling_run):
    energy = renewable_generation_by_area(rolling_run).generation_gwh

    assert energy.to_dict() == pytest.approx({("Chennai", "solar"): 0.02})


def test_capacity_comparison_total_and_shares_exclude_slack(rolling_run):
    observed = pd.DataFrame(
        {"installed_capacity_mw": [100.0, 20.0, 50.0, 170.0], "source": "test"},
        index=pd.Index(["coal", "hydro", "solar", "total"], name="carrier"),
    )

    result = capacity_comparison(rolling_run.base, observed)

    assert result.at["total", "modeled_capacity_mw"] == pytest.approx(168.0)
    assert result.at["total", "modeled_share_pct"] == pytest.approx(100.0)
    assert result.at["coal", "modeled_share_pct"] == pytest.approx(100.0 * 100.0 / 168.0)
    assert result.at["hydro", "difference_mw"] == pytest.approx(-2.0)
