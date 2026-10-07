"""Shared result metrics for every supported solve horizon."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CARRIER_ORDER, RENEWABLE_CARRIERS, canonical_carrier
from .loaders import AnalysisRun


def _group_columns(frame: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(index=frame.index)
    labels = groups.reindex(frame.columns).fillna("unknown").map(canonical_carrier)
    return frame.T.groupby(labels).sum().T


def generation_by_carrier_timeseries(run: AnalysisRun) -> pd.DataFrame:
    base = run.base
    # Generator dispatch represents production in this model. Clip numerical
    # negative tolerances (and malformed legacy values) before aggregation so
    # energy totals and stacked plots share the same physical interpretation.
    generator_p = run.frame("generators", "p").fillna(0.0).clip(lower=0.0)
    generation = _group_columns(generator_p, base.generators.carrier)

    storage_p = run.frame("storage_units", "p").fillna(0.0)
    storage_discharge = _group_columns(
        storage_p.clip(lower=0.0), base.storage_units.carrier
    )
    generation = generation.add(storage_discharge, fill_value=0.0)

    link_p1 = run.frame("links", "p1").fillna(0.0)
    if not link_p1.empty:
        turbine_links = base.links.index[
            base.links.carrier.astype(str).eq("hydro_turbine")
        ].intersection(link_p1.columns)
        if len(turbine_links):
            hydro_output = (-link_p1[turbine_links]).clip(lower=0.0).sum(axis=1)
            generation["hydro"] = generation.get("hydro", 0.0) + hydro_output

    ordered = [carrier for carrier in CARRIER_ORDER if carrier in generation]
    extras = sorted(set(generation.columns) - set(ordered))
    return generation.reindex(columns=ordered + extras, fill_value=0.0)


def generation_by_carrier(run: AnalysisRun) -> pd.DataFrame:
    hourly = generation_by_carrier_timeseries(run)
    energy = hourly.mul(run.weights, axis=0).sum()

    generator_capacity = (
        run.base.generators.assign(
            report_carrier=run.base.generators.carrier.map(canonical_carrier)
        )
        .groupby("report_carrier")["p_nom"]
        .sum()
    )
    storage_capacity = (
        run.base.storage_units.assign(
            report_carrier=run.base.storage_units.carrier.map(canonical_carrier)
        )
        .groupby("report_carrier")["p_nom"]
        .sum()
    )
    capacity = generator_capacity.add(storage_capacity, fill_value=0.0)
    turbine_links = run.base.links.loc[
        run.base.links.carrier.astype(str).eq("hydro_turbine")
    ]
    if not turbine_links.empty:
        hydro_link_capacity = (
            turbine_links.p_nom * turbine_links.efficiency.fillna(1.0)
        ).sum()
        capacity.loc["hydro"] = capacity.get("hydro", 0.0) + hydro_link_capacity
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
    run: AnalysisRun, observed_daily: pd.DataFrame | None
) -> pd.DataFrame:
    """Compare modeled and observed generation over the analyzed period."""
    result = generation_by_carrier(run).rename(
        columns={"generation_gwh": "modeled_generation_gwh"}
    )
    if observed_daily is None:
        return result
    dates = pd.DatetimeIndex(run.snapshots.normalize().unique())
    observed = observed_daily.reindex(dates).sum(min_count=1)
    result["observed_generation_gwh"] = observed.reindex(result.index)
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
    return result


def installed_capacity_by_area(run: AnalysisRun) -> pd.DataFrame:
    """Return modeled generator and storage power capacity by electrical area."""
    base = run.base
    generators = base.generators[["bus", "carrier", "p_nom"]].copy()
    generators["carrier"] = generators.carrier.map(canonical_carrier)
    storage = base.storage_units[["bus", "carrier", "p_nom"]].copy()
    storage["carrier"] = storage.carrier.map(canonical_carrier)
    turbines = base.links.loc[
        base.links.carrier.astype(str).eq("hydro_turbine"),
        ["bus1", "p_nom", "efficiency"],
    ].copy()
    turbines["bus"] = turbines.pop("bus1")
    turbines["carrier"] = "hydro"
    turbines["p_nom"] = turbines.p_nom * turbines.efficiency.fillna(1.0)
    combined = pd.concat(
        [generators, storage, turbines[["bus", "carrier", "p_nom"]]],
        ignore_index=True,
    )
    result = (
        combined.groupby(["bus", "carrier"], as_index=False).p_nom.sum()
        .rename(columns={"bus": "balancing_area", "p_nom": "capacity_mw"})
        .set_index(["balancing_area", "carrier"])
        .sort_index()
    )
    return result


def renewable_generation_by_area(run: AnalysisRun) -> pd.DataFrame:
    """Return solar and wind energy by balancing area for the analyzed period."""
    base = run.base
    dispatch = run.frame("generators", "p").fillna(0.0).clip(lower=0.0)
    metadata = base.generators[["bus", "carrier"]].reindex(dispatch.columns).copy()
    metadata["carrier"] = metadata.carrier.map(canonical_carrier)
    selected = metadata.index[metadata.carrier.isin(RENEWABLE_CARRIERS)]
    if not len(selected):
        return pd.DataFrame(columns=["generation_gwh"])
    energy = dispatch[selected].mul(run.weights, axis=0).sum() / 1_000.0
    rows = metadata.loc[selected].copy()
    rows["generation_gwh"] = energy
    return (
        rows.groupby(["bus", "carrier"])["generation_gwh"]
        .sum()
        .rename_axis(index=["balancing_area", "carrier"])
        .to_frame()
        .sort_index()
    )


def storage_metrics(run: AnalysisRun) -> pd.DataFrame:
    base = run.base
    if base.storage_units.empty:
        return pd.DataFrame()
    dispatch = run.frame("storage_units", "p").reindex(columns=base.storage_units.index).fillna(0.0)
    soc = run.frame("storage_units", "state_of_charge").reindex(columns=base.storage_units.index)
    weights = run.weights
    result = pd.DataFrame(index=base.storage_units.index)
    result.index.name = "storage_unit"
    result["carrier"] = base.storage_units.carrier.map(canonical_carrier)
    result["power_capacity_mw"] = base.storage_units.p_nom
    result["energy_capacity_mwh"] = base.storage_units.p_nom * base.storage_units.max_hours
    result["discharge_gwh"] = dispatch.clip(lower=0.0).mul(weights, axis=0).sum() / 1_000.0
    result["charge_gwh"] = -dispatch.clip(upper=0.0).mul(weights, axis=0).sum() / 1_000.0
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
    dispatch = run.frame("stores", "p").reindex(columns=base.stores.index).fillna(0.0)
    energy = run.frame("stores", "e").reindex(columns=base.stores.index)
    result = pd.DataFrame(index=base.stores.index)
    result.index.name = "store"
    result["carrier"] = base.stores.carrier.map(canonical_carrier)
    result["bus"] = base.stores.bus
    result["energy_capacity_mwh"] = base.stores.e_nom
    result["discharge_gwh"] = dispatch.clip(lower=0.0).mul(run.weights, axis=0).sum() / 1_000.0
    result["charge_gwh"] = -dispatch.clip(upper=0.0).mul(run.weights, axis=0).sum() / 1_000.0
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


def corridor_metrics(run: AnalysisRun) -> pd.DataFrame:
    base = run.base
    flows = run.frame("links", "p0")
    corridors = base.links.index[base.links.carrier.astype(str).eq("AC")].intersection(flows.columns)
    if not len(corridors):
        return pd.DataFrame()
    flow = flows[corridors]
    rating = base.links.loc[corridors, "p_nom"].replace(0.0, np.nan)
    loading = flow.abs().div(rating, axis=1) * 100.0
    result = pd.DataFrame(index=corridors)
    result.index.name = "corridor"
    result["bus0"] = base.links.loc[corridors, "bus0"]
    result["bus1"] = base.links.loc[corridors, "bus1"]
    result["capacity_mw"] = rating
    result["maximum_absolute_flow_mw"] = flow.abs().max()
    result["maximum_loading_pct"] = loading.max()
    result["p95_loading_pct"] = loading.quantile(0.95)
    result["hours_at_or_above_99pct"] = loading.ge(99.0).sum()
    result["net_energy_bus0_to_bus1_gwh"] = flow.mul(run.weights, axis=0).sum() / 1_000.0
    return result.sort_values("maximum_loading_pct", ascending=False)


def _area_timeseries(run: AnalysisRun) -> dict[str, pd.DataFrame]:
    base = run.base
    areas = base.buses.index[base.buses.carrier.astype(str).eq("AC")]
    generator_p = run.frame("generators", "p").fillna(0.0)
    generation = generator_p.T.groupby(base.generators.bus.reindex(generator_p.columns)).sum().T
    storage_p = run.frame("storage_units", "p").fillna(0.0)
    storage = storage_p.T.groupby(base.storage_units.bus.reindex(storage_p.columns)).sum().T
    load_p = run.frame("loads", "p").fillna(0.0)
    if load_p.empty:
        load_p = run.frame("loads", "p_set").fillna(0.0)
    demand = load_p.T.groupby(base.loads.bus.reindex(load_p.columns)).sum().T
    generation = generation.reindex(columns=areas, fill_value=0.0)
    storage = storage.reindex(columns=areas, fill_value=0.0)
    demand = demand.reindex(columns=areas, fill_value=0.0)

    net_import = pd.DataFrame(0.0, index=run.snapshots, columns=areas)
    p0 = run.frame("links", "p0").fillna(0.0)
    p1 = run.frame("links", "p1").fillna(0.0)
    links = base.links.index[base.links.carrier.astype(str).eq("AC")]
    for link in links.intersection(p0.columns):
        bus0 = base.links.at[link, "bus0"]
        bus1 = base.links.at[link, "bus1"]
        if bus0 in net_import:
            net_import[bus0] -= p0[link]
        if bus1 in net_import:
            net_import[bus1] -= p1[link]

    # Non-grid Links include hydro turbines, pumps, and water transfers. Add
    # every electrical-bus injection (positive or negative) so turbine output
    # and pumping demand are both reflected in the local balance.
    non_grid_links = base.links.index[~base.links.carrier.astype(str).eq("AC")]
    bus_columns = sorted(
        column for column in base.links.columns
        if column.startswith("bus") and column[3:].isdigit()
    )
    for bus_column in bus_columns:
        port = bus_column[3:]
        port_flow = run.frame("links", f"p{port}").fillna(0.0)
        for link in non_grid_links.intersection(port_flow.columns):
            bus = base.links.at[link, bus_column]
            if bus in generation.columns:
                generation[bus] -= port_flow[link]

    return {
        "generation": generation,
        "storage": storage,
        "demand": demand,
        "net_import": net_import,
    }


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
    run: AnalysisRun, observed_daily: pd.DataFrame | None
) -> pd.DataFrame:
    modeled = generation_by_carrier_timeseries(run).resample("MS").sum() / 1_000.0
    modeled.index.name = "month"
    modeled_long = modeled.rename_axis(columns="carrier").stack().rename("modeled_gwh").to_frame()
    if observed_daily is None:
        return modeled_long.reset_index().set_index(["month", "carrier"])
    observed = observed_daily.reindex(columns=modeled.columns).resample("MS").sum()
    observed.index.name = "month"
    observed_long = observed.rename_axis(columns="carrier").stack().rename("observed_gwh")
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
