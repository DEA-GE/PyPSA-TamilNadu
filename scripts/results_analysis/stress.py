"""Peak and stress weeks: when the system is tightest, and what runs then.

Each criterion picks one hour of the run: peak demand, peak and minimum net
load (demand minus wind and solar), the least spare thermal capacity, the
most unserved energy and the highest system price. The week around each
hour (from three days before its day) is analysed; criteria whose weeks
overlap by four days or more share one week.

Spare thermal capacity is PyPSA's ``curtailment`` statistic for thermal
generators: capacity available after outages but not dispatched, whether
online or not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .coal import CoalFleet, draw_fleet, fleet_timeseries
from .config import CARRIER_COLORS, CURRENCY, canonical_carrier
from .loaders import AnalysisRun
from .plots import draw_supply_and_demand
from .statistics import statistic_timeseries

THERMAL_CARRIERS = ("coal", "oil_gas", "diesel", "nuclear", "bio_power")

WEEK = pd.Timedelta(days=7)
DAYS_BEFORE = pd.Timedelta(days=3)
MERGE_OVERLAP = pd.Timedelta(days=4)

# Shorter runs are one stressed period already; they get no week selection.
MINIMUM_RUN = pd.Timedelta(days=14)


@dataclass
class StressWeek:
    start: pd.Timestamp
    end: pd.Timestamp
    events: list[tuple[str, pd.Timestamp]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return " · ".join(label for label, _ in self.events)


def spare_thermal_capacity(run: AnalysisRun) -> pd.Series:
    """Hourly thermal capacity (MW) available but not dispatched."""
    unused = statistic_timeseries(run, "curtailment", components="Generator", groupby="carrier")
    if unused.empty:
        return pd.Series(0.0, index=run.snapshots)
    carriers = unused.columns.get_level_values("carrier").map(canonical_carrier)
    return unused.loc[:, carriers.isin(THERMAL_CARRIERS)].clip(lower=0.0).sum(axis=1)


def system_timeseries(
    run: AnalysisRun,
    balance: pd.DataFrame,
    ramps: pd.DataFrame,
    system_price: pd.Series,
) -> pd.DataFrame:
    """Hourly demand, net load, unserved energy, spare thermal capacity and price."""
    return pd.DataFrame(
        {
            "demand_mw": ramps.demand_mw,
            "net_load_mw": ramps.net_load_mw,
            "wind_and_solar_mw": ramps.renewable_generation_mw,
            "unserved_energy_mw": balance.get(
                "unserved_energy", pd.Series(0.0, index=run.snapshots)
            ),
            "spare_thermal_capacity_mw": spare_thermal_capacity(run),
            "system_price": system_price.reindex(run.snapshots),
        },
        index=run.snapshots,
    )


def stress_events(system: pd.DataFrame) -> pd.DataFrame:
    """Return the hour each stress criterion picks, with its value."""
    rows = [
        ("Peak demand", system.demand_mw.idxmax(), system.demand_mw.max(), "MW"),
        ("Peak net load", system.net_load_mw.idxmax(), system.net_load_mw.max(), "MW"),
        ("Minimum net load", system.net_load_mw.idxmin(), system.net_load_mw.min(), "MW"),
        (
            "Least spare thermal capacity",
            system.spare_thermal_capacity_mw.idxmin(),
            system.spare_thermal_capacity_mw.min(),
            "MW",
        ),
    ]
    unserved = system.unserved_energy_mw
    if unserved.gt(1e-6).any():
        daily = unserved.resample("D").sum()
        worst_day = daily.idxmax()
        hours = unserved.loc[worst_day : worst_day + pd.Timedelta(hours=23)]
        rows.append(("Most unserved energy in a day", hours.idxmax(), float(daily.max()), "MWh/day"))
    price = system.system_price
    if price.notna().any():
        rows.append(("Highest system price", price.idxmax(), price.max(), f"{CURRENCY}/MWh"))
    events = pd.DataFrame(rows, columns=["criterion", "timestamp", "value", "unit"])
    return events.set_index("criterion")


def _window(event: pd.Timestamp, first: pd.Timestamp, last: pd.Timestamp, step: pd.Timedelta):
    start = event.normalize() - DAYS_BEFORE
    start = min(max(start, first), max(last + step - WEEK, first))
    end = min(start + WEEK - step, last)
    return start, end


def stress_weeks(system: pd.DataFrame, events: pd.DataFrame) -> list[StressWeek]:
    """Group the events into weeks, merging weeks that overlap by four days or more."""
    index = pd.DatetimeIndex(system.index)
    if len(index) < 2 or index[-1] - index[0] < MINIMUM_RUN:
        return []
    step = index[1] - index[0]
    weeks: list[StressWeek] = []
    for criterion, row in events.iterrows():
        start, end = _window(row.timestamp, index[0], index[-1], step)
        for week in weeks:
            overlap = min(end, week.end) - max(start, week.start) + step
            if overlap >= MERGE_OVERLAP:
                week.events.append((criterion, row.timestamp))
                break
        else:
            weeks.append(StressWeek(start, end, [(criterion, row.timestamp)]))
    return sorted(weeks, key=lambda week: week.start)


def stress_week_table(
    weeks: list[StressWeek],
    system: pd.DataFrame,
    balance: pd.DataFrame,
    weights: pd.Series,
    fleet: CoalFleet | None,
) -> pd.DataFrame:
    """Return one row per week: its criteria and how the system ran."""
    rows = []
    coal = fleet_timeseries(fleet) if fleet is not None else None
    for number, week in enumerate(weeks, start=1):
        hours = slice(week.start, week.end)
        part, w = system.loc[hours], weights.loc[hours]
        energy = lambda values: float(values.mul(w).sum())  # noqa: E731
        demand = energy(part.demand_mw)
        row = {
            "week": number,
            "criteria": week.label,
            "start": week.start,
            "end": week.end,
            "demand_gwh": demand / 1_000.0,
            "peak_demand_mw": part.demand_mw.max(),
            "maximum_net_load_mw": part.net_load_mw.max(),
            "minimum_net_load_mw": part.net_load_mw.min(),
            "wind_and_solar_share_pct": 100.0 * energy(part.wind_and_solar_mw) / demand if demand else np.nan,
            "unserved_energy_mwh": energy(part.unserved_energy_mw),
            "minimum_spare_thermal_capacity_mw": part.spare_thermal_capacity_mw.min(),
            f"mean_system_price_{CURRENCY.lower()}_per_mwh": part.system_price.mean(),
            f"maximum_system_price_{CURRENCY.lower()}_per_mwh": part.system_price.max(),
        }
        if "imports" in balance:
            row["imports_gwh"] = energy(balance.loc[hours, "imports"]) / 1_000.0
        if coal is not None:
            fleet_part = coal.loc[hours]
            row["coal_generation_gwh"] = energy(fleet_part.dispatch) / 1_000.0
            row["coal_capacity_factor_pct"] = 100.0 * energy(fleet_part.dispatch) / energy(fleet_part.capacity)
            row["coal_loading_when_online_pct"] = (
                100.0 * energy(fleet_part.dispatch) / energy(fleet_part.online)
                if energy(fleet_part.online) else np.nan
            )
        rows.append(row)
    return pd.DataFrame(rows).set_index("week") if rows else pd.DataFrame()


def stress_hours(
    events: pd.DataFrame,
    system: pd.DataFrame,
    balance: pd.DataFrame,
    fleet: CoalFleet | None,
) -> pd.DataFrame:
    """Return the supply and system state (MW) in each criterion's hour.

    Rows are the energy-balance categories (``balance_*``, supply positive)
    followed by system indicators; columns are the criteria.
    """
    columns = {}
    coal = fleet_timeseries(fleet) if fleet is not None else None
    for criterion, row in events.iterrows():
        hour = row.timestamp
        values = balance.loc[hour].rename(lambda name: f"balance_{name}_mw")
        indicators = system.loc[hour].copy()
        if coal is not None:
            indicators["coal_online_capacity_mw"] = coal.at[hour, "online"]
            indicators["coal_available_capacity_mw"] = coal.at[hour, "available"]
        columns[criterion] = pd.concat([values, indicators])
    result = pd.DataFrame(columns)
    result.loc["timestamp"] = events.timestamp.astype(str)
    result = result.reindex(["timestamp", *result.index.drop("timestamp")])
    result.index.name = "quantity"
    return result


def stress_week_figure(
    week: StressWeek,
    number: int,
    balance: pd.DataFrame,
    system: pd.DataFrame,
    fleet: CoalFleet | None,
):
    """Hourly supply, coal fleet and prices over one stress week."""
    hours = slice(week.start, week.end)
    has_price = system.system_price.loc[hours].notna().any()
    panels = 1 + (fleet is not None) + 1
    ratios = [3, 2, 1.5][:panels]
    figure, axes = plt.subplots(
        panels, 1, figsize=(15, 2.1 * sum(ratios) + 0.8), sharex=True,
        gridspec_kw={"height_ratios": ratios},
    )
    secondary = draw_supply_and_demand(axes[0], balance.loc[hours])
    # Number the marked hours in time order.
    marked = sorted(week.events, key=lambda event: event[1])
    events = ", ".join(
        f"({marker}) {label} {timestamp:%d %b %H:%M}"
        for marker, (label, timestamp) in enumerate(marked, start=1)
    )
    figure.suptitle(
        f"Week {number}, {week.start:%d %b} to {week.end:%d %b %Y}\n{events}", fontsize=11
    )
    axes[0].set_title("System supply and demand")
    _side_legend(axes[0], secondary, reverse=True)
    axis_index = 1
    if fleet is not None:
        draw_fleet(axes[axis_index], fleet_timeseries(fleet).loc[hours])
        axes[axis_index].set_title("Coal fleet (Tamil Nadu units)")
        _side_legend(axes[axis_index])
        axis_index += 1
    last = axes[axis_index]
    spare = system.spare_thermal_capacity_mw.loc[hours]
    last.plot(spare.index, spare.to_numpy(), color=CARRIER_COLORS["oil_gas"], linewidth=1.2,
              label="Spare thermal capacity (MW)")
    last.set_ylabel("MW")
    last.set_ylim(bottom=0.0)
    price_axis = None
    if has_price:
        price_axis = last.twinx()
        price = system.system_price.loc[hours]
        price_axis.plot(price.index, price.to_numpy(), color="black", linewidth=1.0,
                        label=f"System price ({CURRENCY}/MWh, right axis)")
        price_axis.set_ylabel(f"{CURRENCY}/MWh")
    _side_legend(last, price_axis)
    last.set_title("Spare thermal capacity and system price")
    last.grid(alpha=0.2)
    last.set_xlabel("Time")
    previous = None
    height = 0.98
    for marker, (_, timestamp) in enumerate(marked, start=1):
        for axis in axes:
            axis.axvline(timestamp, color="#D62728", linestyle="--", linewidth=0.9)
        # Stagger the numbers of events a few hours apart so they stay legible.
        close = previous is not None and abs(timestamp - previous) < pd.Timedelta(hours=8)
        height = height - 0.07 if close else 0.98
        previous = timestamp
        axes[0].annotate(
            f"({marker})", (timestamp, height), xycoords=("data", "axes fraction"),
            va="top", ha="left", fontsize=8.5, color="#D62728",
            xytext=(2, 0), textcoords="offset points",
        )
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    return figure


def _side_legend(axis, secondary=None, reverse: bool = False) -> None:
    """One-column legend to the right of *axis*, with *secondary*'s lines last.

    *reverse* lists a stack top-down, in the order its layers appear.
    """
    handles, labels = axis.get_legend_handles_labels()
    if reverse:
        handles, labels = handles[::-1], labels[::-1]
    if secondary is not None:
        extra_handles, extra_labels = secondary.get_legend_handles_labels()
        handles, labels = handles + extra_handles, labels + extra_labels
    axis.legend(
        handles, labels, loc="upper left", bbox_to_anchor=(1.06, 1.0), fontsize=8,
        frameon=False,
    )


def stress_analysis(
    run: AnalysisRun,
    balance: pd.DataFrame,
    ramps: pd.DataFrame,
    system_price: pd.Series,
    fleet: CoalFleet | None,
) -> tuple[dict[str, pd.DataFrame], dict[str, plt.Figure]]:
    """Tables and figures for the peak and stress weeks of a run."""
    system = system_timeseries(run, balance, ramps, system_price)
    events = stress_events(system)
    weeks = stress_weeks(system, events)
    week_of = {
        criterion: number
        for number, week in enumerate(weeks, start=1)
        for criterion, _ in week.events
    }
    events = events.assign(week=events.index.map(week_of))
    tables = {
        "stress_events": events,
        "stress_hours": stress_hours(events, system, balance, fleet),
    }
    figures = {}
    if weeks:
        tables["stress_weeks"] = stress_week_table(weeks, system, balance, run.weights, fleet)
        for number, week in enumerate(weeks, start=1):
            figures[f"stress_week_{number}"] = stress_week_figure(
                week, number, balance, system, fleet
            )
    return tables, figures
