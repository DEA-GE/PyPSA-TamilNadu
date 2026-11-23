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


def order_by_variability(frame: pd.DataFrame) -> pd.DataFrame:
    """Order columns from steadiest to most variable, for stacking bottom-up.

    Variability is the standard deviation of each column as shown, so small
    technologies and those that run flat sit at the bottom of a stack and the
    ones that change over time sit on top, where their changes stay visible
    instead of shifting every layer above them. Ties keep the input order.
    """
    spread = frame.std(ddof=0).fillna(0.0)
    return frame[spread.sort_values(kind="stable").index]


def _stack_legend(axis, secondary=None, ncol: int = 4) -> None:
    """Legend listed top-down in the same order as the stacked layers.

    Lines on a ``secondary`` axis are listed after the stack.
    """
    handles, labels = axis.get_legend_handles_labels()
    handles, labels = handles[::-1], labels[::-1]
    if secondary is not None:
        extra_handles, extra_labels = secondary.get_legend_handles_labels()
        handles += extra_handles
        labels += extra_labels
    axis.legend(
        handles, labels, ncol=ncol, loc="upper center", bbox_to_anchor=(0.5, -0.16),
    )


def _unserved_line(axis, index, values: pd.Series | None, x_offset=pd.Timedelta(0)):
    """Draw unserved energy as a dotted line on a secondary (right) axis, in MW."""
    secondary = axis.twinx()
    values = (
        pd.Series(0.0, index=index) if values is None else values.reindex(index, fill_value=0.0)
    )
    secondary.plot(
        index + x_offset, values.to_numpy(), color=CARRIER_COLORS["unserved_energy"],
        linestyle=":", linewidth=1.6, label=f"{CARRIER_LABELS['unserved_energy']} (right axis)",
    )
    secondary.set_ylim(0.0, max(1.1 * float(values.max()), 1.0))
    secondary.set_ylabel(f"{CARRIER_LABELS['unserved_energy']} (MW)")
    return secondary


def energy_balance_figure(balance: pd.DataFrame):
    """Stack electricity supply and draw demand as a line on top.

    The gap between the top of the stack and the demand line is storage
    charging, pumping and transmission losses, plus any unserved energy,
    which is drawn as a dotted line on the right axis instead of stacked.
    Layers are ordered by ``order_by_variability``.
    """
    figure, axis = plt.subplots(figsize=(14, 6))
    data, resolution = _for_display(balance)
    supply = data.loc[:, data.ge(0.0).all()].drop(columns="unserved_energy", errors="ignore")
    supply = order_by_variability(supply)
    if not supply.empty:
        axis.stackplot(
            supply.index,
            supply.T.values,
            labels=[CARRIER_LABELS.get(column, column) for column in supply],
            colors=_colors(supply.columns),
            linewidth=0,
            alpha=0.9,
        )
    if "demand" in data:
        # Matplotlib, not pandas: pandas' time-series axis would shift the stack.
        axis.plot(
            data.index, -data["demand"], color="black", linewidth=1.2,
            label=CARRIER_LABELS["demand"],
        )
    axis.set_ylim(bottom=0.0)
    axis.set_title(f"System electricity supply and demand ({resolution})")
    axis.set_ylabel("MW")
    axis.set_xlabel("Time")
    secondary = _unserved_line(axis, data.index, data.get("unserved_energy"))
    _stack_legend(axis, secondary)
    axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def daily_supply_shares(balance: pd.DataFrame) -> pd.DataFrame:
    """Each day's served supply by carrier as a percentage of that day's total.

    Supply is every non-negative column of ``energy_balance_timeseries``
    except unserved energy: generation, storage discharge and net imports.
    """
    supply = balance.loc[:, balance.ge(0.0).all()].drop(
        columns="unserved_energy", errors="ignore"
    )
    if supply.empty:
        return supply
    daily = supply.resample("D").sum()
    total = daily.sum(axis=1)
    daily = daily.loc[total.gt(0.0)]
    return daily.div(total[daily.index], axis=0) * 100.0


def daily_generation_share_figure(balance: pd.DataFrame):
    """Daily stacked bars of the served supply mix, each day summing to 100 %.

    Unserved energy is not part of the mix; its daily mean (MW) is a dotted
    line on the right axis.
    """
    figure, axis = plt.subplots(figsize=(14, 6))
    shares = order_by_variability(daily_supply_shares(balance))
    if shares.empty:
        axis.text(0.5, 0.5, "No supply", ha="center", va="center")
        return figure
    bottom = np.zeros(len(shares))
    for column in shares:
        values = shares[column].to_numpy()
        axis.bar(
            shares.index, values, width=1.0, bottom=bottom, align="edge",
            color=CARRIER_COLORS.get(str(column), "#7F7F7F"),
            label=CARRIER_LABELS.get(column, column), linewidth=0,
        )
        bottom += values
    axis.set_xlim(shares.index[0], shares.index[-1] + pd.Timedelta(days=1))
    axis.set_ylim(0.0, 100.0)
    axis.set_title("Daily supply mix (share of each day's served supply)")
    axis.set_ylabel("%")
    axis.set_xlabel("Day")
    unserved = (
        balance["unserved_energy"].resample("D").mean()
        if "unserved_energy" in balance else None
    )
    # Bars span each day from midnight; put the line's points at midday.
    secondary = _unserved_line(axis, shares.index, unserved, pd.Timedelta(hours=12))
    _stack_legend(axis, secondary)
    axis.grid(axis="y", alpha=0.2)
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
