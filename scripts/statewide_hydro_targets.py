"""Statewide conventional- plus small-hydro energy targets and constraints."""

from pathlib import Path

import pandas as pd
import xarray as xr

from nuclear_energy_targets import generator_dimension
from small_hydro_targets import (
    read_small_hydro_monthly_targets,
    small_hydro_energy_target_mwh,
)


ROOT = Path(__file__).resolve().parents[1]
OBSERVED_GENERATION_FILE = (
    ROOT
    / "data"
    / "source"
    / "TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx"
)
FY_START = pd.Timestamp("2025-04-01")
FY_END = pd.Timestamp("2026-03-31")
STATEWIDE_HYDRO_ENERGY_CONSTRAINT = (
    "observed_fy2025_26_statewide_hydro_generation_target"
)


def read_large_hydro_daily_targets(
    path: Path = OBSERVED_GENERATION_FILE,
) -> pd.Series:
    """Return observed Tamil Nadu hydro generation in MWh for every FY day."""
    raw = pd.read_excel(path, sheet_name="Electricity_Power_Generation_")
    selected = raw.loc[
        raw["State"].eq("Tamil Nadu")
        & raw["Parameter"].eq("Hydro - Generation (in MU)")
    ]
    if len(selected) != 1:
        raise ValueError("Expected exactly one Tamil Nadu hydro generation row")

    values: dict[pd.Timestamp, float] = {}
    for column, value in selected.iloc[0].items():
        try:
            date = pd.Timestamp(column).normalize()
        except (TypeError, ValueError):
            continue
        if FY_START <= date <= FY_END:
            values[date] = pd.to_numeric(value, errors="coerce") * 1_000.0

    expected = pd.date_range(FY_START, FY_END, freq="D", name="date")
    daily = pd.Series(values, name="large_hydro_target_mwh").sort_index()
    daily.index.name = "date"
    if not daily.index.equals(expected):
        raise ValueError("Observed hydro generation does not cover every FY2025-26 day")
    if daily.isna().any() or daily.lt(0.0).any():
        raise ValueError("Observed hydro generation must be finite and non-negative")
    return daily


def statewide_hydro_energy_target_mwh(
    snapshots: pd.DatetimeIndex,
    snapshot_weights: pd.Series,
    large_hydro_daily_mwh: pd.Series | None = None,
    small_hydro_monthly_mwh: pd.Series | None = None,
) -> float:
    """Return the conventional- plus small-hydro target for complete solve days."""
    snapshots = pd.DatetimeIndex(snapshots)
    weights = snapshot_weights.reindex(snapshots)
    if weights.isna().any():
        raise ValueError("Missing snapshot weights for statewide hydro target")

    daily_weight = weights.groupby(snapshots.normalize()).sum()
    if not daily_weight.eq(24.0).all():
        raise ValueError("Statewide hydro targets require complete 24-hour days")

    large = (
        read_large_hydro_daily_targets()
        if large_hydro_daily_mwh is None
        else large_hydro_daily_mwh
    )
    dates = pd.DatetimeIndex(daily_weight.index)
    selected_large = large.reindex(dates)
    if selected_large.isna().any():
        missing = ", ".join(
            date.strftime("%Y-%m-%d") for date in dates[selected_large.isna()]
        )
        raise ValueError(f"Missing observed hydro targets for: {missing}")

    small = (
        read_small_hydro_monthly_targets()
        if small_hydro_monthly_mwh is None
        else small_hydro_monthly_mwh
    )
    return float(
        selected_large.sum()
        + small_hydro_energy_target_mwh(snapshots, weights, small)
    )


def add_statewide_hydro_energy_equality(
    network,
    constrained_snapshots: pd.DatetimeIndex,
    target_mwh: float,
    name: str,
) -> None:
    """Constrain electrical output from all hydro technologies to ``target_mwh``."""
    snapshots = pd.DatetimeIndex(constrained_snapshots)
    weights = xr.DataArray(
        network.snapshot_weightings.generators.reindex(snapshots),
        dims="snapshot",
        coords={"snapshot": snapshots},
    )
    terms = []

    generator_dispatch = network.model["Generator-p"]
    generator_dim = generator_dimension(generator_dispatch)
    generator_names = network.generators.index[
        network.generators.carrier.isin(
            ["hydro", "hydro_run_of_river", "small_hydro"]
        )
    ]
    if len(generator_names):
        terms.append(
            (
                generator_dispatch.sel(
                    {generator_dim: generator_names, "snapshot": snapshots}
                )
                * weights
            ).sum()
        )

    storage_names = network.storage_units.index[
        network.storage_units.carrier.eq("hydro")
    ]
    if len(storage_names):
        dispatch = network.model["StorageUnit-p_dispatch"]
        storage_dim = next(dim for dim in dispatch.dims if dim != "snapshot")
        terms.append(
            (
                dispatch.sel({storage_dim: storage_names, "snapshot": snapshots})
                * weights
            ).sum()
        )

    turbine_names = network.links.index[network.links.carrier.eq("hydro_turbine")]
    if len(turbine_names):
        dispatch = network.model["Link-p"]
        link_dim = next(dim for dim in dispatch.dims if dim != "snapshot")
        efficiency = xr.DataArray(
            network.links.loc[turbine_names, "efficiency"],
            dims=link_dim,
            coords={link_dim: turbine_names},
        )
        terms.append(
            (
                dispatch.sel({link_dim: turbine_names, "snapshot": snapshots})
                * efficiency
                * weights
            ).sum()
        )

    if not terms:
        raise ValueError("Statewide hydro target found no hydro generation components")
    expression = terms[0]
    for term in terms[1:]:
        expression = expression + term
    network.model.add_constraints(expression == float(target_mwh), name=name)
