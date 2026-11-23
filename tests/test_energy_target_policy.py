from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pypsa
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import energy_target_policy as policy_module  # noqa: E402
from energy_target_policy import (  # noqa: E402
    DEFAULT_POLICY,
    apply_energy_target_policy,
    is_default,
    normalise_cost_overrides,
    normalise_policy,
    observed_energy_mwh,
    policy_tag,
    scale_to_energy,
    target_enabled,
)


def _network() -> pypsa.Network:
    """Two days of nuclear: one dispatchable unit with an outage hour, one fixed export."""
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2025-04-01", periods=48, freq="h"))
    network.add("Bus", "bus")
    network.add("Generator", "plant", bus="bus", carrier="nuclear", p_nom=100.0, marginal_cost=800.0)
    network.add("Generator", "export", bus="bus", carrier="nuclear", p_nom=10.0)
    network.add("Generator", "gas", bus="bus", carrier="oil_gas", p_nom=50.0, p_max_pu=0.9, marginal_cost=6025.0)
    p_max = pd.Series(1.0, index=network.snapshots)
    p_max.iloc[0] = 0.0  # outage hour
    network.generators_t.p_max_pu["plant"] = p_max
    network.generators_t.p_min_pu["plant"] = 0.8
    network.generators_t.p_max_pu["export"] = 0.5
    network.generators_t.p_min_pu["export"] = 0.5
    return network


def _observed(daily_mwh: dict[str, list[float]]):
    def fake(carrier: str, frequency: str) -> pd.Series:
        index = pd.date_range("2025-04-01", periods=2, freq="D")
        series = pd.Series(daily_mwh[carrier], index=index)
        return series.resample(frequency).sum()
    return fake


def test_scale_to_energy_meets_target_and_clips_at_one():
    base = np.array([[0.2, 1.0], [0.5, 0.0]])
    capacity = np.array([10.0, 20.0])
    weights = np.array([1.0, 1.0])
    values, reached = scale_to_energy(base, capacity, weights, 25.0)
    assert reached == pytest.approx(25.0)
    assert values.max() <= 1.0
    assert values[1, 1] == 0.0  # zero availability stays zero


def test_scale_to_energy_reports_shortfall_and_zero_target():
    base = np.array([[0.2], [0.0]])
    values, reached = scale_to_energy(base, np.array([10.0]), np.array([1.0, 1.0]), 50.0)
    assert reached == pytest.approx(10.0)
    assert values.tolist() == [[1.0], [0.0]]
    values, reached = scale_to_energy(base, np.array([10.0]), np.array([1.0, 1.0]), 0.0)
    assert reached == 0.0 and not values.any()


def test_availability_profile_keeps_fixed_schedules_and_matches_daily_energy(monkeypatch):
    network = _network()
    # 1,200 MWh per day; the fixed export supplies 120 MWh of it.
    monkeypatch.setattr(
        policy_module, "observed_energy_mwh", _observed({"nuclear": [1200.0, 1200.0]})
    )
    resolved, _, availability, _ = apply_energy_target_policy(
        network, {"nuclear": "availability", "availability_period": "daily", "availability_source": "carrier"}, {}
    )
    p_max = network.generators_t.p_max_pu
    assert (p_max["export"] == 0.5).all()
    daily_plant = p_max["plant"].mul(100.0).resample("D").sum()
    assert daily_plant.tolist() == pytest.approx([1080.0, 1080.0])
    assert p_max["plant"].iloc[0] == 0.0
    # The 0.8 minimum output is clipped to the new availability.
    assert (network.generators_t.p_min_pu["plant"] <= p_max["plant"] + 1e-12).all()
    assert availability["fixed_schedule_mwh"].tolist() == pytest.approx([120.0, 120.0])
    assert availability["shortfall_mwh"].sum() == pytest.approx(0.0)
    assert not target_enabled(network, "nuclear")
    assert target_enabled(network, "oil_gas")
    assert resolved["nuclear"] == "availability"


def test_static_availability_becomes_a_flat_profile(monkeypatch):
    network = _network()
    monkeypatch.setattr(
        policy_module, "observed_energy_mwh", _observed({"oil_gas": [240.0, 480.0]})
    )
    apply_energy_target_policy(
        network, {"oil_gas": "availability", "availability_period": "daily", "availability_source": "carrier"}, {}
    )
    gas = network.generators_t.p_max_pu["gas"]
    assert gas.iloc[:24].unique().tolist() == pytest.approx([0.2])
    assert gas.iloc[24:].unique().tolist() == pytest.approx([0.4])


def test_marginal_cost_override_and_default_policy():
    network = _network()
    _, costs, availability, cost_report = apply_energy_target_policy(
        network, None, {"oil_gas": 2500, "nuclear": None}
    )
    assert costs == {"oil_gas": 2500.0}
    assert network.generators.at["gas", "marginal_cost"] == 2500.0
    assert network.generators.at["plant", "marginal_cost"] == 800.0
    assert cost_report["previous_max_inr_per_mwh"].tolist() == [6025.0]
    assert availability.empty
    assert all(target_enabled(network, carrier) for carrier in ("oil_gas", "nuclear"))


def test_networks_without_a_policy_keep_targets():
    assert target_enabled(pypsa.Network(), "nuclear")


def test_policy_validation_and_fingerprint_tag():
    assert normalise_policy(None) == DEFAULT_POLICY
    assert is_default(normalise_policy({"nuclear": "TARGET"}), {})
    assert not is_default(DEFAULT_POLICY, {"oil_gas": 1.0})
    released = normalise_policy({"nuclear": "free"})
    assert policy_tag(released, {}) != policy_tag(DEFAULT_POLICY, {})
    with pytest.raises(ValueError, match="unknown|Unknown"):
        normalise_policy({"coal": "free"})
    with pytest.raises(ValueError, match="must be one of"):
        normalise_policy({"availability_period": "weekly"})
    with pytest.raises(ValueError, match="number or null"):
        normalise_cost_overrides({"oil_gas": True})
    with pytest.raises(ValueError, match="Unknown"):
        normalise_cost_overrides({"coal": 3000})


def _plant_files(tmp_path, monkeypatch, generation_rows):
    mapping = tmp_path / "mapping.csv"
    mapping.write_text(
        "source,plant_name,carrier,component_regex,note\n"
        "oil-gas,Base,oil_gas,^base_,\n"
        "oil-gas,Idle,oil_gas,^idle_,\n"
        "oil-gas,Gone,oil_gas,,not in the model\n",
        encoding="utf-8",
    )
    generation = tmp_path / "generation.csv"
    generation.write_text(
        "source,plant_name,sector,fiscal_year,month,generation_mu\n"
        + "".join(f"oil-gas,{plant},state,2025-26,2025-04-01,{mu}\n" for plant, mu in generation_rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(policy_module, "PLANT_MAPPING_FILE", mapping)
    monkeypatch.setattr(policy_module, "PLANT_GENERATION_FILE", generation)


def _gas_network() -> pypsa.Network:
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2025-04-01", periods=48, freq="h"))
    network.add("Bus", "bus")
    network.add("Generator", "base_1", bus="bus", carrier="oil_gas", p_nom=100.0, p_max_pu=0.9)
    network.add("Generator", "idle_1", bus="bus", carrier="oil_gas", p_nom=300.0, p_max_pu=0.9)
    return network


def test_plant_level_availability_follows_each_plants_share(tmp_path, monkeypatch):
    _plant_files(tmp_path, monkeypatch, [("Base", 3.0), ("Idle", 0.0), ("Gone", 0.0)])
    monkeypatch.setattr(policy_module, "observed_energy_mwh", _observed({"oil_gas": [1500.0, 1500.0]}))
    network = _gas_network()
    _, _, availability, _ = apply_energy_target_policy(network, {"oil_gas": "availability"}, {})
    p_max = network.generators_t.p_max_pu
    # The whole 3,000 MWh goes to the plant that generated: 3,000 / (100 MW x 48 h).
    assert p_max["base_1"].unique().tolist() == pytest.approx([0.625])
    assert (p_max["idle_1"] == 0.0).all()
    assert set(availability["plant"]) == {"Base", "Idle", "Gone"}
    assert availability["shortfall_mwh"].sum() == pytest.approx(0.0)


def test_plant_generation_without_model_units_is_rejected(tmp_path, monkeypatch):
    _plant_files(tmp_path, monkeypatch, [("Base", 3.0), ("Idle", 0.0), ("Gone", 1.0)])
    monkeypatch.setattr(policy_module, "observed_energy_mwh", _observed({"oil_gas": [1500.0, 1500.0]}))
    with pytest.raises(ValueError, match="no dispatchable model units"):
        apply_energy_target_policy(_gas_network(), {"oil_gas": "availability"}, {})


def test_every_unit_must_map_to_one_plant(tmp_path, monkeypatch):
    _plant_files(tmp_path, monkeypatch, [("Base", 3.0), ("Idle", 0.0), ("Gone", 0.0)])
    network = _gas_network()
    network.add("Generator", "unmapped_1", bus="bus", carrier="oil_gas", p_nom=10.0)
    with pytest.raises(ValueError, match="exactly one plant"):
        policy_module.plant_groups(network, "oil_gas")


def test_must_run_fixes_output_at_availability(tmp_path, monkeypatch):
    _plant_files(tmp_path, monkeypatch, [("Base", 3.0), ("Idle", 0.0), ("Gone", 0.0)])
    monkeypatch.setattr(policy_module, "observed_energy_mwh", _observed({"oil_gas": [1500.0, 1500.0]}))
    network = _gas_network()
    apply_energy_target_policy(network, {"oil_gas": "must_run"}, {})
    p_max = network.generators_t.p_max_pu[["base_1", "idle_1"]]
    p_min = network.generators_t.p_min_pu[["base_1", "idle_1"]]
    assert p_min.equals(p_max)
    assert not target_enabled(network, "oil_gas")


def _cheap_gas_network(hours: int = 48) -> pypsa.Network:
    """Cheap gas (base plant) against expensive backup, so the cap binds."""
    network = _gas_network()
    network.set_snapshots(pd.date_range("2025-04-01", periods=hours, freq="h"))
    network.generators.loc[["base_1", "idle_1"], "marginal_cost"] = 10.0
    network.add("Load", "load", bus="bus", p_set=50.0)
    network.add("Generator", "backup", bus="bus", carrier="coal", p_nom=100.0, marginal_cost=100.0)
    return network


def test_energy_cap_sets_flat_availability_and_plant_caps(tmp_path, monkeypatch):
    _plant_files(tmp_path, monkeypatch, [("Base", 3.0), ("Idle", 0.0), ("Gone", 0.0)])
    monkeypatch.setattr(policy_module, "observed_energy_mwh", _observed({"oil_gas": [600.0, 600.0]}))
    network = _cheap_gas_network()
    _, _, report, _ = apply_energy_target_policy(
        network, {"oil_gas": "energy_cap", "cap_period": "monthly", "cap_transfer": "prorate"}, {}
    )
    p_max = network.generators_t.p_max_pu
    assert set(p_max["base_1"].unique()) == {0.9} and set(p_max["idle_1"].unique()) == {0.9}
    caps = report.loc[report["kind"].eq("energy_cap")].set_index("plant")["cap_mwh"]
    assert caps["Base"] == pytest.approx(1200.0)
    assert caps["Idle"] == 0.0 and caps["Gone"] == 0.0
    assert not target_enabled(network, "oil_gas")


@pytest.mark.parametrize("hours, expected", [(48, 1200.0), (24, 600.0)])
def test_energy_cap_binds_and_is_prorated(tmp_path, monkeypatch, hours, expected):
    # April has 720 hours; the 1,200 MWh monthly cap is prorated to the solved hours.
    _plant_files(tmp_path, monkeypatch, [("Base", 3.0), ("Idle", 0.0), ("Gone", 0.0)])
    monkeypatch.setattr(policy_module, "observed_energy_mwh", _observed({"oil_gas": [600.0, 600.0]}))
    network = _cheap_gas_network(hours=720)
    monkeypatch.setattr(
        policy_module, "observed_energy_mwh",
        lambda carrier, frequency: pd.Series([1200.0], index=[pd.Timestamp("2025-04-01")]),
    )
    apply_energy_target_policy(network, {"oil_gas": "energy_cap", "cap_period": "monthly", "cap_transfer": "prorate"}, {})
    solved = network.copy(snapshots=network.snapshots[:hours])
    solved.optimize(
        solver_name="highs",
        extra_functionality=lambda n, s: policy_module.add_energy_caps(n, s),
    )
    gas = solved.generators_t.p[["base_1", "idle_1"]]
    assert gas["idle_1"].sum() == pytest.approx(0.0, abs=1e-6)
    assert gas["base_1"].sum() == pytest.approx(1200.0 * hours / 720, rel=1e-6)


def _capped_source(tmp_path, monkeypatch) -> tuple[pypsa.Network, Path]:
    """Two days: backup costs 100 on day 1 and 50 on day 2; gas (cost 10) is capped at 600 MWh."""
    _plant_files(tmp_path, monkeypatch, [("Base", 3.0), ("Idle", 0.0), ("Gone", 0.0)])
    monkeypatch.setattr(policy_module, "observed_energy_mwh", _observed({"oil_gas": [300.0, 300.0]}))
    network = _cheap_gas_network()
    backup_cost = pd.Series([100.0] * 24 + [50.0] * 24, index=network.snapshots)
    network.generators_t.marginal_cost["backup"] = backup_cost
    apply_energy_target_policy(network, {"oil_gas": "energy_cap", "cap_period": "monthly", "cap_transfer": "prorate"}, {})
    network.optimize(solver_name="highs", extra_functionality=lambda n, s: policy_module.add_energy_caps(n, s))
    folder = tmp_path / "source"
    folder.mkdir()
    network.export_to_netcdf(folder / "solved_network.nc")
    return network, folder


def test_allocation_transfer_follows_the_source_dispatch(tmp_path, monkeypatch):
    source, folder = _capped_source(tmp_path, monkeypatch)
    daily_source = source.generators_t.p["base_1"].groupby(source.snapshots.normalize()).sum()
    assert daily_source.iloc[0] == pytest.approx(600.0) and daily_source.iloc[1] == pytest.approx(0.0, abs=1e-6)
    network = _cheap_gas_network()
    _, _, report, _ = apply_energy_target_policy(
        network,
        {"oil_gas": "energy_cap", "cap_period": "monthly", "cap_transfer": "allocation", "cap_source": str(folder)},
        {},
    )
    assert report.loc[report["plant"].eq("Base"), "allocated_mwh"].iloc[0] == pytest.approx(600.0)
    for day in range(2):
        window = network.copy(snapshots=network.snapshots[24 * day: 24 * (day + 1)])
        window.optimize(solver_name="highs", extra_functionality=lambda n, s: policy_module.add_energy_caps(n, s))
        assert window.generators_t.p["base_1"].sum() == pytest.approx(daily_source.iloc[day], abs=1e-4)


def test_shadow_price_transfer_replaces_the_cap_with_a_cost_adder(tmp_path, monkeypatch):
    _, folder = _capped_source(tmp_path, monkeypatch)
    network = _cheap_gas_network()
    _, _, report, _ = apply_energy_target_policy(
        network,
        {"oil_gas": "energy_cap", "cap_period": "monthly", "cap_transfer": "shadow_price", "cap_source": str(folder)},
        {},
    )
    row = report.loc[report["plant"].eq("Base")].iloc[0]
    assert bool(row["binding_in_source"])
    # Using gas on day 1 saves 90 per MWh, on day 2 only 40: the cap's value lies in between.
    assert 40.0 - 1e-6 <= row["shadow_price_inr_per_mwh"] <= 90.0 + 1e-6
    cost = network.generators_t.marginal_cost["base_1"]
    assert cost.unique().tolist() == pytest.approx([10.0 + row["shadow_price_inr_per_mwh"]])
    caps = network.meta[policy_module.CAPS_KEY]
    assert [cap["plant"] for cap in caps] == ["Idle"]  # only the zero cap stays a hard limit


def test_cap_transfer_needs_a_source(tmp_path, monkeypatch):
    _plant_files(tmp_path, monkeypatch, [("Base", 3.0), ("Idle", 0.0), ("Gone", 0.0)])
    monkeypatch.setattr(policy_module, "observed_energy_mwh", _observed({"oil_gas": [300.0, 300.0]}))
    with pytest.raises(ValueError, match="cap_source"):
        apply_energy_target_policy(
            _cheap_gas_network(), {"oil_gas": "energy_cap", "cap_transfer": "allocation", "cap_source": ""}, {}
        )


def _two_month_bio_network(monkeypatch, april_mwh: float, may_mwh: float) -> pypsa.Network:
    """30 April and 1 May: cheap bio-power (two units) against expensive backup."""
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2025-04-30", periods=48, freq="h"))
    network.add("Bus", "bus")
    network.add("Load", "load", bus="bus", p_set=pd.Series([40.0] * 24 + [60.0] * 24, index=network.snapshots))
    network.add("Generator", "bio_1", bus="bus", carrier="bio_power", p_nom=50.0, marginal_cost=10.0)
    network.add("Generator", "bio_2", bus="bus", carrier="bio_power", p_nom=50.0, marginal_cost=20.0)
    network.add("Generator", "backup", bus="bus", carrier="coal", p_nom=200.0, marginal_cost=100.0)
    monkeypatch.setattr(
        policy_module, "observed_energy_mwh",
        lambda carrier, frequency: pd.Series(
            [april_mwh, may_mwh], index=pd.DatetimeIndex(["2025-04-01", "2025-05-01"])
        ),
    )
    return network


def _solve_with_caps(network: pypsa.Network) -> pypsa.Network:
    network.optimize(solver_name="highs", extra_functionality=lambda n, s: policy_module.add_energy_caps(n, s))
    return network


def test_sparse_annual_cap_matches_a_dense_row(monkeypatch):
    # The annual cap spans April and May here, so it is written with monthly auxiliaries.
    sparse = _two_month_bio_network(monkeypatch, 500.0, 700.0)
    apply_energy_target_policy(sparse, {"bio_power": "energy_cap", "cap_period": "annual", "cap_transfer": "prorate"}, {})
    _solve_with_caps(sparse)
    assert "EnergyCap_bio_power_0_2025043000_monthly_energy" in sparse.model.variables

    dense = _two_month_bio_network(monkeypatch, 500.0, 700.0)
    apply_energy_target_policy(dense, {"bio_power": "energy_cap", "cap_period": "annual", "cap_transfer": "prorate"}, {})
    caps = dense.meta[policy_module.CAPS_KEY]
    dense.meta = {**dense.meta, policy_module.CAPS_KEY: []}

    def dense_row(n, s):
        p = n.model["Generator-p"]
        dim = next(d for d in p.dims if d != "snapshot")
        n.model.add_constraints(p.sel({dim: ["bio_1", "bio_2"]}).sum() <= caps[0]["cap_mwh"], name="dense_cap")

    dense.optimize(solver_name="highs", extra_functionality=dense_row)
    assert caps[0]["cap_mwh"] == pytest.approx(1200.0)
    assert sparse.objective == pytest.approx(dense.objective)
    bio = sparse.generators_t.p[["bio_1", "bio_2"]].sum().sum()
    assert bio == pytest.approx(1200.0)


def test_monthly_caps_stay_separate_in_a_multi_month_solve(monkeypatch):
    # April may use 300 MWh, May nothing; a merged cap would let May use April's fuel.
    # Caps refer to the hours each period has in the network (here one day each).
    network = _two_month_bio_network(monkeypatch, 300.0, 0.0)
    apply_energy_target_policy(network, {"bio_power": "energy_cap", "cap_period": "monthly", "cap_transfer": "prorate"}, {})
    _solve_with_caps(network)
    bio = network.generators_t.p[["bio_1", "bio_2"]].sum(axis=1)
    assert bio.iloc[:24].sum() == pytest.approx(300.0)
    assert bio.iloc[24:].sum() == pytest.approx(0.0, abs=1e-6)


def test_energy_cap_validation():
    with pytest.raises(ValueError, match="must be one of"):
        normalise_policy({"nuclear": "energy_cap"})
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        normalise_policy({"oil_gas_cap_availability": 1.5})
    resolved = normalise_policy({"oil_gas": "energy_cap", "cap_period": "Monthly",
                                 "bio_power_cap_availability": "0.5"})
    assert resolved["cap_period"] == "monthly" and resolved["bio_power_cap_availability"] == 0.5


def test_shipped_plant_mapping_covers_the_model():
    network = pypsa.Network(str(ROOT / "model"))
    for carrier in policy_module.PLANT_LEVEL_CARRIERS:
        groups = policy_module.plant_groups(network, carrier)
        shares = policy_module.plant_monthly_shares(carrier, list(groups))
        fy = shares.loc["2025-04-01":"2026-03-01"]
        assert len(fy) == 12
        assert fy.sum(axis=1).to_numpy() == pytest.approx(1.0)


@pytest.mark.parametrize("carrier", ["nuclear", "oil_gas", "bio_power", "small_hydro"])
def test_observed_energy_is_consistent_across_frequencies(carrier):
    monthly = observed_energy_mwh(carrier, "MS")
    daily = observed_energy_mwh(carrier, "D")
    assert len(monthly) == 12 and len(daily) == 365
    assert daily.resample("MS").sum().to_numpy() == pytest.approx(monthly.to_numpy())
