"""Quantify what a scenario's added technologies change against the baseline.

Both runs must cover the same hours with the same settings except the
scenario's technologies (as the Snakemake workflow arranges). The general
comparison covers any scenario; technology sections are added when the
scenario contains the technology (offshore wind and batteries).

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

Batteries (storage units with carrier ``battery``):
- ``battery_summary``: power and energy capacity, charged and discharged
  energy, losses, cycles, the prices at which they charge and discharge and
  the arbitrage revenue at those prices.
- ``battery_sites``: the same per balancing area.
- ``battery_displacement``: the change by carrier, in net imports and in
  curtailment, per MWh discharged.
- ``battery_value``: operating-cost savings against the battery annualised
  cost, net benefit, break-even cost, avoided CO2, and the change in system
  price, peak prices and the daily price spread.
- ``battery_daily_profile``: charge, discharge and system price over an
  average day.
- ``battery_monthly``: battery energy and the change by carrier, by month.

With more than one technology, ``scenario_value`` sets the combined savings
against the combined cost; each technology section then reports the joint
change of the scenario, which cannot be split between the technologies.

CO2 uses the indicative factors in ``config/emission_factors.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import CARRIER_COLORS, CARRIER_LABELS, CARRIER_ORDER, CURRENCY
from .emissions import load_emission_factors, tn_net_imports
from .grid import corridor_loading
from .loaders import AnalysisRun
from .metrics import (
    corridor_metrics,
    generation_by_carrier_timeseries,
    market_values,
    price_metrics,
    system_metrics,
    system_price_timeseries,
)
from .statistics import price_timeseries, statistic_timeseries

OFFSHORE = "offshore_wind"
BATTERY = "battery"
VRE = ("solar", "wind", OFFSHORE)


@dataclass
class EffectsBundle:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    figures: dict[str, plt.Figure] = field(default_factory=dict)
    headline: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _check_comparable(baseline: AnalysisRun, scenario: AnalysisRun) -> None:
    if not baseline.snapshots.equals(scenario.snapshots):
        raise ValueError("The baseline and scenario runs cover different hours")


def _energy(run: AnalysisRun, hourly: pd.DataFrame | pd.Series) -> pd.Series | float:
    """Weighted energy (MWh) of hourly MW values."""
    return hourly.mul(run.weights, axis=0).sum()


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
        net_imports=tn_net_imports(run),
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


def _battery_units(run: AnalysisRun) -> pd.Index:
    units = run.network.storage_units
    return units.index[units.carrier.astype(str).eq(BATTERY)]


def _battery_flows(run: AnalysisRun, units: pd.Index) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly discharge and charge (both >= 0, MW at the bus) of each battery."""
    dispatch = run.frame("storage_units", "p_dispatch")
    store = run.frame("storage_units", "p_store")
    if units.difference(dispatch.columns).empty and units.difference(store.columns).empty:
        return dispatch[units].fillna(0.0), store[units].fillna(0.0)
    net = run.frame("storage_units", "p").reindex(columns=units).fillna(0.0)
    return net.clip(lower=0.0), (-net).clip(lower=0.0)


def _weighted_price(prices: pd.DataFrame, energy: pd.DataFrame, buses: pd.Series) -> pd.Series:
    """Energy-weighted price per unit, using the price at each unit's bus."""
    columns = {unit: prices[bus] for unit, bus in buses.items() if bus in prices}
    if not columns:
        return pd.Series(np.nan, index=energy.columns)
    unit_prices = pd.DataFrame(columns).reindex(columns=energy.columns)
    return unit_prices.mul(energy).sum() / energy.sum().replace(0.0, np.nan)


def battery_effects(
    baseline: AnalysisRun,
    scenario: AnalysisRun,
    base: _RunSummary,
    scen: _RunSummary,
    factors: dict,
) -> tuple[dict[str, pd.DataFrame], dict[str, float]]:
    """Tables quantifying what the scenario's batteries do.

    Arbitrage revenue values each battery's discharge and charge at the price
    of its own bus; it is what the battery would earn selling into and buying
    from the market at the scenario's prices.
    """
    units = _battery_units(scenario)
    network = scenario.network
    weights = scenario.weights
    table = network.storage_units.loc[units]
    power = table.p_nom.astype(float)
    energy_capacity = power * table.max_hours.astype(float)
    discharge, charge = _battery_flows(scenario, units)
    discharge_energy, charge_energy = discharge.mul(weights, axis=0), charge.mul(weights, axis=0)
    discharged, charged = discharge_energy.sum(), charge_energy.sum()
    days = float(weights.sum()) / 24.0
    prices = price_timeseries(scenario)
    has_prices = not (prices.empty or prices.isna().all().all())
    if has_prices:
        discharge_price = _weighted_price(prices, discharge_energy, table.bus)
        charge_price = _weighted_price(prices, charge_energy, table.bus)
        revenue = (discharge_price * discharged).fillna(0.0) - (charge_price * charged).fillna(0.0)
    else:
        discharge_price = charge_price = revenue = pd.Series(np.nan, index=units)
    annual_cost = table.capital_cost.astype(float) * power

    sites = pd.DataFrame(
        {
            "bus": table.bus,
            "power_mw": power,
            "energy_mwh": energy_capacity,
            "discharged_gwh": discharged / 1e3,
            "charged_gwh": charged / 1e3,
            "equivalent_full_cycles": discharged / energy_capacity.replace(0.0, np.nan),
            "mean_charge_price": charge_price,
            "mean_discharge_price": discharge_price,
            f"arbitrage_revenue_million_{CURRENCY.lower()}": revenue / 1e6,
            f"annualised_cost_million_{CURRENCY.lower()}": annual_cost / 1e6,
            f"revenue_per_mw_{CURRENCY.lower()}_per_year": revenue / power.replace(0.0, np.nan),
        }
    )
    sites.index.name = "battery"

    total_power, total_energy = float(power.sum()), float(energy_capacity.sum())
    total_discharged, total_charged = float(discharged.sum()), float(charged.sum())
    total_revenue = float(revenue.sum()) if has_prices else np.nan
    mean_discharge_price = (
        float((discharge_price * discharged).sum() / total_discharged) if has_prices and total_discharged else np.nan
    )
    mean_charge_price = (
        float((charge_price * charged).sum() / total_charged) if has_prices and total_charged else np.nan
    )
    cycles = total_discharged / total_energy if total_energy else np.nan
    summary = pd.DataFrame(
        [
            ("power_capacity", total_power, "MW"),
            ("energy_capacity", total_energy, "MWh"),
            ("charged_energy", total_charged / 1e3, "GWh"),
            ("discharged_energy", total_discharged / 1e3, "GWh"),
            ("conversion_losses", (total_charged - total_discharged) / 1e3, "GWh"),
            ("achieved_round_trip_efficiency", total_discharged / total_charged if total_charged else np.nan, "-"),
            ("equivalent_full_cycles", cycles, "cycles/year"),
            ("cycles_per_day", cycles / days if days else np.nan, "cycles/day"),
            ("hours_discharging", float(discharge.sum(axis=1).gt(1e-3).mul(weights).sum()), "hours"),
            ("hours_charging", float(charge.sum(axis=1).gt(1e-3).mul(weights).sum()), "hours"),
            ("mean_charge_price", mean_charge_price, f"{CURRENCY}/MWh"),
            ("mean_discharge_price", mean_discharge_price, f"{CURRENCY}/MWh"),
            ("price_spread_captured", mean_discharge_price - mean_charge_price, f"{CURRENCY}/MWh"),
            ("arbitrage_revenue", total_revenue / 1e6, f"million {CURRENCY}"),
            ("arbitrage_revenue_per_mw", total_revenue / total_power if total_power else np.nan, f"{CURRENCY}/MW/year"),
        ],
        columns=["metric", "value", "unit"],
    ).set_index("metric")

    # Displacement: change per MWh the batteries discharge. Batteries shift
    # energy in time, so the changes include the generation that covers the
    # conversion losses.
    change = scen.generation_mwh.sub(base.generation_mwh, fill_value=0.0).drop(BATTERY, errors="ignore")
    net_import_change = float(_energy(scenario, scen.net_imports) - _energy(baseline, base.net_imports))
    curtailment_change = _energy(scenario, scen.curtailment) - _energy(baseline, base.curtailment)
    rows = {carrier: change[carrier] for carrier in _ordered(change.index) if abs(change[carrier]) > 1e-3}
    rows["net_imports_tn"] = net_import_change
    displacement = pd.DataFrame({"change_gwh": pd.Series(rows, dtype=float) / 1e3})
    for carrier, value in curtailment_change.items():
        if abs(value) > 1e-3 or carrier != OFFSHORE:
            displacement.loc[f"{carrier}_curtailment", "change_gwh"] = value / 1e3
    displacement["per_mwh_discharged"] = (
        displacement.change_gwh * 1e3 / total_discharged if total_discharged else np.nan
    )
    displacement.index.name = "item"

    # Value against the battery cost, and the effect on prices.
    def metric(run_summary: _RunSummary, name: str) -> float:
        return float(run_summary.metrics.at[name, "value"]) if name in run_summary.metrics.index else np.nan

    operating_saving = metric(base, "operating_cost") - metric(scen, "operating_cost")
    penalty_saving = metric(base, "penalty_cost") - metric(scen, "penalty_cost")
    total_saving = operating_saving + (0.0 if np.isnan(penalty_saving) else penalty_saving)
    cost = float(annual_cost.sum()) / 1e6
    base_co2 = sum(_emissions(base.generation_mwh, _energy(baseline, base.net_imports), factors))
    scen_co2 = sum(_emissions(scen.generation_mwh, _energy(scenario, scen.net_imports), factors))
    avoided_t = base_co2 - scen_co2
    value_rows = [
        ("operating_cost_saving", operating_saving, f"million {CURRENCY}"),
        ("operating_cost_saving_per_mwh", total_saving * 1e6 / total_discharged if total_discharged else np.nan, f"{CURRENCY}/MWh discharged"),
        ("penalty_cost_saving", penalty_saving, f"million {CURRENCY}"),
        ("unserved_energy_change", metric(scen, "unserved_energy") - metric(base, "unserved_energy"), "MWh"),
        ("battery_annualised_cost", cost, f"million {CURRENCY}"),
        ("battery_cost_per_mwh", cost * 1e6 / total_discharged if total_discharged else np.nan, f"{CURRENCY}/MWh discharged"),
        ("net_benefit", total_saving - cost, f"million {CURRENCY}"),
        ("break_even_annualised_cost", total_saving * 1e6 / total_power if total_power else np.nan, f"{CURRENCY}/MW/year"),
        ("arbitrage_revenue", total_revenue / 1e6, f"million {CURRENCY}"),
        ("arbitrage_revenue_to_cost", total_revenue / 1e6 / cost if cost else np.nan, "-"),
        ("co2_avoided", avoided_t / 1e6, "Mt"),
        ("abatement_cost", (cost - total_saving) * 1e6 / avoided_t if avoided_t > 0 else np.nan, f"{CURRENCY}/tCO2"),
    ]
    value = pd.DataFrame(value_rows, columns=["metric", "value", "unit"]).set_index("metric")
    base_prices = price_timeseries(baseline)
    if has_prices and not (base_prices.empty or base_prices.isna().all().all()):
        base_system, scen_system = system_price_timeseries(baseline, base_prices), system_price_timeseries(scenario, prices)

        def daily_spread(series: pd.Series) -> float:
            daily = series.groupby(series.index.normalize())
            return float((daily.max() - daily.min()).mean())

        for name, base_value, scen_value in (
            ("system_price", metric(base, "load_weighted_average_price"), metric(scen, "load_weighted_average_price")),
            ("p95_system_price", float(base_system.quantile(0.95)), float(scen_system.quantile(0.95))),
            ("maximum_system_price", float(base_system.max()), float(scen_system.max())),
            ("mean_daily_price_spread", daily_spread(base_system), daily_spread(scen_system)),
        ):
            value.loc[f"{name}_change"] = (scen_value - base_value, f"{CURRENCY}/MWh")

    # Average day: battery operation and system price by hour of day.
    hour = scenario.snapshots.hour
    profile = pd.DataFrame(
        {
            "discharge_mw": discharge.sum(axis=1).groupby(hour).mean(),
            "charge_mw": charge.sum(axis=1).groupby(hour).mean(),
        }
    )
    profile["net_output_mw"] = profile.discharge_mw - profile.charge_mw
    if has_prices and not (base_prices.empty or base_prices.isna().all().all()):
        profile["baseline_price"] = base_system.groupby(base_system.index.hour).mean()
        profile["scenario_price"] = scen_system.groupby(scen_system.index.hour).mean()
    profile.index.name = "hour"

    # Monthly view.
    month = scenario.snapshots.to_period("M")
    delta = scen.hourly_generation.sub(base.hourly_generation, fill_value=0.0).drop(columns=BATTERY, errors="ignore")
    monthly = delta.mul(weights, axis=0).groupby(month).sum() / 1e3
    monthly = monthly.loc[:, monthly.abs().max().gt(1e-3)]
    monthly = monthly[_ordered(monthly.columns)].add_suffix("_change_gwh")
    monthly.insert(0, "battery_discharge_gwh", discharge_energy.sum(axis=1).groupby(month).sum() / 1e3)
    monthly.insert(1, "battery_charge_gwh", charge_energy.sum(axis=1).groupby(month).sum() / 1e3)
    monthly["net_imports_tn_change_gwh"] = (scen.net_imports - base.net_imports).mul(weights).groupby(month).sum() / 1e3
    monthly.index = monthly.index.astype(str)
    monthly.index.name = "month"

    headline = {
        "battery_power_mw": total_power,
        "battery_energy_mwh": total_energy,
        "battery_discharge_gwh": total_discharged / 1e3,
        "battery_cycles_per_year": cycles,
        "battery_net_benefit_million": total_saving - cost,
    }
    tables = {
        "battery_summary": summary,
        "battery_sites": sites,
        "battery_displacement": displacement,
        "battery_value": value,
        "battery_daily_profile": profile,
        "battery_monthly": monthly,
    }
    return tables, headline


def scenario_value(
    baseline: AnalysisRun, scenario: AnalysisRun, base: _RunSummary, scen: _RunSummary, factors: dict
) -> pd.DataFrame:
    """Joint value of everything the scenario adds, against their total cost."""
    def metric(run_summary: _RunSummary, name: str) -> float:
        return float(run_summary.metrics.at[name, "value"]) if name in run_summary.metrics.index else np.nan

    network = scenario.network
    costs = {}
    offshore = _offshore_generators(scenario)
    if len(offshore):
        costs[OFFSHORE] = float((network.generators.loc[offshore, "capital_cost"] * network.generators.loc[offshore, "p_nom"]).sum())
    batteries = _battery_units(scenario)
    if len(batteries):
        costs[BATTERY] = float((network.storage_units.loc[batteries, "capital_cost"] * network.storage_units.loc[batteries, "p_nom"]).sum())
    operating_saving = metric(base, "operating_cost") - metric(scen, "operating_cost")
    penalty_saving = metric(base, "penalty_cost") - metric(scen, "penalty_cost")
    total_saving = operating_saving + (0.0 if np.isnan(penalty_saving) else penalty_saving)
    total_cost = sum(costs.values()) / 1e6
    avoided_t = sum(_emissions(base.generation_mwh, _energy(baseline, base.net_imports), factors)) - sum(
        _emissions(scen.generation_mwh, _energy(scenario, scen.net_imports), factors)
    )
    rows = [
        ("operating_cost_saving", operating_saving, f"million {CURRENCY}"),
        ("penalty_cost_saving", penalty_saving, f"million {CURRENCY}"),
        *[(f"{technology}_annualised_cost", value / 1e6, f"million {CURRENCY}") for technology, value in costs.items()],
        ("total_annualised_cost", total_cost, f"million {CURRENCY}"),
        ("net_benefit", total_saving - total_cost, f"million {CURRENCY}"),
        ("co2_avoided", avoided_t / 1e6, "Mt"),
        ("abatement_cost", (total_cost - total_saving) * 1e6 / avoided_t if avoided_t > 0 else np.nan, f"{CURRENCY}/tCO2"),
    ]
    return pd.DataFrame(rows, columns=["metric", "value", "unit"]).set_index("metric")


def battery_profile_figure(profile: pd.DataFrame):
    figure, axis = plt.subplots(figsize=(10, 5))
    hours = profile.index.to_numpy()
    axis.bar(hours, profile.discharge_mw, color=CARRIER_COLORS.get(BATTERY, "#E377C2"), label="Discharge")
    axis.bar(hours, -profile.charge_mw, color=CARRIER_COLORS.get("storage_charging", "#6A3D9A"), label="Charge")
    axis.axhline(0.0, color="black", linewidth=0.8)
    axis.set_xlabel("Hour of day")
    axis.set_ylabel("Average battery power, MW")
    axis.set_xticks(range(0, 24, 2))
    lines = []
    if {"baseline_price", "scenario_price"} <= set(profile.columns):
        price_axis = axis.twinx()
        lines += price_axis.plot(hours, profile.baseline_price, "--", color="black", label="System price, baseline")
        lines += price_axis.plot(hours, profile.scenario_price, "-", color="black", label="System price, scenario")
        price_axis.set_ylabel(f"Average system price, {CURRENCY}/MWh")
    handles, labels = axis.get_legend_handles_labels()
    axis.legend(handles + lines, labels + [line.get_label() for line in lines], fontsize=8, loc="upper left")
    axis.set_title("Battery operation and system price over an average day")
    axis.grid(axis="y", alpha=0.2)
    figure.tight_layout()
    return figure


def _color(name: str) -> str:
    return CARRIER_COLORS.get(name.replace("_change_gwh", "").replace("_curtailment", ""), "#7F7F7F")


def displacement_figure(displacement: pd.DataFrame, title: str = "What offshore wind changes over the year"):
    figure, axis = plt.subplots(figsize=(10, 5))
    data = displacement.change_gwh.sort_values()
    labels = [CARRIER_LABELS.get(item, item.replace("_", " ")) for item in data.index]
    axis.barh(labels, data.values, color=[_color(item) for item in data.index])
    axis.axvline(0.0, color="black", linewidth=0.8)
    axis.set_xlabel("Change against the baseline, GWh")
    axis.set_title(title)
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    return figure


def monthly_figure(
    monthly: pd.DataFrame,
    line_column: str = "offshore_generation_gwh",
    line_label: str = "Offshore generation",
    title: str = "Offshore generation and the change it causes, by month",
):
    figure, axis = plt.subplots(figsize=(13, 5))
    changes = monthly.loc[:, monthly.columns.str.endswith("_change_gwh")]
    positive, negative = changes.clip(lower=0.0), changes.clip(upper=0.0)
    x = np.arange(len(monthly))
    for part in (positive, negative):
        bottom = np.zeros(len(monthly))
        for column in part:
            name = column.replace("_change_gwh", "")
            axis.bar(x, part[column], bottom=bottom, color=_color(column), width=0.7,
                     label=CARRIER_LABELS.get(name, name.replace("_", " ")))
            bottom += part[column].to_numpy()
    axis.plot(x, monthly[line_column], color="black", marker="o", label=line_label)
    axis.axhline(0.0, color="black", linewidth=0.8)
    axis.set_xticks(x, monthly.index, rotation=45)
    axis.set_ylabel("GWh")
    axis.set_title(title)
    handles, labels = axis.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    axis.legend(unique.values(), unique.keys(), ncol=4, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.2))
    axis.grid(axis="y", alpha=0.2)
    figure.tight_layout()
    return figure


def corridor_figure(baseline: AnalysisRun, scenario: AnalysisRun, corridors: pd.DataFrame):
    figure, axis = plt.subplots(figsize=(10, 5))
    loadings = {"baseline": corridor_loading(baseline), "scenario": corridor_loading(scenario)}
    for number, name in enumerate(corridors.index):
        color = plt.get_cmap("tab10")(number % 10)
        for label, style in (("baseline", "--"), ("scenario", "-")):
            loading = loadings[label][name].sort_values(ascending=False).to_numpy()
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
    has_offshore, has_battery = bool(len(_offshore_generators(scenario))), bool(len(_battery_units(scenario)))
    if has_offshore and has_battery:
        bundle.tables["scenario_value"] = scenario_value(baseline, scenario, base, scen, factors)
        bundle.notes.append(
            "This scenario adds more than one technology. The displacement, savings and CO2 in each "
            "technology section are the joint change of the whole scenario, not that technology's "
            "share; the combined value table sets them against the combined cost."
        )
    if has_offshore:
        tables, headline = offshore_effects(baseline, scenario, base, scen, factors)
        bundle.tables.update(tables)
        bundle.headline.update(headline)
        bundle.figures["offshore_displacement"] = displacement_figure(tables["offshore_displacement"])
        bundle.figures["offshore_monthly"] = monthly_figure(tables["offshore_monthly"])
        if not tables["offshore_corridors"].empty:
            bundle.figures["offshore_corridors"] = corridor_figure(baseline, scenario, tables["offshore_corridors"])
    if has_battery:
        tables, headline = battery_effects(baseline, scenario, base, scen, factors)
        bundle.tables.update(tables)
        bundle.headline.update(headline)
        bundle.figures["battery_profile"] = battery_profile_figure(tables["battery_daily_profile"])
        bundle.figures["battery_displacement"] = displacement_figure(
            tables["battery_displacement"], "What the batteries change over the year"
        )
        bundle.figures["battery_monthly"] = monthly_figure(
            tables["battery_monthly"], "battery_discharge_gwh", "Battery discharge",
            "Battery discharge and the change it causes, by month",
        )
        if np.isnan(tables["battery_summary"].at["arbitrage_revenue", "value"]):
            bundle.notes.append("The runs carry no prices, so battery prices and arbitrage revenue are not available.")
    return bundle
