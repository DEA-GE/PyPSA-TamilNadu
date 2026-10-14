"""Audit saved hydro operation against Agriculture data and explicit assumptions.

Reads retained NetCDF checkpoints directly (no solve, no source-model mutation).
Calendar weeks are Monday--Sunday; assumption checks use the actual solve windows.
Run with: python scripts/analyze_hydro_operation.py --run <completed run directory>
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pickle
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import netCDF4
import numpy as np
import pandas as pd
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
CEA_MONTHLY_GENERATION = ROOT / "data/model_inputs/cea_hydro_monthly_generation_FY2025_26.csv"
WATER_MWH_PER_MCFT = 0.0771634 * 100 * 0.9
DIRECT = {
    "Mettur_reservoir": "METTUR", "Periyar_reservoir": "Periyar",
    "Parambikulam": "Parambikulam", "Sholayar": "Sholayar",
    "Papanasam_reservoir": "Papanasam (TN EB Dam)",
}
PROXY = {"Upper_Aliyar": "Parambikulam"}
INFLOW_PROXY_CONTEXT = {"Upper_Kodayar": "Pechiparai"}
# This is a benchmark/source map, not a claim that these constraints were enforced.
BENCHMARKS = {
    "mettur": ("direct Agriculture", "METTUR", "Mettur_reservoir"),
    "periyar": ("direct Agriculture", "Periyar", "Periyar_reservoir"),
    "sholayar": ("direct Agriculture", "Sholayar", "Sholayar"),
    "papanasam": ("upstream Agriculture + assumed pondage", "Papanasam (TN EB Dam)", "Papanasam_reservoir"),
    "servalar": ("upstream Agriculture + assumed reservoir", "Papanasam (TN EB Dam)", "Servalar_reservoir"),
    "sarkarpathy": ("upstream Agriculture + assumed pondage", "Parambikulam", "Parambikulam"),
    "aliyar": ("seasonal proxy only", "Parambikulam", "Upper_Aliyar"),
    "kodayar": ("scaled Pechiparai inflow; assumed initial storage", "Pechiparai", "Upper_Kodayar"),
    "lower_mettur": ("upstream observed release", "METTUR", ""),
    "bhavani_kattalai": ("upstream observed release", "BHAVANISAGAR", ""),
    "kadmparai": ("pumped storage; assumed weekly neutrality", "", "Kadamparai"),
    "kundah": ("modelled cascade; CEA monthly generation benchmark", "", "Upper_Bhavani"),
    "parsons_valley": ("modelled cascade; CEA monthly generation benchmark", "", "Kundah_pondage"),
    "pykara": ("modelled cascade; CEA monthly generation benchmark", "", "Pykara"),
    "pykara_ultimate": ("modelled cascade; CEA monthly generation benchmark", "", "Glenmorgan_forebay"),
    "moyar": ("modelled cascade; CEA monthly generation benchmark", "", "Moyar_pondage"),
    "suruliyar": ("modelled reservoir; CEA monthly generation benchmark", "", "hydro_suruliyar"),
}

# CEA reports individual stations while the model sometimes aggregates a cascade.
# The mapping is deliberately explicit so a future inventory change cannot silently
# turn a station-level observation into a comparison with the wrong model output.
CEA_SYSTEMS_BY_ASSET = {
    "aliyar": ["aliyar"], "bhavani_kattalai": ["bhavani_kattalai", "bhawani_barrage_ii", "bhawani_barrage_iii"],
    "kadmparai": ["kadamparai"], "kodayar": ["kodayar_i", "kodayar_ii"],
    "kundah": ["kundah_i", "kundah_ii", "kundah_iii", "kundah_iv", "kundah_v"],
    "lower_mettur": ["lower_mettur_i", "lower_mettur_ii", "lower_mettur_iii", "lower_mettur_iv"],
    "mettur": ["mettur_dam", "mettur_tunnel"], "moyar": ["moyar"], "papanasam": ["papanasam"],
    "parsons_valley": ["parsons_valley"], "periyar": ["periyar"], "pykara": ["pykara"],
    "pykara_ultimate": ["pykara_ultimate"], "sarkarpathy": ["sarkarpathy"], "sholayar": ["sholayar"],
    "suruliyar": ["suruliyar"],
}


def static(ds, component):
    idx = f"{component}_i"
    names = ds[idx].values.astype(str)
    return pd.DataFrame({key[len(component) + 1:]: ds[key].values
                         for key in ds.data_vars
                         if key.startswith(component + "_") and ds[key].dims == (idx,)}, index=names)


def frame(ds, component, attribute, columns, default=0.0):
    key = f"{component}_t_{attribute}"
    dates = pd.DatetimeIndex(ds.snapshots_snapshot.values)
    if key not in ds:
        return pd.DataFrame(default, index=dates, columns=columns)
    result = ds[key].to_pandas()
    result.index = dates
    result.columns = result.columns.astype(str)
    return result.reindex(columns=columns, fill_value=default).fillna(default)


def available(ds, generators, dynamic_generators):
    result = pd.DataFrame(np.tile(generators.get("p_max_pu", pd.Series(1.0, index=generators.index)),
                                 (ds.sizes["snapshots"], 1)),
                          index=pd.DatetimeIndex(ds.snapshots_snapshot.values), columns=generators.index)
    # PyPSA's exporter omits a dynamic column when all retained values equal
    # the attribute default (1), even when its static value is different (0).
    # Recover those default-only windows using the full run's dynamic schema.
    result.loc[:, result.columns.intersection(dynamic_generators)] = 1.0
    if "generators_t_p_max_pu" in ds:
        dyn = ds.generators_t_p_max_pu.to_pandas()
        dyn.index = result.index
        result.loc[:, dyn.columns] = dyn
    return result.mul(generators.p_nom)


def week_id(index):
    dates = pd.DatetimeIndex(index)
    return (dates.normalize() - pd.to_timedelta(dates.weekday, unit="D")).rename("week_monday")


def weekly_sum(df):
    return df.groupby(week_id(df.index)).sum(min_count=1)


def save(df, name, out):
    df.to_csv(out / f"{name}.csv", index=False, float_format="%.9g")


def cea_monthly_comparison(group_daily):
    """Return explicitly mapped CEA station totals beside model plant-group output."""
    if not CEA_MONTHLY_GENERATION.exists():
        raise FileNotFoundError(f"Missing CEA monthly generation input: {CEA_MONTHLY_GENERATION}")
    cea = pd.read_csv(CEA_MONTHLY_GENERATION, parse_dates=["report_month"])
    expected = {station for stations in CEA_SYSTEMS_BY_ASSET.values() for station in stations}
    unknown = expected.difference(cea.hydro_system.unique())
    if unknown:
        raise ValueError(f"CEA mapping contains systems absent from input: {sorted(unknown)}")
    model_monthly = group_daily.resample("MS").sum()
    rows = []
    for asset, stations in CEA_SYSTEMS_BY_ASSET.items():
        if asset not in model_monthly:
            continue
        observed = cea.loc[cea.hydro_system.isin(stations)].groupby("report_month").agg(
            cea_generation_gwh=("monthly_generation_gwh", "sum"),
            cea_source_files=("source_file", lambda x: ";".join(sorted(x.unique()))),
        )
        comparison = pd.DataFrame({"month": model_monthly.index, "model_generation_gwh": model_monthly[asset].values})
        comparison = comparison.merge(observed, left_on="month", right_index=True, how="inner", validate="one_to_one")
        comparison["asset_id"] = asset
        comparison["cea_systems"] = ";".join(stations)
        comparison["difference_gwh"] = comparison.model_generation_gwh - comparison.cea_generation_gwh
        comparison["difference_pct"] = 100 * comparison.difference_gwh / comparison.cea_generation_gwh.replace(0, np.nan)
        rows.append(comparison)
    result = pd.concat(rows, ignore_index=True)
    result = result[["month", "asset_id", "cea_systems", "model_generation_gwh", "cea_generation_gwh",
                     "difference_gwh", "difference_pct", "cea_source_files"]].sort_values(["asset_id", "month"])
    annual = result.groupby(["asset_id", "cea_systems"], as_index=False).agg(
        months_compared=("month", "size"), model_generation_gwh=("model_generation_gwh", "sum"),
        cea_generation_gwh=("cea_generation_gwh", "sum"), difference_gwh=("difference_gwh", "sum"))
    annual["difference_pct"] = 100 * annual.difference_gwh / annual.cea_generation_gwh.replace(0, np.nan)
    return result, annual


def load_run(run):
    files = sorted((run / "weekly_networks").glob("window_*.nc"))
    if not files:
        raise ValueError("A completed rolling run with retained weekly checkpoints is required")
    signature = hashlib.sha256(("v2|" + "|".join(f"{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in files)).encode()).hexdigest()[:20]
    cache = ROOT / ".tmp" / f"hydro_analysis_{signature}.pkl"
    if cache.exists():
        with cache.open("rb") as handle:
            return pickle.load(handle)
    parts = {key: [] for key in ["generation", "gen_available", "gen_dispatch", "e", "opening_e", "p0", "p1", "p2", "su_soc"]}
    static_tables = None
    windows, handoffs, file_hashes = [], [], []
    prev_end = None
    dynamic_generators = set()
    for file in files:
        with netCDF4.Dataset(file) as nc:
            if "generators_t_p_max_pu_i" in nc.variables:
                dynamic_generators.update(nc["generators_t_p_max_pu_i"][:])
    for number, file in enumerate(files, 1):
        # Direct materialization avoids repeated backend/plugin discovery for 53 files.
        with netCDF4.Dataset(file) as nc:
            variables = {}
            for name, variable in nc.variables.items():
                value = variable[:]
                if name == "snapshots_snapshot":
                    value = pd.DatetimeIndex(netCDF4.num2date(value, variable.units,
                        only_use_cftime_datetimes=False)).values
                variables[name] = (variable.dimensions, value)
            ds = xr.Dataset(variables, attrs={k: nc.getncattr(k) for k in nc.ncattrs()})
        with ds:
            tables = {c: static(ds, c) for c in ["generators", "links", "stores", "storage_units"]}
            if static_tables is None:
                static_tables = tables
            else:
                for c, col in [("generators", "p_nom"), ("links", "p_nom"), ("stores", "e_nom")]:
                    pd.testing.assert_series_equal(tables[c][col], static_tables[c][col])
            g, l, s, su = [tables[k] for k in ["generators", "links", "stores", "storage_units"]]
            idx = pd.DatetimeIndex(ds.snapshots_snapshot.values)
            assert np.allclose(ds.snapshots_generators, 1) and np.allclose(ds.snapshots_stores, 1)
            gp = frame(ds, "generators", "p", g.index)
            lp = {port: frame(ds, "links", port, l.index) for port in ["p0", "p1", "p2"]}
            e = frame(ds, "stores", "e", s.index)
            opening = e.shift(1)
            opening.iloc[0] = s.e_initial
            if prev_end is not None:
                handoffs.append(float((s.e_initial - prev_end).abs().max()))
            prev_end = e.iloc[-1]
            hydro_g = g.index[g.carrier.isin(["hydro", "hydro_run_of_river", "small_hydro"])]
            hydro_l = l.index[l.carrier.eq("hydro_turbine")]
            generation = pd.concat([gp[hydro_g], -lp["p1"][hydro_l], frame(ds, "storage_units", "p", su.index)], axis=1)
            for k, v in {"generation": generation, "gen_available": available(ds, g, dynamic_generators), "gen_dispatch": gp,
                         "e": e, "opening_e": opening, **lp,
                         "su_soc": frame(ds, "storage_units", "state_of_charge", su.index)}.items():
                parts[k].append(v)
            windows.append({"window": number, "start": idx[0], "end": idx[-1], "hours": len(idx)})
        file_hashes.append({"path": str(file.relative_to(ROOT)), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
        if number % 10 == 0:
            print(f"Read {number}/{len(files)} checkpoints", flush=True)
    result = {k: pd.concat(v).sort_index() for k, v in parts.items()}
    idx = result["e"].index
    assert idx.is_unique and idx.equals(pd.date_range(idx[0], idx[-1], freq="h"))
    if len(idx) != 8760:
        raise ValueError(f"Expected a full year of retained hours, found {len(idx)}")
    loaded = result, static_tables, pd.DataFrame(windows), max(handoffs, default=0), file_hashes
    cache.parent.mkdir(exist_ok=True)
    with cache.open("wb") as handle:
        pickle.dump(loaded, handle)
    return loaded


def analyze(run, out):
    out.mkdir(parents=True, exist_ok=True)
    figures = out / "figures"
    figures.mkdir(exist_ok=True)
    data, tables, windows, handoff_error, hashes = load_run(run)
    print("Loaded 53 retained checkpoints", flush=True)
    g, l, s, su = [tables[k] for k in ["generators", "links", "stores", "storage_units"]]
    generation = data["generation"]
    audit = pd.read_csv(ROOT / "model/hydro_capacity_validation.csv")
    audit = audit.loc[audit.model_component_type.str.contains("turbine|run-of-river", regex=True)].copy()
    assert audit.model_component.is_unique
    audit = audit.set_index("model_component")
    small = g.index[g.carrier.eq("small_hydro")]
    assert set(generation.columns) == set(audit.index) | set(small)
    audit["plant"] = audit.official_component_id.str.replace(r"__unit_\d+$", "", regex=True)
    audit.loc[audit.index == "hydro_suruliyar", "plant"] = "suruliyar"
    audit.loc[audit.index.str.startswith("kodayar_I__"), "plant"] = "kodayar_I"
    audit.loc[audit.index.str.startswith("kodayar_II__"), "plant"] = "kodayar_II"
    unit_rows = []
    for unit in generation:
        power = generation[unit]
        if unit in audit.index:
            row = audit.loc[unit]
            capacity = float(row.model_p_nom_mw)
            asset, plant = row.asset_id, row.plant
            kind, reservoir, store = BENCHMARKS[asset]
        else:
            capacity, asset, plant = float(g.at[unit, "p_nom"]), "small_hydro", unit.split("__unit_")[0]
            kind, reservoir, store = "generic availability profile; no plant observation", "", ""
        unit_rows.append({"component": unit, "plant": plant, "asset_id": asset, "capacity_mw": capacity,
                          "generation_gwh": power.sum() / 1000, "capacity_factor_pct": 100 * power.mean() / capacity,
                          "full_load_hours": power.sum() / capacity, "operating_hours": int((power > 1e-5).sum()),
                          "maximum_mw": power.max(), "benchmark_kind": kind, "agriculture_reservoir": reservoir,
                          "benchmark_store": store, "plant_observed_generation_gwh": np.nan})
    units = pd.DataFrame(unit_rows)
    units["availability_gwh_if_generator"] = units.component.map(data["gen_available"].sum() / 1000)
    units["availability_utilization_pct"] = 100 * units.generation_gwh / units.availability_gwh_if_generator
    meta = units.set_index("component")
    conv = meta.index[meta.asset_id.ne("small_hydro")]
    daily = generation.resample("D").sum() / 1000
    group_hourly = generation.T.groupby(meta.asset_id).sum().T
    group_daily = daily.T.groupby(meta.asset_id).sum().T
    cea_monthly, cea_annual = cea_monthly_comparison(group_daily)
    save(cea_monthly, "cea_monthly_generation_comparison", out)
    save(cea_annual, "cea_annual_generation_comparison", out)
    modeled = group_daily.drop(columns="small_hydro", errors="ignore").sum(axis=1)
    supplied = pd.read_csv(run / "modeled_daily_generation.csv", index_col=0, parse_dates=True).hydro
    recon_error = float((modeled - supplied).abs().max())
    assert recon_error < 1e-6, recon_error
    observed = pd.read_csv(run / "observed_daily_generation.csv", index_col=0, parse_dates=True).hydro.reindex(modeled.index)
    assert observed.notna().all()
    fleet_daily = pd.DataFrame({"modeled_gwh": modeled, "observed_gwh": observed})
    fleet_daily["pumped_generation_gwh"] = group_daily.kadmparai
    pump_names = l.index[l.carrier.eq("hydro_pump")]
    # Positive p2 is electrical consumption for this three-port pump.
    pump = data["p2"][pump_names].sum(axis=1)
    fleet_daily["pumping_consumption_gwh"] = pump.resample("D").sum() / 1000
    fleet_daily["conventional_excluding_pumped_gwh"] = modeled - group_daily.kadmparai
    fleet_daily["net_hydro_gwh"] = modeled - fleet_daily.pumping_consumption_gwh
    fleet_daily["small_hydro_separate_gwh"] = group_daily.get("small_hydro", 0)
    fleet_daily["difference_gwh"] = modeled - observed
    weekly = weekly_sum(fleet_daily)
    weekly["difference_pct"] = 100 * weekly.difference_gwh / weekly.observed_gwh
    annual_multiplier = observed.sum() / modeled.sum()
    weekly["annual_scaled_modeled_gwh_diagnostic"] = weekly.modeled_gwh * annual_multiplier
    weekly["annual_scaled_difference_gwh_diagnostic"] = weekly.annual_scaled_modeled_gwh_diagnostic - weekly.observed_gwh
    weekly["days"] = fleet_daily.groupby(week_id(fleet_daily.index)).size()
    weekly["actual_start"] = fleet_daily.groupby(week_id(fleet_daily.index)).apply(lambda x: x.index.min()).values
    weekly["actual_end"] = fleet_daily.groupby(week_id(fleet_daily.index)).apply(lambda x: x.index.max()).values
    monthly = fleet_daily.resample("MS").sum()
    monthly["difference_pct"] = 100 * monthly.difference_gwh / monthly.observed_gwh
    monthly["annual_scaled_difference_pct_diagnostic"] = 100 * (monthly.modeled_gwh * annual_multiplier / monthly.observed_gwh - 1)
    save(fleet_daily.rename_axis("date").reset_index(), "fleet_daily", out)
    save(weekly.reset_index(), "fleet_weekly", out)
    save(monthly.rename_axis("month").reset_index(), "fleet_monthly", out)
    save(units, "unit_annual", out)
    group_stats = units.groupby("asset_id").agg(capacity_mw=("capacity_mw", "sum"), generation_gwh=("generation_gwh", "sum"),
        benchmark_kind=("benchmark_kind", "first"), agriculture_reservoir=("agriculture_reservoir", "first"),
        benchmark_store=("benchmark_store", "first"))
    group_stats["capacity_factor_pct"] = group_stats.generation_gwh * 1e5 / (group_stats.capacity_mw * len(generation))
    group_stats["full_load_hours"] = group_stats.generation_gwh * 1000 / group_stats.capacity_mw
    group_stats["weeks_zero_generation"] = (weekly_sum(group_daily).abs() < 1e-7).sum()
    group_stats["weeks_operating"] = 53 - group_stats.weeks_zero_generation
    group_stats["last_operating_hour"] = pd.Series({name: values[values > 1e-5].index.max() for name, values in group_hourly.items()})
    save(group_stats.reset_index(), "asset_annual", out)
    plant_stats = units.groupby(["asset_id", "plant"]).agg(capacity_mw=("capacity_mw", "sum"), generation_gwh=("generation_gwh", "sum"))
    plant_stats["capacity_factor_pct"] = plant_stats.generation_gwh * 1e5 / (8760 * plant_stats.capacity_mw)
    save(plant_stats.reset_index(), "plant_annual", out)
    unit_week = weekly_sum(daily).rename_axis(columns="component").stack().rename("generation_gwh").reset_index()
    unit_week = unit_week.merge(units.drop(columns=["generation_gwh", "capacity_factor_pct", "full_load_hours", "operating_hours", "maximum_mw", "availability_gwh_if_generator", "availability_utilization_pct"]), on="component", validate="many_to_one")
    availability_week = (weekly_sum(data["gen_available"].reindex(columns=generation.columns.intersection(g.index))) / 1000).rename_axis(columns="component").stack().rename("availability_gwh_if_generator").reset_index()
    unit_week = unit_week.merge(availability_week, on=["week_monday", "component"], how="left", validate="one_to_one")
    unit_week["availability_utilization_pct"] = 100 * unit_week.generation_gwh / unit_week.availability_gwh_if_generator.replace(0, np.nan)
    unit_week["hours"] = unit_week.week_monday.map(weekly.days) * 24
    unit_week["capacity_factor_pct"] = unit_week.generation_gwh * 1e5 / (unit_week.capacity_mw * unit_week.hours)
    save(unit_week, "unit_weekly", out)
    plant_week = unit_week.groupby(["week_monday", "asset_id", "plant"], as_index=False).agg(
        generation_gwh=("generation_gwh", "sum"), capacity_mw=("capacity_mw", "sum"), hours=("hours", "first"),
        benchmark_kind=("benchmark_kind", "first"), agriculture_reservoir=("agriculture_reservoir", "first"))
    plant_week["capacity_factor_pct"] = plant_week.generation_gwh * 1e5 / (plant_week.capacity_mw * plant_week.hours)

    raw = pd.read_csv(ROOT / "data/reservoirs/tn_reservoir_daily_FY2025_26.csv", parse_dates=["date"])
    raw = raw.loc[raw.model_ready.astype(str).str.lower().eq("true")].copy()
    assert not raw.duplicated(["date", "reservoir"]).any()
    raw["week_monday"] = week_id(raw.date).values
    raw["storage_fraction"] = raw.current_storage_mcft / raw.full_capacity_mcft
    raw["inflow_mcft"] = raw.current_inflow_cusec * .0864
    raw["outflow_mcft"] = raw.current_outflow_cusec * .0864
    # Use the scraper's time-varying fields for the full-payload repeat audit.
    # The scraper used to expose a single hard-coded fallback fingerprint, but
    # now detects historical-page fallbacks from repeated complete payloads.
    scraper_tree = ast.parse((ROOT / "scripts/scrape_tn_reservoirs.py").read_text(encoding="utf-8"))
    constants = {node.targets[0].id: ast.literal_eval(node.value) for node in scraper_tree.body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id == "TIME_VARYING_FIELDS"}
    snapshot_fields = list(constants["TIME_VARYING_FIELDS"])
    exact_fingerprint_dates = []
    full_snapshot = raw.pivot(index="date", columns="reservoir", values=snapshot_fields)
    snapshot_hash = pd.util.hash_pandas_object(full_snapshot, index=False)
    repeat_counts = snapshot_hash.map(snapshot_hash.value_counts())
    # Whole 19-reservoir repeats are flagged for review, not automatically
    # treated as false readings. The local audit independently flags these dates.
    bad_dates = sorted(snapshot_hash.index[repeat_counts > 1])
    raw["suspect_repeated_snapshot"] = raw.date.isin(bad_dates)
    suspect_dates = pd.DataFrame({"date": bad_dates, "reason": "identical full 19-reservoir operating snapshot on multiple dates; corroborated by local suspicious-snapshot audit"})
    # Retained for report-schema compatibility.  The current scraper has no
    # fixed fallback fingerprint, so all matches are detected by repetition.
    suspect_dates["exact_current_fallback_fingerprint_match"] = False
    local_audit = pd.read_csv(ROOT / "data/reservoirs/tn_reservoir_suspicious_snapshots_FY2025_26.csv", parse_dates=["date"])
    suspect_dates["listed_in_repository_suspicious_audit"] = suspect_dates.date.isin(local_audit.date)
    suspect_dates["source_url"] = suspect_dates.date.map(
        lambda date: f"https://tnagriculture.in/ARS/home/reservoir/{date:%Y-%m-%d}"
    )
    save(suspect_dates, "suspect_agriculture_dates", out)
    # Independent reconstruction verifies compressed NetCDF availability defaults.
    profile_errors = []
    for asset in ["lower_mettur", "bhavani_kattalai"]:
        names = meta.index[meta.asset_id.eq(asset)]
        source = BENCHMARKS[asset][1]
        rr = raw.loc[raw.reservoir.eq(source)].set_index("date")
        expected = (rr.outflow_mcft.fillna(0) * WATER_MWH_PER_MCFT / 24).clip(upper=meta.loc[names].capacity_mw.sum())
        actual = data["gen_available"][names].sum(axis=1)
        profile_errors.append(float((actual - actual.index.normalize().map(expected)).abs().max()))
        if profile_errors[-1] >= 1e-5:
            raise ValueError(
                f"Retained run availability for {asset} differs from the current "
                f"reservoir input by up to {profile_errors[-1]:.6g} MW. "
                "Use a run generated from the current model inputs, or regenerate "
                "the retained checkpoints before running this analysis."
            )
    for store, source in DIRECT.items():
        names = g.index[g.carrier.eq("water_inflow") & g.bus.eq(s.at[store, "bus"])]
        rr = raw.loc[raw.reservoir.eq(source)].set_index("date")
        expected = rr.inflow_mcft.fillna(0) * WATER_MWH_PER_MCFT / 24
        actual = data["gen_available"][names].sum(axis=1)
        profile_errors.append(float((actual - actual.index.normalize().map(expected)).abs().max()))
        if profile_errors[-1] >= 1e-5:
            raise ValueError(
                f"Retained run inflow availability for {store} differs from the current "
                f"reservoir input by up to {profile_errors[-1]:.6g} MW. "
                "Use a run generated from the current model inputs, or regenerate "
                "the retained checkpoints before running this analysis."
            )
    quality_rows, ag_rows = [], []
    for reservoir, rr in raw.groupby("reservoir"):
        rr = rr.sort_values("date").set_index("date")
        future_stock = rr.current_storage_mcft.shift(-1)
        residual = future_stock - rr.current_storage_mcft - rr.inflow_mcft + rr.outflow_mcft
        reliable_intervals = ~rr.suspect_repeated_snapshot & ~rr.suspect_repeated_snapshot.shift(-1, fill_value=True)
        quality_rows.append({"reservoir": reservoir, "days": len(rr), "missing_flow_days": int(rr[["inflow_mcft", "outflow_mcft"]].isna().any(axis=1).sum()),
            "quality_flags": "; ".join(sorted(rr.quality_flag.dropna().unique())),
            "annual_inflow_mcft": rr.inflow_mcft.sum(min_count=1), "annual_outflow_mcft": rr.outflow_mcft.sum(min_count=1),
            "storage_first_mcft": rr.current_storage_mcft.iloc[0], "storage_last_mcft": rr.current_storage_mcft.iloc[-1],
            "daily_balance_residual_mae_mcft": residual.abs().mean(),
            "balance_residual_364_intervals_mcft": residual.sum(min_count=1),
            "suspect_snapshot_days": int(rr.suspect_repeated_snapshot.sum()),
            "daily_balance_mae_excluding_suspect_adjacent_intervals_mcft": residual[reliable_intervals].abs().mean(),
            "usable_complete_balance_intervals": int(residual[reliable_intervals].count()),
            "source_url_example": rr.source_url.iloc[0]})
        for week, rows in rr.groupby("week_monday"):
            next_date = rows.index[-1] + pd.Timedelta(days=1)
            start_stock = rows.current_storage_mcft.iloc[0]
            end_stock = rr.current_storage_mcft.get(next_date, np.nan)
            ag_rows.append({"week_monday": week, "agriculture_reservoir": reservoir, "agriculture_days": len(rows),
                "suspect_agriculture_days": int(rows.suspect_repeated_snapshot.sum()),
                "inflow_reporting_days": int(rows.inflow_mcft.count()), "outflow_reporting_days": int(rows.outflow_mcft.count()),
                "agriculture_inflow_mcft": rows.inflow_mcft.sum(min_count=len(rows)), "agriculture_outflow_mcft": rows.outflow_mcft.sum(min_count=len(rows)),
                "agriculture_storage_start_mcft": start_stock, "agriculture_storage_end_next_date_mcft": end_stock,
                "agriculture_mean_storage_pct": 100 * rows.storage_fraction.mean(),
                "agriculture_inflow_minus_outflow_mcft": rows.inflow_mcft.sum(min_count=len(rows)) - rows.outflow_mcft.sum(min_count=len(rows)),
                "agriculture_balance_residual_mcft": end_stock - start_stock - rows.inflow_mcft.sum(min_count=len(rows)) + rows.outflow_mcft.sum(min_count=len(rows)),
                "source_url_start": rows.source_url.iloc[0], "source_url_end": rows.source_url.iloc[-1]})
    ag_week = pd.DataFrame(ag_rows)
    weekly["suspect_agriculture_days"] = pd.Series(pd.DatetimeIndex(bad_dates)).groupby(week_id(bad_dates)).size().reindex(weekly.index, fill_value=0)
    save(weekly.reset_index(), "fleet_weekly", out)
    save(pd.DataFrame(quality_rows), "agriculture_source_quality", out)
    save(ag_week, "agriculture_weekly_all_reservoirs", out)
    plant_week = plant_week.merge(ag_week, on=["week_monday", "agriculture_reservoir"], how="left", validate="many_to_one")
    save(plant_week, "plant_weekly", out)

    # Daily storage comparison uses model opening storage vs same-date reading.
    # The source does not give an exact reading time, so sub-daily phase is uncertain.
    open_daily = data["opening_e"].resample("D").first()
    e_daily = data["e"].resample("D").last()
    reservoir_daily, store_week = [], []
    natural = g.index[g.carrier.eq("water_inflow")]
    balance_sources = g.index[g.carrier.eq("water_balance_adjustment")]
    balance_outlets = l.index[l.carrier.eq("water_balance_adjustment")]
    for store, st in s.iterrows():
        bus = st.bus
        gens = natural[g.loc[natural, "bus"].eq(bus)]
        adjustment_gens = balance_sources[g.loc[balance_sources, "bus"].eq(bus)]
        adjustment_links = balance_outlets[l.loc[balance_outlets, "bus0"].eq(bus)]
        available_in = data["gen_available"][gens].sum(axis=1)
        dispatched_in = data["gen_dispatch"][gens].sum(axis=1)
        adjustment_in = data["gen_dispatch"][adjustment_gens].sum(axis=1)
        adjustment_out = data["p0"][adjustment_links].sum(axis=1)
        outgoing = data["p0"][l.index[l.bus0.eq(bus)]].sum(axis=1)
        incoming = -data["p1"][l.index[l.bus1.eq(bus)]].sum(axis=1) - data["p2"][l.index[l.bus2.eq(bus)]].sum(axis=1)
        daily_store = pd.DataFrame({"model_opening_mwh": open_daily[store], "model_closing_mwh": e_daily[store],
            "model_inflow_available_mwh": available_in.resample("D").sum(),
            "model_inflow_used_mwh": dispatched_in.resample("D").sum(),
            "model_balancing_inflow_mwh": adjustment_in.resample("D").sum(),
            "model_balancing_outflow_mwh": adjustment_out.resample("D").sum(),
            "model_outgoing_mwh": outgoing.resample("D").sum(), "model_incoming_routed_mwh": incoming.resample("D").sum()})
        daily_store["model_inflow_discarded_mwh"] = daily_store.model_inflow_available_mwh - daily_store.model_inflow_used_mwh
        daily_store["model_balance_residual_mwh"] = daily_store.model_closing_mwh - daily_store.model_opening_mwh - daily_store.model_inflow_used_mwh - daily_store.model_balancing_inflow_mwh - daily_store.model_incoming_routed_mwh + daily_store.model_outgoing_mwh
        source = DIRECT.get(store, PROXY.get(store, INFLOW_PROXY_CONTEXT.get(store, "")))
        source_kind = ("direct" if store in DIRECT else "proxy" if store in PROXY
                       else "inflow_proxy_only" if store in INFLOW_PROXY_CONTEXT
                       else "assumption_or_sink")
        if source:
            rr = raw.loc[raw.reservoir.eq(source)].set_index("date").reindex(daily_store.index)
            daily_store["agriculture_storage_pct"] = 100 * rr.storage_fraction
            daily_store["agriculture_inflow_mcft"] = rr.inflow_mcft
            daily_store["agriculture_outflow_mcft"] = rr.outflow_mcft
            daily_store["agriculture_outflow_equiv_mwh"] = rr.outflow_mcft * WATER_MWH_PER_MCFT
        daily_store["model_opening_pct"] = daily_store.model_opening_mwh / st.e_nom * 100
        daily_store["storage_error_pp"] = daily_store.model_opening_pct - daily_store.get("agriculture_storage_pct", np.nan)
        if store in INFLOW_PROXY_CONTEXT:
            daily_store["storage_error_pp"] = np.nan
        daily_store["store"] = store
        daily_store["suspect_repeated_snapshot"] = daily_store.index.isin(bad_dates)
        daily_store["benchmark_kind"] = source_kind
        daily_store["agriculture_reservoir"] = source
        reservoir_daily.append(daily_store.rename_axis("date").reset_index())
        for week, rr in daily_store.groupby(week_id(daily_store.index)):
            row = {"week_monday": week, "store": store, "benchmark_kind": source_kind, "agriculture_reservoir": source,
                "days": len(rr), "capacity_mwh": st.e_nom,
                "opening_mwh": rr.model_opening_mwh.iloc[0], "closing_mwh": rr.model_closing_mwh.iloc[-1],
                "storage_change_mwh": rr.model_closing_mwh.iloc[-1] - rr.model_opening_mwh.iloc[0],
                "model_mean_opening_pct": rr.model_opening_pct.mean(), "mean_storage_error_pp": rr.storage_error_pp.mean(),
                "storage_mae_pp": rr.storage_error_pp.abs().mean(),
                "suspect_agriculture_days": int(rr.suspect_repeated_snapshot.sum()) if source else 0,
                "storage_mae_excluding_suspect_pp": rr.loc[~rr.suspect_repeated_snapshot, "storage_error_pp"].abs().mean(),
                "inflow_available_mwh": rr.model_inflow_available_mwh.sum(), "inflow_used_mwh": rr.model_inflow_used_mwh.sum(),
                "inflow_discarded_mwh": rr.model_inflow_discarded_mwh.sum(), "outgoing_mwh": rr.model_outgoing_mwh.sum(),
                "balancing_inflow_mwh": rr.model_balancing_inflow_mwh.sum(),
                "balancing_outflow_mwh": rr.model_balancing_outflow_mwh.sum(),
                "incoming_routed_mwh": rr.model_incoming_routed_mwh.sum(),
                "max_abs_balance_residual_mwh": rr.model_balance_residual_mwh.abs().max()}
            if source_kind == "direct":
                row["modeled_outgoing_mcft"] = row["outgoing_mwh"] / WATER_MWH_PER_MCFT
                row["agriculture_outflow_mcft"] = rr.agriculture_outflow_mcft.sum(min_count=len(rr))
                row["release_error_mcft"] = row["modeled_outgoing_mcft"] - row["agriculture_outflow_mcft"]
            store_week.append(row)
    reservoir_daily = pd.concat(reservoir_daily, ignore_index=True)
    store_week = pd.DataFrame(store_week)
    save(reservoir_daily, "reservoir_daily", out)
    save(store_week, "reservoir_weekly", out)
    store_annual = store_week.groupby("store").agg(benchmark_kind=("benchmark_kind", "first"),
        agriculture_reservoir=("agriculture_reservoir", "first"), inflow_available_mwh=("inflow_available_mwh", "sum"),
        inflow_used_mwh=("inflow_used_mwh", "sum"), inflow_discarded_mwh=("inflow_discarded_mwh", "sum"),
        balancing_inflow_mwh=("balancing_inflow_mwh", "sum"),
        balancing_outflow_mwh=("balancing_outflow_mwh", "sum"),
        outgoing_mwh=("outgoing_mwh", "sum"), opening_mwh=("opening_mwh", "first"), closing_mwh=("closing_mwh", "last"))
    store_annual["storage_mae_pp"] = reservoir_daily.groupby("store").storage_error_pp.apply(lambda x: x.abs().mean())
    store_annual["storage_mae_excluding_suspect_pp"] = reservoir_daily.loc[~reservoir_daily.suspect_repeated_snapshot].groupby("store").storage_error_pp.apply(lambda x: x.abs().mean())
    save(store_annual.reset_index(), "reservoir_annual", out)
    # Assumption checks use actual 7-day accepted windows, not calendar weeks.
    parameters = pd.read_csv(ROOT / "model/hydro_reservoir_parameters.csv").set_index("pypsa_component")
    boundary_rows = []
    for _, window in windows.iterrows():
        for store in [*s.index, *su.index]:
            if store not in parameters.index:
                # Runtime-only water-balance sink is an accounting component,
                # not a reservoir with a seasonal storage assumption.
                continue
            if store in s.index:
                initial, final = data["opening_e"].at[window.start, store], data["e"].at[window.end, store]
                cap = s.at[store, "e_nom"]
            else:
                initial = 0.0 if window.window == 1 else data["su_soc"].at[window.start - pd.Timedelta(hours=1), store]
                final = data["su_soc"].at[window.end, store]
                cap = su.at[store, "p_nom"] * su.at[store, "max_hours"]
            par = parameters.loc[store]
            testable = par.terminal_soc_method in ["end_equals_start", "explicit_assumed_target", "monthly_interpolated_tolerance"]
            boundary_rows.append({"window": window.window, "start": window.start, "end": window.end,
                "store": store, "source": par.soc_source, "documented_terminal_method": par.terminal_soc_method,
                "opening_mwh": initial, "closing_mwh": final, "change_mwh": final - initial,
                "change_pct_capacity": 100 * (final - initial) / cap,
                "neutral_benchmark_applicable": testable,
                "neutrality_error_mwh": final - initial if testable else np.nan,
                "neutrality_pass_1kwh": abs(final - initial) <= .001 if testable else None,
                "monthly_series_available": (ROOT / "model/hydro_monthly_stored_energy.csv").exists()})
    boundaries = pd.DataFrame(boundary_rows)
    save(boundaries, "assumption_checks_by_solve_window", out)

    asset_week = weekly_sum(group_daily).rename_axis(columns="asset_id").stack().rename("generation_gwh").reset_index()
    asset_week = asset_week.merge(group_stats.drop(columns=["generation_gwh", "capacity_factor_pct", "full_load_hours", "weeks_zero_generation", "weeks_operating", "last_operating_hour"]), on="asset_id")
    asset_week["days"] = asset_week.week_monday.map(weekly.days)
    asset_week["capacity_factor_pct"] = asset_week.generation_gwh * 1e5 / (asset_week.capacity_mw * asset_week.days * 24)
    asset_week = asset_week.merge(ag_week, on=["week_monday", "agriculture_reservoir"], how="left", validate="many_to_one")
    asset_week = asset_week.merge(store_week[["week_monday", "store", "opening_mwh", "closing_mwh", "storage_change_mwh", "mean_storage_error_pp", "storage_mae_pp"]],
                                  left_on=["week_monday", "benchmark_store"], right_on=["week_monday", "store"], how="left", validate="many_to_one")
    asset_week["assumed_neutral_storage_error_mwh"] = np.where(asset_week.agriculture_reservoir.fillna("").eq("") & asset_week.benchmark_store.fillna("").ne(""), asset_week.storage_change_mwh, np.nan)
    asset_week.loc[asset_week.asset_id.eq("suruliyar"), ["opening_mwh", "closing_mwh", "storage_change_mwh", "assumed_neutral_storage_error_mwh"]] = 0.0
    save(asset_week, "asset_weekly_benchmarks", out)
    # Repeat shared storage benchmarks at plant level for convenient filtering.
    # These reservoir totals must not be summed across plants sharing a reservoir.
    plant_week = plant_week.merge(asset_week[["week_monday", "asset_id", "benchmark_store", "opening_mwh", "closing_mwh", "storage_change_mwh", "storage_mae_pp", "assumed_neutral_storage_error_mwh"]],
                                  on=["week_monday", "asset_id"], how="left", validate="many_to_one")
    save(plant_week, "plant_weekly", out)

    # ROR release-derived availability is a potential benchmark, not plant generation observation.
    ror_rows = []
    for asset in ["lower_mettur", "bhavani_kattalai"]:
        names = meta.index[meta.asset_id.eq(asset)]
        avail = weekly_sum(data["gen_available"][names].sum(axis=1).to_frame("available_gwh")) / 1000
        actual = weekly_sum(group_daily[[asset]])[asset]
        for week in avail.index:
            a, b = avail.at[week, "available_gwh"], actual.at[week]
            ror_rows.append({"week_monday": week, "asset_id": asset, "release_derived_available_gwh": a,
                "generation_gwh": b, "unused_potential_gwh": a - b, "utilization_pct": 100 * b / a if a else np.nan})
    ror = pd.DataFrame(ror_rows)
    assert ror.unused_potential_gwh.min() > -1e-7
    save(ror, "run_of_river_weekly", out)
    # Direct-reservoir head-only diagnostics: annual release equivalent capped by nameplate.
    sensitivity = []
    for asset, store in [("mettur", "Mettur_reservoir"), ("periyar", "Periyar_reservoir"), ("sholayar", "Sholayar"), ("papanasam", "Papanasam_reservoir")]:
        rr = raw.loc[raw.reservoir.eq(DIRECT[store])].set_index("date")
        capacity = group_stats.at[asset, "capacity_mw"]
        for head_multiplier in [.5, 1, 2, 3]:
            potential = np.minimum(rr.outflow_mcft * WATER_MWH_PER_MCFT * head_multiplier, capacity * 24).sum() / 1000
            sensitivity.append({"asset_id": asset, "assumed_head_m": 100 * head_multiplier,
                "all_release_turbined_daily_cap_gwh": potential,
                "actual_modeled_generation_gwh": group_stats.at[asset, "generation_gwh"],
                "interpretation": "illustrative potential only; all releases through turbine, 90% efficiency, daily MW cap; no reoptimization or observed plant output"})
    save(pd.DataFrame(sensitivity), "head_release_sensitivity", out)

    # Cross-run context, never combine plant histories from different runs.
    run_rows = []
    for f in (ROOT / "results/run_results").rglob("generation_comparison_summary.csv"):
        if "rolling" not in str(f):
            continue
        v = f.parent / "validation.json"
        if not v.exists() or not json.loads(v.read_text()).get("complete"):
            continue
        summary = pd.read_csv(f)
        if "carrier" in summary and summary.carrier.eq("hydro").any():
            row = summary.loc[summary.carrier.eq("hydro")].iloc[0].to_dict()
            row["run"] = str(f.parent.relative_to(ROOT / "results/run_results"))
            run_rows.append(row)
    save(pd.DataFrame(run_rows), "completed_run_comparison", out)
    suspect_comparison = fleet_daily.assign(suspect_repeated_snapshot=fleet_daily.index.isin(bad_dates)).groupby("suspect_repeated_snapshot").agg(
        days=("modeled_gwh", "size"), modeled_gwh=("modeled_gwh", "sum"), observed_gwh=("observed_gwh", "sum"), difference_gwh=("difference_gwh", "sum"))
    suspect_comparison["difference_pct"] = suspect_comparison.difference_gwh / suspect_comparison.observed_gwh * 100
    save(suspect_comparison.reset_index(), "generation_on_suspect_and_other_dates", out)
    metrics = {"run": str(run.relative_to(ROOT)), "hours": len(generation), "calendar_weeks": len(weekly),
        "plants_including_small_hydro": len(plant_stats), "conventional_components": len(conv),
        "hydro_capacity_mw": float(meta.loc[conv].capacity_mw.sum()),
        "modeled_gwh": modeled.sum(), "observed_gwh": observed.sum(), "difference_gwh": (modeled - observed).sum(),
        "difference_pct": 100 * (modeled.sum() / observed.sum() - 1),
        "weekly_mae_gwh": weekly.difference_gwh.abs().mean(), "weekly_rmse_gwh": np.sqrt((weekly.difference_gwh ** 2).mean()),
        "weekly_correlation": weekly.modeled_gwh.corr(weekly.observed_gwh),
        "annual_generation_multiplier_diagnostic": annual_multiplier,
        "weeks_under": int((weekly.difference_gwh < 0).sum()), "daily_mae_gwh": (modeled - observed).abs().mean(),
        "pumped_generation_gwh": group_daily.kadmparai.sum(), "pumping_consumption_gwh": pump.sum() / 1000,
        "pump_implied_cycle_efficiency": float(l.loc[pump_names, "efficiency"].iloc[0] / -l.loc[pump_names, "efficiency2"].iloc[0]),
        "pump_and_generation_simultaneous_hours": int(((pump > 1e-5) & (group_hourly.kadmparai > 1e-5)).sum()),
        "inflow_available_gwh": data["gen_available"][natural].sum().sum() / 1000,
        "inflow_used_gwh": data["gen_dispatch"][natural].sum().sum() / 1000,
        "balancing_inflow_gwh_equiv": data["gen_dispatch"][balance_sources].sum().sum() / 1000,
        "balancing_outflow_gwh_equiv": data["p0"][balance_outlets].sum().sum() / 1000,
        "non_generating_release_gwh_equiv": data["p0"][l.index[l.carrier.eq("water_spill")]].sum().sum() / 1000,
        "daily_generation_reconciliation_max_gwh": recon_error,
        "storage_handoff_max_error_mwh": handoff_error,
        "max_daily_store_balance_residual_mwh": reservoir_daily.model_balance_residual_mwh.abs().max(),
        "neutral_benchmark_failures": int((boundaries.neutral_benchmark_applicable & boundaries.neutrality_pass_1kwh.eq(False)).sum()),
        "suspect_repeated_snapshot_days": len(bad_dates), "exact_current_fallback_fingerprint_days": len(exact_fingerprint_dates),
        "weeks_containing_suspect_snapshot": int((weekly.suspect_agriculture_days > 0).sum()),
        "cea_monthly_comparison_rows": len(cea_monthly), "cea_compared_assets": int(cea_annual.asset_id.nunique()),
        "hydro_availability_reconstruction_max_error_mw": max(profile_errors),
        "observation_time_caveat": "same-date opening comparison; source timestamp within day unspecified"}
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    inputs = [ROOT / p for p in ["model/hydro_capacity_validation.csv", "model/hydro_reservoir_parameters.csv", "data/reservoirs/tn_reservoir_daily_FY2025_26.csv", "data/reservoirs/tn_reservoir_suspicious_snapshots_FY2025_26.csv", "data/source/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx", "data/model_inputs/cea_hydro_monthly_generation_FY2025_26.csv", "scripts/run_9ba_rolling.py", "scripts/build_9ba_model.py", "scripts/scrape_tn_reservoirs.py"]]
    inputs += [run / "observed_daily_generation.csv", run / "modeled_daily_generation.csv"]
    (out / "source_manifest.json").write_text(json.dumps({"checkpoints": hashes, "inputs": [
        {"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in inputs],
        "scope": "checkpoint operation; current metadata and code used for interpretation; no historical code hash in checkpoints",
        "weekly_definition": "Monday--Sunday, clipped to FY; separate actual solve-window assumption table"}, indent=2), encoding="utf-8")

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(2, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]}, constrained_layout=True)
    ax[0].plot(weekly.index, weekly.observed_gwh, label="Observed statewide hydro", color="#163e62", lw=2)
    ax[0].plot(weekly.index, weekly.modeled_gwh, label="Modeled gross hydro", color="#cd692b", lw=2)
    suspect_weeks = weekly.suspect_agriculture_days > 0
    ax[0].scatter(weekly.index[suspect_weeks], weekly.observed_gwh[suspect_weeks], marker="x", color="#a74141", s=25,
                  label="Week includes suspect Agriculture input", zorder=3)
    ax[0].set(ylabel="GWh per calendar week", title="Hydro generation | selected completed run")
    ax[0].legend()
    ax[1].bar(weekly.index, weekly.difference_gwh, width=5, color="#c66558")
    ax[1].axhline(0, color="#777", lw=.6)
    ax[1].set(ylabel="Modeled − observed (GWh)", xlabel="Monday of week; first and last weeks are partial")
    fig.savefig(figures / "fleet_weekly.png", dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(3, 2, figsize=(13, 10), sharex=True, constrained_layout=True)
    for ax, (store, source) in zip(axes.flat, {**DIRECT, "Upper_Kodayar": "Pechiparai"}.items()):
        rr = reservoir_daily.loc[reservoir_daily.store.eq(store)].set_index("date")
        ax.plot(rr.index, rr.agriculture_storage_pct.mask(rr.suspect_repeated_snapshot), label="Agriculture" if store in DIRECT else "Pechiparai context (inflow proxy only)", color="#163e62")
        suspect = rr.suspect_repeated_snapshot
        ax.scatter(rr.index[suspect], rr.agriculture_storage_pct[suspect], color="#b95e68", s=9, marker="x", label="Suspect repeated snapshot")
        ax.plot(rr.index, rr.model_opening_pct, label="Model opening storage", color="#cd692b")
        ax.set(title=store.replace("_", " "), ylabel="% of gross capacity", ylim=(-3, max(103, rr.agriculture_storage_pct.max() + 3)))
        ax.legend(fontsize=8)
    fig.savefig(figures / "reservoir_storage.png", dpi=160)
    plt.close(fig)
    pivot = asset_week.pivot(index="asset_id", columns="week_monday", values="capacity_factor_pct")
    fig, ax = plt.subplots(figsize=(14, 7), constrained_layout=True)
    im = ax.imshow(pivot, aspect="auto", vmin=0, vmax=100, cmap="YlGnBu")
    ax.set_yticks(range(len(pivot)), pivot.index.str.replace("_", " "))
    ticks = list(range(0, len(pivot.columns), 4))
    ax.set_xticks(ticks, [pivot.columns[x].strftime("%d %b") for x in ticks], rotation=40, ha="right")
    ax.set(title="Weekly plant-group utilization", xlabel="Monday of calendar week")
    fig.colorbar(im, ax=ax, label="Capacity factor (%)")
    fig.savefig(figures / "asset_weekly_utilization.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 6), constrained_layout=True)
    inflows = store_annual.loc[list(DIRECT), ["inflow_used_mwh", "inflow_discarded_mwh"]] / 1000
    inflows.index = inflows.index.str.replace("_", " ")
    inflows.rename(columns={"inflow_used_mwh": "Admitted to model", "inflow_discarded_mwh": "Available but not admitted"}).plot.barh(stacked=True, ax=ax, color=["#287d8e", "#d79d6a"])
    ax.set(xlabel="GWh of water equivalent at the model's 100 m head", ylabel="", title="Observed inflow is currently an optional input")
    fig.savefig(figures / "inflow_accounting.png", dpi=160)
    plt.close(fig)
    assets = list(cea_annual.sort_values("asset_id").asset_id)
    cols = 4
    fig, axes = plt.subplots(int(np.ceil(len(assets) / cols)), cols, figsize=(15, 10), sharex=True, constrained_layout=True)
    for ax, asset in zip(axes.flat, assets):
        values = cea_monthly.loc[cea_monthly.asset_id.eq(asset)].sort_values("month")
        ax.plot(values.month, values.model_generation_gwh, color="#cd692b", marker="o", ms=3, label="Model")
        ax.plot(values.month, values.cea_generation_gwh, color="#163e62", marker="o", ms=3, label="CEA")
        ax.set(title=asset.replace("_", " ").title(), ylabel="GWh")
        ax.tick_params(axis="x", rotation=35, labelsize=7)
    for ax in axes.flat[len(assets):]:
        ax.set_visible(False)
    fig.legend(["Model", "CEA observed"], loc="lower center", ncol=2)
    fig.suptitle("Monthly hydro generation comparison by explicitly mapped system", fontsize=14)
    fig.savefig(figures / "cea_monthly_generation_comparison.png", dpi=160)
    plt.close(fig)
    print(json.dumps(metrics, indent=2), flush=True)
    print(group_stats[["capacity_mw", "generation_gwh", "capacity_factor_pct", "weeks_operating"]].round(3).to_string(), flush=True)
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Completed rolling-run directory with weekly checkpoints")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    analyze(run, args.output or run / "analysis/hydro_deep_dive")
