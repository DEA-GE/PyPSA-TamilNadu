"""Shared result metrics for every supported solve horizon."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa

from .config import RENEWABLE_CARRIERS, canonical_carrier
from .loaders import AnalysisRun
from .statistics import (
    ELECTRICITY_BUS_CARRIER,
    GENERATING_COMPONENTS,
    TRANSMISSION_LINK_CARRIERS,
    is_transmission,
    network_statistic,
    order_carriers,
    statistic_timeseries,
    statistic_total,
    to_report_carriers,
)

# Generator carriers that are modelling devices, not installed plant.
NON_PLANT_CARRIERS = ("unserved_energy", "imports")


def generation_by_carrier_timeseries(run: AnalysisRun) -> pd.DataFrame:
    """Return hourly electricity generation (MW) by reporting carrier.

    Generation is PyPSA's ``supply`` at the electricity buses from generators,
    storage discharge and hydro-turbine output.
    """
    supply = statistic_timeseries(
        run, "supply", components=GENERATING_COMPONENTS, groupby="carrier"
    )
    if supply.empty:
        return pd.DataFrame(index=run.snapshots)
    return order_carriers(to_report_carriers(supply, axis=1))


def installed_generation_capacity(
    network: pypsa.Network, levels: tuple[str, ...] = ()
) -> pd.Series:
    """Return generating power capacity (MW) at the electricity buses.

    Hydro turbines count with their grid-side output rating (``p_nom`` times
    efficiency). *levels* adds grouping levels such as ``"bus"``.
    """
    capacity = network_statistic(
        network,
        "installed_capacity",
        components=GENERATING_COMPONENTS,
        groupby=[*levels, "carrier"],
    )
    return to_report_carriers(capacity, levels=levels)


def generation_by_carrier(run: AnalysisRun) -> pd.DataFrame:
    hourly = generation_by_carrier_timeseries(run)
    energy = hourly.mul(run.weights, axis=0).sum()
    capacity = installed_generation_capacity(run.base)
    result = pd.DataFrame(
        {
            "capacity_mw": capacity.reindex(energy.index).fillna(0.0),
            "generation_gwh": energy / 1_000.0,
        }
    )
    result["full_load_hours"] = (
        energy / result.capacity_mw.replace(0.0, np.nan)
    )
    result.index.name = "carrier"
    return result


def generation_comparison(
    run: AnalysisRun,
    observed_daily: pd.DataFrame | None,
    observed_annual: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compare modeled and observed generation over the analyzed period."""
    result = generation_by_carrier(run).rename(
        columns={"generation_gwh": "modeled_generation_gwh"}
    )
    # Keep the comparison taxonomy aligned with the annual NITI Dashboard input.
    # Model dispatch is stored in MWh and generation_by_carrier converts it to
    # GWh (1 MU = 1 GWh) before these aggregates are formed.
    if {"bio_power", "small_hydro"}.intersection(result.index):
        result.loc["other_res", "modeled_generation_gwh"] = result.reindex(
            ["bio_power", "small_hydro"], fill_value=0.0
        )["modeled_generation_gwh"].sum()
    dates = pd.DatetimeIndex(run.snapshots.normalize().unique())
    annual_match = False
    if observed_annual is not None and not observed_annual.empty:
        period_start = pd.Timestamp(observed_annual["period_start"].iloc[0]).normalize()
        period_end = pd.Timestamp(observed_annual["period_end"].iloc[0]).normalize()
        expected_dates = pd.date_range(period_start, period_end, freq="D")
        annual_match = dates.equals(expected_dates)
    if annual_match:
        comparable_carriers = [
            "coal",
            "oil_gas",
            "nuclear",
            "hydro",
            "solar",
            "wind",
            "small_hydro",
            "bio_power",
        ]
        result.loc["comparable_total", "modeled_generation_gwh"] = result.reindex(
            comparable_carriers, fill_value=0.0
        )["modeled_generation_gwh"].sum()
        result["observed_generation_gwh"] = observed_annual["generation_gwh"].reindex(
            result.index
        )
        result["observed_source"] = observed_annual["source"].reindex(result.index)
        result["observed_period"] = (
            f"{period_start.date()} to {period_end.date()}"
        )
    elif observed_daily is not None:
        daily_comparable_carriers = [
            "coal", "oil_gas", "nuclear", "hydro", "solar", "wind", "other_res"
        ]
        result.loc["comparable_total", "modeled_generation_gwh"] = result.reindex(
            daily_comparable_carriers, fill_value=0.0
        )["modeled_generation_gwh"].sum()
        observed = observed_daily.reindex(dates).sum(min_count=1)
        result["observed_generation_gwh"] = observed.reindex(result.index)
        result["observed_source"] = "daily historical generation"
        result["observed_period"] = (
            f"{dates.min().date()} to {dates.max().date()}"
        )
    else:
        return result
    result["difference_gwh"] = (
        result.modeled_generation_gwh - result.observed_generation_gwh
    )
    result["difference_pct"] = (
        100.0
        * result.difference_gwh
        / result.observed_generation_gwh.replace(0.0, np.nan)
    )
    result["observed_implied_full_load_hours"] = (
        result.observed_generation_gwh * 1_000.0
        / result.capacity_mw.replace(0.0, np.nan)
    )
    result.index.name = "carrier"
    return result


def installed_capacity_by_area(run: AnalysisRun) -> pd.DataFrame:
    """Return modeled generator and storage power capacity by electrical area."""
    return (
        installed_generation_capacity(run.base, levels=("bus",))
        .rename_axis(index=["balancing_area", "carrier"])
        .rename("capacity_mw")
        .to_frame()
        .sort_index()
    )


def installed_capacity_comparison(
    run: AnalysisRun, observed_capacity: pd.DataFrame | None
) -> pd.DataFrame:
    """Compare model capacity with the supplied existing-capacity benchmark."""
    return capacity_comparison(run.base, observed_capacity)


def capacity_comparison(
    network: pypsa.Network, observed_capacity: pd.DataFrame | None
) -> pd.DataFrame:
    """Compare a network's plant capacity with the existing-capacity benchmark.

    The ``total`` row and the model shares cover physical plant only: the
    unserved-energy slack generators and market imports are not installed
    capacity. ``storage`` is the storage units' power rating, which is also
    counted in its carrier (pumped storage in ``hydro``).
    """
    capacity = installed_generation_capacity(network)
    plant = capacity.drop(list(NON_PLANT_CARRIERS), errors="ignore")
    modeled = plant.rename("modeled_capacity_mw").to_frame()
    modeled.loc["storage", "modeled_capacity_mw"] = float(
        network_statistic(network, "installed_capacity", components="StorageUnit").sum()
    )
    modeled.loc["total", "modeled_capacity_mw"] = plant.sum()
    modeled.index.name = "carrier"
    if observed_capacity is None:
        return modeled
    result = modeled.reindex(observed_capacity.index).join(
        observed_capacity.rename(
            columns={
                "installed_capacity_mw": "historical_capacity_mw",
                "share_pct": "historical_share_pct",
                "source": "historical_source",
            }
        )
    )
    result["modeled_share_pct"] = (
        100.0 * result.modeled_capacity_mw / modeled.at["total", "modeled_capacity_mw"]
    )
    result["difference_mw"] = (
        result.modeled_capacity_mw - result.historical_capacity_mw
    )
    result["difference_pct"] = np.where(
        result.historical_capacity_mw.eq(0.0),
        np.nan,
        100.0 * result.difference_mw / result.historical_capacity_mw,
    )
    result.index.name = "carrier"
    return result


def renewable_generation_by_area(run: AnalysisRun) -> pd.DataFrame:
    """Return solar and wind energy by balancing area for the analyzed period."""
    energy = statistic_total(
        run,
        "supply",
        components="Generator",
        carrier=list(RENEWABLE_CARRIERS),
        groupby=["bus", "carrier"],
    )
    if energy.empty:
        return pd.DataFrame(columns=["generation_gwh"])
    return (
        to_report_carriers(energy, levels=("bus",))
        .div(1_000.0)
        .rename_axis(index=["balancing_area", "carrier"])
        .rename("generation_gwh")
        .to_frame()
        .sort_index()
    )


def _energy_by_name(
    run: AnalysisRun, component: str, index: pd.Index, bus_carrier: str | None
) -> tuple[pd.Series, pd.Series]:
    """Return discharge and charge (GWh) per asset from ``supply``/``withdrawal``."""
    options = {"components": component, "groupby": "name", "bus_carrier": bus_carrier}
    discharge = statistic_total(run, "supply", **options)
    charge = statistic_total(run, "withdrawal", **options)
    return (
        discharge.reindex(index, fill_value=0.0) / 1_000.0,
        charge.reindex(index, fill_value=0.0) / 1_000.0,
    )


def storage_metrics(run: AnalysisRun) -> pd.DataFrame:
    base = run.base
    if base.storage_units.empty:
        return pd.DataFrame()
    units = base.storage_units.index
    soc = run.frame("storage_units", "state_of_charge").reindex(columns=units)
    result = pd.DataFrame(index=units)
    result.index.name = "storage_unit"
    result["carrier"] = base.storage_units.carrier.map(canonical_carrier)
    result["power_capacity_mw"] = network_statistic(
        base, "installed_capacity", components="StorageUnit", groupby="name",
        bus_carrier=None,
    ).reindex(units)
    result["energy_capacity_mwh"] = base.storage_units.p_nom * base.storage_units.max_hours
    result["discharge_gwh"], result["charge_gwh"] = _energy_by_name(
        run, "StorageUnit", units, bus_carrier=None
    )
    result["minimum_soc_mwh"] = soc.min()
    result["maximum_soc_mwh"] = soc.max()
    result["equivalent_full_cycles"] = (
        result.discharge_gwh * 1_000.0 / result.energy_capacity_mwh.replace(0.0, np.nan)
    )
    return result


def store_metrics(run: AnalysisRun) -> pd.DataFrame:
    """Summarize explicit PyPSA Stores used by cascade-reservoir systems."""
    base = run.base
    if base.stores.empty:
        return pd.DataFrame()
    stores = base.stores.index
    energy = run.frame("stores", "e").reindex(columns=stores)
    result = pd.DataFrame(index=stores)
    result.index.name = "store"
    result["carrier"] = base.stores.carrier.map(canonical_carrier)
    result["bus"] = base.stores.bus
    result["energy_capacity_mwh"] = network_statistic(
        base, "installed_capacity", components="Store", groupby="name",
        bus_carrier=None,
    ).reindex(stores)
    result["discharge_gwh"], result["charge_gwh"] = _energy_by_name(
        run, "Store", stores, bus_carrier=None
    )
    result["minimum_stored_energy_mwh"] = energy.min()
    result["maximum_stored_energy_mwh"] = energy.max()
    return result


def commitment_metrics(run: AnalysisRun, tolerance: float = 1e-6) -> pd.DataFrame:
    base = run.base
    units = base.generators.index[base.generators.committable.fillna(False)]
    status = run.frame("generators", "status").reindex(columns=units)
    if not len(units) or status.empty:
        return pd.DataFrame()
    start_up = run.frame("generators", "start_up").reindex(columns=units).fillna(0.0)
    shut_down = run.frame("generators", "shut_down").reindex(columns=units).fillna(0.0)
    dispatch = run.frame("generators", "p").reindex(columns=units).fillna(0.0)
    weights = run.weights
    result = pd.DataFrame(index=units)
    result.index.name = "generator"
    result["carrier"] = base.generators.loc[units, "carrier"].map(canonical_carrier)
    result["capacity_mw"] = base.generators.loc[units, "p_nom"]
    result["equivalent_online_hours"] = status.mul(weights, axis=0).sum()
    result["start_up_equivalents"] = start_up.mul(weights, axis=0).sum()
    result["shut_down_equivalents"] = shut_down.mul(weights, axis=0).sum()
    result["fractional_status_hours"] = (
        status.gt(tolerance) & status.lt(1.0 - tolerance)
    ).sum()
    result["generation_gwh"] = dispatch.mul(weights, axis=0).sum() / 1_000.0
    return result


def commitment_by_carrier(unit_metrics: pd.DataFrame) -> pd.DataFrame:
    if unit_metrics.empty:
        return pd.DataFrame()
    result = unit_metrics.groupby("carrier").agg(
        units=("carrier", "size"),
        capacity_mw=("capacity_mw", "sum"),
        equivalent_online_hours=("equivalent_online_hours", "sum"),
        start_up_equivalents=("start_up_equivalents", "sum"),
        shut_down_equivalents=("shut_down_equivalents", "sum"),
        fractional_status_hours=("fractional_status_hours", "sum"),
        generation_gwh=("generation_gwh", "sum"),
    )
    result["start_up_equivalents_per_unit"] = result.start_up_equivalents / result.units
    return result


def corridor_flows(run: AnalysisRun) -> pd.DataFrame:
    """Return hourly flow (MW, bus0 to bus1) on every Line and AC/DC Link.

    PyPSA's ``transmission`` statistic covers branches whose two ends are
    electricity buses, so hydro turbines and pumps are excluded.
    """
    flows = statistic_timeseries(run, "transmission", groupby="name")
    if flows.empty:
        return flows
    flows.columns = flows.columns.get_level_values("name")
    return flows


def _corridor_ratings(network: pypsa.Network) -> pd.DataFrame:
    lines = network.lines
    links = network.links
    links = links.loc[links.carrier.astype(str).isin(TRANSMISSION_LINK_CARRIERS)]
    return pd.concat(
        [
            pd.DataFrame(
                {
                    "component": "Line",
                    "carrier": lines.carrier,
                    "bus0": lines.bus0,
                    "bus1": lines.bus1,
                    "capacity_mw": lines.s_nom,
                    "limit_mw": lines.s_nom * lines.s_max_pu,
                }
            ),
            pd.DataFrame(
                {
                    "component": "Link",
                    "carrier": links.carrier,
                    "bus0": links.bus0,
                    "bus1": links.bus1,
                    "capacity_mw": links.p_nom,
                    "limit_mw": links.p_nom * links.p_max_pu,
                }
            ),
        ]
    )


def corridor_metrics(
    run: AnalysisRun, flows: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Summarize flows and loading on every transmission Line and AC/DC Link.

    Loading is the absolute flow relative to the corridor's operating limit
    (``s_nom * s_max_pu`` for Lines, ``p_nom * p_max_pu`` for Links), so 100%
    means the flow limit binds. ``capacity_mw`` is the nominal rating.
    """
    flows = corridor_flows(run) if flows is None else flows
    if flows.empty:
        return pd.DataFrame()
    result = _corridor_ratings(run.base).reindex(flows.columns)
    result.index.name = "corridor"
    loading = flows.abs().div(result.limit_mw.replace(0.0, np.nan), axis=1) * 100.0
    result["maximum_absolute_flow_mw"] = flows.abs().max()
    result["maximum_loading_pct"] = loading.max()
    result["p95_loading_pct"] = loading.quantile(0.95)
    result["hours_at_or_above_99pct"] = loading.ge(99.0).sum()
    result["net_energy_bus0_to_bus1_gwh"] = (
        flows.mul(run.weights, axis=0).sum() / 1_000.0
    )
    return result.sort_values("maximum_loading_pct", ascending=False)


def _area_timeseries(run: AnalysisRun) -> dict[str, pd.DataFrame]:
    """Split each electricity bus's energy balance into its four parts (MW).

    Built from PyPSA's ``energy_balance`` at the electricity buses, so the
    parts close at every bus: generation + storage + net_import = demand.
    ``generation`` includes hydro-turbine output less pumping demand;
    ``net_import`` covers Lines and AC/DC Links, with line losses.
    """
    areas = run.base.buses.index[
        run.base.buses.carrier.astype(str).eq(ELECTRICITY_BUS_CARRIER)
    ]
    balance = statistic_timeseries(
        run, "energy_balance", groupby=["bus", "carrier"]
    )
    columns = balance.columns
    components = columns.get_level_values("component")
    part = np.select(
        [
            components == "Load",
            components == "StorageUnit",
            is_transmission(components, columns.get_level_values("carrier")),
        ],
        ["demand", "storage", "net_import"],
        default="generation",
    )
    buses = columns.get_level_values("bus")
    frames = {}
    for name in ("generation", "storage", "demand", "net_import"):
        selected = balance.loc[:, part == name]
        frame = selected.T.groupby(buses[part == name]).sum().T
        frames[name] = frame.reindex(index=run.snapshots, columns=areas, fill_value=0.0)
    frames["demand"] = -frames["demand"]
    return frames


def balancing_area_metrics(run: AnalysisRun) -> pd.DataFrame:
    frames = _area_timeseries(run)
    weights = run.weights
    areas = frames["demand"].columns
    result = pd.DataFrame(index=areas)
    result.index.name = "balancing_area"
    for name, frame in frames.items():
        result[f"{name}_gwh"] = frame.mul(weights, axis=0).sum() / 1_000.0
    result["balance_residual_gwh"] = (
        result.generation_gwh
        + result.storage_gwh
        + result.net_import_gwh
        - result.demand_gwh
    )
    result["peak_demand_mw"] = frames["demand"].max()
    return result


def peak_hour_by_area(run: AnalysisRun) -> pd.DataFrame:
    frames = _area_timeseries(run)
    peak_time = frames["demand"].sum(axis=1).idxmax()
    result = pd.DataFrame(index=frames["demand"].columns)
    result.index.name = "balancing_area"
    result["timestamp"] = peak_time
    for name, frame in frames.items():
        result[f"{name}_mw"] = frame.loc[peak_time]
    result["balance_residual_mw"] = (
        result.generation_mw
        + result.storage_mw
        + result.net_import_mw
        - result.demand_mw
    )
    return result


def ramp_timeseries(run: AnalysisRun) -> pd.DataFrame:
    generation = generation_by_carrier_timeseries(run)
    demand_frame = _area_timeseries(run)["demand"]
    demand = demand_frame.sum(axis=1)
    vre = generation.reindex(columns=RENEWABLE_CARRIERS, fill_value=0.0).sum(axis=1)
    net_load = demand - vre
    result = pd.DataFrame(
        {
            "demand_mw": demand,
            "renewable_generation_mw": vre,
            "net_load_mw": net_load,
            "net_load_ramp_mw_per_h": net_load.diff(),
            "generation_ramp_mw_per_h": generation.sum(axis=1).diff(),
        }
    )
    return result


def system_metrics(run: AnalysisRun) -> pd.DataFrame:
    generation = generation_by_carrier_timeseries(run)
    area = _area_timeseries(run)
    demand = area["demand"].sum(axis=1)
    weights = run.weights
    ramps = ramp_timeseries(run)
    corridors = corridor_metrics(run)
    unserved = generation.get("unserved_energy", pd.Series(0.0, index=run.snapshots))
    renewable = generation.reindex(columns=RENEWABLE_CARRIERS, fill_value=0.0).sum(axis=1)
    rows = [
        ("demand_energy", demand.mul(weights).sum() / 1_000.0, "GWh"),
        ("peak_demand", demand.max(), "MW"),
        ("renewable_generation", renewable.mul(weights).sum() / 1_000.0, "GWh"),
        ("unserved_energy", unserved.mul(weights).sum(), "MWh"),
        ("hours_with_unserved_energy", unserved.gt(1e-6).sum(), "hours"),
        ("maximum_unserved_power", unserved.max(), "MW"),
        ("maximum_upward_net_load_ramp", ramps.net_load_ramp_mw_per_h.max(), "MW/h"),
        ("maximum_downward_net_load_ramp", ramps.net_load_ramp_mw_per_h.min(), "MW/h"),
        (
            "maximum_corridor_loading",
            corridors.maximum_loading_pct.max() if not corridors.empty else np.nan,
            "%",
        ),
    ]
    return pd.DataFrame(rows, columns=["metric", "value", "unit"]).set_index("metric")


def monthly_generation_comparison(
    run: AnalysisRun,
    observed_daily: pd.DataFrame | None,
    observed_monthly: pd.DataFrame | None = None,
) -> pd.DataFrame:
    modeled = generation_by_carrier_timeseries(run).resample("MS").sum() / 1_000.0
    modeled.index.name = "month"
    modeled_long = modeled.rename_axis(columns="carrier").stack().rename("modeled_gwh").to_frame()
    if observed_monthly is not None:
        observed = observed_monthly.reindex(columns=modeled.columns)
        observed.index.name = "month"
        observed_long = observed.rename_axis(columns="carrier").stack().rename(
            "observed_gwh"
        )
    elif observed_daily is not None:
        observed = observed_daily.reindex(columns=modeled.columns).resample("MS").sum()
        observed.index.name = "month"
        observed_long = observed.rename_axis(columns="carrier").stack().rename(
            "observed_gwh"
        )
    else:
        return modeled_long.reset_index().set_index(["month", "carrier"])
    result = modeled_long.join(observed_long, how="left")
    result["difference_gwh"] = result.modeled_gwh - result.observed_gwh
    result["difference_pct"] = 100.0 * result.difference_gwh / result.observed_gwh.replace(0.0, np.nan)
    return result


def monthly_resource_seasonality(
    run: AnalysisRun, observed_daily: pd.DataFrame | None
) -> pd.DataFrame:
    generation = generation_by_carrier_timeseries(run)
    hours = pd.Series(1.0, index=run.snapshots).resample("MS").sum()
    base = run.base
    result = pd.DataFrame(index=hours.index)
    result.index.name = "month"
    for carrier in ("wind", "hydro"):
        generator_capacity = base.generators.loc[
            base.generators.carrier.map(canonical_carrier).eq(carrier), "p_nom"
        ].sum()
        storage_capacity = base.storage_units.loc[
            base.storage_units.carrier.map(canonical_carrier).eq(carrier), "p_nom"
        ].sum()
        capacity = generator_capacity + storage_capacity
        modeled_gwh = generation.get(carrier, pd.Series(0.0, index=run.snapshots)).resample("MS").sum() / 1_000.0
        result[f"{carrier}_modeled_cf"] = modeled_gwh * 1_000.0 / (capacity * hours)
        if observed_daily is not None and carrier in observed_daily:
            observed_gwh = observed_daily[carrier].resample("MS").sum()
            result[f"{carrier}_observed_cf"] = observed_gwh * 1_000.0 / (capacity * hours)

    available = run.frame("generators", "p_max_pu")
    wind_units = base.generators.index[
        base.generators.carrier.map(canonical_carrier).eq("wind")
    ].intersection(available.columns)
    wind_capacity = base.generators.loc[wind_units, "p_nom"]
    if len(wind_units) and wind_capacity.sum() > 0:
        weighted_availability = available[wind_units].mul(wind_capacity, axis=1).sum(axis=1) / wind_capacity.sum()
        result["wind_resource_cf"] = weighted_availability.resample("MS").mean()
    return result


def seasonality_summary(monthly: pd.DataFrame) -> pd.DataFrame:
    """Summarize amplitude and timing for each monthly utilization series."""
    rows = []
    for name in monthly.columns:
        series = monthly[name].dropna()
        if series.empty:
            continue
        rows.append(
            {
                "series": name,
                "mean_pct": 100.0 * series.mean(),
                "minimum_pct": 100.0 * series.min(),
                "maximum_pct": 100.0 * series.max(),
                "amplitude_percentage_points": 100.0 * (series.max() - series.min()),
                "peak_month": series.idxmax().strftime("%Y-%m"),
                "minimum_month": series.idxmin().strftime("%Y-%m"),
            }
        )
    return pd.DataFrame(rows).set_index("series") if rows else pd.DataFrame()


def seasonality_fit(monthly: pd.DataFrame) -> pd.DataFrame:
    """Compare modeled and observed monthly wind and hydro patterns."""
    rows = []
    for carrier in ("wind", "hydro"):
        modeled_name = f"{carrier}_modeled_cf"
        observed_name = f"{carrier}_observed_cf"
        if modeled_name not in monthly or observed_name not in monthly:
            continue
        pair = monthly[[modeled_name, observed_name]].dropna()
        if pair.empty:
            continue
        rows.append(
            {
                "carrier": carrier,
                "monthly_correlation": pair[modeled_name].corr(pair[observed_name]),
                "mean_absolute_cf_error_percentage_points": (
                    100.0 * (pair[modeled_name] - pair[observed_name]).abs().mean()
                ),
                "peak_month_matches": pair[modeled_name].idxmax().month
                == pair[observed_name].idxmax().month,
                "minimum_month_matches": pair[modeled_name].idxmin().month
                == pair[observed_name].idxmin().month,
            }
        )
    return pd.DataFrame(rows).set_index("carrier") if rows else pd.DataFrame()
