from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import solar_seasonal_correction as ssc  # noqa: E402


def test_soiling_builds_up_and_resets_on_rain_and_cleaning():
    days = pd.date_range("2025-01-01", periods=60, freq="D")
    rain = pd.Series(0.0, index=days)
    rain.iloc[10] = ssc.RAIN_CLEANING_THRESHOLD_MM

    loss = ssc.soiling_loss(rain)

    assert loss.iloc[9] == pytest.approx(10 * ssc.SOILING_RATE_PER_DAY)
    assert loss.iloc[10] == 0.0
    cleaning_day = 10 + ssc.MANUAL_CLEANING_INTERVAL_DAYS
    assert loss.iloc[cleaning_day - 1] > 0.0
    assert loss.iloc[cleaning_day] == 0.0
    assert loss.max() <= ssc.MAX_SOILING_LOSS


def test_wind_cooling_gain_rises_with_wind_and_is_neutral_at_huld_equivalent_wind():
    neutral_module_wind = (1.0 / ssc.HULD_IRRADIANCE_COEFFICIENT - ssc.FAIMAN_U0) / ssc.FAIMAN_U1
    neutral_10m_kmh = neutral_module_wind / ssc.MODULE_HEIGHT_WIND_FACTOR * 3.6
    gain = ssc.wind_cooling_gain(pd.Series([neutral_10m_kmh, 10.0, 20.0, 30.0]))

    assert gain.iloc[0] == pytest.approx(1.0)
    assert gain.is_monotonic_increasing


def test_smooth_factor_keeps_monthly_means_and_has_no_month_end_jumps():
    factors = pd.Series([0.93, 0.98, 0.95, 0.94, 0.98, 1.07, 1.10, 1.09, 1.05, 1.02, 1.00, 0.97], index=range(1, 13))
    days = pd.date_range("2025-04-01", "2026-03-31", freq="D")

    daily = ssc.smooth_monthly_factor(days, factors)

    monthly = daily.groupby(daily.index.month).mean()
    np.testing.assert_allclose(monthly.sort_index(), factors.sort_index(), atol=1e-9)
    assert daily.diff().abs().max() < 0.01


def test_historical_capacity_matches_mnre_fiscal_year_ends():
    capacity = ssc.historical_solar_capacity()
    benchmark = pd.read_excel(ROOT / "data" / "source" / "Capacity_Metatable_Data_Sept26.xlsx")
    solar = benchmark.loc[
        benchmark["State"].eq("Tamil Nadu") & benchmark["Source"].eq("solar")
    ].set_index("Year")["Capacity in MW"]

    # The monthly series agrees with the annual table from March 2022; the
    # March 2019-2021 values differ by 44-52 MW (later revisions of one source).
    for year, end in [("2021-22", "2022-03-31"), ("2023-24", "2024-03-31"), ("2025-26", "2026-03-31")]:
        assert capacity.loc[end] == pytest.approx(solar.loc[year], abs=0.011)
    assert abs(capacity.loc["2020-03-31"] - solar.loc["2019-20"]) < 60.0
    assert capacity.index.min() == pd.Timestamp("2020-03-31")
    assert capacity.index.max() == pd.Timestamp("2026-03-31")


def test_stored_factors_reproduce_the_fit_and_exclude_the_test_year():
    stored = pd.read_csv(ssc.FACTOR_FILE)
    refit = ssc.fit_seasonal_factors()

    np.testing.assert_allclose(stored["seasonal_factor"], refit["seasonal_factor"], rtol=0, atol=1e-9)
    training = stored[[f"ratio_FY{year}" for year in ssc.TRAINING_FISCAL_YEARS]].median(axis=1)
    np.testing.assert_allclose(stored["seasonal_factor"], training, atol=1e-12)
    assert ssc.TEST_FISCAL_YEAR not in ssc.TRAINING_FISCAL_YEARS


def test_factors_fitted_to_another_profile_are_rejected(tmp_path):
    stored = pd.read_csv(ssc.FACTOR_FILE)
    stored["profile_input_sha256"] = "0" * 64
    path = tmp_path / "factors.csv"
    stored.to_csv(path, index=False)

    with pytest.raises(ValueError, match="different renewable profile"):
        ssc.read_seasonal_factors(path)


def test_builder_corrects_solar_by_area_and_leaves_other_carriers():
    from build_9ba_model import apply_solar_seasonal_correction

    snapshots = pd.Series(
        pd.date_range("2025-04-01", "2026-03-31 23:00", freq="h").strftime("%Y-%m-%d %H:%M:%S")
    )
    generators = pd.DataFrame(
        {
            "name": ["solar_tir", "solar_che", "wind_tir"],
            "bus": ["Tirunelveli", "Chennai North", "Tirunelveli"],
            "carrier": ["solar", "solar", "wind"],
            "p_nom": [100.0, 50.0, 80.0],
        }
    )
    profiles = pd.DataFrame(0.5, index=range(len(snapshots)), columns=generators["name"])

    corrected, audit = apply_solar_seasonal_correction(profiles, generators, snapshots)

    expected = ssc.hourly_solar_correction(pd.DatetimeIndex(pd.to_datetime(snapshots)))
    np.testing.assert_allclose(corrected["solar_tir"], 0.5 * expected["tirunelveli"])
    np.testing.assert_allclose(corrected["solar_che"], 0.5 * expected["chennai_north"])
    assert (corrected["wind_tir"] == 0.5).all()
    assert len(audit) == 12
