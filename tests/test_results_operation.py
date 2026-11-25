"""Coal fleet, corridor utilisation and stress-week analysis."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from results_analysis.coal import (  # noqa: E402
    coal_fleet,
    coal_monthly,
    coal_plant_operation,
    station_name,
)
from results_analysis.grid import (  # noqa: E402
    corridor_flows,
    corridor_loading,
    corridor_metrics,
    corridor_name,
)
from results_analysis.adequacy import firm_capacity  # noqa: E402
from results_analysis.emissions import co2_monthly  # noqa: E402
from results_analysis.grid import corridor_map_figure  # noqa: E402
from results_analysis.loaders import AnalysisRun  # noqa: E402
from results_analysis.price_setting import CONSTRAINT, price_setters, price_setting_shares  # noqa: E402
from results_analysis.renewables import curtailment_monthly  # noqa: E402
from results_analysis.stress import stress_events, stress_weeks  # noqa: E402

UNIT_1 = "plant_x__unit_01"
UNIT_2 = "plant_x__unit_02"
RESERVATION = "rea_export_reservation__plant_y"
SOLAR = "solar_a"


def _frame(data: dict, index: pd.Index) -> pd.DataFrame:
    return pd.DataFrame(data, index=index).rename_axis(columns="name")


@pytest.fixture
def run() -> AnalysisRun:
    """Four hours with two committable coal units, an export reservation,
    curtailed solar, a line and an interconnector modelled as one link per
    direction. Area A is in Tamil Nadu, B is a neighbour.
    """
    network = pypsa.Network()
    index = pd.date_range("2025-04-30 22:00", periods=4, freq="h")
    network.set_snapshots(index)
    network.add("Carrier", "AC")
    network.add("Bus", ["A", "B"], carrier="AC", x=[78.0, 79.0], y=[11.0, 12.0])
    network.buses["kind"] = ["TN balancing area", "neighbouring state"]
    network.add(
        "Generator", [UNIT_1, UNIT_2], bus="A", carrier="coal", p_nom=100.0,
        p_min_pu=0.4, committable=True, marginal_cost=2_000.0,
    )
    network.add(
        "Generator", RESERVATION, bus="A", carrier="coal", p_nom=50.0,
        p_min_pu=0.5, p_max_pu=0.5, marginal_cost=3_000.0,
    )
    network.add("Generator", SOLAR, bus="A", carrier="solar", p_nom=50.0)
    # PyPSA statistics use the optimised capacity, as in a solved network.
    network.generators["p_nom_opt"] = network.generators.p_nom
    network.add("Load", "load", bus="B", carrier="electricity")
    network.add("Load", "load_A", bus="A", carrier="electricity")
    network.add("Line", "A-B", bus0="A", bus1="B", carrier="AC", s_nom=100.0, s_max_pu=0.5, x=0.1)
    network.add("Link", "Tie →", bus0="A", bus1="B", carrier="AC", p_nom=100.0)
    network.add("Link", "Tie ←", bus0="B", bus1="A", carrier="AC", p_nom=100.0, p_max_pu=0.5)

    # Unit 1 loses half its capacity in hour 3 and all of it in hour 4.
    network.generators_t.p_max_pu = _frame(
        {UNIT_1: [1.0, 1.0, 0.5, 0.0], SOLAR: [0.5, 1.0, 0.0, 0.0]}, index
    )
    network.generators_t.p = _frame(
        {
            UNIT_1: [40.0, 80.0, 50.0, 0.0], UNIT_2: [0.0, 40.0, 40.0, 100.0],
            RESERVATION: 25.0, SOLAR: [20.0, 50.0, 0.0, 0.0],
        },
        index,
    )
    network.loads_t.p = _frame({"load": 0.0, "load_A": 100.0}, index)
    network.generators_t.status = _frame(
        {UNIT_1: [1.0, 1.0, 1.0, 0.0], UNIT_2: [0.0, 1.0, 1.0, 1.0], RESERVATION: 0.0}, index
    )
    network.generators_t.start_up = _frame(
        {UNIT_1: [1.0, 0.0, 0.0, 0.0], UNIT_2: [0.0, 1.0, 0.0, 0.0], RESERVATION: 0.0}, index
    )
    tie = {"Tie →": [80.0, 0.0, 0.0, 10.0], "Tie ←": [0.0, 25.0, 50.0, 0.0]}
    network.links_t.p0 = _frame(tie, index)
    network.links_t.p1 = -network.links_t.p0
    network.lines_t.p0 = _frame({"A-B": [10.0, -50.0, 0.0, 25.0]}, index)
    network.lines_t.p1 = -network.lines_t.p0
    network.buses_t.marginal_price = _frame({"A": [1_000.0, 2_000.0, 3_000.0, 4_000.0], "B": 0.0}, index)
    return AnalysisRun(
        result_dir=Path("."), run_type="single_network", network=network,
        source_files=(), metadata={},
    )


def test_corridor_names_drop_the_direction_arrow():
    assert corridor_name("Vellore–Karnataka (AC tie) →") == "Vellore–Karnataka (AC tie)"
    assert corridor_name("Vellore–Karnataka (AC tie) ←") == "Vellore–Karnataka (AC tie)"
    assert corridor_name("Erode–Trichy") == "Erode–Trichy"


def test_link_pair_is_one_corridor_with_a_limit_per_direction(run):
    flows = corridor_flows(run)
    loading = corridor_loading(run, flows)
    metrics = corridor_metrics(run, flows)

    assert set(flows.columns) == {"A-B", "Tie"}
    assert flows["Tie"].tolist() == pytest.approx([80.0, -25.0, -50.0, 10.0])
    # Forward limit 100 MW, reverse limit 50 MW.
    assert loading["Tie"].tolist() == pytest.approx([80.0, 50.0, 100.0, 10.0])
    assert loading["A-B"].tolist() == pytest.approx([20.0, 100.0, 0.0, 50.0])
    tie = metrics.loc["Tie"]
    assert tie.branches == 2
    assert tie.limit_mw == pytest.approx(100.0)
    assert tie.reverse_limit_mw == pytest.approx(50.0)
    assert tie.mean_loading_pct == pytest.approx(60.0)
    assert tie.hours_at_or_above_99pct == 1
    assert tie.energy_bus0_to_bus1_gwh == pytest.approx(0.090)
    assert tie.energy_bus1_to_bus0_gwh == pytest.approx(0.075)


def test_station_names():
    assert station_name(UNIT_1) == "plant_x"
    assert station_name(RESERVATION) == "export reservation: plant_y"


def test_coal_fleet_hourly_operation(run):
    fleet = coal_fleet(run)

    assert fleet.available[UNIT_1].tolist() == pytest.approx([100.0, 100.0, 50.0, 0.0])
    assert fleet.online[UNIT_2].tolist() == pytest.approx([0.0, 100.0, 100.0, 100.0])
    assert fleet.minimum[UNIT_1].tolist() == pytest.approx([40.0, 40.0, 40.0, 0.0])
    # The reservation is not committable: online whenever available.
    assert fleet.status[RESERVATION].tolist() == [1.0] * 4
    assert list(fleet.dispatched_units) == [UNIT_1, UNIT_2]


def test_coal_plant_operation(run):
    plants = coal_plant_operation(coal_fleet(run), run)

    plant = plants.loc["plant_x"]
    assert plant.units == 2
    assert plant.generation_gwh == pytest.approx(0.35)
    assert plant.capacity_factor_pct == pytest.approx(100.0 * 350.0 / 800.0)
    assert plant.availability_pct == pytest.approx(100.0 * 650.0 / 800.0)
    assert plant.online_pct == pytest.approx(100.0 * 550.0 / 800.0)
    assert plant.loading_when_online_pct == pytest.approx(100.0 * 350.0 / 550.0)
    # Unit 1 at minimum in hour 1, unit 2 in hours 2 and 3: 3 of 6 online unit-hours.
    assert plant.hours_at_minimum_pct == pytest.approx(50.0)
    assert plant.start_ups == pytest.approx(2.0)
    assert plant.market_value_inr_per_mwh == pytest.approx(950_000.0 / 350.0)
    assert plant.margin_over_marginal_cost_inr_per_mwh == pytest.approx(950_000.0 / 350.0 - 2_000.0)

    reservation = plants.loc["export reservation: plant_y"]
    assert bool(reservation.export_reservation)
    assert reservation.capacity_factor_pct == pytest.approx(50.0)
    # Cheapest dispatched station first, reservations last.
    assert list(plants.index) == ["plant_x", "export reservation: plant_y"]


def test_coal_monthly_splits_months_and_ramps(run):
    monthly = coal_monthly(coal_fleet(run))

    assert list(monthly.index) == ["2025-04", "2025-05"]
    april, may = monthly.loc["2025-04"], monthly.loc["2025-05"]
    assert april.generation_gwh == pytest.approx(0.160)
    assert may.generation_gwh == pytest.approx(0.190)
    assert april.start_ups == pytest.approx(2.0)
    assert april.largest_hourly_ramp_up_mw == pytest.approx(80.0)
    assert may.largest_hourly_ramp_down_mw == pytest.approx(-30.0)
    assert monthly.export_reservation_gwh.sum() == pytest.approx(0.1)


def _system(days: int = 30) -> pd.DataFrame:
    index = pd.date_range("2025-04-01", periods=24 * days, freq="h")
    system = pd.DataFrame(
        {
            "demand_mw": 100.0,
            "net_load_mw": 50.0,
            "wind_and_solar_mw": 50.0,
            "unserved_energy_mw": 0.0,
            "spare_thermal_capacity_mw": 500.0,
            "system_price": np.nan,
        },
        index=index,
    )
    system.loc["2025-04-11 19:00", "demand_mw"] = 200.0
    system.loc["2025-04-11 20:00", "net_load_mw"] = 150.0
    system.loc["2025-04-12 13:00", "net_load_mw"] = 5.0
    system.loc["2025-04-29 20:00", "spare_thermal_capacity_mw"] = 10.0
    return system


def test_overlapping_stress_weeks_merge_and_clip_to_the_run():
    system = _system()
    events = stress_events(system)

    # No unserved energy and no prices: those criteria are left out.
    assert list(events.index) == [
        "Peak demand", "Peak net load", "Minimum net load", "Least spare thermal capacity",
    ]
    weeks = stress_weeks(system, events)
    assert len(weeks) == 2
    peak = weeks[0]
    assert peak.start == pd.Timestamp("2025-04-08")
    assert [label for label, _ in peak.events] == ["Peak demand", "Peak net load", "Minimum net load"]
    # The last event's week is moved back to end with the run.
    assert weeks[1].end == system.index[-1]
    assert weeks[1].end - weeks[1].start == pd.Timedelta(days=7) - pd.Timedelta(hours=1)


def test_short_runs_have_no_stress_weeks():
    system = _system().loc[:"2025-04-12 23:00"]
    assert stress_weeks(system, stress_events(system)) == []


def test_curtailment_by_month(run):
    curtailment = curtailment_monthly(run)

    # Solar could give 25 and 50 MW in April's two hours and gave 20 and 50.
    april = curtailment.loc[("2025-04", "solar")]
    assert april.available_gwh == pytest.approx(0.075)
    assert april.curtailed_gwh == pytest.approx(0.005)
    assert april.curtailed_pct == pytest.approx(100.0 * 5.0 / 75.0)
    assert curtailment.loc[("total", "all"), "curtailed_gwh"] == pytest.approx(0.005)


def test_firm_capacity_counts_available_thermal_not_solar(run):
    firm = firm_capacity(run)

    # Unit 1 (100, 100, 50, 0) + unit 2 (100) + the reservation (25); no hydro.
    assert firm.generators.tolist() == pytest.approx([225.0, 225.0, 175.0, 125.0])
    assert firm.hydro.eq(0.0).all()


def test_price_setter_is_the_part_loaded_unit_at_the_price(run):
    setters, exact = price_setters(run)

    # Only area A is in Tamil Nadu. In hour 2 unit 1 runs between its
    # minimum and maximum at a marginal cost equal to the price (2,000).
    assert list(setters.columns) == ["A"]
    assert setters["A"].tolist() == [CONSTRAINT, "coal", CONSTRAINT, CONSTRAINT]
    assert exact["A"].tolist() == [False, True, False, False]
    overall = price_setting_shares(run, setters, exact)["price_setting_overall"]
    assert overall.at["coal", "share_pct"] == pytest.approx(25.0)
    assert overall.at["coal", "exact_match_pct"] == pytest.approx(100.0)


def test_co2_by_month(run):
    co2 = co2_monthly(run, {"carriers": {"coal": 1.0}, "net_imports": 0.0})

    # Coal in April: 65 + 145 MWh; in May: 115 + 125 MWh, at 1 t/MWh.
    assert co2.at["2025-04", "coal_kt"] == pytest.approx(0.210)
    assert co2.at["2025-05", "coal_kt"] == pytest.approx(0.240)
    assert co2.at["total", "total_kt"] == pytest.approx(0.450)
    assert co2.at["total", "tn_demand_gwh"] == pytest.approx(0.4)
    assert co2.at["total", "tn_demand_intensity_t_per_mwh"] == pytest.approx(450.0 / 400.0)


def test_corridor_map_draws(run):
    figure = corridor_map_figure(run, corridor_metrics(run))
    assert figure.axes
