"""Build the FY2025-26 offshore wind capacity factors from the block 2 simulation.

``data/source/ts_power_150m_block2.xlsx`` holds one weather year (8 January
2020 to 6 January 2021, hourly) of simulated output of an offshore wind farm
of IEA 15 MW reference turbines at 150 m hub height. ``Production_wp1 (GW)``
is the farm output with wake effects (homogeneous layout). It levels off at
1.020 GW, the rated output of 68 turbines x 15 MW, so the capacity factor is
the output divided by 1.020 GW.

The series is from the NIWE / DTU Wind wake study for Tamil Nadu (WRF 4.2.2
driven by ERA5, representative year 2020; block 2 = Case 1 layout, 68 x
15 MW, capacity factor 56.2 % with wakes).

Time: neither the workbook nor the study states a time zone. WRF and its
ERA5 forcing run in UTC and WRF writes instantaneous values, so the
timestamps are taken as UTC and the values as instantaneous, like the
ERA5-based onshore wind profiles (``prepare_lava_profiles.py``): model hour
``L`` (IST, UTC+5:30) takes the value at ``L - 5 h`` UTC, the middle of the
model hour.

Year: the weather year is mapped onto FY2025-26 by calendar date and hour.
1-6 January come from 2021; 7 January, missing from the source, takes the
values of 8 January 2020; 29 February 2020 is not used.

    python scripts/prepare_offshore_wind_profile.py

writes ``data/model_inputs/offshore_wind_profiles.csv`` (column ``block2``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source" / "ts_power_150m_block2.xlsx"
OUTPUT = ROOT / "data" / "model_inputs" / "offshore_wind_profiles.csv"
PROFILE = "block2"
PRODUCTION_COLUMN = "Production_wp1 (GW)"
TURBINE_MW = 15.0
TURBINES = 68
FARM_MW = TURBINE_MW * TURBINES  # 1,020 MW, the plateau of the production series
MODEL_HOURS = pd.date_range("2025-04-01 00:00", "2026-03-31 23:00", freq="h", name="snapshot")
UTC_FROM_IST_MIDPOINT = pd.Timedelta(hours=5)  # L + 0:30 IST = L - 5 h UTC


def read_capacity_factor(path: Path = SOURCE) -> pd.Series:
    """Hourly farm capacity factor indexed by the workbook's (UTC) timestamps."""
    raw = pd.read_excel(path, usecols=["time", PRODUCTION_COLUMN])
    output_mw = raw.set_index(pd.DatetimeIndex(raw["time"]))[PRODUCTION_COLUMN] * 1_000.0
    if not output_mw.index.is_unique:
        raise ValueError(f"{path}: duplicate timestamps")
    if output_mw.max() > FARM_MW * 1.001:
        raise ValueError(f"{path}: output above the {FARM_MW:,.0f} MW farm rating")
    return (output_mw / FARM_MW).clip(0.0, 1.0)


def _weather_time(utc: pd.DatetimeIndex, source: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Map FY2025-26 UTC times onto the same date and hour of the weather year."""
    years = np.where((utc.month == 1) & (utc.day <= 6), 2021, 2020)
    days = np.where((utc.month == 1) & (utc.day == 7), 8, utc.day)  # 7 Jan is not in the source
    mapped = pd.to_datetime(
        {"year": years, "month": utc.month, "day": days, "hour": utc.hour}
    )
    mapped = pd.DatetimeIndex(mapped)
    missing = mapped.difference(source)
    if len(missing):
        raise ValueError(f"The weather year lacks {len(missing)} mapped hour(s), e.g. {missing[0]}")
    return mapped


def build_profile(capacity_factor: pd.Series) -> pd.DataFrame:
    """Capacity factor for every IST model hour of FY2025-26."""
    utc = MODEL_HOURS - UTC_FROM_IST_MIDPOINT
    values = capacity_factor.loc[_weather_time(utc, capacity_factor.index)].to_numpy()
    return pd.DataFrame({PROFILE: values}, index=MODEL_HOURS)


def main() -> None:
    profile = build_profile(read_capacity_factor())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    profile.to_csv(OUTPUT, float_format="%.6f")
    print(
        f"Wrote {OUTPUT}: {len(profile)} hours, mean capacity factor "
        f"{profile[PROFILE].mean():.3f} ({TURBINES} x {TURBINE_MW:.0f} MW farm)"
    )


if __name__ == "__main__":
    main()
