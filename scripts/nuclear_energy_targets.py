"""Observed nuclear daily energy equalities for hourly 9BA optimization."""
from pathlib import Path
import numpy as np
import pandas as pd
import xarray as xr

TARGET = "observed_fy2025_26_nuclear_generation_target"
BUDGET = Path(__file__).resolve().parents[1] / "9_BA/model/nuclear_daily_energy_budget.csv"


def add_nuclear_targets(network, snapshots):
    budget = pd.read_csv(BUDGET, parse_dates=["date"]).set_index("date")
    snapshots = pd.DatetimeIndex(snapshots)
    units = network.generators.index[network.generators.carrier.eq("nuclear")]
    for date in snapshots.normalize().unique():
        hours = snapshots[snapshots.normalize() == date]
        if len(hours) != 24:
            raise ValueError("Nuclear daily targets require complete 24-hour days")
        weights = xr.DataArray(network.snapshot_weightings.generators.loc[hours], dims="snapshot", coords={"snapshot": hours})
        expression = (network.model["Generator-p"].sel(name=units, snapshot=hours) * weights).sum()
        network.model.add_constraints(expression, "==", float(budget.at[date, "daily_energy_target_mwh"]), name=f"NuclearDailyTarget-{date:%Y%m%d}")


def prepare_nuclear_targets(network):
    budget = pd.read_csv(BUDGET, parse_dates=["date"]).set_index("date")
    dates = network.snapshots.normalize().unique()
    network.global_constraints.at[TARGET, "constant"] = budget.loc[dates, "daily_energy_target_mwh"].sum()


def validate_nuclear_targets(network):
    budget = pd.read_csv(BUDGET, parse_dates=["date"]).set_index("date")
    units = network.generators.index[network.generators.carrier.eq("nuclear")]
    daily = network.generators_t.p[units].sum(axis=1).mul(network.snapshot_weightings.generators).groupby(network.snapshots.normalize()).sum()
    deviation = float((daily - budget.loc[daily.index, "daily_energy_target_mwh"]).abs().max())
    if not np.isfinite(deviation) or deviation > 1e-5:
        raise ValueError(f"Nuclear daily equality deviation: {deviation} MWh")
    return deviation
