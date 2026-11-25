"""Transmission corridors: flows, loading and utilisation histograms.

A corridor is one connection between two electricity buses. Lines carry
flow both ways. Interconnectors are modelled as one Link per direction,
named ``"<corridor> →"`` and ``"<corridor> ←"``; the pair is merged into one
corridor here, so its loading in either direction is measured against that
direction's own limit.

Flows come from PyPSA's ``transmission`` statistic at the electricity buses.
"""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa

from .loaders import AnalysisRun
from .statistics import TRANSMISSION_LINK_CARRIERS, statistic_timeseries

DIRECTION_MARKERS = ("→", "←")

# Loading at or above this share of the operating limit counts as binding.
AT_LIMIT_PCT = 99.0

FORWARD_COLOR = "#1F78B4"
REVERSE_COLOR = "#E6AB02"


def corridor_name(branch: str) -> str:
    """Return the corridor a branch belongs to: its name without a direction arrow."""
    name = str(branch).rstrip()
    for marker in DIRECTION_MARKERS:
        if name.endswith(marker):
            return name[: -len(marker)].rstrip()
    return name


def branch_flows(run: AnalysisRun) -> pd.DataFrame:
    """Return hourly flow (MW, bus0 to bus1) on every Line and AC/DC Link.

    PyPSA's ``transmission`` statistic covers branches whose two ends are
    electricity buses, so hydro turbines and pumps are excluded.
    """
    flows = statistic_timeseries(run, "transmission", groupby="name")
    if flows.empty:
        return flows
    flows.columns = flows.columns.get_level_values("name")
    return flows


def _branch_ratings(network: pypsa.Network) -> pd.DataFrame:
    """Each branch's buses and its limit (MW) in its own two directions."""
    lines = network.lines
    links = network.links
    links = links.loc[links.carrier.astype(str).isin(TRANSMISSION_LINK_CARRIERS)]
    line_limit = lines.s_nom * lines.s_max_pu
    return pd.concat(
        [
            pd.DataFrame(
                {
                    "component": "Line",
                    "carrier": lines.carrier.astype(str),
                    "bus0": lines.bus0,
                    "bus1": lines.bus1,
                    "capacity_mw": lines.s_nom,
                    "forward_mw": line_limit,
                    "reverse_mw": line_limit,
                }
            ),
            pd.DataFrame(
                {
                    "component": "Link",
                    "carrier": links.carrier.astype(str),
                    "bus0": links.bus0,
                    "bus1": links.bus1,
                    "capacity_mw": links.p_nom,
                    "forward_mw": links.p_nom * links.p_max_pu,
                    "reverse_mw": (-links.p_nom * links.p_min_pu).clip(lower=0.0),
                }
            ),
        ]
    )


def corridor_table(network: pypsa.Network) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the corridors and the branches that make them up.

    The corridor is oriented like its first branch: ``limit_mw`` is its limit
    from ``bus0`` to ``bus1`` and ``reverse_limit_mw`` the other way. The
    branch table maps each branch to its corridor and gives the sign that
    turns the branch's flow into the corridor's direction. Branches that
    share a name but not the same two buses stay separate corridors.
    """
    branches = _branch_ratings(network)
    branches["corridor"] = [corridor_name(name) for name in branches.index]
    pairs = branches[["bus0", "bus1"]].apply(frozenset, axis=1)
    mixed = pairs.groupby(branches.corridor).transform("nunique").gt(1)
    branches.loc[mixed, "corridor"] = branches.index[mixed]

    rows = {}
    signs = pd.Series(1.0, index=branches.index)
    for corridor, group in branches.groupby("corridor", sort=False):
        first = group.iloc[0]
        forward = group.bus0.eq(first.bus0)
        signs[group.index] = np.where(forward, 1.0, -1.0)
        rows[corridor] = {
            "component": "Link" if group.component.eq("Link").any() else "Line",
            "carrier": "/".join(sorted(group.carrier.unique())),
            "branches": len(group),
            "bus0": first.bus0,
            "bus1": first.bus1,
            "capacity_mw": group.capacity_mw.max(),
            "limit_mw": group.forward_mw.where(forward, group.reverse_mw).sum(),
            "reverse_limit_mw": group.reverse_mw.where(forward, group.forward_mw).sum(),
        }
    corridors = pd.DataFrame.from_dict(rows, orient="index")
    corridors.index.name = "corridor"
    branches["sign"] = signs
    return corridors, branches[["corridor", "sign"]]


def corridor_flows(run: AnalysisRun, flows: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return hourly net flow (MW) per corridor, positive from bus0 to bus1."""
    flows = branch_flows(run) if flows is None else flows
    corridors, branches = corridor_table(run.base)
    if corridors.empty:
        return pd.DataFrame(index=run.snapshots)
    signed = flows.reindex(columns=branches.index, fill_value=0.0).mul(branches.sign, axis=1)
    result = signed.T.groupby(branches.corridor).sum().T
    return result.reindex(index=run.snapshots, columns=corridors.index, fill_value=0.0)


def corridor_loading(
    run: AnalysisRun, flows: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Return hourly loading (%) of each corridor's limit in the flow's direction.

    *flows* are corridor flows from ``corridor_flows``. A direction without
    any capacity gives NaN.
    """
    flows = corridor_flows(run) if flows is None else flows
    corridors, _ = corridor_table(run.base)
    forward = corridors.limit_mw.replace(0.0, np.nan)
    reverse = corridors.reverse_limit_mw.replace(0.0, np.nan)
    loading = flows.clip(lower=0.0).div(forward, axis=1).where(
        flows.ge(0.0), (-flows).div(reverse, axis=1)
    )
    # A corridor that carries no flow is idle, not undefined.
    return (loading * 100.0).where(flows.ne(0.0), 0.0)


def corridor_metrics(
    run: AnalysisRun, flows: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Summarize flows and loading on every corridor.

    Loading is the flow relative to the corridor's operating limit in the
    flow's direction (``s_nom * s_max_pu`` for Lines, ``p_nom * p_max_pu``
    for each direction's Link), so 100% means the limit binds. Hours are
    snapshot-weighted. *flows* are corridor flows from ``corridor_flows``.
    """
    flows = corridor_flows(run) if flows is None else flows
    corridors, _ = corridor_table(run.base)
    if corridors.empty:
        return pd.DataFrame()
    loading = corridor_loading(run, flows)
    weights = run.weights
    hours = float(weights.sum())
    result = corridors.copy()
    result["maximum_absolute_flow_mw"] = flows.abs().max()
    result["mean_loading_pct"] = loading.mul(weights, axis=0).sum() / hours
    result["p95_loading_pct"] = loading.quantile(0.95)
    result["maximum_loading_pct"] = loading.max()
    result["hours_at_or_above_99pct"] = loading.ge(AT_LIMIT_PCT).mul(weights, axis=0).sum()
    result["hours_above_80pct"] = loading.gt(80.0).mul(weights, axis=0).sum()
    result["energy_bus0_to_bus1_gwh"] = flows.clip(lower=0.0).mul(weights, axis=0).sum() / 1_000.0
    result["energy_bus1_to_bus0_gwh"] = (-flows).clip(lower=0.0).mul(weights, axis=0).sum() / 1_000.0
    result["net_energy_bus0_to_bus1_gwh"] = flows.mul(weights, axis=0).sum() / 1_000.0
    return result.sort_values("mean_loading_pct", ascending=False)


def utilisation_histogram_figure(
    flows: pd.DataFrame,
    loading: pd.DataFrame,
    metrics: pd.DataFrame,
    weights: pd.Series,
    title: str,
    bin_width: float = 10.0,
):
    """Small-multiple histograms of corridor loading, split by flow direction.

    Each panel shows the share of hours a corridor spends in each loading
    band, with the two flow directions stacked. Panels are ordered by mean
    loading, most used first.
    """
    corridors = [name for name in metrics.index if name in loading]
    columns = 3
    rows = max(1, math.ceil(len(corridors) / columns))
    figure, axes = plt.subplots(
        rows, columns, figsize=(15, 2.7 * rows + 0.8), sharex=True, sharey=True, squeeze=False
    )
    if not corridors:
        axes[0, 0].text(0.5, 0.5, "No corridors", ha="center", va="center")
    bins = np.arange(0.0, 100.0 + bin_width, bin_width)
    total = float(weights.sum())
    for axis, name in zip(axes.flat, corridors):
        row = metrics.loc[name]
        values = loading[name].clip(upper=100.0).fillna(0.0)
        forward = flows[name].ge(0.0)
        forward_share, _ = np.histogram(values[forward], bins, weights=weights[forward])
        reverse_share, _ = np.histogram(values[~forward], bins, weights=weights[~forward])
        forward_share = forward_share / total * 100.0
        reverse_share = reverse_share / total * 100.0
        centers = bins[:-1] + bin_width / 2.0
        axis.bar(
            centers, forward_share, width=bin_width * 0.92, color=FORWARD_COLOR,
            label=f"to {row.bus1}",
        )
        axis.bar(
            centers, reverse_share, width=bin_width * 0.92, bottom=forward_share,
            color=REVERSE_COLOR, label=f"to {row.bus0}",
        )
        axis.set_title(name, fontsize=9.5)
        axis.text(
            0.98, 0.95,
            f"mean {row.mean_loading_pct:.0f}%\n"
            f"≥{AT_LIMIT_PCT:.0f}%: {row.hours_at_or_above_99pct:,.0f} h",
            transform=axis.transAxes, ha="right", va="top", fontsize=8,
        )
        axis.legend(fontsize=7, loc="upper center", frameon=False)
        axis.grid(axis="y", alpha=0.2)
    for axis in axes.flat[len(corridors):]:
        axis.axis("off")
    for axis in axes[-1]:
        axis.set_xlabel("Loading of the operating limit (%)")
    for axis in axes[:, 0]:
        axis.set_ylabel("% of hours")
    axes[0, 0].set_xlim(0.0, 100.0)
    figure.suptitle(title)
    figure.tight_layout(rect=(0, 0, 1, 0.98))
    return figure
