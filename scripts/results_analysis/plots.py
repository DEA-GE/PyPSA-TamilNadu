"""Standard figures shared by all result-analysis notebooks."""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
import pandas as pd

from .config import CARRIER_COLORS, CARRIER_LABELS


def _colors(columns) -> list[str]:
    return [CARRIER_COLORS.get(str(column), "#7F7F7F") for column in columns]


def generation_figure(hourly: pd.DataFrame, demand: pd.Series):
    figure, axis = plt.subplots(figsize=(14, 6))
    plotted = hourly.drop(columns=["unserved_energy"], errors="ignore")
    plotted.rename(columns=CARRIER_LABELS).plot.area(
        ax=axis, stacked=True, linewidth=0, color=_colors(plotted.columns), alpha=0.9
    )
    demand.plot(ax=axis, color="black", linewidth=1.4, label="Demand")
    axis.set_title("System generation and demand")
    axis.set_ylabel("MW")
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


def storage_figure(dispatch: pd.DataFrame, state_of_charge: pd.DataFrame):
    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    dispatch.sum(axis=1).plot(ax=axes[0], color=CARRIER_COLORS["hydro"])
    axes[0].axhline(0.0, color="black", linewidth=0.7)
    axes[0].set_title("Net storage dispatch")
    axes[0].set_ylabel("MW")
    state_of_charge.plot(ax=axes[1], linewidth=1.0)
    axes[1].set_title("Storage state of charge")
    axes[1].set_ylabel("MWh")
    axes[1].set_xlabel("Time")
    axes[1].legend(ncol=4, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.18))
    for axis in axes:
        axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def transmission_figure(flows: pd.DataFrame, corridor_table: pd.DataFrame):
    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    if corridor_table.empty:
        axes[0].text(0.5, 0.5, "No AC corridors", ha="center", va="center")
        axes[1].axis("off")
        return figure
    top = corridor_table.head(8).index.intersection(flows.columns)
    flows[top].plot(ax=axes[0], linewidth=1.2)
    axes[0].set_title("Flows on the most heavily loaded corridors")
    axes[0].set_ylabel("MW")
    capacities = corridor_table.loc[top, "capacity_mw"]
    flows[top].abs().div(capacities, axis=1).mul(100).plot(ax=axes[1], linewidth=1.2)
    axes[1].axhline(100.0, color="black", linestyle="--", linewidth=0.8)
    axes[1].set_title("Absolute corridor loading")
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
