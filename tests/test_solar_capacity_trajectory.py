from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from solar_capacity_trajectory import (  # noqa: E402
    CAPACITY_FILE,
    RELEASE_FILE,
    monthly_solar_capacity,
    read_capacity_releases,
    read_monthly_capacity,
    solar_capacity_mw,
)


FY_HOURS = pd.date_range("2025-04-01 00:00", "2026-03-31 23:00", freq="h")


def test_monthly_capacity_matches_every_mnre_release():
    capacity = read_monthly_capacity()
    releases = read_capacity_releases()

    np.testing.assert_allclose(
        capacity.loc[releases.index, "solar_mw"], releases["mnre_solar_total_mw"], atol=0.011
    )
    np.testing.assert_allclose(
        capacity.loc[releases.index, "wind_mw"], releases["mnre_wind_mw"], atol=0.011
    )


def test_fy_capacity_is_grid_connected_and_rising():
    monthly = monthly_solar_capacity()

    assert len(monthly) == 13
    assert monthly.loc["2026-03-31", "solar_grid_connected_mw"] == pytest.approx(13_579.67 - 74.55)
    assert monthly.loc["2025-03-31", "solar_off_grid_mw"] == pytest.approx(70.4)
    assert monthly["solar_grid_connected_mw"].is_monotonic_increasing


def test_capacity_is_linear_between_month_ends():
    monthly = monthly_solar_capacity()
    capacity = solar_capacity_mw(FY_HOURS, monthly)

    april_start = monthly.loc["2025-03-31", "solar_grid_connected_mw"]
    may_start = monthly.loc["2025-04-30", "solar_grid_connected_mw"]
    assert capacity.loc["2025-04-01 00:00"] == pytest.approx(april_start)
    assert capacity.loc["2025-05-01 00:00"] == pytest.approx(may_start)
    assert capacity.loc["2025-04-16 00:00"] == pytest.approx((april_start + may_start) / 2)
    assert capacity.is_monotonic_increasing


def test_snapshots_beyond_monthly_data_are_rejected():
    with pytest.raises(ValueError, match="beyond the monthly capacity data"):
        solar_capacity_mw(pd.DatetimeIndex(["2026-04-02 00:00"]))


def test_monthly_capacity_disagreeing_with_mnre_is_rejected():
    capacity = pd.read_csv(CAPACITY_FILE, parse_dates=["as_on_date"]).set_index("as_on_date")
    capacity.loc["2025-07-31", "solar_mw"] += 10.0

    with pytest.raises(ValueError, match="disagrees with MNRE"):
        monthly_solar_capacity(capacity=capacity)


def test_inconsistent_mnre_release_is_rejected(tmp_path):
    releases = pd.read_csv(RELEASE_FILE)
    releases.loc[releases["as_on_date"].eq("2025-07-31"), "mnre_solar_rooftop_mw"] += 5.0
    path = tmp_path / "releases.csv"
    releases.to_csv(path, index=False)

    with pytest.raises(ValueError, match="solar types do not sum"):
        read_capacity_releases(path)


def test_builder_scales_solar_by_installed_share_and_calibrates_annual_energy():
    from build_9ba_model import apply_solar_capacity_trajectory, read_observed_annual_solar_mwh

    generators = pd.DataFrame(
        {
            "name": ["solar_a", "solar_b", "wind_a"],
            "carrier": ["solar", "solar", "wind"],
            "p_nom": [9_000.0, 5_000.0, 1_000.0],
        }
    )
    profiles = pd.DataFrame(0.2, index=range(len(FY_HOURS)), columns=generators["name"])
    snapshots = pd.Series(FY_HOURS.strftime("%Y-%m-%d %H:%M:%S"))

    scaled, audit, calibration = apply_solar_capacity_trajectory(profiles, generators, snapshots)

    capacity = solar_capacity_mw(FY_HOURS).to_numpy()
    multiplier = calibration.loc[0, "availability_multiplier"]
    np.testing.assert_allclose(scaled["solar_a"], 0.2 * capacity / 14_000.0 * multiplier)
    np.testing.assert_allclose(scaled["solar_a"], scaled["solar_b"])
    assert (scaled["wind_a"] == 0.2).all()
    potential_mwh = (scaled[["solar_a", "solar_b"]] * [9_000.0, 5_000.0]).to_numpy().sum()
    assert potential_mwh == pytest.approx(read_observed_annual_solar_mwh())
    assert len(audit) == 13
