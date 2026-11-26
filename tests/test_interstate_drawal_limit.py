from pathlib import Path
import sys

import pandas as pd
import pypsa
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import interstate_drawal_limit as drawal  # noqa: E402

SNAPSHOTS = pd.date_range("2025-06-30 22:00", periods=4, freq="h")  # June and July


def settings(tmp_path: Path, re_mw: float = 100.0) -> dict:
    capability = tmp_path / "ttc.csv"
    pd.DataFrame({
        "area": ["Tamil Nadu"] * 4 + ["Karnataka"],
        "month": ["2025-06", "2025-06", "2025-07", "2025-07", "2025-07"],
        "latest": [False, True, True, True, True],
        "ttc_mw": [900.0, 1000.0, 1200.0, 1200.0, 50.0],
        "atc_mw": [850.0, 950.0, 1150.0, 1150.0, 40.0],
    }).to_csv(capability, index=False)
    metered = tmp_path / "metered.csv"
    pd.DataFrame({"snapshot": SNAPSHOTS, "interstate_re_mw": re_mw}).to_csv(metered, index=False)
    return {"drawal_limit": "ttc", "transfer_capability": str(capability), "metered_interchange": str(metered)}


def small_network(neighbour_supply: float = 500.0) -> pypsa.Network:
    """One Tamil Nadu area fed by a neighbour with a fixed net supply, one central station and own plants."""
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    n.add("Bus", "A", kind="TN balancing area", carrier="AC")
    n.add("Bus", "Neighbour", kind="neighbouring state", carrier="AC")
    n.add("Carrier", ["AC", "coal"])
    n.add("Link", "A–Neighbour (AC tie) ←", bus0="Neighbour", bus1="A", p_nom=5000.0, efficiency=0.97, carrier="AC")
    n.add("Link", "A–Neighbour (AC tie) →", bus0="A", bus1="Neighbour", p_nom=5000.0, efficiency=0.97, carrier="AC")
    n.add("Load", "interstate_import", bus="Neighbour", p_set=-neighbour_supply)
    n.add("Load", "A load", bus="A", p_set=2000.0)
    n.add("Generator", "vallur_tpp__unit_01", bus="A", p_nom=1500.0, marginal_cost=1000.0, carrier="coal")
    n.add("Generator", "tn_own_plant__unit_01", bus="A", p_nom=3000.0, marginal_cost=5000.0, carrier="coal")
    return n


def test_monthly_limit_uses_the_latest_revision_and_fills_missing_months(tmp_path):
    limits = drawal.limit_mw(pd.date_range("2025-06-30 23:00", "2025-09-01", freq="31D"), settings(tmp_path))
    assert limits.tolist() == [1000.0, 1200.0, 1200.0]  # June latest; July; August takes July's


def test_drawal_is_limited_in_every_snapshot(tmp_path):
    n = small_network()
    report = drawal.apply_drawal_limit(n, settings(tmp_path))
    assert report.fixed_drawal_mw.iloc[0] == pytest.approx(500 * 0.97)

    status, _ = n.optimize(solver_name="highs", extra_functionality=drawal.add_drawal_limit, assign_all_duals=True)

    assert status == "ok"
    value = drawal.drawal_mw(n)
    expected = report.limit_mw  # the cheap central station runs up to the limit
    assert value.to_numpy() == pytest.approx(expected.to_numpy())
    central = n.generators_t.p["vallur_tpp__unit_01"]
    assert central.to_numpy() == pytest.approx((expected - 100.0 - 500 * 0.97).to_numpy())
    assert n.generators_t.p["tn_own_plant__unit_01"].gt(0).all()


def test_off_without_settings_or_with_none(tmp_path):
    n = small_network()
    assert drawal.apply_drawal_limit(n, None).empty
    assert drawal.apply_drawal_limit(n, {**settings(tmp_path), "drawal_limit": "none"}).empty
    assert drawal.tag(n) is None


def test_fixed_schedule_above_the_limit_raises(tmp_path):
    with pytest.raises(drawal.DrawalLimitError, match="fixed interstate schedule alone"):
        drawal.apply_drawal_limit(small_network(neighbour_supply=2000.0), settings(tmp_path))


def test_settings_are_validated():
    with pytest.raises(drawal.DrawalLimitError, match="Unknown interstate keys"):
        drawal.validate_section({"limit": "ttc"})
    with pytest.raises(drawal.DrawalLimitError, match="drawal_limit"):
        drawal.validate_section({"drawal_limit": "rm"})
