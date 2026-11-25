"""Assemble standardized tables and figures for any supported result horizon."""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib.pyplot as plt
import pandas as pd

from .coal import (
    coal_fleet,
    coal_monthly,
    coal_plant_operation,
    dispatch_heatmap_figure,
    fleet_figure,
    loading_and_ramps_figure,
    plant_use_figure,
)
from .grid import corridor_loading, utilisation_histogram_figure
from .loaders import AnalysisRun
from .metrics import (
    average_price,
    balancing_area_metrics,
    commitment_by_carrier,
    commitment_metrics,
    corridor_flows,
    corridor_metrics,
    energy_balance_timeseries,
    generation_by_carrier,
    generation_comparison,
    installed_capacity_by_area,
    installed_capacity_comparison,
    market_values,
    monthly_generation_comparison,
    operating_costs,
    peak_hour_by_area,
    price_metrics,
    ramp_timeseries,
    renewable_generation_by_area,
    system_metrics,
    system_price_timeseries,
)
from .plots import (
    cost_figure,
    daily_generation_share_figure,
    energy_balance_figure,
    monthly_generation_figure,
    price_figure,
)
from .statistics import price_timeseries
from .storage import storage_annual, storage_monthly
from .stress import stress_analysis
from .validation import boundary_handoff_metrics, chronology_validation, topology_validation

# Runs at least this long get monthly tables, the coal heatmap and the
# daily supply mix.
MONTHLY_MINIMUM_HOURS = 28 * 24


@dataclass
class AnalysisBundle:
    tables: dict[str, pd.DataFrame]
    figures: dict[str, plt.Figure]


def analyze_run(
    run: AnalysisRun,
    observed_daily: pd.DataFrame | None = None,
    observed_monthly: pd.DataFrame | None = None,
    observed_annual: pd.DataFrame | None = None,
    observed_capacity: pd.DataFrame | None = None,
) -> AnalysisBundle:
    """Calculate the common schema plus applicable horizon-specific outputs."""
    balance = energy_balance_timeseries(run)
    flows = corridor_flows(run)
    loading = corridor_loading(run, flows)
    corridors = corridor_metrics(run, flows)
    ramps = ramp_timeseries(run)
    unit_commitment = commitment_metrics(run)
    prices = price_timeseries(run)
    system_price = system_price_timeseries(run, prices)
    costs = operating_costs(run)
    market = market_values(run)
    fleet = coal_fleet(run)
    long_run = len(run.snapshots) >= MONTHLY_MINIMUM_HOURS

    tables = {
        "chronology_validation": chronology_validation(run),
        "topology_validation": topology_validation(run),
        "system_metrics": system_metrics(run),
        "generation_by_carrier": generation_by_carrier(run),
        "generation_comparison": generation_comparison(
            run, observed_daily, observed_annual
        ),
        "storage_annual": storage_annual(run),
        "commitment_by_unit": unit_commitment,
        "commitment_by_carrier": commitment_by_carrier(unit_commitment),
        "balancing_area_metrics": balancing_area_metrics(run),
        "peak_hour_by_area": peak_hour_by_area(run),
        "installed_capacity_by_area": installed_capacity_by_area(run),
        "installed_capacity_comparison": installed_capacity_comparison(
            run, observed_capacity
        ),
        "renewable_generation_by_area": renewable_generation_by_area(run),
        "corridor_metrics": corridors,
        "ramp_timeseries": ramps,
        "operating_costs": costs,
        "price_metrics": price_metrics(run),
        "market_values": market,
    }
    if len(run.windows) > 1:
        tables["boundary_handoffs"] = boundary_handoff_metrics(run)
    if long_run:
        tables["monthly_generation"] = monthly_generation_comparison(
            run, observed_daily, observed_monthly
        )
        tables["storage_monthly"], tables["storage_monthly_by_class"] = storage_monthly(run)
    if fleet is not None:
        tables["coal_plant_operation"] = coal_plant_operation(fleet, run)
        if long_run:
            tables["coal_monthly"] = coal_monthly(fleet)

    figures = {"energy_balance": energy_balance_figure(balance)}
    if len(run.snapshots) >= 2 * 24:
        figures["daily_generation_share"] = daily_generation_share_figure(balance)
    if "monthly_generation" in tables:
        figures["monthly_generation"] = monthly_generation_figure(tables["monthly_generation"])
    if fleet is not None:
        figures["coal_fleet"] = fleet_figure(fleet)
        if long_run:
            figures["coal_dispatch_heatmap"] = dispatch_heatmap_figure(fleet)
        figures["coal_plant_use"] = plant_use_figure(tables["coal_plant_operation"])
        figures["coal_loading_and_ramps"] = loading_and_ramps_figure(
            fleet, ramps.net_load_ramp_mw_per_h
        )
    for component, name, title in (
        ("Line", "grid_utilisation_lines", "Loading of the AC lines between balancing areas"),
        ("Link", "grid_utilisation_interconnectors", "Loading of the interconnectors (AC ties and HVDC)"),
    ):
        selected = corridors.loc[corridors.component.eq(component)] if not corridors.empty else corridors
        if not selected.empty:
            figures[name] = utilisation_histogram_figure(
                flows, loading, selected, run.weights, title
            )
    stress_tables, stress_figures = stress_analysis(run, balance, ramps, system_price, fleet)
    tables |= stress_tables
    figures |= stress_figures
    figures |= {
        "prices": price_figure(prices, system_price),
        "costs": cost_figure(costs, market, average_price(run, prices)),
    }
    return AnalysisBundle(tables=tables, figures=figures)
