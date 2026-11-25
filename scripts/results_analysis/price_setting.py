"""Which technology sets the marginal price, by month and hour of day.

A generator can set the price when it can still move: it is part-loaded
(above its minimum stable output and below its online available capacity)
or, with relaxed commitment, partly committed. A generator sets the price at
its own bus; in each hour and Tamil Nadu area, the price setter is the
flexible generator whose marginal cost is nearest its own bus price, among
generators whose bus price is within 3% of the area's (the difference being
marginal losses, so no congestion lies between them). A match within 0.2%
(or 1 per MWh) is exact; up to 10% away it is still that generator, the difference being
the start-up and commitment costs that relaxed commitment adds to the price.
Hours with no such generator are labelled by the price: at the
unserved-energy cost (scarcity), at zero (surplus), or otherwise set by a
constraint that links hours or limits energy (storage and reservoir water
values, energy caps, ramping).

Shares weight each area-hour by the area's demand.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import CARRIER_COLORS, CARRIER_LABELS, CARRIER_ORDER, canonical_carrier
from .emissions import tn_areas
from .loaders import AnalysisRun
from .metrics import _area_timeseries
from .statistics import price_timeseries, statistic_timeseries

SCARCITY = "scarcity (unserved energy)"
SURPLUS = "zero-price surplus"
CONSTRAINT = "inter-temporal or energy constraint"
PUMPING = "pumped-storage pumping"
CATEGORY_COLORS = {
    SCARCITY: CARRIER_COLORS["unserved_energy"],
    SURPLUS: "#C7E9C0",
    CONSTRAINT: "#BDBDBD",
    PUMPING: CARRIER_COLORS["storage_charging"],
}

LOADING_TOLERANCE_MW = 0.1
PRICE_TOLERANCE = 1.0  # currency per MWh, or the share below of the price
PRICE_TOLERANCE_SHARE = 0.002
# Bus prices this close differ by marginal losses rather than congestion.
LOSS_DIFFERENCE_SHARE = 0.03
# Gaps up to this share of the price are start-up and commitment costs.
COMMITMENT_PREMIUM_SHARE = 0.10
PARTLY_COMMITTED = (0.01, 0.99)
SCARCITY_SHARE = 0.95


def _dense(run: AnalysisRun, component: str, attribute: str, names: pd.Index) -> pd.DataFrame:
    values = run.network.get_switchable_as_dense(component, attribute)[names]
    values.index = run.snapshots
    return values.astype(float)


def _generator_candidates(run: AnalysisRun, prices: pd.DataFrame):
    """Flexibility, marginal cost, own bus price and carrier of each generator."""
    base = run.base
    generators = base.generators.loc[base.generators.bus.isin(prices.columns)]
    units = generators.index
    dispatch = run.frame("generators", "p").reindex(columns=units).fillna(0.0)
    unused = statistic_timeseries(run, "curtailment", components="Generator", groupby="name")
    if not unused.empty:
        unused.columns = unused.columns.get_level_values("name")
    available = dispatch + unused.reindex(columns=units, fill_value=0.0).clip(lower=0.0)
    committable = generators.committable.fillna(False).astype(bool)
    status = run.frame("generators", "status").reindex(columns=units).fillna(0.0)
    status.loc[:, ~committable.to_numpy()] = 1.0
    minimum = status * _dense(run, "Generator", "p_min_pu", units) * generators.p_nom
    part_loaded = dispatch.gt(minimum + LOADING_TOLERANCE_MW) & (status * available).gt(
        dispatch + LOADING_TOLERANCE_MW
    )
    low, high = PARTLY_COMMITTED
    partly_committed = status.gt(low) & status.lt(high) & committable.to_numpy()
    flexible = (part_loaded | partly_committed) & dispatch.gt(LOADING_TOLERANCE_MW)
    return (
        flexible.to_numpy(),
        _dense(run, "Generator", "marginal_cost", units).to_numpy(),
        prices[generators.bus].to_numpy(),
        np.asarray(generators.carrier.map(canonical_carrier), dtype=object),
    )


def _hydro_candidates(run: AnalysisRun, prices: pd.DataFrame):
    """The same for hydro turbines and pumps, priced at their reservoir's water value.

    A part-loaded turbine sets the grid price at (water price + cost) /
    efficiency; a part-loaded pump at water price * efficiency - cost.
    """
    links = run.base.links
    carrier = links.carrier.astype(str)
    turbines = links.index[carrier.eq("hydro_turbine") & links.bus1.isin(prices.columns)]
    pumps = links.index[carrier.eq("hydro_pump") & links.bus0.isin(prices.columns)]
    names = turbines.append(pumps)
    if not len(names):
        return None
    water = run.frame("buses", "marginal_price")
    flow = run.frame("links", "p0").reindex(columns=names).fillna(0.0).abs()
    limit = _dense(run, "Link", "p_max_pu", names) * links.loc[names, "p_nom"]
    flexible = flow.gt(LOADING_TOLERANCE_MW) & flow.lt(limit - LOADING_TOLERANCE_MW)
    efficiency = _dense(run, "Link", "efficiency", names)
    cost = _dense(run, "Link", "marginal_cost", names)
    turbine_water = water.reindex(columns=links.loc[turbines, "bus0"]).to_numpy()
    pump_water = water.reindex(columns=links.loc[pumps, "bus1"]).to_numpy()
    implied = np.concatenate(
        [
            (turbine_water + cost[turbines].to_numpy()) / efficiency[turbines].to_numpy(),
            pump_water * efficiency[pumps].to_numpy() - cost[pumps].to_numpy(),
        ],
        axis=1,
    )
    own_price = np.concatenate(
        [prices[links.loc[turbines, "bus1"]].to_numpy(), prices[links.loc[pumps, "bus0"]].to_numpy()],
        axis=1,
    )
    labels = np.asarray(["hydro"] * len(turbines) + [PUMPING] * len(pumps), dtype=object)
    return flexible.to_numpy(), np.nan_to_num(implied, nan=np.inf), own_price, labels


def price_setters(run: AnalysisRun) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return the price setter per hour (rows) and TN area (columns), and whether it matched exactly.

    Both frames are empty when the solves carry no prices.
    """
    prices = price_timeseries(run)
    areas = tn_areas(run).intersection(prices.columns)
    if prices.empty or prices.isna().all().all() or not len(areas):
        return pd.DataFrame(), pd.DataFrame()
    parts = [_generator_candidates(run, prices), _hydro_candidates(run, prices)]
    flexible, cost, own_price, carriers = (
        np.concatenate(arrays, axis=-1) for arrays in zip(*[part for part in parts if part is not None])
    )
    generators = run.base.generators
    scarcity_cost = generators.loc[generators.carrier.astype(str).eq("unserved_energy"), "marginal_cost"]
    scarcity = float(scarcity_cost.min()) if len(scarcity_cost) else np.inf

    # Each candidate's gap to the price at its own bus, relative to that price.
    own_tolerance = np.maximum(PRICE_TOLERANCE, PRICE_TOLERANCE_SHARE * np.abs(own_price))
    own_gap = np.abs(cost - own_price)
    exact_unit = own_gap <= own_tolerance
    near_unit = own_gap <= np.maximum(own_tolerance, COMMITMENT_PREMIUM_SHARE * np.abs(own_price))
    relative_gap = own_gap / np.maximum(np.abs(own_price), PRICE_TOLERANCE)
    result, exact = {}, {}
    for area in areas:
        price = prices[area].to_numpy()[:, None]
        tolerance = np.maximum(PRICE_TOLERANCE, LOSS_DIFFERENCE_SHARE * np.abs(price))
        reachable = flexible & near_unit & (np.abs(own_price - price) <= tolerance)
        residual = np.where(reachable, relative_gap, np.inf)
        best = residual.argmin(axis=1)
        rows = np.arange(len(best))
        matched = np.isfinite(residual[rows, best])
        exact[area] = matched & exact_unit[rows, best]
        labels = np.where(matched, carriers[best], "")
        price = price[:, 0]
        labels = np.where(~matched & (price >= SCARCITY_SHARE * scarcity), SCARCITY, labels)
        labels = np.where(~matched & (labels == "") & (np.abs(price) <= PRICE_TOLERANCE), SURPLUS, labels)
        labels = np.where(labels == "", CONSTRAINT, labels)
        # The unserved-energy generators are the scarcity category.
        labels = np.where(labels == "unserved_energy", SCARCITY, labels)
        result[area] = labels
    return pd.DataFrame(result, index=run.snapshots), pd.DataFrame(exact, index=run.snapshots)


def _ordered(columns) -> list[str]:
    carriers = [c for c in CARRIER_ORDER if c in columns]
    categories = [c for c in (PUMPING, SURPLUS, CONSTRAINT, SCARCITY) if c in columns]
    return carriers + sorted(set(columns) - set(carriers) - set(categories)) + categories


def price_setting_shares(
    run: AnalysisRun, setters: pd.DataFrame, exact: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Demand-weighted shares (%) of each price setter by month, by hour of day and overall.

    The overall table adds the share of each setter's demand-hours in which
    its marginal cost matched the price exactly.
    """
    demand = _area_timeseries(run)["demand"].reindex(columns=setters.columns, fill_value=0.0)
    weight = demand.clip(lower=0.0).mul(run.weights, axis=0)
    long = pd.DataFrame(
        {
            "setter": setters.to_numpy().ravel(),
            "weight": weight.to_numpy().ravel(),
            "exact": exact.reindex_like(setters).to_numpy().ravel().astype(bool),
            "month": np.repeat(np.asarray(run.snapshots.to_period("M").astype(str)), setters.shape[1]),
            "hour": np.repeat(run.snapshots.hour.to_numpy(), setters.shape[1]),
        }
    )
    tables = {}
    for key, column in (("price_setting_monthly", "month"), ("price_setting_by_hour", "hour")):
        table = long.pivot_table(index=column, columns="setter", values="weight", aggfunc="sum", fill_value=0.0)
        table = 100.0 * table.div(table.sum(axis=1), axis=0)
        tables[key] = table[_ordered(table.columns)]
    overall = long.groupby("setter").weight.sum()
    exact_weight = long.loc[long.exact].groupby("setter").weight.sum().reindex(overall.index, fill_value=0.0)
    table = pd.DataFrame(
        {
            "share_pct": 100.0 * overall / overall.sum(),
            "exact_match_pct": 100.0 * exact_weight / overall.replace(0.0, np.nan),
        }
    )
    table.loc[[c for c in (SURPLUS, CONSTRAINT, SCARCITY) if c in table.index], "exact_match_pct"] = np.nan
    table.index.name = "setter"
    tables["price_setting_overall"] = table.reindex(_ordered(table.index))
    return tables


def price_setting_figure(tables: dict[str, pd.DataFrame]):
    """Stacked shares of price setters by month and by hour of day."""
    figure, axes = plt.subplots(1, 2, figsize=(15, 5.6), sharey=True)
    for axis, key, xlabel in (
        (axes[0], "price_setting_monthly", "Month"),
        (axes[1], "price_setting_by_hour", "Hour of day"),
    ):
        table = tables[key]
        positions = np.arange(len(table))
        bottom = np.zeros(len(table))
        for column in table:
            values = table[column].to_numpy()
            axis.bar(positions, values, bottom=bottom, width=0.85,
                     color=CATEGORY_COLORS.get(column, CARRIER_COLORS.get(column, "#7F7F7F")),
                     label=CARRIER_LABELS.get(column, column[:1].upper() + column[1:]))
            bottom += values
        labels = [str(value) for value in table.index]
        step = 1 if len(labels) <= 13 else 3
        axis.set_xticks(positions[::step], labels[::step], rotation=45 if key.endswith("monthly") else 0,
                        ha="right" if key.endswith("monthly") else "center")
        axis.set_xlabel(xlabel)
        axis.set_ylim(0.0, 100.0)
        axis.grid(axis="y", alpha=0.2)
    axes[0].set_ylabel("% of Tamil Nadu demand-hours")
    axes[0].set_title("Price setter by month")
    axes[1].set_title("Price setter by hour of day")
    handles, labels = axes[1].get_legend_handles_labels()
    figure.legend(handles[::-1], labels[::-1], loc="center right", fontsize=8.5, frameon=False)
    figure.tight_layout(rect=(0, 0, 0.82, 1))
    return figure
