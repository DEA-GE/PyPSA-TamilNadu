"""Wind and solar curtailment by month and hour of day.

Generation is PyPSA's ``supply`` statistic and curtailment its
``curtailment`` statistic (``p_max_pu * p_nom - p``) for the variable
renewable generators; available energy is their sum.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import CARRIER_COLORS, CARRIER_LABELS, RENEWABLE_CARRIERS, canonical_carrier
from .loaders import AnalysisRun
from .statistics import statistic_timeseries


def _by_carrier(run: AnalysisRun, name: str) -> pd.DataFrame:
    values = statistic_timeseries(run, name, components="Generator", groupby="carrier")
    if values.empty:
        return pd.DataFrame(index=run.snapshots)
    carriers = values.columns.get_level_values("carrier").map(canonical_carrier)
    values = values.T.groupby(carriers).sum().T
    return values.reindex(columns=[c for c in RENEWABLE_CARRIERS if c in values.columns])


def vre_hourly(run: AnalysisRun) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly generation and curtailment (MW) of each variable renewable carrier."""
    generation = _by_carrier(run, "supply")
    curtailment = _by_carrier(run, "curtailment").clip(lower=0.0)
    carriers = [c for c in RENEWABLE_CARRIERS if c in generation.columns or c in curtailment.columns]
    return (
        generation.reindex(columns=carriers, fill_value=0.0),
        curtailment.reindex(columns=carriers, fill_value=0.0),
    )


def curtailment_monthly(run: AnalysisRun) -> pd.DataFrame:
    """Return available, generated and curtailed energy by month and carrier.

    The ``all`` carrier sums the variable renewables; the ``total`` month
    covers the whole run.
    """
    generation, curtailment = vre_hourly(run)
    if generation.empty and curtailment.empty:
        return pd.DataFrame()
    generation["all"] = generation.sum(axis=1)
    curtailment["all"] = curtailment.sum(axis=1)
    weights = run.weights
    months = np.asarray(run.snapshots.to_period("M").astype(str))
    frames = {}
    for label, selected in [(month, months == month) for month in pd.unique(months)] + [("total", slice(None))]:
        w = weights[selected]
        generated = generation[selected].mul(w, axis=0).sum() / 1e3
        curtailed = curtailment[selected].mul(w, axis=0).sum() / 1e3
        frame = pd.DataFrame({"generated_gwh": generated, "curtailed_gwh": curtailed})
        frame["available_gwh"] = frame.generated_gwh + frame.curtailed_gwh
        frame["curtailed_pct"] = 100.0 * frame.curtailed_gwh / frame.available_gwh.replace(0.0, np.nan)
        frames[label] = frame[["available_gwh", "generated_gwh", "curtailed_gwh", "curtailed_pct"]]
    return pd.concat(frames, names=["month", "carrier"])


def curtailment_figure(run: AnalysisRun, monthly: pd.DataFrame):
    """Monthly curtailed energy by carrier, and the curtailed share by month and hour of day."""
    generation, curtailment = vre_hourly(run)
    data = monthly.drop(index="total", level="month", errors="ignore")
    months = list(dict.fromkeys(data.index.get_level_values("month")))
    carriers = [c for c in generation.columns]
    figure, axes = plt.subplots(1, 2, figsize=(15, 5.2), gridspec_kw={"width_ratios": [1.1, 1]})

    positions = np.arange(len(months))
    bottom = np.zeros(len(months))
    for carrier in carriers:
        values = data.xs(carrier, level="carrier").reindex(months).curtailed_gwh.fillna(0.0).to_numpy()
        axes[0].bar(positions, values, bottom=bottom, width=0.75,
                    color=CARRIER_COLORS.get(carrier, "#7F7F7F"), label=CARRIER_LABELS.get(carrier, carrier))
        bottom += values
    axes[0].set_xticks(positions, months, rotation=45, ha="right")
    axes[0].set_ylabel("Curtailed GWh")
    share = axes[0].twinx()
    share.plot(positions, data.xs("all", level="carrier").reindex(months).curtailed_pct.to_numpy(),
               color="black", marker="o", linewidth=1.2, label="Share of available energy (right axis)")
    share.set_ylabel("% of available wind and solar energy")
    share.set_ylim(bottom=0.0)
    handles, labels = axes[0].get_legend_handles_labels()
    extra_handles, extra_labels = share.get_legend_handles_labels()
    axes[0].legend(handles + extra_handles, labels + extra_labels, fontsize=8, loc="upper left")
    total = monthly.loc[("total", "all")]
    axes[0].set_title(
        f"Wind and solar curtailment: {total.curtailed_gwh:,.0f} GWh, "
        f"{total.curtailed_pct:.1f}% of available energy"
    )
    axes[0].grid(axis="y", alpha=0.2)

    available = generation.sum(axis=1) + curtailment.sum(axis=1)
    frame = pd.DataFrame(
        {
            "curtailed": curtailment.sum(axis=1).mul(run.weights),
            "available": available.mul(run.weights),
            "month": run.snapshots.to_period("M").astype(str),
            "hour": run.snapshots.hour,
        }
    )
    grid = frame.groupby(["hour", "month"])[["curtailed", "available"]].sum()
    grid = (100.0 * grid.curtailed / grid.available.replace(0.0, np.nan)).unstack("month")
    grid = grid.reindex(index=range(24), columns=months)
    image = axes[1].pcolormesh(np.arange(len(months) + 1), np.arange(25), grid.to_numpy(),
                               cmap="viridis", vmin=0.0, shading="flat")
    axes[1].set_xticks(np.arange(len(months)) + 0.5, months, rotation=45, ha="right")
    axes[1].set_yticks([0, 6, 12, 18, 24])
    axes[1].set_ylabel("Hour of day")
    axes[1].set_title("Curtailed share of available wind and solar energy")
    colorbar = figure.colorbar(image, ax=axes[1], pad=0.01)
    colorbar.set_label("%")
    figure.tight_layout()
    return figure
