"""CO2 emissions by month, from indicative emission factors.

The model's carriers carry no emissions. ``config/emission_factors.yaml``
gives tCO2 per MWh generated for the fossil carriers and for net imports
into Tamil Nadu. Coal is counted on a gross basis (net dispatch plus the
auxiliary consumption of the units), matching the heat rate behind its
factor.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from .config import CARRIER_COLORS, CARRIER_LABELS
from .loaders import AnalysisRun
from .metrics import _area_timeseries, coal_own_use_timeseries, generation_by_carrier_timeseries

ROOT = Path(__file__).resolve().parents[2]
EMISSION_FACTORS_FILE = ROOT / "config" / "emission_factors.yaml"
TN_AREA_KIND = "TN balancing area"
IMPORTS_COLOR = CARRIER_COLORS["imports"]


def load_emission_factors(path: Path = EMISSION_FACTORS_FILE) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {
        "carriers": {str(key): float(value) for key, value in (data.get("carriers") or {}).items()},
        "net_imports": float(data.get("net_imports") or 0.0),
    }


def tn_areas(run: AnalysisRun) -> pd.Index:
    """The Tamil Nadu balancing areas (buses marked ``kind == "TN balancing area"``)."""
    buses = run.network.buses
    return buses.index[buses.get("kind", pd.Series("", index=buses.index)).eq(TN_AREA_KIND)]


def tn_net_imports(run: AnalysisRun) -> pd.Series:
    """Hourly net imports into the Tamil Nadu areas (MW), after internal losses."""
    return _area_timeseries(run)["net_import"].reindex(
        columns=tn_areas(run), fill_value=0.0
    ).sum(axis=1)


def co2_monthly(run: AnalysisRun, factors: dict | None = None) -> pd.DataFrame:
    """Return CO2 (kt) by month and source, with generation and intensities.

    Columns ``<carrier>_kt`` cover generation in the model's areas and
    ``net_imports_kt`` the net imports into Tamil Nadu (none when it exports).
    The generation intensity divides generation CO2 by all generation; the
    Tamil Nadu intensity divides generation plus import CO2 by Tamil Nadu's
    demand.
    """
    factors = factors or load_emission_factors()
    weights = run.weights
    months = pd.Index(run.snapshots.to_period("M").astype(str), name="month")
    generation = generation_by_carrier_timeseries(run)
    if "coal" in generation:
        generation["coal"] = generation["coal"] + coal_own_use_timeseries(run).reindex(
            generation.index
        ).fillna(0.0)
    energy = generation.drop(columns="unserved_energy", errors="ignore").mul(weights, axis=0)
    rates = pd.Series(factors["carriers"])
    emitting = [carrier for carrier in rates.index if carrier in energy]
    result = pd.DataFrame(index=months.unique())
    for carrier in emitting:
        result[f"{carrier}_kt"] = (energy[carrier] * rates[carrier]).groupby(np.asarray(months)).sum() / 1e3
    imports = tn_net_imports(run).clip(lower=0.0).mul(weights)
    result["net_imports_kt"] = (imports * factors["net_imports"]).groupby(np.asarray(months)).sum() / 1e3
    sources = [column for column in result if column.endswith("_kt")]
    result["total_kt"] = result[sources].sum(axis=1)
    generation_mwh = energy.sum(axis=1).groupby(np.asarray(months)).sum()
    demand = _area_timeseries(run)["demand"].reindex(columns=tn_areas(run), fill_value=0.0).sum(axis=1)
    demand_mwh = demand.mul(weights).groupby(np.asarray(months)).sum()
    result["generation_gwh"] = generation_mwh / 1e3
    result["tn_demand_gwh"] = demand_mwh / 1e3
    result["generation_intensity_t_per_mwh"] = (
        result[[f"{carrier}_kt" for carrier in emitting]].sum(axis=1) * 1e3
        / generation_mwh.replace(0.0, np.nan)
    )
    result["tn_demand_intensity_t_per_mwh"] = result.total_kt * 1e3 / demand_mwh.replace(0.0, np.nan)
    result.loc["total"] = result.sum()
    total = result.loc["total"]
    result.loc["total", "generation_intensity_t_per_mwh"] = (
        total[[f"{carrier}_kt" for carrier in emitting]].sum() / total.generation_gwh
        if total.generation_gwh else np.nan
    )
    result.loc["total", "tn_demand_intensity_t_per_mwh"] = (
        total.total_kt / total.tn_demand_gwh if total.tn_demand_gwh else np.nan
    )
    return result


def co2_figure(monthly: pd.DataFrame):
    """Monthly CO2 by source, with Tamil Nadu's demand intensity on the right."""
    data = monthly.drop(index="total", errors="ignore")
    sources = [column for column in data if column.endswith("_kt") and column != "total_kt"]
    figure, axis = plt.subplots(figsize=(14, 5.5))
    positions = np.arange(len(data))
    bottom = np.zeros(len(data))
    for column in sources:
        carrier = column.removesuffix("_kt")
        values = data[column].to_numpy() / 1e3
        color = IMPORTS_COLOR if carrier == "net_imports" else CARRIER_COLORS.get(carrier, "#7F7F7F")
        label = "Net imports into Tamil Nadu" if carrier == "net_imports" else CARRIER_LABELS.get(carrier, carrier)
        axis.bar(positions, values, bottom=bottom, color=color, label=label, width=0.75)
        bottom += values
    axis.set_xticks(positions, data.index, rotation=0)
    axis.set_ylabel("Mt CO2")
    secondary = axis.twinx()
    secondary.plot(positions, data.tn_demand_intensity_t_per_mwh, color="black", marker="o",
                   linewidth=1.2, label="Tamil Nadu demand intensity (right axis)")
    secondary.plot(positions, data.generation_intensity_t_per_mwh, color="black", marker="s",
                   linestyle="--", linewidth=1.0, label="Generation intensity (right axis)")
    secondary.set_ylabel("t CO2 per MWh")
    secondary.set_ylim(bottom=0.0)
    total = monthly.loc["total"] if "total" in monthly.index else data.sum()
    axis.set_title(
        f"CO2 by month: {total.total_kt / 1e3:,.1f} Mt over the run "
        f"({total.tn_demand_intensity_t_per_mwh:.2f} t/MWh of Tamil Nadu demand)"
    )
    handles, labels = axis.get_legend_handles_labels()
    extra_handles, extra_labels = secondary.get_legend_handles_labels()
    axis.legend(handles[::-1] + extra_handles, labels[::-1] + extra_labels, fontsize=8,
                loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=3)
    axis.grid(axis="y", alpha=0.2)
    figure.tight_layout()
    return figure
