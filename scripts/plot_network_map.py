"""Draw the map of the model's balancing areas, corridors and interstate ties.

The nine Tamil Nadu balancing areas come from ``data/source/TamilNadu_9BAs.gpkg``
(digitised from Figure 2 of NREL/TP-6A20-78266); buses, corridors and ties come from the
built network in ``model/``. Line width is the operating limit: for a corridor
(PyPSA ``Line``) ``s_nom * s_max_pu``, for an interstate tie (a pair of one-way
``Link``s) the rating into Tamil Nadu.

    python scripts/plot_network_map.py [--network model] [--output docs/figures/network_map.png]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import geopandas as gpd
import matplotlib.patheffects as patheffects
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
AREAS_FILE = ROOT / "data" / "source" / "TamilNadu_9BAs.gpkg"
DEFAULT_OUTPUT = ROOT / "docs" / "figures" / "network_map.png"

CORRIDOR, TIE = "#2a78d6", "#eb6834"  # categorical slots 1 and 2
INK, INK_2, MUTED, AREA_FILL, AREA_EDGE, SURFACE = (
    "#0b0b0b", "#52514e", "#898781", "#f0efec", "#c3c2b7", "#fcfcfb",
)
HVDC_DASH = (0, (3.5, 1.8))

# Label offsets (points) from each bus, chosen to keep names clear of the lines.
LABEL_OFFSET = {
    "Chennai North": (8, 6), "Chennai South": (9, -10), "Vellore": (-12, 9),
    "Villupuram": (9, -17), "Erode": (-10, 9), "Coimbatore": (-9, -14),
    "Trichy": (9, 2), "Madurai": (9, -6), "Tirunelveli": (9, -8),
    "Andhra Pradesh": (8, 4), "Karnataka": (-8, -10), "Kerala": (-8, -10),
    "Puducherry": (8, -4), "Western Region": (8, 5),
}


def width(mw: float | np.ndarray) -> float | np.ndarray:
    """Line width in points, linear in MW."""
    return 0.6 + np.asarray(mw) / 2_000.0


def ties(network: pypsa.Network, areas: list[str]) -> pd.DataFrame:
    """One row per interstate tie (pair of one-way links), rated into Tamil Nadu."""
    ac = network.buses.index[network.buses.carrier.eq("AC")]
    links = network.links[network.links.bus0.isin(ac) & network.links.bus1.isin(ac)]
    inward = links[links.bus1.isin(areas)]
    return pd.DataFrame({
        "area": inward.bus1, "neighbour": inward.bus0, "carrier": inward.carrier,
        "limit_mw": inward.p_nom * inward.p_max_pu,
    }).groupby(["area", "neighbour", "carrier"], as_index=False).limit_mw.sum()


def label(ax, text: str, xy, offset, *, color: str, size: float, weight: str = "normal") -> None:
    dx, dy = offset
    ax.annotate(
        text, xy, xytext=(dx, dy), textcoords="offset points", color=color, fontsize=size,
        fontweight=weight, ha="left" if dx >= 0 else "right", va="center", zorder=8,
        path_effects=[patheffects.withStroke(linewidth=3, foreground=SURFACE)],
    )


def draw(network_path: Path, output: Path) -> None:
    network = pypsa.Network(network_path)
    buses = network.buses
    areas = list(buses.index[buses.kind.eq("TN balancing area")])
    neighbours = list(buses.index[buses.kind.str.startswith("neighbouring")])
    polygons = gpd.read_file(AREAS_FILE)

    fig, ax = plt.subplots(figsize=(7.2, 8.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    # Areas share one neutral fill; the surface-coloured edges separate them.
    polygons.plot(ax=ax, color=AREA_FILL, edgecolor=SURFACE, linewidth=1.4, zorder=1)
    polygons.dissolve().boundary.plot(ax=ax, color=AREA_EDGE, linewidth=0.7, zorder=1)

    tie_table = ties(network, areas)
    for row in tie_table.itertuples():
        (x0, y0), (x1, y1) = buses.loc[row.area, ["x", "y"]], buses.loc[row.neighbour, ["x", "y"]]
        ax.plot([x0, x1], [y0, y1], color=TIE, lw=width(row.limit_mw), solid_capstyle="round",
                linestyle=HVDC_DASH if row.carrier == "DC" else "-", zorder=2)

    lines = network.lines.assign(limit_mw=lambda d: d.s_nom * d.s_max_pu)
    for row in lines.itertuples():
        (x0, y0), (x1, y1) = buses.loc[row.bus0, ["x", "y"]], buses.loc[row.bus1, ["x", "y"]]
        ax.plot([x0, x1], [y0, y1], color=CORRIDOR, lw=width(row.limit_mw), solid_capstyle="round", zorder=3)

    ax.scatter(buses.loc[areas, "x"], buses.loc[areas, "y"], s=46, color=INK, edgecolor=SURFACE,
               linewidth=1.6, zorder=5)
    ax.scatter(buses.loc[neighbours, "x"], buses.loc[neighbours, "y"], s=58, marker="s", color=MUTED,
               edgecolor=SURFACE, linewidth=1.6, zorder=5)
    for bus in areas:
        label(ax, bus, buses.loc[bus, ["x", "y"]], LABEL_OFFSET.get(bus, (8, 0)), color=INK, size=9.5,
              weight="bold")
    for bus in neighbours:
        label(ax, bus, buses.loc[bus, ["x", "y"]], LABEL_OFFSET.get(bus, (8, 0)), color=INK_2, size=9)

    kinds = [
        Line2D([], [], color=CORRIDOR, lw=3, label="Corridor between areas (AC Line)"),
        Line2D([], [], color=TIE, lw=3, label="Interstate tie, AC (Link pair)"),
        Line2D([], [], color=TIE, lw=3, linestyle=HVDC_DASH, label="Interstate tie, HVDC (Link pair)"),
        Line2D([], [], marker="o", color=INK, lw=0, markersize=6, label="Tamil Nadu balancing area"),
        Line2D([], [], marker="s", color=MUTED, lw=0, markersize=6, label="Neighbouring state or region"),
    ]
    sizes = [Line2D([], [], color=INK_2, lw=width(mw), solid_capstyle="butt", label=f"{mw / 1000:g} GW")
             for mw in (1_000, 5_000, 10_000)]
    first = ax.legend(handles=kinds, loc="lower right", bbox_to_anchor=(1.0, 0.0), frameon=False, fontsize=8,
                      labelcolor=INK_2, handlelength=2.6, borderaxespad=0.2)
    ax.add_artist(first)
    ax.legend(handles=sizes, title="Operating limit", title_fontsize=8, loc="lower right",
              bbox_to_anchor=(1.0, 0.19), frameon=False, fontsize=8, labelcolor=INK_2, handlelength=2.6,
              borderaxespad=0.2, alignment="left")

    ax.set_xlim(75.7, 81.6)
    ax.set_ylim(7.85, 14.45)
    ax.set_aspect(1.0 / np.cos(np.deg2rad(11.0)))
    ax.set_axis_off()
    ax.text(0.0, 1.045, "Balancing areas, corridors and interstate ties", transform=ax.transAxes,
            fontsize=11.5, color=INK, fontweight="bold", va="bottom")
    ax.text(0.0, 1.015, "Line width is the operating limit. Area shapes digitised from NREL/TP-6A20-78266.",
            transform=ax.transAxes, fontsize=8.5, color=INK_2, va="bottom")
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", pad_inches=0.15, facecolor=SURFACE)
    plt.close(fig)
    print(f"Wrote {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--network", type=Path, default=ROOT / "model")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    draw(args.network, args.output)


if __name__ == "__main__":
    main()
