"""Standard figures shared by all result-analysis notebooks."""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import CARRIER_COLORS, CARRIER_LABELS, CURRENCY, PENALTY_CARRIERS


def _colors(columns) -> list[str]:
    return [CARRIER_COLORS.get(str(column), "#7F7F7F") for column in columns]


def _for_display(frame: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Average hourly data to daily means when the period exceeds a month."""
    if len(frame) and frame.index.max() - frame.index.min() > pd.Timedelta(days=31):
        return frame.resample("D").mean(), "daily mean"
    return frame, "hourly"


def energy_balance_figure(balance: pd.DataFrame):
    """Stack supply above and use below zero, as PyPSA's energy-balance plot."""
    figure, axis = plt.subplots(figsize=(14, 6))
    data, resolution = _for_display(balance)
    supply = data.loc[:, data.ge(0.0).all()]
    use = data.drop(columns=supply.columns)
    for part in (supply, use):
        if part.empty:
            continue
        axis.stackplot(
            part.index,
            part.T.values,
            labels=[CARRIER_LABELS.get(column, column) for column in part],
            colors=_colors(part.columns),
            linewidth=0,
            alpha=0.9,
        )
    axis.axhline(0.0, color="black", linewidth=0.7)
    axis.set_title(f"System electricity balance ({resolution})")
    axis.set_ylabel("MW (supply positive, use negative)")
    axis.set_xlabel("Time")
    axis.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.16))
    axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def generation_comparison_figure(comparison: pd.DataFrame):
    figure, axes = plt.subplots(1, 2, figsize=(15, 5))
    available = comparison.dropna(subset=["modeled_generation_gwh"])
    columns = ["modeled_generation_gwh"]
    labels = ["Modeled"]
    if "observed_generation_gwh" in available:
        columns.append("observed_generation_gwh")
        labels.append("Observed")
    available[columns].rename(columns=dict(zip(columns, labels))).plot.bar(ax=axes[0])
    axes[0].set_title("Generation over the analyzed period")
    axes[0].set_ylabel("GWh")
    if "difference_pct" in available:
        available.difference_pct.plot.bar(ax=axes[1], color="#D95F02")
        axes[1].axhline(0.0, color="black", linewidth=0.7)
        axes[1].set_title("Modeled minus observed")
        axes[1].set_ylabel("%")
    else:
        axes[1].axis("off")
    for axis in axes:
        axis.grid(axis="y", alpha=0.2)
    figure.tight_layout()
    return figure


def storage_figure(
    dispatch: pd.DataFrame,
    state_of_charge: pd.DataFrame,
    store_energy: pd.DataFrame,
    store_capacity: pd.Series,
):
    """Plot electricity storage units and water reservoirs separately.

    Storage units are in electrical MW and MWh; reservoir stores hold water
    energy, shown as a share of each reservoir's capacity.
    """
    figure, axes = plt.subplots(3, 1, figsize=(14, 11), sharex=True)
    dispatch_shown, resolution = _for_display(dispatch)
    soc_shown, _ = _for_display(state_of_charge)
    capacity = store_capacity.replace(0.0, float("nan"))
    reservoirs = [
        store for store in store_energy.columns
        if "downstream" not in str(store).casefold()
        and pd.notna(capacity.get(store)) and math.isfinite(capacity[store])
    ]
    filling, _ = _for_display(store_energy[reservoirs].div(capacity[reservoirs]) * 100.0)

    if dispatch_shown.empty:
        axes[0].text(0.5, 0.5, "No storage units", ha="center", va="center")
    else:
        dispatch_shown.plot(ax=axes[0], linewidth=1.0)
        axes[0].axhline(0.0, color="black", linewidth=0.7)
    axes[0].set_title(f"Storage-unit dispatch, discharge positive ({resolution})")
    axes[0].set_ylabel("MW")
    if not soc_shown.empty:
        soc_shown.plot(ax=axes[1], linewidth=1.0)
    axes[1].set_title("Storage-unit state of charge")
    axes[1].set_ylabel("MWh")
    if not filling.empty:
        filling.plot(ax=axes[2], linewidth=1.0)
        axes[2].legend(ncol=5, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.22))
    axes[2].set_title("Reservoir filling")
    axes[2].set_ylabel("% of capacity")
    axes[2].set_xlabel("Time")
    for axis in axes:
        axis.grid(alpha=0.2)
        legend = axis.get_legend()
        if legend is not None:
            legend.set_title(None)
    figure.tight_layout()
    return figure


def price_figure(prices: pd.DataFrame, system_price: pd.Series):
    """Plot price duration curves per area and the system price over time."""
    figure, axes = plt.subplots(2, 1, figsize=(14, 9))
    if prices.empty:
        axes[0].text(
            0.5, 0.5, "No marginal prices: binary unit-commitment solves have no duals",
            ha="center", va="center",
        )
        axes[1].axis("off")
        return figure
    palette = plt.get_cmap("tab20").colors
    for number, area in enumerate(prices.columns):
        values = prices[area].dropna().sort_values(ascending=False).to_numpy()
        if len(values):
            share = (np.arange(len(values)) + 0.5) / len(values) * 100.0
            axes[0].plot(
                share, values, linewidth=1.0, label=area,
                color=palette[number % len(palette)],
            )
    # Scarcity hours priced at the unserved-energy cost would flatten the
    # rest of the curves; cap the axis and say how many hours lie above it.
    cap = 1.2 * float(prices.quantile(0.95).max())
    above = int(prices.gt(cap).any(axis=1).sum())
    title = "Price duration curves"
    bottom = min(float(prices.min().min()), 0.0) - 0.05 * cap
    if above:
        axes[0].set_ylim(bottom, cap)
        title += f" (axis capped at {cap:,.0f}; {above} hours above it, mostly scarcity)"
    axes[0].set_title(title)
    axes[0].set_xlabel("Share of hours (%)")
    axes[0].set_ylabel(f"{CURRENCY}/MWh")
    axes[0].legend(ncol=5, fontsize=8, loc="lower left")
    shown, resolution = _for_display(system_price.to_frame("price"))
    shown["price"].plot(ax=axes[1], color="black", linewidth=1.0)
    if above:
        axes[1].set_ylim(bottom, cap)
    axes[1].set_title(f"Load-weighted system price ({resolution})")
    axes[1].set_ylabel(f"{CURRENCY}/MWh")
    axes[1].set_xlabel("Time")
    for axis in axes:
        axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def cost_figure(costs: pd.DataFrame, market: pd.DataFrame, average_price: float):
    """Plot operating costs by carrier and cost type, and market values."""
    figure, axes = plt.subplots(1, 2, figsize=(15, 6))
    title = "Operating cost by carrier"
    if costs.empty:
        axes[0].text(0.5, 0.5, "No operating costs", ha="center", va="center")
    else:
        totals = ["operating_total", "penalty_total", "total"]
        parts = costs.drop(index=totals + list(PENALTY_CARRIERS), errors="ignore").drop(
            columns=[column for column in costs if column.startswith("total_")]
        )
        parts = parts.loc[parts.abs().sum(axis=1).ge(0.01)]
        parts.columns = [
            column.split("_cost")[0].replace("_", " ").capitalize() for column in parts
        ]
        parts.rename(index=CARRIER_LABELS).plot.barh(ax=axes[0], stacked=True)
        axes[0].invert_yaxis()
        axes[0].set_xlabel(f"million {CURRENCY}")
        axes[0].set_ylabel("")
        if "penalty_total" in costs.index:
            penalty = float(costs.loc["penalty_total"].iloc[-1])
            title += f"\n(model penalties not shown: {penalty:,.0f} million {CURRENCY})"
    axes[0].set_title(title)
    if market.empty:
        axes[1].text(
            0.5, 0.5, "No marginal prices: binary unit-commitment solves have no duals",
            ha="center", va="center", wrap=True,
        )
        axes[1].axis("off")
    else:
        values = market[f"market_value_{CURRENCY.lower()}_per_mwh"].dropna()
        axes[1].barh(
            [CARRIER_LABELS.get(carrier, carrier) for carrier in values.index],
            values,
            color=_colors(values.index),
        )
        axes[1].invert_yaxis()
        axes[1].axvline(
            average_price, color="black", linestyle="--", linewidth=0.8,
            label="Load-weighted average price",
        )
        axes[1].set_title("Market value of supplied energy")
        axes[1].set_xlabel(f"{CURRENCY}/MWh")
        axes[1].legend(loc="lower right")
    for axis in axes:
        axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def transmission_figure(flows: pd.DataFrame, corridor_table: pd.DataFrame):
    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    if corridor_table.empty:
        axes[0].text(0.5, 0.5, "No transmission corridors", ha="center", va="center")
        axes[1].axis("off")
        return figure
    top = corridor_table.head(8).index.intersection(flows.columns)
    flows[top].plot(ax=axes[0], linewidth=1.2)
    axes[0].set_title("Flows on the most heavily loaded corridors")
    axes[0].set_ylabel("MW")
    limits = corridor_table.loc[top, "limit_mw"]
    flows[top].abs().div(limits, axis=1).mul(100).plot(ax=axes[1], linewidth=1.2)
    axes[1].axhline(100.0, color="black", linestyle="--", linewidth=0.8)
    axes[1].set_title("Corridor loading relative to the operating limit")
    axes[1].set_ylabel("%")
    axes[1].set_xlabel("Time")
    for axis in axes:
        axis.legend(ncol=4, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.18))
        axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def ramp_figure(ramps: pd.DataFrame):
    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    ramps[["demand_mw", "renewable_generation_mw", "net_load_mw"]].plot(ax=axes[0])
    axes[0].set_title("Demand, renewable generation, and net load")
    axes[0].set_ylabel("MW")
    ramps[["net_load_ramp_mw_per_h", "generation_ramp_mw_per_h"]].plot(ax=axes[1])
    axes[1].axhline(0.0, color="black", linewidth=0.7)
    axes[1].set_title("Hourly net-load requirement and generation response")
    axes[1].set_ylabel("MW/h")
    axes[1].set_xlabel("Time")
    for axis in axes:
        axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def monthly_generation_figure(comparison: pd.DataFrame):
    carriers = comparison.reset_index().carrier.unique()
    carriers = [carrier for carrier in carriers if carrier in CARRIER_LABELS]
    rows = max(1, math.ceil(len(carriers) / 2))
    figure, axes = plt.subplots(rows, 2, figsize=(15, 3.5 * rows), sharex=True, squeeze=False)
    for axis, carrier in zip(axes.flat, carriers):
        data = comparison.xs(carrier, level="carrier")
        data.modeled_gwh.plot(ax=axis, marker="o", label="Modeled")
        if "observed_gwh" in data:
            data.observed_gwh.plot(ax=axis, marker="o", label="Observed")
        axis.set_title(CARRIER_LABELS.get(carrier, carrier))
        axis.set_ylabel("GWh")
        axis.grid(alpha=0.2)
    for axis in axes.flat[len(carriers):]:
        axis.axis("off")
    if carriers:
        axes.flat[0].legend()
    figure.suptitle("Monthly generation comparison")
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    return figure


def seasonality_figure(monthly: pd.DataFrame):
    figure, axes = plt.subplots(1, 2, figsize=(15, 5), sharex=True)
    for axis, carrier in zip(axes, ("wind", "hydro")):
        columns = [column for column in monthly if column.startswith(f"{carrier}_")]
        (monthly[columns] * 100.0).rename(
            columns=lambda value: value.removeprefix(f"{carrier}_").removesuffix("_cf").replace("_", " ").title()
        ).plot(ax=axis, marker="o")
        axis.set_title(f"{carrier.title()} monthly utilization")
        axis.set_ylabel("Capacity factor (%)")
        axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure
