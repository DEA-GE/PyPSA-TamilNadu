"""Scenario technologies (batteries, offshore wind) and the scenario workflow pieces."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pypsa
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "workflow"))

from compare_scenarios import collect  # noqa: E402
from run_config import ConfigError, load_run_config  # noqa: E402
from scenario_technologies import (  # noqa: E402
    TechnologyError,
    apply_technologies,
    needs_capacity_expansion,
    network_fingerprint,
    validate_section,
    parse_sites,
    resolve_sites,
    write_optimised_capacities,
)

HOURS = pd.date_range("2025-04-01", periods=4, freq="h")


def _network() -> pypsa.Network:
    network = pypsa.Network()
    network.set_snapshots(HOURS)
    network.add("Carrier", ["AC", "gas"])
    network.add("Bus", ["Erode", "Tirunelveli"], carrier="AC")
    network.add("Line", "Erode-Tirunelveli", bus0="Erode", bus1="Tirunelveli", s_nom=1_000.0, x=0.1)
    network.add("Load", "demand", bus="Erode", p_set=[100.0, 200.0, 300.0, 200.0])
    network.add("Generator", "gas", bus="Erode", carrier="gas", p_nom=1_000.0, marginal_cost=5_000.0)
    return network


@pytest.fixture
def profiles(tmp_path) -> Path:
    path = tmp_path / "offshore_profiles.csv"
    pd.DataFrame({"gulf": [0.9, 0.1, 0.5, 1.0]}, index=HOURS).to_csv(path)
    return path


def _section(profiles: Path | None = None, **sites) -> dict:
    parameters = {
        "battery": {
            "max_hours": 2.0, "efficiency_store": 0.9, "efficiency_dispatch": 0.9,
            "capital_cost": 1_000.0,
        },
        "offshore_wind": {"profiles": str(profiles) if profiles else None, "capital_cost": 2_000.0},
    }
    section = {
        "data": None, "parameters": parameters, "battery": {}, "offshore_wind": {},
        "totals": {}, "fixed_capacities": None,
    }
    section.update(sites)
    return section


def test_sites_are_fixed_or_optimised_with_bus_defaulting_to_the_name():
    sites = parse_sites({
        "battery": {
            "Erode": {"capacity_mw": 250},
            "south": {"bus": "Tirunelveli", "capacity_mw": "optimise", "max_mw": 900},
        },
    })

    fixed, optimised = sites
    assert (fixed.bus, fixed.capacity_mw, fixed.optimise) == ("Erode", 250.0, False)
    assert (optimised.bus, optimised.optimise, optimised.min_mw, optimised.max_mw) == (
        "Tirunelveli", True, 0.0, 900.0
    )


@pytest.mark.parametrize(
    "settings, message",
    [
        ({}, "capacity_mw"),
        ({"capacity_mw": -5}, "non-negative"),
        ({"capacity_mw": "optimise", "min_mw": 10, "max_mw": 5}, "min_mw is larger"),
        ({"capacity_mw": 100, "max_mw": 200}, "apply only with"),
        ({"capacity_mw": 100, "colour": "red"}, "unknown setting"),
        ({"capacity_mw": 100, "profile": "x"}, "offshore_wind only"),
    ],
)
def test_invalid_site_settings_are_rejected(settings, message):
    with pytest.raises(TechnologyError, match=message):
        parse_sites({"battery": {"Erode": settings}})


def test_fixed_capacities_are_added(profiles):
    network = _network()
    section = _section(
        profiles,
        battery={"Erode": {"capacity_mw": 250}},
        offshore_wind={"gulf": {"bus": "Tirunelveli", "capacity_mw": 400}},
    )

    added = apply_technologies(network, section)

    battery = network.storage_units.loc["battery Erode"]
    assert (battery.p_nom, battery.max_hours, battery.p_nom_extendable) == (250.0, 2.0, False)
    assert battery.carrier == "battery" and battery.scenario_site == "Erode"
    offshore = network.generators.loc["offshore_wind gulf"]
    assert (offshore.bus, offshore.p_nom, offshore.p_nom_extendable) == ("Tirunelveli", 400.0, False)
    assert network.generators_t.p_max_pu["offshore_wind gulf"].tolist() == [0.9, 0.1, 0.5, 1.0]
    assert {"battery", "offshore_wind"} <= set(network.carriers.index)
    assert list(added.component) == ["battery Erode", "offshore_wind gulf"]


def test_missing_technology_data_names_the_values():
    section = _section(battery={"Erode": {"capacity_mw": "optimise"}})
    section["parameters"] = {"battery": {"max_hours": None, "capital_cost": None}}

    with pytest.raises(TechnologyError) as error:
        apply_technologies(_network(), section)

    for name in ("battery.max_hours", "battery.efficiency_store", "battery.capital_cost"):
        assert name in str(error.value)


def test_shipped_battery_data_is_a_two_hour_lfp_battery_at_85_percent_round_trip():
    from scenario_technologies import load_parameters

    battery = load_parameters({"data": "config/technologies.yaml"})["battery"]
    assert battery["max_hours"] == 2
    assert battery["efficiency_store"] * battery["efficiency_dispatch"] == pytest.approx(0.85, abs=0.001)
    assert battery["capital_cost"] == pytest.approx(2.3e5 * 12)  # INR 2.3 lakh per MW per month

    network = _network()
    apply_technologies(network, {"data": "config/technologies.yaml", "battery": {"Erode": {"capacity_mw": "optimise"}}})
    assert network.storage_units.at["battery Erode", "capital_cost"] == pytest.approx(2_760_000)


def test_shipped_offshore_cost_is_the_fimoi_lcoe_in_2025_prices():
    from scenario_technologies import load_parameters

    offshore = load_parameters({"data": "config/technologies.yaml"})["offshore_wind"]
    # FIMOI v2: 7.4 INR/kWh (2021 prices) x 1.05^4 x 53 % net capacity factor.
    assert offshore["capital_cost"] == pytest.approx(7.4e3 * 1.05**4 * 0.53 * 8760, rel=1e-4)


def test_unknown_bus_is_rejected():
    with pytest.raises(TechnologyError, match="Unknown bus"):
        apply_technologies(_network(), _section(battery={"Atlantis": {"capacity_mw": 10}}))


def test_optimised_capacity_is_solved_saved_and_fixed(tmp_path, profiles):
    network = _network()
    section = _section(
        profiles,
        offshore_wind={"gulf": {"bus": "Tirunelveli", "capacity_mw": "optimise", "max_mw": 1_000}},
        battery={"Erode": {"capacity_mw": "optimise", "max_mw": 100}},
        totals={"offshore_wind": {"max_mw": 300}},
    )
    apply_technologies(network, section)
    assert network.generators.at["offshore_wind gulf", "p_nom_extendable"]

    status, _ = network.optimize(solver_name="highs")
    assert status == "ok"
    built = network.generators.at["offshore_wind gulf", "p_nom_opt"]
    assert 0.0 < built <= 300.0 + 1e-6  # the total limit binds before max_mw

    path = tmp_path / "optimised_capacities.csv"
    table = write_optimised_capacities(network, path)
    assert set(table.technology) == {"battery", "offshore_wind"}

    fixed = {**section, "fixed_capacities": str(path)}
    assert not needs_capacity_expansion(fixed)
    resolved = {site.name: site for site in resolve_sites(fixed)}
    assert resolved["gulf"].capacity_mw == pytest.approx(built)
    operational = _network()
    apply_technologies(operational, fixed)
    assert not operational.generators.at["offshore_wind gulf", "p_nom_extendable"]
    assert operational.generators.at["offshore_wind gulf", "p_nom"] == pytest.approx(built)
    assert operational.global_constraints.empty  # nothing left to limit


def test_scenario_configs_validate_and_optimised_sites_need_annual_mode(tmp_path):
    assert load_run_config(ROOT / "config" / "scenarios" / "baseline.yaml")["technologies"]["battery"] == {}

    battery = ROOT / "config" / "scenarios" / "battery.yaml"
    with pytest.raises(ConfigError, match="run.mode: annual"):
        load_run_config(battery)
    load_run_config(battery, ["run.mode=annual", "formulation.unit_commitment=none"])

    areas = list(yaml.safe_load(battery.read_text(encoding="utf-8"))["technologies"]["battery"])
    assert len(areas) == 9  # optimised in every balancing area
    capacities = tmp_path / "capacities.csv"
    pd.DataFrame(
        {"technology": "battery", "site": areas, "bus": areas, "capacity_mw": 100.0}
    ).to_csv(capacities, index=False)
    config = load_run_config(battery, [f"technologies.fixed_capacities={capacities.as_posix()}"])
    assert config["run"]["mode"] == "rolling"

    for path in (ROOT / "config" / "scenarios").glob("*.yaml"):
        load_run_config(path, ["run.mode=annual", "formulation.unit_commitment=none"])


def test_existing_run_configs_add_no_technologies():
    for path in (ROOT / "config" / "runs").glob("*.yaml"):
        technologies = load_run_config(path)["technologies"]
        assert not parse_sites(technologies)


def test_network_fingerprint_follows_content():
    first, second = _network(), _network()
    assert network_fingerprint(first) == network_fingerprint(second)

    second.generators.loc["gas", "p_nom"] = 900.0
    assert network_fingerprint(first) != network_fingerprint(second)
    third = _network()
    third.loads_t.p_set.iloc[0, 0] = 101.0
    assert network_fingerprint(first) != network_fingerprint(third)


def test_comparison_puts_scenarios_side_by_side(tmp_path):
    runs = {}
    for scenario, demand, battery in (("baseline", 100.0, None), ("battery", 100.0, 250.0)):
        run_dir = tmp_path / scenario
        tables = run_dir / "analysis" / "tables"
        tables.mkdir(parents=True)
        pd.DataFrame(
            {"value": [demand, 5.0], "unit": ["GWh", "MWh"]},
            index=pd.Index(["demand_energy", "unserved_energy"], name="metric"),
        ).to_csv(tables / "system_metrics.csv")
        pd.DataFrame(
            {"capacity_mw": [10.0], "generation_gwh": [3.0], "full_load_hours": [300.0]},
            index=pd.Index(["coal"], name="carrier"),
        ).to_csv(tables / "generation_by_carrier.csv")
        if battery:
            pd.DataFrame(
                {"technology": ["battery"], "site": ["Erode"], "bus": ["Erode"], "capacity_mw": [battery]}
            ).to_csv(run_dir / "scenario_technologies.csv", index=False)
        summary = tmp_path / f"{scenario}.yaml"
        summary.write_text(yaml.safe_dump({"output_dir": str(run_dir)}), encoding="utf-8")
        runs[scenario] = summary

    tables = collect(runs)

    metrics = tables["system_metrics"]
    assert list(metrics.columns) == ["unit", "baseline", "battery"]
    assert metrics.at["demand_energy", "unit"] == "GWh"
    assert tables["generation_gwh"].at["coal", "battery"] == 3.0
    assert tables["scenario_technologies"].loc["battery", "capacity_mw"] == 250.0


def test_offshore_wind_connects_only_to_allowed_buses(profiles):
    section = _section(profiles, offshore_wind={"gulf": {"bus": "Erode", "capacity_mw": 100}})
    section["parameters"]["offshore_wind"]["allowed_buses"] = ["Tirunelveli"]

    with pytest.raises(TechnologyError, match="can connect only to"):
        apply_technologies(_network(), section)


def test_shipped_offshore_data_uses_the_block2_profile_in_tirunelveli():
    import pypsa as _pypsa

    from scenario_technologies import load_parameters

    parameters = load_parameters({"data": "config/technologies.yaml"})["offshore_wind"]
    assert parameters["allowed_buses"] == ["Tirunelveli"]
    assert parameters["turbine_mw"] == 15
    profile = pd.read_csv(ROOT / parameters["profiles"], index_col=0, parse_dates=True)
    assert len(profile) == 8760 and profile.index[0] == pd.Timestamp("2025-04-01 00:00")
    assert profile["block2"].between(0.0, 1.0).all()

    network = _pypsa.Network()
    network.set_snapshots(profile.index[:24])
    network.add("Bus", ["Tirunelveli", "Madurai"], carrier="AC")
    section = {"data": "config/technologies.yaml", "offshore_wind": {"block2": {"bus": "Tirunelveli", "capacity_mw": 1020}}}
    apply_technologies(network, section)
    assert network.generators_t.p_max_pu["offshore_wind block2"].tolist() == profile["block2"].iloc[:24].tolist()
    with pytest.raises(TechnologyError, match="can connect only to"):
        apply_technologies(network, {**section, "offshore_wind": {"south": {"bus": "Madurai", "capacity_mw": 15, "profile": "block2"}}})


def test_offshore_profile_maps_the_weather_year_to_ist_model_hours():
    from prepare_offshore_wind_profile import FARM_MW, build_profile

    source_hours = pd.date_range("2020-01-08", "2021-01-06 23:00", freq="h")
    capacity_factor = pd.Series(range(len(source_hours)), index=source_hours, dtype=float)

    profile = build_profile(capacity_factor)["block2"]

    assert FARM_MW == 1020.0
    # 10:00 IST = 04:30 UTC; the value at the hour's middle is 05:00 UTC.
    assert profile[pd.Timestamp("2025-07-11 10:00")] == capacity_factor[pd.Timestamp("2020-07-11 05:00")]
    assert profile[pd.Timestamp("2026-01-03 12:00")] == capacity_factor[pd.Timestamp("2021-01-03 07:00")]
    # 7 January is not in the weather year; it takes 8 January 2020.
    assert profile[pd.Timestamp("2026-01-07 12:00")] == capacity_factor[pd.Timestamp("2020-01-08 07:00")]


def test_offshore_wind_capacity_is_a_fixed_choice_in_whole_turbines():
    shipped = {"data": "config/technologies.yaml"}
    site = {"bus": "Tirunelveli", "profile": "block2"}

    with pytest.raises(TechnologyError, match="scenario choice"):
        validate_section({**shipped, "offshore_wind": {"block2": {**site, "capacity_mw": "optimise"}}})
    with pytest.raises(TechnologyError, match="whole number of 15 MW turbines"):
        validate_section({**shipped, "offshore_wind": {"block2": {**site, "capacity_mw": 1000}}})
    validate_section({**shipped, "offshore_wind": {"block2": {**site, "capacity_mw": 1020}}})
    # Batteries remain optimisable.
    validate_section({**shipped, "battery": {"Erode": {"capacity_mw": "optimise"}}})


def test_offshore_scenarios_need_no_capacity_expansion_for_offshore():
    for name, optimised in (("offshore_wind", False), ("battery_offshore_wind", True)):
        config = load_run_config(ROOT / "config" / "scenarios" / f"{name}.yaml", ["run.mode=annual", "formulation.unit_commitment=none"])
        sites = parse_sites(config["technologies"])
        assert all(not site.optimise for site in sites if site.technology == "offshore_wind")
        assert needs_capacity_expansion(config["technologies"]) is optimised
