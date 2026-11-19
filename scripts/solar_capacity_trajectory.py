"""Tamil Nadu grid-connected solar capacity through FY2025-26.

Month-end installed solar capacity comes from the NITI Dashboard (ICED)
monthly export, which reproduces MNRE's state-wise releases exactly in every
month where both are available (checked on read).  The model excludes off-grid
solar, so the trajectory is grid-connected capacity: total solar minus the
off-grid solar reported in MNRE's releases, interpolated in time between them.
A value "as on D" is the capacity at the end of day D; between month ends
capacity is interpolated linearly in time.
Sources: ``data/source/NITI dashboard/monthly installed capacity Tamil Nadu``
and ``data/source/re_capacity_releases_fy2025_26``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CAPACITY_FILE = (
    ROOT / "data" / "model_inputs" / "tamil_nadu_installed_capacity_monthly_2019_2026.csv"
)
RELEASE_FILE = (
    ROOT / "data" / "model_inputs" / "tamil_nadu_re_capacity_releases_FY2025_26.csv"
)
FIRST_AS_ON = pd.Timestamp("2025-03-31")
LAST_AS_ON = pd.Timestamp("2026-03-31")
# Transcribed values carry at most two decimals.
TOLERANCE_MW = 0.011

NON_SOLAR_RES = ["mnre_small_hydro_mw", "mnre_wind_mw", "mnre_bio_power_mw"]
SOLAR_PARTS = [
    "mnre_solar_ground_mounted_mw",
    "mnre_solar_rooftop_mw",
    "mnre_solar_off_grid_mw",
]
REPORTED_COLUMNS = [*NON_SOLAR_RES, "mnre_solar_total_mw", "mnre_large_hydro_mw"]


def _hours(index: pd.DatetimeIndex) -> np.ndarray:
    """Hours since a fixed origin, independent of the datetime resolution."""
    return np.asarray((index - pd.Timestamp("2019-01-01")) / pd.Timedelta(hours=1), dtype=float)


def _interpolate_in_time(series: pd.Series) -> pd.Series:
    """Fill gaps linearly in time; hold the nearest value beyond the ends."""
    known = series.dropna()
    if known.empty:
        raise ValueError(f"No reported values to interpolate for {series.name}")
    values = np.interp(
        _hours(pd.DatetimeIndex(series.index)),
        _hours(pd.DatetimeIndex(known.index)),
        known.to_numpy(dtype=float),
    )
    return pd.Series(values, index=series.index, name=series.name)


def read_monthly_capacity(path: Path = CAPACITY_FILE) -> pd.DataFrame:
    """Month-end Tamil Nadu installed capacity by source (MW), indexed by as-on date."""
    frame = pd.read_csv(path, parse_dates=["as_on_date"]).set_index("as_on_date")
    expected = pd.date_range(frame.index.min(), frame.index.max(), freq="ME")
    if not frame.index.equals(expected):
        raise ValueError("Monthly installed capacity must list consecutive month ends once, in order")
    if frame["solar_mw"].isna().any() or frame["solar_mw"].le(0).any():
        raise ValueError("Monthly solar capacity must be positive and complete")
    return frame


def read_capacity_releases(path: Path = RELEASE_FILE) -> pd.DataFrame:
    """Validated MNRE state-wise release rows, indexed by as-on date."""
    frame = pd.read_csv(path, parse_dates=["as_on_date"])
    required = {"as_on_date", *REPORTED_COLUMNS, *SOLAR_PARTS, "mnre_total_mw", "mnre_source"}
    if missing := sorted(required - set(frame)):
        raise ValueError(f"MNRE release file is missing columns: {missing}")
    frame = frame.set_index("as_on_date").sort_index()
    if frame.index.duplicated().any() or (frame.index != frame.index + pd.offsets.MonthEnd(0)).any():
        raise ValueError("MNRE releases must be dated at distinct month ends")
    if frame[[*REPORTED_COLUMNS, "mnre_total_mw", "mnre_source"]].isna().any().any():
        raise ValueError("Every MNRE row needs sourced technology capacities and a total")
    if not np.allclose(
        frame[REPORTED_COLUMNS].sum(axis=1), frame["mnre_total_mw"], rtol=0.0, atol=TOLERANCE_MW
    ):
        raise ValueError("MNRE technology capacities do not sum to the reported total")
    split = frame.dropna(subset=SOLAR_PARTS, how="all")
    if split[SOLAR_PARTS].isna().any().any():
        raise ValueError("An MNRE solar split must report every solar type")
    if not np.allclose(
        split[SOLAR_PARTS].sum(axis=1), split["mnre_solar_total_mw"], rtol=0.0, atol=TOLERANCE_MW
    ):
        raise ValueError("MNRE solar types do not sum to the reported solar total")
    return frame


def monthly_solar_capacity(
    capacity: pd.DataFrame | None = None, releases: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Month-end solar capacity (MW) for FY2025-26 with its off-grid share."""
    capacity = read_monthly_capacity() if capacity is None else capacity
    releases = read_capacity_releases() if releases is None else releases
    ends = pd.date_range(FIRST_AS_ON, LAST_AS_ON, freq="ME")
    if not ends.isin(capacity.index).all():
        raise ValueError("Monthly installed capacity does not cover FY2025-26")
    solar = capacity.loc[ends, "solar_mw"]
    overlap = releases.index.intersection(ends)
    if not np.allclose(
        solar.loc[overlap], releases.loc[overlap, "mnre_solar_total_mw"], rtol=0.0, atol=TOLERANCE_MW
    ):
        raise ValueError("Monthly solar capacity disagrees with MNRE's state-wise releases")
    off_grid = _interpolate_in_time(
        releases["mnre_solar_off_grid_mw"].reindex(releases.index.union(ends))
    ).reindex(ends)
    monthly = pd.DataFrame(
        {
            "solar_total_mw": solar,
            "solar_off_grid_mw": off_grid,
            "solar_grid_connected_mw": solar - off_grid,
            "off_grid_reported": releases["mnre_solar_off_grid_mw"].reindex(ends).notna(),
            "mnre_release": releases["mnre_source"].reindex(ends),
            "source": capacity.loc[ends, "source"],
        },
        index=ends,
    )
    monthly.index.name = "as_on_date"
    return monthly


def solar_capacity_mw(
    snapshots: pd.DatetimeIndex, monthly: pd.DataFrame | None = None
) -> pd.Series:
    """Grid-connected solar capacity (MW) at each snapshot, linear between month ends."""
    capacity = monthly_solar_capacity() if monthly is None else monthly
    # A value "as on D" is the cumulative capacity at the end of day D.
    anchors = pd.DatetimeIndex(capacity.index) + pd.Timedelta(days=1)
    index = pd.DatetimeIndex(snapshots)
    if index.min() < anchors.min() or index.max() > anchors.max():
        raise ValueError(
            "Snapshots extend beyond the monthly capacity data "
            f"({anchors.min()} to {anchors.max()})"
        )
    values = np.interp(
        _hours(index), _hours(anchors), capacity["solar_grid_connected_mw"].to_numpy(dtype=float)
    )
    return pd.Series(values, index=index, name="solar_grid_connected_mw")
