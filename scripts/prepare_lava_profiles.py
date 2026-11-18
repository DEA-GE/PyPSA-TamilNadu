"""Build the FY2025-26 wind and solar capacity factors from the LAVA/atlite profiles.

LAVA runs atlite on an ERA5 cutout and writes area profiles labelled with the
cutout's UTC timestamps.  The model's hour ``L`` is ``[L, L + 1 h)`` in IST
(UTC+5:30).  The two technologies need different conversions because ERA5
stores them differently:

* **Solar** (radiation is accumulated): the value at ``T`` (UTC) is the mean
  over ``(T - 1 h, T]``.  Each model hour takes half of two atlite hours::

      solar(L) = 0.5 * (atlite(L - 5 h) + atlite(L - 4 h))

* **Wind** (100 m wind is instantaneous): the value at ``T`` (UTC) is a
  snapshot.  ``T = L - 5 h`` (UTC) is ``L + 0:30`` IST, the middle of model
  hour ``L``::

      wind(L) = atlite(L - 5 h)

The source window starts before and ends after the fiscal year, so nothing
wraps around.  The script writes the 18 profile columns of
``data/model_inputs/renewable_profiles.csv`` and the FY wind table
``data/model_inputs/Onshorewind_9BAs_AggrPCWiderCUF_FY2025-26.csv``.

``python scripts/prepare_lava_profiles.py`` uses the extracts in
``data/source/lava_profiles_fy2025_26``.  Add ``--extract-from <LAVA data
folder>`` to rebuild those extracts from the LAVA area files first.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source" / "lava_profiles_fy2025_26"
SOLAR_EXTRACT = SOURCE / "lava_solar_9BA_extract_utc.csv"
WIND_EXTRACT = SOURCE / "lava_wind_AggrPCWiderCUF_extract_utc.csv"
PROFILE_FILE = ROOT / "data" / "model_inputs" / "renewable_profiles.csv"
FY_WIND_FILE = ROOT / "data" / "model_inputs" / "Onshorewind_9BAs_AggrPCWiderCUF_FY2025-26.csv"
AREAS = {
    "chennai_north": "ChennaiNorth",
    "chennai_south": "ChennaiSouth",
    "coimbatore": "Coimbatore",
    "erode": "Erode",
    "madurai": "Madurai",
    "tirunelveli": "Tirunelveli",
    "trichy": "Trichy",
    "vellore": "Vellore",
    "villupuram": "Villupuram",
}
# LAVA file name patterns per technology (the wind files end in an area code).
SOURCE_PATTERNS = {
    "solar": "{area}_solar_9BA_profile_{year}.csv",
    "wind": "{area}_onshorewind_AggrPCWiderCUF_profile_{year}_*.csv",
}
MODEL_HOURS = pd.date_range("2025-04-01 00:00", "2026-03-31 23:00", freq="h")
IST_OFFSET = pd.Timedelta(hours=5, minutes=30)
EXTRACT_START = pd.Timestamp("2025-03-31 18:00")
EXTRACT_END = pd.Timestamp("2026-04-01 00:00")


def build_extract(lava_data: Path, technology: str, path: Path) -> pd.DataFrame:
    """Copy the UTC source hours the fiscal year needs from the LAVA area files."""
    columns = {}
    for area in AREAS.values():
        parts = []
        for year in (2025, 2026):
            files = sorted((lava_data / area / "0_profiles").glob(SOURCE_PATTERNS[technology].format(area=area, year=year)))
            if len(files) != 1:
                raise ValueError(f"Expected one LAVA {technology} file for {area} {year}, found {len(files)}")
            parts.append(pd.read_csv(files[0], index_col=0, parse_dates=True)[area])
        series = pd.concat(parts)
        if not series.index.is_unique:
            raise ValueError(f"Duplicate hours in the LAVA {technology} profile for {area}")
        columns[area] = series.loc[EXTRACT_START:EXTRACT_END]
    extract = pd.DataFrame(columns)
    extract.index.name = "time_utc"
    extract.to_csv(path)
    return extract


def read_extract(path: Path) -> pd.DataFrame:
    extract = pd.read_csv(path, index_col="time_utc", parse_dates=True)
    expected = pd.date_range(EXTRACT_START, EXTRACT_END, freq="h")
    if not extract.index.equals(expected) or list(extract.columns) != list(AREAS.values()):
        raise ValueError(f"{path} must hold hourly UTC values {EXTRACT_START}..{EXTRACT_END} for the nine areas")
    if extract.isna().any().any() or ((extract < 0) | (extract > 1)).any().any():
        raise ValueError(f"{path} capacity factors must be complete and between 0 and 1")
    return extract


def _select(source: pd.DataFrame, labels: pd.DatetimeIndex) -> np.ndarray:
    values = source.reindex(labels)
    if values.isna().any().any():
        raise ValueError("The source window does not cover every model hour")
    return values.to_numpy()


def align_solar(hour_ending_utc: pd.DataFrame, model_hours: pd.DatetimeIndex = MODEL_HOURS) -> pd.DataFrame:
    """Hour-beginning IST means from hour-ending UTC means."""
    utc_start = model_hours - IST_OFFSET  # UTC time at the start of each model hour (:30)
    first = _select(hour_ending_utc, utc_start + pd.Timedelta(minutes=30))   # ends L - 5 h
    second = _select(hour_ending_utc, utc_start + pd.Timedelta(minutes=90))  # ends L - 4 h
    return pd.DataFrame(0.5 * (first + second), index=model_hours, columns=hour_ending_utc.columns)


def align_wind(instantaneous_utc: pd.DataFrame, model_hours: pd.DatetimeIndex = MODEL_HOURS) -> pd.DataFrame:
    """Value at the middle of each IST model hour from instantaneous UTC values."""
    midpoint_utc = model_hours + pd.Timedelta(minutes=30) - IST_OFFSET  # = L - 5 h
    return pd.DataFrame(_select(instantaneous_utc, midpoint_utc), index=model_hours, columns=instantaneous_utc.columns)


def write_profiles(solar: pd.DataFrame, wind: pd.DataFrame, path: Path = PROFILE_FILE) -> pd.DataFrame:
    """Replace the wind and solar columns of the model's renewable profile input."""
    profiles = pd.read_csv(path)
    snapshots = pd.DatetimeIndex(pd.to_datetime(profiles["snapshot"]))
    if not (snapshots.equals(solar.index) and snapshots.equals(wind.index)):
        raise ValueError(f"{path} snapshots differ from the FY2025-26 model hours")
    for slug, area in AREAS.items():
        profiles[f"{slug}_wind"] = wind[area].to_numpy()
        profiles[f"{slug}_solar"] = solar[area].to_numpy()
    profiles.to_csv(path, index=False)
    return profiles


def write_fy_wind_table(wind: pd.DataFrame, path: Path = FY_WIND_FILE) -> None:
    table = wind.copy()
    table.index = table.index.strftime("%Y-%m-%d %H:%M:%S")
    table.index.name = "time"
    table.to_csv(path)


def daily_centroid_hour(series: pd.Series) -> float:
    """Energy-weighted mean hour label of the average daily profile."""
    by_hour = series.groupby(series.index.hour).mean()
    return float((by_hour.index.to_numpy() * by_hour.to_numpy()).sum() / by_hour.sum())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extract-from", type=Path, help="LAVA data folder to rebuild the UTC extracts from")
    args = parser.parse_args()
    if args.extract_from:
        build_extract(args.extract_from, "solar", SOLAR_EXTRACT)
        build_extract(args.extract_from, "wind", WIND_EXTRACT)
    solar = align_solar(read_extract(SOLAR_EXTRACT))
    wind = align_wind(read_extract(WIND_EXTRACT))
    write_profiles(solar, wind)
    write_fy_wind_table(wind)
    print(
        "Wrote renewable_profiles.csv and the FY wind table; average-day solar centroid hour "
        f"Tirunelveli {daily_centroid_hour(solar['Tirunelveli']):.2f}"
    )


if __name__ == "__main__":
    main()
