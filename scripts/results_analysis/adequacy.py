"""Net-load duration against firm available capacity.

Net load is the same as in the ramp and stress analyses: demand, including
neighbouring-region loads and net of the scheduled interstate imports
(modelled as negative loads), minus wind and solar. It is what the model's
own plants must cover. Firm available capacity in an hour is:

- every other generator's available capacity: its dispatch (PyPSA's
  ``supply``) plus what it left unused (``curtailment``), so after outages;
- hydro at its full grid-side turbine and storage-unit rating, an upper
  bound since reservoirs limit energy.

The margin is firm available capacity minus net load.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import CARRIER_COLORS, CARRIER_LABELS, RENEWABLE_CARRIERS, canonical_carrier
from .loaders import AnalysisRun
from .metrics import NON_PLANT_CARRIERS, installed_generation_capacity
from .plots import order_by_variability
from .statistics import statistic_timeseries

FIRM_COLOR = "#D62728"
TOP_HOURS = 200


def firm_capacity(run: AnalysisRun) -> pd.DataFrame:
    """Hourly firm available capacity (MW) by part: generators and hydro."""
    options = {"components": "Generator", "groupby": "carrier"}
    supply = statistic_timeseries(run, "supply", **options)
    unused = statistic_timeseries(run, "curtailment", **options).clip(lower=0.0)
    available = supply.add(unused, fill_value=0.0)
    carriers = available.columns.get_level_values("carrier").map(canonical_carrier)
    keep = ~carriers.isin([*RENEWABLE_CARRIERS, *NON_PLANT_CARRIERS, "hydro"])
    hydro = installed_generation_capacity(run.base).get("hydro", 0.0)
    return pd.DataFrame(
        {
            "generators": available.loc[:, keep].sum(axis=1),
            "hydro": float(hydro),
        },
        index=run.snapshots,
    )


def _sorted_hours(net_load: pd.Series, *frames: pd.DataFrame):
    order = net_load.sort_values(ascending=False, kind="stable").index
    return [frame.loc[order].reset_index(drop=True) for frame in (net_load.to_frame("net_load"), *frames)]


def net_load_duration(
    run: AnalysisRun, balance: pd.DataFrame, net_load: pd.Series, bins: int = 100
) -> pd.DataFrame:
    """Hours sorted by net load, averaged in *bins* equal groups.

    Each row is one group: its share of hours, mean net load, firm available
    capacity and margin, the smallest margin in the group and its unserved
    energy.
    """
    firm = firm_capacity(run).sum(axis=1).rename("firm")
    unserved = balance.get("unserved_energy", pd.Series(0.0, index=run.snapshots)).rename("unserved")
    load, extra = _sorted_hours(net_load, pd.concat([firm, unserved], axis=1))
    frame = pd.concat([load, extra], axis=1)
    frame["margin"] = frame.firm - frame.net_load
    group = np.minimum((np.arange(len(frame)) * bins) // len(frame), bins - 1)
    grouped = frame.groupby(group)
    result = pd.DataFrame(
        {
            "hours_from_pct": grouped.apply(lambda part: 100.0 * part.index[0] / len(frame)),
            "hours_to_pct": grouped.apply(lambda part: 100.0 * (part.index[-1] + 1) / len(frame)),
            "mean_net_load_mw": grouped.net_load.mean(),
            "maximum_net_load_mw": grouped.net_load.max(),
            "mean_firm_available_mw": grouped.firm.mean(),
            "mean_margin_mw": grouped.margin.mean(),
            "minimum_margin_mw": grouped.margin.min(),
            "unserved_energy_mwh": grouped.unserved.sum(),
        }
    )
    result.index.name = "group"
    return result


def adequacy_figure(run: AnalysisRun, balance: pd.DataFrame, net_load: pd.Series):
    """What covers net load, with hours sorted from the highest net load.

    Left: every hour, averaged in 1% groups. Right: the highest-net-load
    hours one by one.
    """
    firm = firm_capacity(run).sum(axis=1)
    # Scheduled imports are already netted out of net load.
    covering = balance.loc[:, balance.ge(0.0).all()].drop(
        columns=[c for c in (*RENEWABLE_CARRIERS, "unserved_energy", "imports") if c in balance]
    )
    covering = order_by_variability(covering)
    unserved = balance.get("unserved_energy", pd.Series(0.0, index=run.snapshots))
    load, supply, extra = _sorted_hours(
        net_load, covering, pd.concat([firm.rename("firm"), unserved.rename("unserved")], axis=1)
    )
    hours = len(load)
    figure, axes = plt.subplots(1, 2, figsize=(15, 5.8), gridspec_kw={"width_ratios": [1.4, 1]})

    groups = min(100, hours)
    group = np.minimum((np.arange(hours) * groups) // hours, groups - 1)
    binned = [frame.groupby(group).mean() for frame in (load, supply, extra)]
    top = min(TOP_HOURS, hours)
    for axis, (load_part, supply_part, extra_part), x, xlabel in (
        (axes[0], binned, (np.arange(groups) + 0.5) * 100.0 / groups,
         "Share of hours, highest net load first (%)"),
        (axes[1], [frame.iloc[:top] for frame in (load, supply, extra)], np.arange(top) + 1,
         f"Hour rank (the {top} highest net-load hours)"),
    ):
        axis.stackplot(
            x, supply_part.T.values,
            labels=[CARRIER_LABELS.get(column, column) for column in supply_part],
            colors=[CARRIER_COLORS.get(str(column), "#7F7F7F") for column in supply_part],
            linewidth=0, alpha=0.85,
        )
        axis.plot(x, load_part.net_load, color="black", linewidth=1.4, label="Net load (demand net of scheduled imports, minus wind and solar)")
        axis.plot(x, extra_part.firm, color=FIRM_COLOR, linewidth=1.4, label="Firm available capacity (hydro at full rating)")
        if extra_part.unserved.gt(1e-6).any():
            axis.bar(x, extra_part.unserved, bottom=load_part.net_load - extra_part.unserved,
                     width=1.0, color=CARRIER_COLORS["unserved_energy"], alpha=0.6,
                     label=CARRIER_LABELS["unserved_energy"])
        axis.set_xlabel(xlabel)
        axis.set_ylabel("MW")
        axis.grid(alpha=0.2)
    axes[0].set_xlim(0.0, 100.0)
    axes[1].set_xlim(0.5, top + 0.5)
    margin = extra.firm - load.net_load
    tightest = int(margin.idxmin())
    axes[0].set_title(f"Net-load duration and what covers it ({100.0 / groups:.0f}% groups)")
    axes[1].set_title(
        f"Highest net-load hours (tightest margin {margin.min():,.0f} MW at rank {tightest + 1})"
    )
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles[::-1], labels[::-1], fontsize=8.5, ncol=4, loc="lower center", frameon=False)
    figure.tight_layout(rect=(0, 0.12, 1, 1))
    return figure
