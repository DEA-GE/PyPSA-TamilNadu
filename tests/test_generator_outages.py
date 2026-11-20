"""Coal unit availability: auxiliary consumption, planned maintenance and forced outages."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generator_outages import (  # noqa: E402
    OutageError,
    apply_outages,
    forced_outage_parameters,
    hourly_outage,
    out_of_service_units,
    read_auxiliary,
    read_capability,
    read_record,
    sample_forced_outages,
    validate_section,
)
from run_config import ConfigError, load_run_config  # noqa: E402

DAYS = pd.date_range("2025-04-01", periods=4, freq="D")
HOURS = pd.date_range("2025-04-01", periods=4 * 24, freq="h")


def _network(hours: pd.DatetimeIndex = HOURS, demand: list[float] | np.ndarray | None = None) -> pypsa.Network:
    network = pypsa.Network()
    network.set_snapshots(hours)
    network.add("Carrier", ["AC", "coal", "gas"])
    network.add("Bus", "Erode", carrier="AC")
    load = np.full(len(hours), 150.0) if demand is None else np.asarray(demand, dtype=float)
    network.add("Load", "demand", bus="Erode", p_set=load)
    network.add(
        "Generator", ["coal_a", "coal_b"], bus="Erode", carrier="coal", p_nom=100.0,
        p_max_pu=0.85, p_min_pu=0.0, marginal_cost=3_000.0, committable=False,
    )
    network.add("Generator", "gas", bus="Erode", carrier="gas", p_nom=300.0, marginal_cost=9_000.0)
    return network


def _files(
    tmp_path: Path, rows: list[dict], aux: dict[str, float] | None = None,
    capability: dict[str, float] | None = None,
) -> dict:
    record = pd.DataFrame(rows, columns=["component", "date", "kind", "reason", "outage_days", "capacity_mw"])
    record_path = tmp_path / "record.csv"
    record.to_csv(record_path, index=False)
    aux = aux or {"coal_a": 8.0, "coal_b": 6.0}
    aux_path = tmp_path / "aux.csv"
    pd.DataFrame({"component": list(aux), "auxiliary_consumption_pct": list(aux.values())}).to_csv(aux_path, index=False)
    capability = capability or {"coal_a": 1.0, "coal_b": 1.0}
    capability_path = tmp_path / "capability.csv"
    pd.DataFrame({"component": list(capability), "capability_pu": list(capability.values())}).to_csv(
        capability_path, index=False
    )
    return {
        "enabled": True, "base_availability": "net_of_auxiliary", "partial_derating": "demonstrated",
        "planned": "actual", "forced": "actual", "seed": 1, "record": str(record_path),
        "auxiliary": str(aux_path), "capability": str(capability_path), "maintenance_schedule": None,
    }


def _row(component, day, kind, share=1.0, reason="x", capacity=100.0):
    return {"component": component, "date": pd.Timestamp(day).strftime("%Y-%m-%d"), "kind": kind,
            "reason": reason, "outage_days": share, "capacity_mw": capacity}


def test_partial_days_are_placed_next_to_the_outage_they_belong_to():
    rows = pd.DataFrame([
        _row("a", DAYS[0], "forced", 0.25),   # alone: middle of the day, 09:00-15:00
        _row("u", DAYS[1], "planned", 0.5),   # followed by an outage: end of the day
        _row("u", DAYS[2], "planned", 1.0),
        _row("u", DAYS[3], "planned", 0.25),  # preceded by an outage: start of the day
    ])
    shares = hourly_outage(rows, HOURS)
    forced = shares["forced"]["a"]
    assert forced.sum() == pytest.approx(6.0)
    assert forced["2025-04-01 09:00":"2025-04-01 14:00"].eq(1.0).all()
    planned = shares["planned"]["u"]
    assert planned["2025-04-02 00:00":"2025-04-02 11:00"].eq(0.0).all()
    assert planned["2025-04-02 12:00":"2025-04-04 05:00"].eq(1.0).all()
    assert planned["2025-04-04 06:00":].eq(0.0).all()
    # A fractional hour at the edge of an outage.
    edge = hourly_outage(pd.DataFrame([_row("u", DAYS[1], "planned", 0.9), _row("u", DAYS[2], "planned")]), HOURS)
    assert edge["planned"]["u"]["2025-04-02 02:00"] == pytest.approx(0.6)


def test_outages_and_net_availability_set_the_coal_units_hourly_limit(tmp_path):
    network = _network()
    settings = _files(tmp_path, [
        _row("coal_a", DAYS[1], "planned"),
        _row("coal_b", DAYS[2], "forced", 0.5),
        _row("coal_b", DAYS[3], "reserve_shutdown"),  # economic: never imposed
    ])
    result = apply_outages(network, settings)
    p_max = network.generators_t.p_max_pu
    assert network.generators.loc["coal_a", "p_max_pu"] == pytest.approx(0.92)
    assert p_max["coal_a"]["2025-04-01"].eq(0.92).all()
    assert p_max["coal_a"]["2025-04-02"].eq(0.0).all()
    assert p_max["coal_b"]["2025-04-03"].sum() == pytest.approx(0.94 * 12)
    assert p_max["coal_b"]["2025-04-04"].eq(0.94).all()
    assert "gas" not in p_max.columns
    assert result.units.loc["coal_a", "planned_hours"] == pytest.approx(24.0)
    assert result.units.loc["coal_b", "forced_hours"] == pytest.approx(12.0)
    status, _ = network.optimize(solver_name="highs")
    assert status == "ok"
    assert network.generators_t.p["coal_a"]["2025-04-02"].abs().max() < 1e-6


def test_demonstrated_capability_caps_running_units(tmp_path):
    settings = _files(tmp_path, [_row("coal_a", DAYS[1], "planned", 0.5)], capability={"coal_a": 0.75})
    network = _network()
    result = apply_outages(network, settings)
    p_max = network.generators_t.p_max_pu
    assert np.allclose(p_max["coal_a"]["2025-04-01"], 0.92 * 0.75)
    assert p_max["coal_a"]["2025-04-02"].sum() == pytest.approx(0.92 * 0.75 * 12)
    # Units missing from the capability table are not capped.
    assert p_max["coal_b"].eq(0.94).all()
    assert result.units.loc["coal_a", "capability_pu"] == pytest.approx(0.75)
    network = _network()
    apply_outages(network, {**settings, "partial_derating": "none"})
    assert network.generators_t.p_max_pu["coal_a"]["2025-04-01"].eq(0.92).all()


def test_choices_switch_kinds_and_base_availability(tmp_path):
    settings = _files(tmp_path, [_row("coal_a", DAYS[1], "planned"), _row("coal_b", DAYS[2], "forced")])
    network = _network()
    apply_outages(network, {**settings, "planned": "none", "forced": "none", "base_availability": "gross"})
    assert network.generators_t.p_max_pu[["coal_a", "coal_b"]].eq(1.0).all().all()
    network = _network()
    apply_outages(network, {**settings, "base_availability": "model"})
    assert network.generators_t.p_max_pu["coal_b"]["2025-04-01"].eq(0.85).all()
    network = _network()
    assert apply_outages(network, {**settings, "enabled": False}) is None
    assert network.generators_t.p_max_pu.empty


def test_units_out_all_period_are_out_of_service_and_start_offline(tmp_path):
    rows = [_row("coal_a", day, "forced") for day in DAYS] + [_row("coal_b", DAYS[0], "planned")]
    settings = _files(tmp_path, rows)
    assert out_of_service_units(read_record(settings["record"])) == ["coal_a"]
    assert out_of_service_units(read_record(settings["record"]), DAYS[:1]) == ["coal_a", "coal_b"]
    network = _network()
    network.generators.loc[["coal_a", "coal_b"], "committable"] = True
    network.generators.loc[["coal_a", "coal_b"], "up_time_before"] = 168
    result = apply_outages(network, {**settings, "forced": "none"})
    # Out of service applies even without forced outages.
    assert network.generators_t.p_max_pu["coal_a"].eq(0.0).all()
    assert result.units.loc["coal_a", "out_of_service_hours"] == pytest.approx(96.0)
    assert network.generators.loc["coal_a", ["up_time_before", "down_time_before"]].tolist() == [0, 168]
    assert network.generators.loc["coal_b", ["up_time_before", "down_time_before"]].tolist() == [0, 168]


def test_forced_outage_parameters_merge_consecutive_days():
    record = pd.DataFrame([
        _row("a", "2025-04-01", "forced", 1.0, capacity=210.0),
        _row("a", "2025-04-02", "forced", 1.0, capacity=210.0),
        _row("a", "2025-04-03", "forced", 0.5, capacity=210.0),
        _row("a", "2025-04-10", "forced", 0.25, capacity=210.0),
        _row("a", "2025-04-20", "planned", 1.0, capacity=210.0),
        _row("b", "2025-04-30", "commercial", 1.0, capacity=210.0),
    ])
    record["date"] = pd.to_datetime(record.date)
    parameters = forced_outage_parameters(record, pd.Series({"a": 210.0, "b": 210.0}))
    row = parameters.loc["200-400 MW"]
    assert sorted(row.durations_h) == [6.0, 60.0]
    assert row.outages == 2
    exposure = 2 * 30 * 24 - 2 * 24
    assert row.exposure_h == pytest.approx(exposure)
    assert row.forced_outage_rate == pytest.approx(66.0 / exposure)


def test_sampled_outages_are_reproducible_per_unit_and_match_the_rate():
    parameters = pd.DataFrame(
        {"forced_outage_rate": [0.1], "mean_time_between_h": [540.0], "durations_h": [[30.0, 60.0, 90.0]]},
        index=pd.Index(["200-400 MW"], name="size_class"),
    )
    hours = pd.date_range("2025-04-01", periods=24 * 365 * 4, freq="h")
    units = pd.Series(210.0, index=[f"u{i}" for i in range(6)])
    first = sample_forced_outages(parameters, units, hours, seed=7)
    again = sample_forced_outages(parameters, units[["u3", "u1"]], hours, seed=7)
    assert first[["u3", "u1"]].equals(again)
    assert not first.u0.equals(first.u1)
    assert not first.equals(sample_forced_outages(parameters, units, hours, seed=8))
    assert first.to_numpy().mean() == pytest.approx(60.0 / 600.0, abs=0.02)
    assert first.isin([0.0, 1.0]).to_numpy().mean() > 0.99


def test_settings_are_validated():
    good = validate_section({
        "enabled": True, "base_availability": "Gross", "partial_derating": "none", "planned": "actual",
        "forced": "sampled", "seed": 3, "record": "r.csv", "auxiliary": "a.csv", "capability": "c.csv",
        "maintenance_schedule": None,
    })
    assert good["base_availability"] == "gross"
    with pytest.raises(OutageError, match="partial_derating"):
        validate_section({**good, "partial_derating": "observed"})
    with pytest.raises(OutageError, match="planned"):
        validate_section({**good, "planned": "sometimes"})
    with pytest.raises(OutageError, match="maintenance_schedule"):
        validate_section({**good, "maintenance_schedule": "s.csv"})
    with pytest.raises(OutageError, match="seed"):
        validate_section({**good, "seed": -1})
    with pytest.raises(OutageError, match="unknown"):
        validate_section({**good, "rate": 0.1})


def test_run_config_carries_the_outages_section(tmp_path):
    config = load_run_config(None, ["run.name=t", "outages.enabled=true", "outages.forced=sampled"])
    assert config["outages"]["forced"] == "sampled"
    with pytest.raises(ConfigError, match="outages.forced"):
        load_run_config(None, ["run.name=t", "outages.forced=often"])
    defaults = load_run_config(None, ["run.name=t"])
    assert defaults["outages"]["enabled"] is False


def test_shipped_outage_data_covers_every_committable_coal_unit():
    generators = pd.read_csv(ROOT / "model" / "generators.csv", index_col=0)
    coal = set(generators.index[(generators.carrier == "coal") & generators.committable])
    auxiliary = read_auxiliary()
    assert set(auxiliary.index) == coal
    assert auxiliary.between(5.0, 13.0).all()
    record = read_record()
    assert set(record.component) <= coal
    assert not record.duplicated(["component", "date"]).any()
    assert record.date.min() >= pd.Timestamp("2025-04-01") and record.date.max() <= pd.Timestamp("2026-03-31")
    # Tuticorin TPS units 1-2 (cable-gallery fire), Tuticorin (P) TPP (no fuel
    # supply agreement; 2 June 2025 is missing from the data) and TCP were out all year.
    assert set(out_of_service_units(record)) == {
        "tuticorin_tps__unit_01", "tuticorin_tps__unit_02",
        "tuticorin_p_tpp__unit_01", "tuticorin_p_tpp__unit_02", "tcp_limited__unit_01",
    }
    # North Chennai Stage III was commissioned on 24 January 2026.
    late = record[(record.component == "north_chennai_tps__unit_06") & (record.kind == "commissioning")]
    assert late.date.max() == pd.Timestamp("2026-01-23")
    # Every unit has a capability; capped units can still run at minimum load.
    capability = read_capability()
    assert set(capability.index) == coal
    assert capability.between(0.7, 1.0).all()
    assert capability["north_chennai_tps__unit_04"] == pytest.approx(0.76)
    static = capability * (1.0 - auxiliary / 100.0)
    assert (static >= generators.loc[static.index, "p_min_pu"]).all()


def test_maintenance_is_optimised_into_low_demand_days(tmp_path):
    hours = pd.date_range("2025-04-01", periods=20 * 24, freq="h")
    demand = np.where(np.arange(len(hours)) < 10 * 24, 180.0, 80.0)
    network = _network(hours, demand)
    rows = [_row(unit, day, "planned") for unit in ("coal_a", "coal_b") for day in hours[:72:24]]
    settings = {**_files(tmp_path, rows), "planned": "optimise", "forced": "none"}
    result = apply_outages(network, settings)
    schedule = result.maintenance_schedule
    assert schedule.groupby("component").outage_days.sum().to_dict() == {"coal_a": 3.0, "coal_b": 3.0}
    assert (pd.to_datetime(schedule.date) >= pd.Timestamp("2025-04-11")).all()
    p_max = network.generators_t.p_max_pu
    assert (p_max[["coal_a", "coal_b"]] == 0.0).sum().to_dict() == {"coal_a": 72, "coal_b": 72}
    # A saved schedule is reused instead of scheduling again.
    path = tmp_path / "schedule.csv"
    schedule.to_csv(path, index=False)
    again = _network(hours, demand)
    apply_outages(again, {**settings, "maintenance_schedule": str(path)})
    assert again.generators_t.p_max_pu[["coal_a", "coal_b"]].equals(p_max[["coal_a", "coal_b"]])
