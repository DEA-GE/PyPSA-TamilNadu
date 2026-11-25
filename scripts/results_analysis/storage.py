"""Storage statistics over the run and by month.

Covers PyPSA StorageUnits (in electrical MW and MWh) and Stores with a
finite energy capacity, which in this model are the reservoirs of the
water-energy layer. Charge and discharge come from PyPSA's ``withdrawal``
and ``supply`` statistics at each asset; levels are the state of charge
(StorageUnits) or stored energy (Stores) at the end of each hour, as a share
of the asset's energy capacity.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import canonical_carrier
from .loaders import AnalysisRun
from .statistics import statistic_timeseries

# Levels at or above / at or below these shares of capacity count as full / empty.
FULL_PCT = 99.0
EMPTY_PCT = 1.0

TOTAL_PREFIX = "All "


def storage_assets(run: AnalysisRun) -> pd.DataFrame:
    """Return the storage assets with their class, bus and capacities."""
    base = run.base
    units = base.storage_units
    stores = base.stores
    stores = stores.loc[stores.e_nom.map(lambda value: math.isfinite(value) and value > 0.0)]
    unit_carrier = units.carrier.map(canonical_carrier)
    store_carrier = stores.carrier.astype(str)
    frames = [
        pd.DataFrame(
            {
                "component": "StorageUnit",
                "asset_class": unit_carrier + " storage unit",
                "carrier": unit_carrier,
                "bus": units.bus,
                "power_capacity_mw": units.p_nom,
                "energy_capacity_mwh": units.p_nom * units.max_hours,
            }
        ),
        pd.DataFrame(
            {
                "component": "Store",
                "asset_class": np.where(
                    store_carrier.eq("water_energy"), "reservoir", store_carrier + " store"
                ),
                "carrier": store_carrier,
                "bus": stores.bus,
                "power_capacity_mw": np.nan,
                "energy_capacity_mwh": stores.e_nom,
            },
            index=stores.index,
        ),
    ]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    assets = pd.concat(frames)
    assets.index.name = "asset"
    return assets


def _flow(run: AnalysisRun, name: str, component: str, assets: pd.Index) -> pd.DataFrame:
    """Hourly ``supply`` or ``withdrawal`` (MW) of one component, by asset."""
    values = statistic_timeseries(
        run, name, components=component, groupby="name", bus_carrier=None
    )
    if not values.empty:
        values.columns = values.columns.get_level_values("name")
    return values.reindex(index=run.snapshots, columns=assets, fill_value=0.0)


def _hourly(run: AnalysisRun, assets: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Hourly levels (MWh) and flows (MW) of every asset."""
    parts: dict[str, list[pd.DataFrame]] = {
        "level": [], "discharge": [], "charge": [], "inflow": [], "spill": []
    }
    for component, level_attribute, prefix in (
        ("StorageUnit", "state_of_charge", "storage_units"),
        ("Store", "e", "stores"),
    ):
        names = assets.index[assets.component.eq(component)]
        if not len(names):
            continue
        frame = run.frame(prefix, level_attribute).reindex(columns=names)
        parts["level"].append(frame.fillna(0.0))
        parts["discharge"].append(_flow(run, "supply", component, names))
        parts["charge"].append(_flow(run, "withdrawal", component, names))
        for attribute in ("inflow", "spill"):
            values = (
                run.frame(prefix, attribute)
                if component == "StorageUnit"
                else pd.DataFrame(index=run.snapshots)
            )
            parts[attribute].append(
                values.reindex(columns=names, fill_value=0.0).fillna(0.0)
            )
    return {
        key: pd.concat(frames, axis=1).reindex(columns=assets.index)
        for key, frames in parts.items()
    }


def _statistics(
    hourly: dict[str, pd.DataFrame],
    weights: pd.Series,
    energy_mwh: pd.Series,
    power_mw: pd.Series,
) -> pd.DataFrame:
    """Energy, cycling and level statistics of each column over the given hours."""
    hours = float(weights.sum())
    level_pct = hourly["level"].div(energy_mwh.replace(0.0, np.nan), axis=1) * 100.0
    result = pd.DataFrame(index=hourly["level"].columns)
    for key in ("discharge", "charge", "inflow", "spill"):
        result[f"{key}_gwh"] = hourly[key].mul(weights, axis=0).sum() / 1_000.0
    result["equivalent_full_cycles"] = (
        result.discharge_gwh * 1_000.0 / energy_mwh.replace(0.0, np.nan)
    )
    result["discharge_capacity_factor_pct"] = (
        100.0 * result.discharge_gwh * 1_000.0 / (power_mw.replace(0.0, np.nan) * hours)
    )
    result["level_start_pct"] = level_pct.iloc[0]
    result["level_end_pct"] = level_pct.iloc[-1]
    result["level_minimum_pct"] = level_pct.min()
    result["level_mean_pct"] = level_pct.mul(weights, axis=0).sum() / hours
    result["level_maximum_pct"] = level_pct.max()
    result["hours_full"] = level_pct.ge(FULL_PCT).mul(weights, axis=0).sum()
    result["hours_empty"] = level_pct.le(EMPTY_PCT).mul(weights, axis=0).sum()
    return result


def _by_class(hourly: dict[str, pd.DataFrame], assets: pd.DataFrame):
    """Sum hourly levels and flows, and capacities, over each asset class."""
    labels = TOTAL_PREFIX + assets.asset_class + "s"
    totals = {key: frame.T.groupby(labels).sum().T for key, frame in hourly.items()}
    energy = assets.energy_capacity_mwh.groupby(labels).sum()
    power = assets.power_capacity_mw.groupby(labels).sum(min_count=1)
    return totals, energy, power


def _months(index: pd.DatetimeIndex) -> pd.Index:
    return pd.Index(index.to_period("M").astype(str), name="month")


def storage_annual(run: AnalysisRun) -> pd.DataFrame:
    """Return storage statistics over the whole run: one row per asset and per class.

    Class rows (``All reservoirs`` and so on) sum the assets' energy and
    capacities, so their levels are the class's combined fill.
    """
    assets = storage_assets(run)
    if assets.empty:
        return pd.DataFrame()
    hourly = _hourly(run, assets)
    weights = run.weights
    per_asset = assets.join(
        _statistics(hourly, weights, assets.energy_capacity_mwh, assets.power_capacity_mw)
    )
    totals, energy, power = _by_class(hourly, assets)
    per_class = _statistics(totals, weights, energy, power)
    per_class.insert(0, "energy_capacity_mwh", energy)
    per_class.insert(0, "power_capacity_mw", power)
    per_class.insert(0, "asset_class", [label.removeprefix(TOTAL_PREFIX)[:-1] for label in per_class.index])
    result = pd.concat([per_asset, per_class])
    result["duration_h"] = result.energy_capacity_mwh / result.power_capacity_mw.replace(0.0, np.nan)
    result.index.name = "asset"
    return result


def _monthly(hourly, weights, energy, power) -> pd.DataFrame:
    months = _months(pd.DatetimeIndex(weights.index))
    frames = {}
    for month in months.unique():
        selected = np.asarray(months == month)
        frames[month] = _statistics(
            {key: frame.loc[selected] for key, frame in hourly.items()},
            weights.loc[selected], energy, power,
        )
    result = pd.concat(frames, names=["month", "asset"])
    return result


def storage_monthly(run: AnalysisRun) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return monthly storage statistics per asset and per asset class.

    Levels at the start and end of a month are those at the end of its first
    and last hour.
    """
    assets = storage_assets(run)
    if assets.empty:
        return pd.DataFrame(), pd.DataFrame()
    hourly = _hourly(run, assets)
    weights = run.weights
    per_asset = _monthly(hourly, weights, assets.energy_capacity_mwh, assets.power_capacity_mw)
    per_asset = per_asset.join(assets[["asset_class"]], on="asset")
    totals, energy, power = _by_class(hourly, assets)
    per_class = _monthly(totals, weights, energy, power)
    per_class.index = per_class.index.set_names("asset_class", level="asset")
    per_class = per_class.rename(
        index=lambda label: label.removeprefix(TOTAL_PREFIX)[:-1]
        if isinstance(label, str) and label.startswith(TOTAL_PREFIX) else label,
        level="asset_class",
    )
    return per_asset, per_class
