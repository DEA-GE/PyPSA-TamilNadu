"""Plot the model's hourly Tamil Nadu price against the IEX Day-Ahead Market price.

The model price is the load-weighted mean marginal price over the nine Tamil
Nadu areas, capped at the exchange ceiling (10,000 INR/MWh) so that
unserved-energy hours compare with IEX's capped clearing price. The IEX series
is the all-India MCP from ``data/source/iex_dam_fy2025_26``.

    python scripts/plot_price_comparison.py --network <stitched_network.nc> [--output <png>]

The figure has four panels: the year (daily mean with the daily min-max band),
two example weeks hourly, the average day per quarter, and price-duration
curves. A CSV of the aligned hourly series is written next to the figure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from compare_energy_target_runs import AREAS  # noqa: E402

IEX_FILE = ROOT / "data" / "source" / "iex_dam_fy2025_26" / "iex_dam_hourly_fy2025_26.csv"
CAP = 10_000.0
WEEKS = (("2025-08-11", "Monsoon week, 11–17 Aug 2025"), ("2026-02-09", "Winter week, 9–15 Feb 2026"))
QUARTERS = {"Apr–Jun": (4, 5, 6), "Jul–Sep": (7, 8, 9), "Oct–Dec": (10, 11, 12), "Jan–Mar": (1, 2, 3)}

IEX_COLOR, MODEL_COLOR = "#2a78d6", "#eb6834"  # categorical slots 1 and 2
INK, INK_2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"


def model_price(network_path: Path) -> pd.DataFrame:
    """Hourly load-weighted Tamil Nadu marginal price and load."""
    n = pypsa.Network(network_path)
    loads = n.loads.loc[n.loads.bus.isin(AREAS)]
    load = n.loads_t.p_set[loads.index].T.groupby(loads.bus).sum().T
    price = n.buses_t.marginal_price[load.columns]
    return pd.DataFrame({"model": price.mul(load).sum(axis=1) / load.sum(axis=1), "load": load.sum(axis=1)})


def aligned(network_path: Path) -> pd.DataFrame:
    iex = pd.read_csv(IEX_FILE, parse_dates=["timestamp"], index_col="timestamp")["mcp_inr_per_mwh"]
    frame = model_price(network_path).join(iex.rename("iex"), how="inner")
    frame["model_capped"] = frame["model"].clip(upper=CAP)
    return frame


def style(ax, ylabel: str | None = "INR/MWh") -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_ylim(0, CAP * 1.05)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK_2, fontsize=8)


def title(ax, text: str) -> None:
    ax.set_title(text, loc="left", color=INK, fontsize=10, fontweight="semibold", pad=6)


def plot(frame: pd.DataFrame, output: Path, subtitle: str) -> None:
    series = (("iex", "IEX DAM (all-India MCP)", IEX_COLOR), ("model_capped", "Model, Tamil Nadu", MODEL_COLOR))
    fig = plt.figure(figsize=(13, 15), facecolor=SURFACE)
    grid = fig.add_gridspec(4, 4, height_ratios=[1.1, 1, 0.9, 1], hspace=0.45, wspace=0.12)

    # 1. Year: daily mean line + daily min-max band.
    ax = fig.add_subplot(grid[0, :])
    day = frame.groupby(frame.index.normalize())
    for col, label, color in series:
        ax.fill_between(day[col].min().index, day[col].min(), day[col].max(), color=color, alpha=0.12, linewidth=0)
        ax.plot(day[col].mean(), color=color, linewidth=1.6, label=label)
    for start, _ in WEEKS:
        s = pd.Timestamp(start)
        ax.axvspan(s, s + pd.Timedelta(days=7), color=MUTED, alpha=0.12, linewidth=0)
    style(ax)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.set_xlim(frame.index.min(), frame.index.max())
    title(ax, "Daily mean price, with each day's hourly min–max band (shaded weeks are shown below)")
    ax.legend(loc="upper right", frameon=False, fontsize=8, labelcolor=INK_2, ncols=2)

    # 2. Two example weeks, hourly.
    for i, (start, label) in enumerate(WEEKS):
        ax = fig.add_subplot(grid[1, 2 * i: 2 * i + 2])
        s = pd.Timestamp(start)
        week = frame.loc[s: s + pd.Timedelta(days=7) - pd.Timedelta(hours=1)]
        for col, name, color in series:
            ax.plot(week.index, week[col], color=color, linewidth=1.6, label=name)
        style(ax, "INR/MWh" if i == 0 else None)
        ax.xaxis.set_major_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d"))
        ax.set_xlim(week.index.min(), week.index.max())
        title(ax, f"{label}, hourly")

    # 3. Average day per quarter.
    hours = np.arange(24)
    for i, (name, months) in enumerate(QUARTERS.items()):
        ax = fig.add_subplot(grid[2, i])
        q = frame.loc[frame.index.month.isin(months)]
        prof = q.groupby(q.index.hour)[["iex", "model_capped"]].mean()
        for col, _, color in series:
            ax.plot(hours, prof[col], color=color, linewidth=1.6)
        style(ax, "INR/MWh" if i == 0 else None)
        if i:
            ax.tick_params(labelleft=False)
        ax.set_xticks([0, 6, 12, 18, 23], ["0h", "6h", "12h", "18h", "23h"])
        ax.set_xlim(0, 23)
        title(ax, f"Average day, {name}")

    # 4. Price-duration curves + summary.
    ax = fig.add_subplot(grid[3, :3])
    share = np.linspace(0, 100, len(frame))
    for col, label, color in series:
        ax.plot(share, np.sort(frame[col].to_numpy())[::-1], color=color, linewidth=1.8, label=label)
    style(ax)
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xlabel("Share of hours at or above the price", color=INK_2, fontsize=8)
    title(ax, "Price-duration curve (all 8,760 hours)")
    ax.legend(loc="upper right", frameon=False, fontsize=8, labelcolor=INK_2)

    ax = fig.add_subplot(grid[3, 3])
    ax.axis("off")
    w = frame["load"]
    rows = [
        ("", "IEX", "Model"),
        ("Load-weighted mean",f"{(frame.iex * w).sum() / w.sum():,.0f}", f"{(frame.model_capped * w).sum() / w.sum():,.0f}"),
        ("Median", f"{frame.iex.median():,.0f}", f"{frame.model_capped.median():,.0f}"),
        ("Hours at 10,000 cap", f"{(frame.iex >= CAP - 1).sum():,}", f"{(frame.model_capped >= CAP - 1).sum():,}"),
        ("Hours below 2,500", f"{(frame.iex < 2500).sum():,}", f"{(frame.model_capped < 2500).sum():,}"),
        ("Hourly correlation", "", f"{frame.iex.corr(frame.model_capped):.2f}"),
    ]
    for r, (label, a, b) in enumerate(rows):
        y = 0.9 - r * 0.13
        weight = "semibold" if r == 0 else "normal"
        ax.text(0.0, y, label, color=INK_2, fontsize=8.5, fontweight=weight, transform=ax.transAxes)
        ax.text(0.72, y, a, color=INK, fontsize=8.5, fontweight=weight, ha="right", transform=ax.transAxes)
        ax.text(0.98, y, b, color=INK, fontsize=8.5, fontweight=weight, ha="right", transform=ax.transAxes)
    ax.text(0.0, 0.9 - len(rows) * 0.13, "INR/MWh, weighted by TN load;\nmodel capped at the exchange ceiling",
            color=MUTED, fontsize=7.5, transform=ax.transAxes)

    fig.suptitle("Hourly electricity price: model vs IEX Day-Ahead Market, FY2025-26",
                 x=0.125, ha="left", y=0.935, color=INK, fontsize=14, fontweight="semibold")
    fig.text(0.125, 0.915, subtitle, color=INK_2, fontsize=9)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--network", type=Path, required=True, help="solved or stitched FY2025-26 network")
    parser.add_argument("--output", type=Path, help="PNG path (default: price_vs_iex.png next to the network)")
    args = parser.parse_args()
    output = args.output or args.network.parent / "price_vs_iex.png"

    frame = aligned(args.network)
    if len(frame) != 8760:
        print(f"Warning: {len(frame)} overlapping hours (expected 8,760)")
    run = args.network.parent.relative_to(ROOT) if args.network.parent.is_relative_to(ROOT) else args.network.parent
    plot(frame, output, f"Model run: {run.as_posix()}")
    frame[["iex", "model", "model_capped", "load"]].to_csv(output.with_suffix(".csv"), float_format="%.2f")
    print(f"Wrote {output} and {output.with_suffix('.csv').name}")


if __name__ == "__main__":
    main()
