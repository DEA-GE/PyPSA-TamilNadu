"""Coal fleet operation: availability, commitment, loading and start-ups.

Dispatch is PyPSA's ``supply`` statistic per unit. Available capacity is
dispatch plus PyPSA's ``curtailment`` statistic (``p_max_pu * p_nom - p``),
so it already reflects planned and forced outages and derating. Online
capacity is the available capacity of committed units (``status``, which a
relaxed commitment lets be fractional); units that are not committable count
as online whenever they are available. The minimum stable output is
``status * p_min_pu * p_nom``.

Generators named ``rea_export_reservation__*`` hold other states' fixed
shares of in-state central stations. They run to a fixed profile, so they
are reported apart from the units the model dispatches for Tamil Nadu.
"""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import CARRIER_COLORS, CURRENCY, canonical_carrier
from .loaders import AnalysisRun
from .statistics import statistic_sum, statistic_timeseries

RESERVATION_PREFIX = "rea_export_reservation__"
UNIT_SEPARATOR = "__unit_"
RESERVATION_LABEL = "Export reservations (fixed)"

# Online units within this margin (MW, or share of capacity) of their
# minimum stable output count as running at minimum.
MINIMUM_TOLERANCE_MW = 0.5
MINIMUM_TOLERANCE_SHARE = 0.005
ONLINE_TOLERANCE = 1e-3

PER_MWH = f"{CURRENCY.lower()}_per_mwh"

USE_COLORS = {
    "generated": CARRIER_COLORS["coal"],
    "online_unused": "#9E9E9E",
    "available_offline": "#D9D9D9",
    "unavailable": "#D95F02",
}
USE_LABELS = {
    "generated": "Generated",
    "online_unused": "Online, not generated (part load)",
    "available_offline": "Available, offline",
    "unavailable": "Unavailable (outage or derating)",
}


def station_name(unit: str) -> str:
    """Return the station a coal unit belongs to."""
    name = str(unit)
    if name.startswith(RESERVATION_PREFIX):
        return "export reservation: " + name.removeprefix(RESERVATION_PREFIX)
    return name.split(UNIT_SEPARATOR)[0]


@dataclass
class CoalFleet:
    """Hourly operation (MW) of every coal unit; columns are units."""

    units: pd.DataFrame
    dispatch: pd.DataFrame
    available: pd.DataFrame
    online: pd.DataFrame
    status: pd.DataFrame
    minimum: pd.DataFrame
    start_ups: pd.DataFrame
    weights: pd.Series

    @property
    def dispatched_units(self) -> pd.Index:
        """Units the model dispatches, i.e. not fixed export reservations."""
        return self.units.index[~self.units.export_reservation]

    def fleet(self, frame: pd.DataFrame) -> pd.Series:
        """Sum a unit frame over the dispatched units."""
        return frame[self.dispatched_units].sum(axis=1)


def _by_name(values: pd.DataFrame, units: pd.Index) -> pd.DataFrame:
    if not values.empty:
        values.columns = values.columns.get_level_values("name")
    return values.reindex(columns=units, fill_value=0.0)


def _dense(run: AnalysisRun, attribute: str, units: pd.Index) -> pd.DataFrame:
    """A static-or-varying generator attribute for every snapshot."""
    values = run.network.get_switchable_as_dense("Generator", attribute)[units]
    values.index = run.snapshots
    return values.astype(float)


def coal_fleet(run: AnalysisRun) -> CoalFleet | None:
    """Collect the hourly operation of the coal units, or None without coal."""
    generators = run.base.generators
    units = generators.index[generators.carrier.map(canonical_carrier).eq("coal")]
    if not len(units):
        return None
    carriers = sorted(generators.loc[units, "carrier"].astype(str).unique())
    options = {"components": "Generator", "carrier": carriers, "groupby": "name"}
    dispatch = _by_name(statistic_timeseries(run, "supply", **options), units)
    unused = _by_name(statistic_timeseries(run, "curtailment", **options), units)
    available = dispatch + unused.clip(lower=0.0)

    committable = generators.loc[units, "committable"].fillna(False).astype(bool)
    fixed = units[~committable.to_numpy()]
    status = run.frame("generators", "status").reindex(columns=units).fillna(0.0).clip(0.0, 1.0)
    # Units that are not committable are online whenever they are available.
    status[fixed] = available[fixed].gt(0.0).astype(float)
    start_ups = run.frame("generators", "start_up").reindex(columns=units).fillna(0.0)
    start_ups[fixed] = 0.0
    capacity = generators.loc[units, "p_nom"].astype(float)
    minimum = status * _dense(run, "p_min_pu", units) * capacity

    table = pd.DataFrame(index=units)
    table.index.name = "unit"
    table["station"] = [station_name(unit) for unit in units]
    table["bus"] = generators.loc[units, "bus"]
    table["capacity_mw"] = capacity
    table["marginal_cost"] = _dense(run, "marginal_cost", units).mean()
    table["committable"] = committable
    table["export_reservation"] = units.astype(str).str.startswith(RESERVATION_PREFIX)
    return CoalFleet(
        units=table,
        dispatch=dispatch,
        available=available,
        online=status * available,
        status=status,
        minimum=minimum,
        start_ups=start_ups,
        weights=run.weights,
    )


def _at_minimum(fleet: CoalFleet) -> pd.DataFrame:
    """Online status in the hours each unit runs at its minimum stable output."""
    tolerance = np.maximum(
        MINIMUM_TOLERANCE_MW, MINIMUM_TOLERANCE_SHARE * fleet.units.capacity_mw
    )
    at_minimum = fleet.dispatch.le(fleet.minimum + tolerance) & fleet.minimum.gt(0.0)
    return fleet.status.where(at_minimum & fleet.status.gt(ONLINE_TOLERANCE), 0.0)


def _energy(frame: pd.DataFrame, weights: pd.Series) -> pd.Series:
    return frame.mul(weights, axis=0).sum()


def _use_shares(table: pd.DataFrame) -> pd.DataFrame:
    """Shares (%) of capacity-hours generated, online, available and unavailable."""
    capacity_mwh = table.capacity_mwh.replace(0.0, np.nan)
    return pd.DataFrame(
        {
            "capacity_factor_pct": 100.0 * table.generation_mwh / capacity_mwh,
            "online_pct": 100.0 * table.online_mwh / capacity_mwh,
            "availability_pct": 100.0 * table.available_mwh / capacity_mwh,
            "loading_when_online_pct": 100.0 * table.generation_mwh / table.online_mwh.replace(0.0, np.nan),
            "hours_at_minimum_pct": 100.0 * table.minimum_unit_hours / table.online_unit_hours.replace(0.0, np.nan),
        }
    )


def coal_plant_operation(fleet: CoalFleet, run: AnalysisRun | None = None) -> pd.DataFrame:
    """Return the operation of each coal station over the run.

    Stations are ordered by marginal cost, cheapest first, with the fixed
    export reservations last. With *run* and marginal prices, the table adds
    each station's market value (revenue per MWh) and its margin over
    marginal cost.
    """
    weights = fleet.weights
    units = fleet.units
    per_unit = pd.DataFrame(
        {
            "station": units.station,
            "export_reservation": units.export_reservation,
            "units": 1,
            "capacity_mw": units.capacity_mw,
            "capacity_mwh": units.capacity_mw * float(weights.sum()),
            "cost_weight": units.capacity_mw * units.marginal_cost,
            "generation_mwh": _energy(fleet.dispatch, weights),
            "available_mwh": _energy(fleet.available, weights),
            "online_mwh": _energy(fleet.online, weights),
            "online_unit_hours": _energy(fleet.status, weights),
            "minimum_unit_hours": _energy(_at_minimum(fleet), weights),
            "start_ups": _energy(fleet.start_ups, pd.Series(1.0, index=weights.index)),
        }
    )
    if run is not None and not run.network.buses_t.marginal_price.empty:
        revenue = statistic_sum(
            run, "revenue", components="Generator", groupby="name",
            carrier=sorted(run.base.generators.loc[units.index, "carrier"].astype(str).unique()),
        )
        if not revenue.empty:
            revenue.index = revenue.index.get_level_values("name")
        per_unit["revenue"] = revenue.reindex(units.index, fill_value=0.0)
    table = per_unit.groupby(["station", "export_reservation"]).sum()
    table = table.reset_index("export_reservation")
    table["marginal_cost"] = table.cost_weight / table.capacity_mw.replace(0.0, np.nan)
    result = pd.DataFrame(index=table.index)
    result["export_reservation"] = table.export_reservation
    result["units"] = table.units
    result["capacity_mw"] = table.capacity_mw
    result[f"marginal_cost_{PER_MWH}"] = table.marginal_cost
    result["generation_gwh"] = table.generation_mwh / 1_000.0
    result = result.join(_use_shares(table))
    result["online_unit_hours"] = table.online_unit_hours
    result["start_ups"] = table.start_ups
    result["start_ups_per_unit"] = table.start_ups / table.units
    if "revenue" in table:
        result[f"market_value_{PER_MWH}"] = table.revenue / table.generation_mwh.replace(0.0, np.nan)
        result[f"margin_over_marginal_cost_{PER_MWH}"] = (
            result[f"market_value_{PER_MWH}"] - result[f"marginal_cost_{PER_MWH}"]
        )
    result.index.name = "station"
    return result.sort_values(
        ["export_reservation", f"marginal_cost_{PER_MWH}", "capacity_mw"],
        ascending=[True, True, False],
    )


def coal_monthly(fleet: CoalFleet) -> pd.DataFrame:
    """Return the dispatched coal fleet's operation by month.

    Covers the units the model dispatches; the fixed export reservations'
    generation is a separate column.
    """
    weights = fleet.weights
    months = pd.Index(fleet.dispatch.index.to_period("M").astype(str), name="month")
    units = fleet.dispatched_units
    dispatch = fleet.fleet(fleet.dispatch)
    hourly = pd.DataFrame(
        {
            "generation_mwh": dispatch * weights,
            "available_mwh": fleet.fleet(fleet.available) * weights,
            "online_mwh": fleet.fleet(fleet.online) * weights,
            "online_unit_hours": fleet.fleet(fleet.status) * weights,
            "minimum_unit_hours": fleet.fleet(_at_minimum(fleet)) * weights,
            "start_ups": fleet.fleet(fleet.start_ups),
            "hours": weights,
            "reservation_mwh": fleet.dispatch.drop(columns=units).sum(axis=1) * weights,
        }
    )
    table = hourly.groupby(np.asarray(months)).sum()
    table["capacity_mwh"] = float(fleet.units.loc[units, "capacity_mw"].sum()) * table.hours
    result = pd.DataFrame(index=table.index)
    result.index.name = "month"
    result["capacity_mw"] = float(fleet.units.loc[units, "capacity_mw"].sum())
    result["average_available_mw"] = table.available_mwh / table.hours
    result["average_online_mw"] = table.online_mwh / table.hours
    result["generation_gwh"] = table.generation_mwh / 1_000.0
    result = result.join(_use_shares(table))
    result["start_ups"] = table.start_ups
    grouped = dispatch.groupby(np.asarray(months))
    result["minimum_hourly_dispatch_mw"] = grouped.min()
    result["maximum_hourly_dispatch_mw"] = grouped.max()
    ramps = dispatch.diff()
    result["largest_hourly_ramp_up_mw"] = ramps.groupby(np.asarray(months)).max()
    result["largest_hourly_ramp_down_mw"] = ramps.groupby(np.asarray(months)).min()
    result["export_reservation_gwh"] = table.reservation_mwh / 1_000.0
    return result


def fleet_timeseries(fleet: CoalFleet) -> pd.DataFrame:
    """Hourly totals (MW) of the dispatched coal fleet."""
    return pd.DataFrame(
        {
            "capacity": float(fleet.units.loc[fleet.dispatched_units, "capacity_mw"].sum()),
            "available": fleet.fleet(fleet.available),
            "online": fleet.fleet(fleet.online),
            "minimum": fleet.fleet(fleet.minimum),
            "dispatch": fleet.fleet(fleet.dispatch),
        },
        index=fleet.dispatch.index,
    )


def draw_fleet(axis, totals: pd.DataFrame) -> None:
    """Draw dispatch against online, available and installed capacity."""
    index = totals.index
    axis.fill_between(
        index, 0.0, totals.dispatch, color=CARRIER_COLORS["coal"], alpha=0.85,
        linewidth=0, label="Dispatch",
    )
    axis.plot(index, totals.minimum, color="black", linestyle=":", linewidth=1.1,
              label="Minimum stable output of online units")
    axis.plot(index, totals.online, color="#1F78B4", linewidth=1.2, label="Online (committed) capacity")
    axis.plot(index, totals.available, color="#D95F02", linewidth=1.2,
              label="Available capacity (after outages)")
    axis.plot(index, totals.capacity, color="#7F7F7F", linestyle="--", linewidth=0.9,
              label="Installed capacity")
    axis.set_ylim(bottom=0.0)
    axis.set_ylabel("MW")
    axis.grid(alpha=0.2)


def fleet_figure(fleet: CoalFleet):
    """Daily coal fleet operation and daily start-ups."""
    totals = fleet_timeseries(fleet)
    long_run = totals.index.max() - totals.index.min() > pd.Timedelta(days=31)
    shown = totals.resample("D").mean() if long_run else totals
    resolution = "daily mean" if long_run else "hourly"
    starts = fleet.fleet(fleet.start_ups).resample("D").sum()
    figure, axes = plt.subplots(
        2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    draw_fleet(axes[0], shown)
    reserved = fleet.dispatch.drop(columns=fleet.dispatched_units).sum(axis=1)
    title = f"Coal fleet operation ({resolution}; Tamil Nadu units"
    if reserved.any():
        title += f", excluding {reserved.mul(fleet.weights).sum() / 1e3:,.0f} GWh of fixed export reservations"
    axes[0].set_title(title + ")")
    axes[0].legend(ncol=3, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.04))
    axes[1].bar(starts.index, starts.to_numpy(), width=1.0, align="edge",
                color=CARRIER_COLORS["coal"], linewidth=0)
    axes[1].set_ylabel("Start-ups per day")
    axes[1].set_xlabel("Time")
    axes[1].grid(axis="y", alpha=0.2)
    figure.tight_layout()
    return figure


def dispatch_heatmap_figure(fleet: CoalFleet):
    """Coal fleet dispatch by hour of day and day, as a share of available capacity."""
    totals = fleet_timeseries(fleet)
    share = 100.0 * totals.dispatch / totals.available.replace(0.0, np.nan)
    frame = pd.DataFrame({"share": share, "day": share.index.normalize(), "hour": share.index.hour})
    grid = frame.pivot_table(index="hour", columns="day", values="share", aggfunc="mean")
    figure, axis = plt.subplots(figsize=(14, 4.8))
    days = pd.DatetimeIndex(grid.columns)
    image = axis.pcolormesh(
        days.append(pd.DatetimeIndex([days[-1] + pd.Timedelta(days=1)])),
        np.arange(25),
        grid.to_numpy(),
        cmap="viridis", vmin=0.0, vmax=100.0, shading="flat",
    )
    axis.set_yticks([0, 6, 12, 18, 24])
    axis.set_ylabel("Hour of day")
    axis.set_xlabel("Day")
    axis.set_title("Coal fleet dispatch as a share of available capacity, by hour of day")
    colorbar = figure.colorbar(image, ax=axis, pad=0.01)
    colorbar.set_label("% of available capacity")
    figure.tight_layout()
    return figure


def plant_use_figure(plants: pd.DataFrame):
    """Each station's capacity-hours split into generated, online, offline and unavailable.

    Stations are in merit order, cheapest at the top; the fixed export
    reservations are one bar at the bottom.
    """
    dispatched = plants.loc[~plants.export_reservation]
    reserved = plants.loc[plants.export_reservation]
    rows = dispatched.copy()
    if not reserved.empty:
        weight = reserved.capacity_mw
        combined = {
            column: float((reserved[column] * weight).sum() / weight.sum())
            for column in ("capacity_factor_pct", "online_pct", "availability_pct")
        }
        combined.update(
            capacity_mw=float(weight.sum()), units=int(reserved.units.sum()),
            start_ups=0.0, **{f"marginal_cost_{PER_MWH}": np.nan},
        )
        rows.loc[RESERVATION_LABEL] = pd.Series(combined)
    rows = rows.fillna({"capacity_factor_pct": 0.0, "online_pct": 0.0, "availability_pct": 0.0})
    parts = pd.DataFrame(
        {
            "generated": rows.capacity_factor_pct,
            "online_unused": rows.online_pct - rows.capacity_factor_pct,
            "available_offline": rows.availability_pct - rows.online_pct,
            "unavailable": 100.0 - rows.availability_pct,
        }
    ).clip(lower=0.0)
    labels = []
    for name, row in rows.iterrows():
        cost = row[f"marginal_cost_{PER_MWH}"]
        detail = f"{row.capacity_mw:,.0f} MW" + ("" if pd.isna(cost) else f", {cost:,.0f} {CURRENCY}/MWh")
        labels.append(f"{name} ({detail})")
    figure, axis = plt.subplots(figsize=(14, 0.42 * len(rows) + 1.8))
    positions = np.arange(len(rows))
    left = np.zeros(len(rows))
    for column, values in parts.items():
        axis.barh(positions, values.to_numpy(), left=left, color=USE_COLORS[column],
                  label=USE_LABELS[column], height=0.75)
        left += values.to_numpy()
    for position, (_, row) in zip(positions, rows.iterrows()):
        if row.start_ups > 0:
            axis.text(101.0, position, f"{row.start_ups:,.0f} starts", va="center", fontsize=8)
    axis.set_yticks(positions, labels, fontsize=8.5)
    axis.invert_yaxis()
    axis.set_xlim(0.0, 112.0)
    axis.set_xticks(np.arange(0, 101, 20))
    axis.set_xlabel("% of installed capacity-hours")
    axis.set_title("Use of each coal station's capacity, in merit order (cheapest at the top)")
    axis.grid(axis="x", alpha=0.2)
    handles, legend_labels = axis.get_legend_handles_labels()
    figure.legend(handles, legend_labels, ncol=4, fontsize=8.5, loc="lower center", frameon=False)
    figure.tight_layout(rect=(0, 0.06 if len(rows) < 12 else 0.035, 1, 1))
    return figure


def loading_and_ramps_figure(fleet: CoalFleet, net_load_ramp: pd.Series):
    """Distribution of unit loading when online, and of hourly fleet and net-load ramps."""
    units = fleet.units.index[~fleet.units.export_reservation & fleet.units.committable]
    online = fleet.online[units]
    status = fleet.status[units].where(online.gt(ONLINE_TOLERANCE), 0.0)
    loading = (100.0 * fleet.dispatch[units] / online.where(online.gt(ONLINE_TOLERANCE))).clip(upper=100.0)
    weights = status.mul(fleet.weights, axis=0)
    values = loading.to_numpy().ravel()
    unit_hours = weights.to_numpy().ravel()
    keep = np.isfinite(values) & (unit_hours > 0.0)

    figure, axes = plt.subplots(1, 2, figsize=(15, 5))
    bins = np.arange(0.0, 105.0, 5.0)
    if keep.any():
        share, _ = np.histogram(values[keep], bins, weights=unit_hours[keep])
        axes[0].bar(bins[:-1] + 2.5, 100.0 * share / share.sum(), width=4.6,
                    color=CARRIER_COLORS["coal"])
        p_min = (
            100.0 * fleet.minimum[units].where(online.gt(ONLINE_TOLERANCE))
            / online.where(online.gt(ONLINE_TOLERANCE))
        ).stack().median()
        if np.isfinite(p_min):
            axes[0].axvline(p_min, color="black", linestyle=":", linewidth=1.0,
                            label=f"Median minimum stable output ({p_min:.0f}%)")
            axes[0].legend(fontsize=8, loc="upper left")
    axes[0].set_xlim(0.0, 100.0)
    axes[0].set_xlabel("Unit output as % of its online available capacity")
    axes[0].set_ylabel("% of online unit-hours")
    axes[0].set_title("Coal unit loading when online")

    fleet_ramp = fleet.fleet(fleet.dispatch).diff().dropna()
    net_ramp = net_load_ramp.dropna()
    if len(fleet_ramp) and len(net_ramp):
        limit = float(np.nanpercentile(np.abs(np.concatenate([fleet_ramp, net_ramp])), 99.9))
        ramp_bins = np.linspace(-limit, limit, 61)
        for values, label, color in (
            (net_ramp, "Net load (demand minus wind and solar)", "#7F7F7F"),
            (fleet_ramp, "Coal fleet dispatch", CARRIER_COLORS["coal"]),
        ):
            share, _ = np.histogram(values.clip(-limit, limit), ramp_bins)
            axes[1].stairs(100.0 * share / share.sum(), ramp_bins, label=label,
                           color=color, linewidth=1.6)
        axes[1].axvline(0.0, color="black", linewidth=0.7)
        axes[1].legend(fontsize=8)
    axes[1].set_xlabel("Change from the previous hour (MW/h)")
    axes[1].set_ylabel("% of hours")
    axes[1].set_title("Hourly ramps: what the coal fleet follows")
    for axis in axes:
        axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure
