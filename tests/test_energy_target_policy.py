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
        network, {"nuclear": "availability", "availability_period": "daily"}, {}
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
        network, {"oil_gas": "availability", "availability_period": "daily"}, {}
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


@pytest.mark.parametrize("carrier", ["nuclear", "oil_gas", "bio_power", "small_hydro"])
def test_observed_energy_is_consistent_across_frequencies(carrier):
    monthly = observed_energy_mwh(carrier, "MS")
    daily = observed_energy_mwh(carrier, "D")
    assert len(monthly) == 12 and len(daily) == 365
    assert daily.resample("MS").sum().to_numpy() == pytest.approx(monthly.to_numpy())
