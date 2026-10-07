"""Assemble standardized tables and figures for any supported result horizon."""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib.pyplot as plt
import pandas as pd

from .loaders import AnalysisRun
from .metrics import (
    balancing_area_metrics,
    commitment_by_carrier,
    commitment_metrics,
    corridor_metrics,
    generation_by_carrier,
    generation_by_carrier_timeseries,
    generation_comparison,
    installed_capacity_by_area,
    monthly_generation_comparison,
    monthly_resource_seasonality,
    peak_hour_by_area,
    ramp_timeseries,
    renewable_generation_by_area,
    seasonality_fit,
    seasonality_summary,
    storage_metrics,
    store_metrics,
    system_metrics,
)
from .plots import (
    generation_figure,
    generation_comparison_figure,
    monthly_generation_figure,
    ramp_figure,
    storage_figure,
    seasonality_figure,
    transmission_figure,
)
from .validation import boundary_handoff_metrics, chronology_validation, topology_validation


@dataclass
class AnalysisBundle:
    tables: dict[str, pd.DataFrame]
    figures: dict[str, plt.Figure]


def analyze_run(
    run: AnalysisRun,
    observed_daily: pd.DataFrame | None = None,
) -> AnalysisBundle:
    """Calculate the common schema plus applicable horizon-specific outputs."""
    generation_hourly = generation_by_carrier_timeseries(run)
    load = run.frame("loads", "p")
    if load.empty:
        load = run.frame("loads", "p_set")
    demand = load.sum(axis=1)
    storage_dispatch = run.frame("storage_units", "p")
    storage_soc = run.frame("storage_units", "state_of_charge")
    store_dispatch = run.frame("stores", "p")
    store_energy = run.frame("stores", "e")
    link_flows = run.frame("links", "p0")
    ramps = ramp_timeseries(run)
    corridors = corridor_metrics(run)
    unit_commitment = commitment_metrics(run)

    tables = {
        "chronology_validation": chronology_validation(run),
        "topology_validation": topology_validation(run),
        "system_metrics": system_metrics(run),
        "generation_by_carrier": generation_by_carrier(run),
        "generation_comparison": generation_comparison(run, observed_daily),
        "storage_metrics": storage_metrics(run),
        "store_metrics": store_metrics(run),
        "commitment_by_unit": unit_commitment,
        "commitment_by_carrier": commitment_by_carrier(unit_commitment),
        "balancing_area_metrics": balancing_area_metrics(run),
        "installed_capacity_by_area": installed_capacity_by_area(run),
        "renewable_generation_by_area": renewable_generation_by_area(run),
        "corridor_metrics": corridors,
        "ramp_timeseries": ramps,
    }

    if run.run_type in {"peak", "peak_week", "selected_week", "single_network"}:
        tables["peak_hour_by_area"] = peak_hour_by_area(run)
    if run.run_type == "rolling" or len(run.networks) > 1:
        tables["boundary_handoffs"] = boundary_handoff_metrics(run)
    if len(run.snapshots) >= 28 * 24:
        tables["monthly_generation"] = monthly_generation_comparison(run, observed_daily)
        monthly_seasonality = monthly_resource_seasonality(run, observed_daily)
        tables["monthly_resource_seasonality"] = monthly_seasonality
        tables["seasonality_summary"] = seasonality_summary(monthly_seasonality)
        tables["seasonality_fit"] = seasonality_fit(monthly_seasonality)

    figures = {
        "generation": generation_figure(generation_hourly, demand),
        "generation_comparison": generation_comparison_figure(
            tables["generation_comparison"]
        ),
        "storage": storage_figure(
            pd.concat([storage_dispatch, store_dispatch], axis=1),
            pd.concat([storage_soc, store_energy], axis=1),
        ),
        "transmission": transmission_figure(link_flows, corridors),
        "ramps": ramp_figure(ramps),
    }
    if "monthly_generation" in tables:
        figures["monthly_generation"] = monthly_generation_figure(tables["monthly_generation"])
        figures["resource_seasonality"] = seasonality_figure(
            tables["monthly_resource_seasonality"]
        )
    return AnalysisBundle(tables=tables, figures=figures)
