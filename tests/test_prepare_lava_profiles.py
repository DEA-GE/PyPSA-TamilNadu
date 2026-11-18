from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_lava_profiles as plp  # noqa: E402


UTC = pd.date_range("2025-03-31 18:00", "2025-04-01 08:00", freq="h")
HOURS = pd.date_range("2025-04-01 00:00", "2025-04-01 03:00", freq="h")


def test_solar_hour_averages_the_two_overlapping_atlite_hours():
    source = pd.DataFrame({"A": np.arange(len(UTC), dtype=float)}, index=UTC)

    aligned = plp.align_solar(source, HOURS)

    # IST 00:00-01:00 = UTC 18:30-19:30: half of the hour ending 19:00, half of the one ending 20:00.
    expected = 0.5 * (source.loc["2025-03-31 19:00", "A"] + source.loc["2025-03-31 20:00", "A"])
    assert aligned.loc["2025-04-01 00:00", "A"] == pytest.approx(expected)


def test_wind_hour_takes_the_instantaneous_value_at_its_midpoint():
    source = pd.DataFrame({"A": np.arange(len(UTC), dtype=float)}, index=UTC)

    aligned = plp.align_wind(source, HOURS)

    # The middle of IST 00:00-01:00 is 00:30 IST = 19:00 UTC.
    assert aligned.loc["2025-04-01 00:00", "A"] == source.loc["2025-03-31 19:00", "A"]
    assert aligned.loc["2025-04-01 03:00", "A"] == source.loc["2025-03-31 22:00", "A"]


def test_missing_source_hours_are_rejected_rather_than_wrapped():
    source = pd.DataFrame({"A": 1.0}, index=pd.date_range("2025-03-31 20:00", "2025-04-01 08:00", freq="h"))

    with pytest.raises(ValueError, match="does not cover"):
        plp.align_solar(source, HOURS)
    with pytest.raises(ValueError, match="does not cover"):
        plp.align_wind(source, HOURS)


def test_active_profiles_and_fy_wind_table_are_the_aligned_extracts():
    solar = plp.align_solar(plp.read_extract(plp.SOLAR_EXTRACT))
    wind = plp.align_wind(plp.read_extract(plp.WIND_EXTRACT))
    profiles = pd.read_csv(plp.PROFILE_FILE)
    fy_wind = pd.read_csv(plp.FY_WIND_FILE, index_col="time", parse_dates=True)

    assert fy_wind.index.equals(plp.MODEL_HOURS)
    for slug, area in plp.AREAS.items():
        np.testing.assert_allclose(profiles[f"{slug}_solar"], solar[area], rtol=0, atol=1e-12)
        np.testing.assert_allclose(profiles[f"{slug}_wind"], wind[area], rtol=0, atol=1e-12)
        np.testing.assert_allclose(fy_wind[area], wind[area], rtol=0, atol=1e-12)


def test_solar_alignment_keeps_energy_and_centres_output_before_solar_noon():
    extract = plp.read_extract(plp.SOLAR_EXTRACT)
    solar = plp.align_solar(extract)

    assert solar.sum().sum() == pytest.approx(extract.iloc[1:-1].sum().sum(), rel=1e-3)
    # Hour-beginning labels: the midpoint of the average day sits ~0.5 h before solar noon (~12.3 IST).
    assert 11.6 < plp.daily_centroid_hour(solar["Tirunelveli"]) < 11.9
