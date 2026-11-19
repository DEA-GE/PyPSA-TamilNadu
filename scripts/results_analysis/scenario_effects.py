"""Quantify what a scenario's added technologies change against the baseline.

Both runs must cover the same hours with the same settings except the
scenario's technologies (as the Snakemake workflow arranges). The general
comparison covers any scenario; technology sections are added when the
scenario contains the technology (offshore wind now; batteries to follow).

General:
- ``system_change``: system metrics, net imports into Tamil Nadu,
  curtailment and CO2 emissions, baseline vs scenario.
- ``generation_change``: generation by carrier and its change.

Offshore wind (generators with carrier ``offshore_wind``):
- ``offshore_summary``: capacity, available and generated energy,
  curtailment, capacity factors, market value and capture ratio.
- ``offshore_displacement``: what each MWh of offshore wind replaced, by
  carrier and in net imports, and the change in onshore curtailment.
- ``offshore_value``: operating-cost savings against the offshore annualised
  cost, net benefit, break-even cost, and avoided CO2.
- ``offshore_corridors``: loading of the corridors at the offshore bus.
- ``offshore_monthly``: offshore generation and the displacement by month.

CO2 uses the indicative factors in ``config/emission_factors.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from .config import CARRIER_COLORS, CARRIER_LABELS, CARRIER_ORDER, CURRENCY
from .loaders import AnalysisRun
from .metrics import (
    _area_timeseries,
    corridor_metrics,
    generation_by_carrier_timeseries,
    market_values,
    price_metrics,
    system_metrics,
)
from .statistics import statistic_timeseries

ROOT = Path(__file__).resolve().parents[2]
EMISSION_FACTORS_FILE = ROOT / "config" / "emission_factors.yaml"
OFFSHORE = "offshore_wind"
VRE = ("solar", "wind", OFFSHORE)
TN_AREA_KIND = "TN balancing area"


@dataclass
class EffectsBundle:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    figures: dict[str, plt.Figure] = field(default_factory=dict)
    headline: dict[str, float] = field(default_factory=dict)


def load_emission_factors(path: Path = EMISSION_FACTORS_FILE) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {
        "carriers": {str(key): float(value) for key, value in (data.get("carriers") or {}).items()},
        "net_imports": float(data.get("net_imports") or 0.0),
    }


def _check_comparable(baseline: AnalysisRun, scenario: AnalysisRun) -> None:
    if not baseline.snapshots.equals(scenario.snapshots):
        raise ValueError("The baseline and scenario runs cover different hours")


def _energy(run: AnalysisRun, hourly: pd.DataFrame | pd.Series) -> pd.Series | float:
    """Weighted energy (MWh) of hourly MW values."""
    return hourly.mul(run.weights, axis=0).sum()


def _tn_net_imports(run: AnalysisRun) -> pd.Series:
    """Hourly net imports into the Tamil Nadu areas (MW), after internal losses."""
    areas = run.network.buses.index[run.network.buses.get("kind", pd.Series(dtype=str)).eq(TN_AREA_KIND)]
    return _area_timeseries(run)["net_import"].reindex(columns=areas, fill_value=0.0).sum(axis=1)


def _curtailment(run: AnalysisRun) -> pd.DataFrame:
    """Hourly curtailment (MW) of variable renewables by carrier."""
    curtailed = statistic_timeseries(
        run, "curtailment", components="Generator", carrier=list(VRE), groupby="carrier"
    )
    if curtailed.empty:
        return pd.DataFrame(0.0, index=run.snapshots, columns=list(VRE))
    curtailed.columns = curtailed.columns.get_level_values("carrier")
    return curtailed.T.groupby(level=0).sum().T.reindex(columns=list(VRE), fill_value=0.0)


def _emissions(generation_mwh: pd.Series, net_imports_mwh: float, factors: dict) -> tuple[float, float]:
    """Return (generation, net-import) CO2 in tonnes."""
    rates = pd.Series(factors["carriers"]).reindex(generation_mwh.index, fill_value=0.0)
    return float((generation_mwh * rates).sum()), float(net_imports_mwh * factors["net_imports"])


@dataclass
class _RunSummary:
    hourly_generation: pd.DataFrame
    generation_mwh: pd.Series
    net_imports: pd.Series
    curtailment: pd.DataFrame
    metrics: pd.DataFrame


def _summarise(run: AnalysisRun) -> _RunSummary:
    hourly = generation_by_carrier_timeseries(run)
    return _RunSummary(
        hourly_generation=hourly,
        generation_mwh=_energy(run, hourly),
        net_imports=_tn_net_imports(run),
        curtailment=_curtailment(run),
        metrics=system_metrics(run),
    )


def _ordered(index: pd.Index) -> list[str]:
    ordered = [carrier for carrier in CARRIER_ORDER if carrier in index]
    return ordered + sorted(set(index) - set(ordered))


def generation_change(base: _RunSummary, scen: _RunSummary) -> pd.DataFrame:
    carriers = _ordered(base.generation_mwh.index.union(scen.generation_mwh.index))
    table = pd.DataFrame(
        {
            "baseline_gwh": base.generation_mwh.reindex(carriers, fill_value=0.0) / 1e3,
            "scenario_gwh": scen.generation_mwh.reindex(carriers, fill_value=0.0) / 1e3,
        }
    )
    table["change_gwh"] = table.scenario_gwh - table.baseline_gwh
    table["change_pct"] = 100.0 * table.change_gwh / table.baseline_gwh.replace(0.0, np.nan)
    table.index.name = "carrier"
    return table


def system_change(
    baseline: AnalysisRun, scenario: AnalysisRun, base: _RunSummary, scen: _RunSummary, factors: dict
) -> pd.DataFrame:
    rows = {}
    for metric in base.metrics.index.intersection(scen.metrics.index):
        rows[metric] = (base.metrics.at[metric, "unit"], base.metrics.at[metric, "value"], scen.metrics.at[metric, "value"])
    rows["tn_net_imports"] = ("GWh", _energy(baseline, base.net_imports) / 1e3, _energy(scenario, scen.net_imports) / 1e3)
    for carrier in VRE:
        rows[f"{carrier}_curtailment"] = (
            "GWh",
            _energy(baseline, base.curtailment[carrier]) / 1e3,
            _energy(scenario, scen.curtailment[carrier]) / 1e3,
        )
    base_co2 = _emissions(base.generation_mwh, _energy(baseline, base.net_imports), factors)
    scen_co2 = _emissions(scen.generation_mwh, _energy(scenario, scen.net_imports), factors)
    rows["co2_generation"] = ("Mt", base_co2[0] / 1e6, scen_co2[0] / 1e6)
    rows["co2_net_imports"] = ("Mt", base_co2[1] / 1e6, scen_co2[1] / 1e6)
    table = pd.DataFrame(rows, index=["unit", "baseline", "scenario"]).T
    table[["baseline", "scenario"]] = table[["baseline", "scenario"]].astype(float)
    table["change"] = table.scenario - table.baseline
    table.index.name = "metric"
    return table


def _offshore_generators(run: AnalysisRun) -> pd.Index:
    generators = run.network.generators
    return generators.index[generators.carrier.astype(str).eq(OFFSHORE)]


def offshore_effects(
    baseline: AnalysisRun,
    scenario: AnalysisRun,
    base: _RunSummary,
    scen: _RunSummary,
    factors: dict,
) -> tuple[dict[str, pd.DataFrame], dict[str, float]]:
    """Tables quantifying what the scenario's offshore wind does."""
    units = _offshore_generators(scenario)
    network = scenario.network
    capacity = network.generators.loc[units, "p_nom"]
    available = network.get_switchable_as_dense("Generator", "p_max_pu")[units].mul(capacity, axis=1)
    available.index = scenario.snapshots
    generated = scenario.frame("generators", "p")[units].fillna(0.0)
    available_mwh = float(_energy(scenario, available).sum())
    generated_mwh = float(_energy(scenario, generated).sum())
    hours = float(scenario.weights.sum())
    market = market_values(scenario)
    value_per_mwh = market.at[OFFSHORE, f"market_value_{CURRENCY.lower()}_per_mwh"] if OFFSHORE in market.index else np.nan
    capture = market.at[OFFSHORE, "capture_ratio"] if OFFSHORE in market.index else np.nan
    summary = pd.DataFrame(
        {
            "value": [
                capacity.sum(), available_mwh / 1e3, generated_mwh / 1e3,
                (available_mwh - generated_mwh) / 1e3,
                100.0 * (available_mwh - generated_mwh) / available_mwh if available_mwh else np.nan,
                available_mwh / (capacity.sum() * hours) if capacity.sum() else np.nan,
                generated_mwh / (capacity.sum() * hours) if capacity.sum() else np.nan,
                value_per_mwh, capture,
            ],
            "unit": ["MW", "GWh", "GWh", "GWh", "%", "-", "-", f"{CURRENCY}/MWh", "-"],
        },
        index=pd.Index(
            [
                "capacity", "available_energy", "generated_energy", "curtailed_energy",
                "curtailment_share", "capacity_factor_available", "capacity_factor_achieved",
                "market_value", "capture_ratio",
            ],
            name="metric",
        ),
    )

    # Displacement: change per MWh of offshore generation.
    change = scen.generation_mwh.sub(base.generation_mwh, fill_value=0.0).drop(OFFSHORE, errors="ignore")
    net_import_change = float(_energy(scenario, scen.net_imports) - _energy(baseline, base.net_imports))
    curtailment_change = (_energy(scenario, scen.curtailment) - _energy(baseline, base.curtailment)).drop(OFFSHORE)
    rows = {carrier: change[carrier] for carrier in _ordered(change.index) if abs(change[carrier]) > 1e-3}
    rows["net_imports_tn"] = net_import_change
    displacement = pd.DataFrame({"change_gwh": pd.Series(rows) / 1e3})
    displacement["per_mwh_offshore"] = displacement.change_gwh * 1e3 / generated_mwh if generated_mwh else np.nan
    for carrier, value in curtailment_change.items():
        displacement.loc[f"{carrier}_curtailment", ["change_gwh", "per_mwh_offshore"]] = (
            value / 1e3, value / generated_mwh if generated_mwh else np.nan
        )
    displacement.index.name = "item"

    # Value: operating-cost savings against the offshore cost.
    def metric(run_summary: _RunSummary, name: str) -> float:
        return float(run_summary.metrics.at[name, "value"]) if name in run_summary.metrics.index else np.nan

    operating_saving = metric(base, "operating_cost") - metric(scen, "operating_cost")
    penalty_saving = metric(base, "penalty_cost") - metric(scen, "penalty_cost")
    annual_cost = float((network.generators.loc[units, "capital_cost"] * capacity).sum()) / 1e6
    total_saving = operating_saving + (0.0 if np.isnan(penalty_saving) else penalty_saving)
    base_co2 = sum(_emissions(base.generation_mwh, _energy(baseline, base.net_imports), factors))
    scen_co2 = sum(_emissions(scen.generation_mwh, _energy(scenario, scen.net_imports), factors))
    avoided_t = base_co2 - scen_co2
    per_mwh = (lambda value: value * 1e6 / generated_mwh) if generated_mwh else (lambda value: np.nan)
    value_rows = [
        ("operating_cost_saving", operating_saving, f"million {CURRENCY}"),
        ("operating_cost_saving_per_mwh", per_mwh(operating_saving), f"{CURRENCY}/MWh"),
        ("penalty_cost_saving", penalty_saving, f"million {CURRENCY}"),
        ("offshore_annualised_cost", annual_cost, f"million {CURRENCY}"),
        ("offshore_cost_per_mwh", per_mwh(annual_cost), f"{CURRENCY}/MWh"),
        ("net_benefit", total_saving - annual_cost, f"million {CURRENCY}"),
        ("break_even_annualised_cost", total_saving * 1e6 / capacity.sum() if capacity.sum() else np.nan, f"{CURRENCY}/MW/year"),
        ("co2_avoided", avoided_t / 1e6, "Mt"),
        ("co2_avoided_per_mwh", avoided_t / generated_mwh if generated_mwh else np.nan, "t/MWh"),
        ("abatement_cost", (annual_cost - total_saving) * 1e6 / avoided_t if avoided_t > 0 else np.nan, f"{CURRENCY}/tCO2"),
    ]
    value = pd.DataFrame(value_rows, columns=["metric", "value", "unit"]).set_index("metric")

    # Congestion at the offshore buses.
    buses = set(network.generators.loc[units, "bus"])
    base_corridors, scen_corridors = corridor_metrics(baseline), corridor_metrics(scenario)
    touching = scen_corridors.index[scen_corridors.bus0.isin(buses) | scen_corridors.bus1.isin(buses)]
    columns = ["maximum_loading_pct", "p95_loading_pct", "hours_at_or_above_99pct", "net_energy_bus0_to_bus1_gwh"]
    corridors = scen_corridors.loc[touching, ["component", "bus0", "bus1", "limit_mw"]].join(
        base_corridors.loc[touching, columns].add_prefix("baseline_")
    ).join(scen_corridors.loc[touching, columns].add_prefix("scenario_"))
    base_prices, scen_prices = price_metrics(baseline), price_metrics(scenario)
    price_column = f"load_weighted_mean_{CURRENCY.lower()}_per_mwh"
    if not base_prices.empty and not scen_prices.empty:
        for bus in sorted(buses):
            value.loc[f"price_at_{bus}_change"] = (
                scen_prices.at[bus, f"time_weighted_mean_{CURRENCY.lower()}_per_mwh"]
                - base_prices.at[bus, f"time_weighted_mean_{CURRENCY.lower()}_per_mwh"],
                f"{CURRENCY}/MWh",
            )
        value.loc["system_price_change"] = (
            float(scen.metrics.at["load_weighted_average_price", "value"])
            - float(base.metrics.at["load_weighted_average_price", "value"]),
            f"{CURRENCY}/MWh",
        )

    # Monthly view.
    month = scenario.snapshots.to_period("M")
    weights = scenario.weights
    delta = scen.hourly_generation.sub(base.hourly_generation, fill_value=0.0).drop(columns=OFFSHORE, errors="ignore")
    monthly = delta.mul(weights, axis=0).groupby(month).sum() / 1e3
    monthly = monthly.loc[:, monthly.abs().max().gt(1e-3)]
    monthly = monthly[_ordered(monthly.columns)].add_suffix("_change_gwh")
    monthly.insert(0, "offshore_generation_gwh", generated.sum(axis=1).mul(weights).groupby(month).sum() / 1e3)
    monthly["net_imports_tn_change_gwh"] = (scen.net_imports - base.net_imports).mul(weights).groupby(month).sum() / 1e3
    monthly.index = monthly.index.astype(str)
    monthly.index.name = "month"

    headline = {
        "offshore_capacity_mw": float(capacity.sum()),
        "offshore_generation_gwh": generated_mwh / 1e3,
        "offshore_curtailment_pct": float(summary.at["curtailment_share", "value"]),
        "operating_cost_saving_million": operating_saving,
        "net_benefit_million": total_saving - annual_cost,
        "co2_avoided_mt": avoided_t / 1e6,
    }
    tables = {
        "offshore_summary": summary,
        "offshore_displacement": displacement,
        "offshore_value": value,
        "offshore_corridors": corridors,
        "offshore_monthly": monthly,
    }
    return tables, headline


def _color(name: str) -> str:
    return CARRIER_COLORS.get(name.replace("_change_gwh", "").replace("_curtailment", ""), "#7F7F7F")


def displacement_figure(displacement: pd.DataFrame):
    figure, axis = plt.subplots(figsize=(10, 5))
    data = displacement.change_gwh.sort_values()
    labels = [CARRIER_LABELS.get(item, item.replace("_", " ")) for item in data.index]
    axis.barh(labels, data.values, color=[_color(item) for item in data.index])
    axis.axvline(0.0, color="black", linewidth=0.8)
    axis.set_xlabel("Change against the baseline, GWh")
    axis.set_title("What offshore wind changes over the year")
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    return figure


def monthly_figure(monthly: pd.DataFrame):
    figure, axis = plt.subplots(figsize=(13, 5))
    changes = monthly.drop(columns=["offshore_generation_gwh"])
    positive, negative = changes.clip(lower=0.0), changes.clip(upper=0.0)
    x = np.arange(len(monthly))
    for part in (positive, negative):
        bottom = np.zeros(len(monthly))
        for column in part:
            name = column.replace("_change_gwh", "")
            axis.bar(x, part[column], bottom=bottom, color=_color(column), width=0.7,
                     label=CARRIER_LABELS.get(name, name.replace("_", " ")))
            bottom += part[column].to_numpy()
    axis.plot(x, monthly.offshore_generation_gwh, color="black", marker="o", label="Offshore generation")
    axis.axhline(0.0, color="black", linewidth=0.8)
    axis.set_xticks(x, monthly.index, rotation=45)
    axis.set_ylabel("GWh")
    axis.set_title("Offshore generation and the change it causes, by month")
    handles, labels = axis.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    axis.legend(unique.values(), unique.keys(), ncol=4, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.2))
    axis.grid(axis="y", alpha=0.2)
    figure.tight_layout()
    return figure


def corridor_figure(baseline: AnalysisRun, scenario: AnalysisRun, corridors: pd.DataFrame):
    figure, axis = plt.subplots(figsize=(10, 5))
    flows = {}
    for label, run in (("baseline", baseline), ("scenario", scenario)):
        frame = statistic_timeseries(run, "transmission", groupby="name")
        frame.columns = frame.columns.get_level_values("name")
        flows[label] = frame
    for number, (name, row) in enumerate(corridors.iterrows()):
        color = plt.get_cmap("tab10")(number % 10)
        for label, style in (("baseline", "--"), ("scenario", "-")):
            loading = (flows[label][name].abs() / row.limit_mw * 100.0).sort_values(ascending=False).to_numpy()
            share = (np.arange(len(loading)) + 0.5) / len(loading) * 100.0
            axis.plot(share, loading, style, color=color, linewidth=1.1, label=f"{name} ({label})")
    axis.axhline(100.0, color="black", linewidth=0.8)
    axis.set_xlabel("Share of hours (%)")
    axis.set_ylabel("Loading of the operating limit (%)")
    axis.set_title("Corridors at the offshore connection: loading duration")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def compare_scenario(
    baseline: AnalysisRun, scenario: AnalysisRun, *, emission_factors: dict | None = None
) -> EffectsBundle:
    """Compare a scenario with the baseline and add its technology sections."""
    _check_comparable(baseline, scenario)
    factors = emission_factors or load_emission_factors()
    base, scen = _summarise(baseline), _summarise(scenario)
    bundle = EffectsBundle()
    bundle.tables["system_change"] = system_change(baseline, scenario, base, scen, factors)
    bundle.tables["generation_change"] = generation_change(base, scen)
    if len(_offshore_generators(scenario)):
        tables, headline = offshore_effects(baseline, scenario, base, scen, factors)
        bundle.tables.update(tables)
        bundle.headline.update(headline)
        bundle.figures["offshore_displacement"] = displacement_figure(tables["offshore_displacement"])
        bundle.figures["offshore_monthly"] = monthly_figure(tables["offshore_monthly"])
        if not tables["offshore_corridors"].empty:
            bundle.figures["offshore_corridors"] = corridor_figure(baseline, scenario, tables["offshore_corridors"])
    return bundle
