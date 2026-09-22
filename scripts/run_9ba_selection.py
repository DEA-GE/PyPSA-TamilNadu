"""Unified daily, monthly, selected-week, peak-week, and rolling-year runner."""

from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa
import xarray as xr

from nuclear_energy_targets import (
    add_nuclear_targets,
    generator_dimension,
    prepare_nuclear_targets,
)
from run_9ba_weekly_days_2025_26 import (
    COMPARISON_CATEGORIES,
    MODEL_DIR,
    OIL_GAS_BUDGET_CONSTRAINT,
    PLOT_LABELS,
    read_observed_generation,
    read_oil_gas_budget,
    solve_day,
)
from run_9ba_rolling import (
    BIOMASS_ENERGY_CONSTRAINT,
    CHECKPOINT_MODEL_VERSION,
    run_rolling_year,
)


DEFAULT_NETWORK = MODEL_DIR
DEFAULT_RESULTS_ROOT = MODEL_DIR.parent / "run_results"
HYDRO_OBSERVED_SOC_FILE = MODEL_DIR / "hydro_observed_soc.csv"
HYDRO_PARAMETERS_FILE = MODEL_DIR / "hydro_reservoir_parameters.csv"
# Optional input, deliberately monthly: date, pypsa_component, stored_energy_mwh.
# It is not expanded into a synthetic daily series.
HYDRO_MONTHLY_SOC_FILE = MODEL_DIR / "hydro_monthly_stored_energy.csv"
DEFAULT_SOC_BOUNDARY_TOLERANCE_PU = 0.05

# Importing the full-year network from the CSV folder is needlessly repeated
# when users adjust settings and re-run the notebook in the same kernel.  Keep
# an untouched source network in memory and hand each run a deep copy, since
# every run changes constraints and time-series data.  The signature means an
# edited input file is picked up without requiring a kernel restart.
_NETWORK_CACHE: dict[Path, tuple[tuple[tuple[str, int, int], ...], pypsa.Network]] = {}


def _network_signature(network_path: Path) -> tuple[tuple[str, int, int], ...]:
    """Return a cheap content-change signature for a network file or folder."""
    if network_path.is_dir():
        files = sorted(path for path in network_path.rglob("*") if path.is_file())
        return tuple(
            (
                str(path.relative_to(network_path)),
                path.stat().st_mtime_ns,
                path.stat().st_size,
            )
            for path in files
        )
    stat = network_path.stat()
    return ((network_path.name, stat.st_mtime_ns, stat.st_size),)


def _load_base_network(network_path: Path) -> tuple[pypsa.Network, bool]:
    """Load the annual network once per unchanged input set and return a copy."""
    resolved_path = network_path.resolve()
    signature = _network_signature(resolved_path)
    cached = _NETWORK_CACHE.get(resolved_path)
    if cached is None or cached[0] != signature:
        _NETWORK_CACHE[resolved_path] = (signature, pypsa.Network(resolved_path))
        return _NETWORK_CACHE[resolved_path][1].copy(), False
    return cached[1].copy(), True


def _normalise_mode(run_mode: str) -> str:
    aliases = {
        "day": "daily",
        "daily": "daily",
        "month": "monthly",
        "monthly": "monthly",
        "peak": "peak",
        "peak_week": "peak",
        "selected_weeks": "selected_weeks",
        "weeks": "selected_weeks",
        "rolling": "rolling",
        "rolling_year": "rolling",
    }
    mode = aliases.get(str(run_mode).strip().lower())
    if mode is None:
        raise ValueError(
            "RUN_MODE must be 'daily', 'monthly', 'peak', 'selected_weeks', or 'rolling'."
        )
    return mode


def _ensure_available(dates: pd.DatetimeIndex, available: pd.DatetimeIndex) -> None:
    missing = dates.difference(available)
    if len(missing):
        shown = ", ".join(str(item.date()) for item in missing[:5])
        raise ValueError(f"Requested date(s) are outside the model horizon: {shown}")


def _dates_for_mode(
    mode: str,
    reference_date: str,
    load: pd.Series,
) -> tuple[pd.DatetimeIndex, str]:
    available = pd.DatetimeIndex(load.index).normalize().unique().sort_values()
    if mode == "peak":
        peak_hour = pd.Timestamp(load.idxmax())
        start = peak_hour.normalize() - pd.Timedelta(days=peak_hour.weekday())
        dates = pd.date_range(start, periods=7, freq="D")
        _ensure_available(dates, available)
        return dates, f"peak_{start:%Y-%m-%d}"

    if mode == "rolling":
        return available, f"rolling_{available[0]:%Y-%m-%d}_{available[-1]:%Y-%m-%d}"

    reference = pd.Timestamp(reference_date).normalize()
    if mode == "daily":
        dates = pd.DatetimeIndex([reference])
        label = f"daily_{reference:%Y-%m-%d}"
    else:
        start = reference.replace(day=1)
        end = start + pd.offsets.MonthEnd(0)
        dates = pd.date_range(start, end, freq="D")
        label = f"monthly_{start:%Y-%m}"
    _ensure_available(dates, available)
    return dates, label


def _selected_week_dates(
    week_starts: list[str] | tuple[str, ...] | pd.DatetimeIndex | None,
    available: pd.DatetimeIndex,
    warmup_days: int,
    lookahead_days: int,
) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Validate independent test weeks and return starts plus retained dates."""
    if week_starts is None or len(week_starts) == 0:
        raise ValueError("WEEK_STARTS must contain at least one date in selected_weeks mode.")
    if warmup_days < 0 or lookahead_days < 0:
        raise ValueError("WARMUP_DAYS and LOOKAHEAD_DAYS must be non-negative.")

    starts = pd.DatetimeIndex(pd.to_datetime(list(week_starts))).normalize().sort_values()
    if starts.has_duplicates:
        raise ValueError("WEEK_STARTS contains duplicate dates.")
    if len(starts) > 1 and (starts.to_series().diff().dropna() < pd.Timedelta(days=7)).any():
        raise ValueError("Selected seven-day test periods must not overlap.")

    retained = pd.DatetimeIndex(
        np.concatenate([pd.date_range(start, periods=7, freq="D") for start in starts])
    ).sort_values()
    required = pd.DatetimeIndex(
        np.concatenate(
            [
                pd.date_range(
                    start - pd.Timedelta(days=warmup_days),
                    periods=warmup_days + 7 + lookahead_days,
                    freq="D",
                )
                for start in starts
            ]
        )
    ).unique().sort_values()
    _ensure_available(required, available)
    return starts, retained


def _hydro_soc_targets() -> pd.DataFrame:
    """Read observed/proxy daily SOC targets exported by the input builder."""
    if not HYDRO_OBSERVED_SOC_FILE.exists():
        return pd.DataFrame(columns=["date", "pypsa_component", "soc_mwh"])
    targets = pd.read_csv(HYDRO_OBSERVED_SOC_FILE, parse_dates=["date"])
    required = {"date", "pypsa_component", "soc_mwh"}
    missing = required - set(targets.columns)
    if missing:
        raise ValueError(f"Hydro SOC target file missing columns: {sorted(missing)}")
    return targets.set_index(["date", "pypsa_component"]).sort_index()


def _soc_at_date(targets: pd.DataFrame, date: pd.Timestamp, components: pd.Index) -> pd.Series:
    """Use the nearest available daily observation, without interpolation."""
    if targets.empty:
        return pd.Series(dtype=float)
    day = pd.Timestamp(date).normalize()
    dates = pd.DatetimeIndex(targets.index.get_level_values("date").unique())
    nearest = dates[np.argmin(np.abs(dates - day))]
    selected = targets.xs(nearest, level="date")["soc_mwh"]
    return selected.reindex(components).dropna()


def _hydro_boundary_parameters() -> pd.DataFrame:
    """Return traceable SOC-boundary metadata emitted by the input builder."""
    if not HYDRO_PARAMETERS_FILE.exists():
        return pd.DataFrame()
    parameters = pd.read_csv(HYDRO_PARAMETERS_FILE)
    required = {"pypsa_component", "soc_source", "initial_soc_method", "terminal_soc_method"}
    missing = required - set(parameters.columns)
    if missing:
        raise ValueError(f"Hydro boundary metadata missing columns: {sorted(missing)}")
    return parameters.drop_duplicates("pypsa_component", keep="last").set_index("pypsa_component")


def _monthly_boundary_targets(parameters: pd.DataFrame, boundary: pd.Timestamp) -> pd.Series:
    """Interpolate monthly stored-energy observations only at a week boundary."""
    monthly = parameters.loc[parameters["boundary_data_resolution"].eq("monthly")]
    if monthly.empty:
        return pd.Series(dtype=float)
    if HYDRO_MONTHLY_SOC_FILE.exists():
        raw = pd.read_csv(HYDRO_MONTHLY_SOC_FILE, parse_dates=["date"])
        required = {"date", "pypsa_component", "stored_energy_mwh"}
        if required - set(raw.columns):
            raise ValueError(f"{HYDRO_MONTHLY_SOC_FILE.name} must contain {sorted(required)}")
        raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
        values: dict[str, float] = {}
        for component, row in monthly.iterrows():
            series = raw.loc[raw["pypsa_component"].eq(component)].set_index("date")["stored_energy_mwh"].sort_index()
            if len(series):
                # Time interpolation is evaluated at this boundary only.
                points = series.index.union(pd.DatetimeIndex([boundary])).sort_values()
                value = series.reindex(points).interpolate(method="time").at[boundary]
                values[component] = float(np.clip(value, 0.0, float(row["e_nom_mwh"])))
        # A partially loaded file must not silently yield NaN constraints. Its
        # missing components retain the documented provisional neutral target.
        return pd.Series(values, dtype=float).reindex(monthly.index).fillna(
            monthly["e_nom_mwh"].astype(float) * 0.50
        )
    # Explicit provisional method while SRPC/TANGEDCO series are not loaded.
    return (monthly["e_nom_mwh"].astype(float) * 0.50).rename("soc_mwh")


def _carrier_energy_by_day(network: pypsa.Network) -> pd.DataFrame:
    weights = network.snapshot_weightings.generators.reindex(network.snapshots)
    weighted = network.generators_t.p.mul(weights, axis=0)
    by_carrier_hourly = weighted.T.groupby(network.generators.carrier).sum().T
    daily = by_carrier_hourly.groupby(by_carrier_hourly.index.normalize()).sum() / 1000.0

    storage_discharge = network.storage_units_t.p.clip(lower=0).mul(weights, axis=0)
    storage_hourly_by_carrier = storage_discharge.T.groupby(
        network.storage_units.carrier
    ).sum().T
    storage_daily_by_carrier = (
        storage_hourly_by_carrier.groupby(storage_hourly_by_carrier.index.normalize()).sum()
        / 1000.0
    )

    output = pd.DataFrame(index=daily.index)
    for carrier in ["coal", "oil_gas", "nuclear", "hydro", "solar", "wind", "diesel"]:
        output[carrier] = daily.get(carrier, pd.Series(0.0, index=daily.index))
    output["hydro"] = output["hydro"].add(
        storage_daily_by_carrier.get("hydro", pd.Series(0.0, index=daily.index)),
        fill_value=0.0,
    )
    output["hydro"] = output["hydro"].add(
        daily.get("hydro_run_of_river", pd.Series(0.0, index=daily.index)),
        fill_value=0.0,
    )
    hydro_links = network.links.index[network.links.carrier.eq("hydro_turbine")]
    if len(hydro_links):
        link_daily = (-network.links_t.p1[hydro_links]).mul(weights, axis=0).sum(axis=1)
        link_daily = link_daily.groupby(link_daily.index.normalize()).sum() / 1_000.0
        output["hydro"] = output["hydro"].add(link_daily, fill_value=0.0)
    output["other_res"] = sum(
        (daily.get(carrier, pd.Series(0.0, index=daily.index)) for carrier in ["bio_power", "small_hydro"]),
        start=pd.Series(0.0, index=daily.index),
    )
    output["market_import"] = daily.get("market_import", pd.Series(0.0, index=daily.index))
    output["unserved_energy"] = daily.get("unserved_energy", pd.Series(0.0, index=daily.index))
    output["system_plant_generation"] = output[
        ["coal", "oil_gas", "nuclear", "hydro", "solar", "wind", "diesel", "other_res"]
    ].sum(axis=1)
    output["comparable_total"] = output[
        ["coal", "oil_gas", "nuclear", "hydro", "solar", "wind"]
    ].sum(axis=1)
    output.index.name = "date"
    return output


def _solve_independent_days(
    base_network: pypsa.Network,
    dates: pd.DatetimeIndex,
    observed: pd.DataFrame,
    oil_gas_budget: pd.DataFrame,
    solver_name: str,
    mip_rel_gap: float,
) -> tuple[pd.DataFrame, dict, pypsa.Network | None]:
    rows: list[dict] = []
    conditions: list[str] = []
    last_network: pypsa.Network | None = None
    started = time.perf_counter()

    for position, date in enumerate(dates, start=1):
        print(f"Solving {date.date()} ({position}/{len(dates)}) ...", flush=True)
        last_network, result = solve_day(
            base_network,
            date,
            solver_name,
            float(oil_gas_budget.at[date, "daily_energy_target_mwh"]),
            float(oil_gas_budget.at[date, "annual_share"]),
            mip_rel_gap=mip_rel_gap,
        )
        rows.append(result)
        conditions.append(str(result["termination_condition"]))

    modeled = pd.DataFrame(rows).set_index("date")
    modeled.index = pd.to_datetime(modeled.index)
    validation = {
        "formulation": "independent 24-hour unit-commitment solves",
        "days_solved": int(len(dates)),
        "all_solver_statuses_ok": bool(all(item == "optimal" for item in conditions)),
        "max_abs_gas_target_deviation_mwh": float(modeled["oil_gas_target_deviation_mwh"].abs().max()),
        "max_abs_nuclear_target_deviation_mwh": float(modeled["nuclear_target_deviation_mwh"].abs().max()),
        "max_abs_nodal_residual_mw": float(modeled["max_nodal_residual_mw"].max()),
        "max_link_loading_pct": float(modeled["max_corridor_loading_pct"].max()),
        "total_unserved_gwh": float(modeled["unserved_energy"].sum()),
        "solver_runtime_seconds_sum": float(modeled["runtime_seconds"].sum()),
        "wall_clock_seconds": float(time.perf_counter() - started),
    }
    return modeled, validation, last_network if len(dates) == 1 else None


def _solve_peak_week(
    base_network: pypsa.Network,
    dates: pd.DatetimeIndex,
    observed: pd.DataFrame,
    oil_gas_budget: pd.DataFrame,
    solver_name: str,
    mip_rel_gap: float,
) -> tuple[pd.DataFrame, dict, pypsa.Network]:
    snapshots = base_network.snapshots[base_network.snapshots.normalize().isin(dates)]
    network = base_network.copy(snapshots=snapshots)
    gas_units = network.generators.index[network.generators.carrier == "oil_gas"]
    gas_targets = oil_gas_budget["daily_energy_target_mwh"].reindex(dates)

    gas_constraint = OIL_GAS_BUDGET_CONSTRAINT
    if gas_constraint in network.global_constraints.index:
        network.global_constraints.at[gas_constraint, "constant"] = float(gas_targets.sum())
    prepare_nuclear_targets(network)

    committable = network.generators.index[network.generators.committable]
    if len(committable):
        initial_off = network.generators.loc[committable, "down_time_before"].fillna(0.0) > 0.0
        network.generators.loc[committable[initial_off], "p_init"] = np.nan

    def extra_functionality(active_network: pypsa.Network, active_snapshots: pd.DatetimeIndex) -> None:
        add_nuclear_targets(active_network, active_snapshots)
        dispatch = active_network.model["Generator-p"]
        generator_dim = generator_dimension(dispatch)
        for date, target in gas_targets.items():
            day_snapshots = active_snapshots[active_snapshots.normalize() == date]
            if not len(day_snapshots):
                continue
            active_network.model.add_constraints(
                dispatch.sel(
                    {generator_dim: gas_units, "snapshot": day_snapshots}
                ).sum() == float(target),
                name=f"daily_oil_gas_energy_{date:%Y_%m_%d}",
            )

    print(f"Solving peak week {dates[0].date()} to {dates[-1].date()} ...", flush=True)
    started = time.perf_counter()
    status, condition = network.optimize(
        solver_name=solver_name,
        solver_options={"mip_rel_gap": mip_rel_gap},
        extra_functionality=extra_functionality,
        include_objective_constant=False,
    )
    runtime = time.perf_counter() - started
    if status != "ok":
        raise RuntimeError(f"Peak-week solve failed: status={status}, condition={condition}")

    modeled = _carrier_energy_by_day(network)
    gas_deviation = modeled["oil_gas"].reindex(dates).mul(1000.0).sub(gas_targets)
    nuclear_deviation = modeled["nuclear"].reindex(dates).sub(observed["nuclear"].reindex(dates)).mul(1000.0)
    residual = network.buses_t.p.sum(axis=1).abs().max()
    if len(network.links):
        loading = network.links_t.p0.abs().div(network.links.p_nom).max().max() * 100.0
    else:
        loading = 0.0
    validation = {
        "formulation": "continuous 168-hour unit-commitment solve",
        "days_solved": int(len(dates)),
        "solver_status": str(status),
        "solver_condition": str(condition),
        "max_abs_gas_target_deviation_mwh": float(gas_deviation.abs().max()),
        "max_abs_nuclear_target_deviation_mwh": float(nuclear_deviation.abs().max()),
        "max_abs_nodal_residual_mw": float(residual),
        "max_link_loading_pct": float(loading),
        "total_unserved_gwh": float(modeled["unserved_energy"].sum()),
        "wall_clock_seconds": float(runtime),
    }
    return modeled, validation, network


def _solve_selected_weeks(
    base_network: pypsa.Network,
    starts: pd.DatetimeIndex,
    observed: pd.DataFrame,
    oil_gas_budget: pd.DataFrame,
    biomass_annual_target_mwh: float,
    full_horizon_weight: float,
    solver_name: str,
    mip_rel_gap: float,
    output_dir: Path,
    warmup_days: int,
    lookahead_days: int,
    reservoir_initial_soc_fraction: float,
    soc_boundary_tolerance_pu: float,
    resume: bool,
) -> tuple[pd.DataFrame, dict, None]:
    """Solve independent continuous weeks with warm-up and look-ahead days."""
    if not 0.0 <= reservoir_initial_soc_fraction <= 1.0:
        raise ValueError("RESERVOIR_INITIAL_SOC_FRACTION must be between zero and one.")
    if not 0.0 <= soc_boundary_tolerance_pu <= 1.0:
        raise ValueError("SOC_BOUNDARY_TOLERANCE_PU must be between zero and one.")
    if biomass_annual_target_mwh <= 0.0 or full_horizon_weight <= 0.0:
        raise ValueError("Selected-week biomass target requires a positive annual target and horizon weight.")

    modeled_parts: list[pd.DataFrame] = []
    week_rows: list[dict[str, object]] = []
    observed_soc = _hydro_soc_targets()
    boundary_parameters = _hydro_boundary_parameters()
    observed_daily_components = boundary_parameters.index[
        boundary_parameters["terminal_soc_method"].eq("observed_daily_target")
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    run_started = time.perf_counter()

    for position, start in enumerate(starts, start=1):
        retained_dates = pd.date_range(start, periods=7, freq="D")
        retained_base_snapshots = base_network.snapshots[
            base_network.snapshots.normalize().isin(retained_dates)
        ]
        expected_biomass_target_mwh = (
            biomass_annual_target_mwh
            * float(
                base_network.snapshot_weightings.generators.reindex(
                    retained_base_snapshots
                ).sum()
            )
            / full_horizon_weight
        )
        week_dir = output_dir / f"week_{start:%Y-%m-%d}"
        week_dir.mkdir(parents=True, exist_ok=True)
        modeled_file = week_dir / "modeled_daily_generation.csv"
        validation_file = week_dir / "validation.json"
        network_file = week_dir / "solved_network.nc"
        if resume and modeled_file.exists() and validation_file.exists() and network_file.exists():
            saved_validation = json.loads(validation_file.read_text(encoding="utf-8"))
            compatible = (
                saved_validation.get("model_version") == CHECKPOINT_MODEL_VERSION
                and saved_validation.get("warmup_days") == warmup_days
                and saved_validation.get("lookahead_days") == lookahead_days
                and saved_validation.get("solver_name") == solver_name
                and np.isclose(saved_validation.get("mip_rel_gap", np.nan), mip_rel_gap)
                and np.isclose(
                    saved_validation.get("reservoir_initial_soc_fraction", np.nan),
                    reservoir_initial_soc_fraction,
                )
                and np.isclose(
                    saved_validation.get("soc_boundary_tolerance_pu", np.nan),
                    soc_boundary_tolerance_pu,
                )
                and np.isclose(
                    saved_validation.get("biomass_target_mwh", np.nan),
                    expected_biomass_target_mwh,
                )
            )
            if compatible:
                print(
                    f"Loading completed selected week {position}/{len(starts)}: "
                    f"{start.date()}",
                    flush=True,
                )
                modeled = pd.read_csv(modeled_file, index_col="date", parse_dates=True)
                modeled = modeled.reindex(retained_dates)
                modeled_parts.append(modeled)
                week_rows.append(saved_validation)
                pd.DataFrame(week_rows).to_csv(
                    output_dir / "selected_week_log.csv", index=False
                )
                continue
            print(
                f"Re-solving stale selected week {position}/{len(starts)}: {start.date()}",
                flush=True,
            )

        active_dates = pd.date_range(
            start - pd.Timedelta(days=warmup_days),
            periods=warmup_days + 7 + lookahead_days,
            freq="D",
        )
        snapshots = base_network.snapshots[
            base_network.snapshots.normalize().isin(active_dates)
        ]
        expected_hours = 24 * len(active_dates)
        if len(snapshots) != expected_hours:
            raise ValueError(
                f"Expected {expected_hours} active snapshots for week {start.date()}, "
                f"found {len(snapshots)}."
            )

        network = base_network.copy(snapshots=snapshots)
        # The native operational limit covers every active snapshot, including
        # warm-up and look-ahead days. Replace it with a retained-week target
        # below so each independent seven-day run receives exactly its
        # proportional share of the annual CEA biomass energy target.
        if BIOMASS_ENERGY_CONSTRAINT in network.global_constraints.index:
            network.remove("GlobalConstraint", BIOMASS_ENERGY_CONSTRAINT)
        reservoir_units = network.storage_units.index[
            network.storage_units.carrier.eq("hydro")
            & network.storage_units.p_min_pu.ge(0.0)
        ]
        if len(reservoir_units):
            network.storage_units.loc[reservoir_units, "cyclic_state_of_charge"] = False
            network.storage_units.loc[reservoir_units, "state_of_charge_initial"] = (
                reservoir_initial_soc_fraction
                * network.storage_units.loc[reservoir_units, "p_nom"]
                * network.storage_units.loc[reservoir_units, "max_hours"]
            )
            # The selected-week observations are retained-week start/end
            # boundaries. Do not add a third observed boundary at the warm-up
            # day: discontinuities in the source series can otherwise require
            # physically impossible filling before the retained week starts.
            observed_initial = pd.Series(dtype=float)
            observed_terminal = _soc_at_date(
                observed_soc,
                retained_dates[-1],
                reservoir_units.intersection(observed_daily_components),
            )
        else:
            observed_terminal = pd.Series(dtype=float)
        observed_store_initial = pd.Series(dtype=float)
        observed_store_terminal = _soc_at_date(
            observed_soc,
            retained_dates[-1],
            network.stores.index.intersection(observed_daily_components),
        )
        observed_week_start = _soc_at_date(
            observed_soc,
            retained_dates[0],
            reservoir_units.intersection(observed_daily_components),
        )
        observed_store_week_start = _soc_at_date(
            observed_soc,
            retained_dates[0],
            network.stores.index.intersection(observed_daily_components),
        )
        # Independent selected weeks initialize observed reservoirs from the
        # retained-week start observation. The warm-up may optimize operations
        # around that level, while the explicit start constraint below still
        # enforces the exact observed boundary at the first retained snapshot.
        if len(observed_week_start):
            network.storage_units.loc[
                observed_week_start.index, "state_of_charge_initial"
            ] = observed_week_start
        if len(observed_store_week_start):
            network.stores.loc[observed_store_week_start.index, "e_initial"] = (
                observed_store_week_start
            )
        monthly_initial = _monthly_boundary_targets(boundary_parameters, retained_dates[0])
        monthly_terminal = _monthly_boundary_targets(boundary_parameters, retained_dates[-1])
        # Intermediate pondage and explicitly assumption-based reservoirs do
        # not have a seasonal target.  Keep their retained-week water balance
        # neutral instead of allowing the selected-week solve to draw down an
        # arbitrary initial store.  The metadata identifies both StorageUnits
        # and hydraulic Stores with the same boundary convention.
        water_neutral_components = boundary_parameters.index[
            boundary_parameters["weekly_balance_method"].eq("end_equals_start")
        ]
        pondage_units = reservoir_units.intersection(water_neutral_components)
        pondage_stores = network.stores.index.intersection(water_neutral_components)
        # A terminal tailwater Store has incoming turbine discharge but no
        # outgoing hydraulic path.  Pinning its retained-week end level to its
        # start level would force every upstream turbine to zero dispatch.  In
        # particular, that contradicts a declining observed reservoir target
        # at Papanasam, whose turbines discharge into Servalar_pondage.  Apply
        # water neutrality only to genuine intermediate/closed pondages; a
        # terminal routing sink must be free to absorb the week's net release.
        link_bus0 = network.links.get("bus0", pd.Series(dtype=object))
        outflow_buses = pd.Index(link_bus0.dropna().astype(str).unique())
        stores_with_outflow = network.stores.index[
            network.stores["bus"].astype(str).isin(outflow_buses)
        ]
        pondage_stores = pondage_stores.intersection(stores_with_outflow)
        committable = network.generators.index[network.generators.committable]
        if len(committable):
            initially_down = (
                network.generators.loc[committable, "down_time_before"].fillna(0.0) > 0.0
            )
            network.generators.loc[committable[initially_down], "p_init"] = np.nan

        gas_targets = oil_gas_budget["daily_energy_target_mwh"].reindex(active_dates)
        if gas_targets.isna().any():
            raise ValueError(f"Missing oil/gas targets for week starting {start.date()}.")
        if OIL_GAS_BUDGET_CONSTRAINT in network.global_constraints.index:
            network.global_constraints.at[OIL_GAS_BUDGET_CONSTRAINT, "constant"] = float(
                gas_targets.sum()
            )
        prepare_nuclear_targets(network)

        gas_units = network.generators.index[network.generators.carrier.eq("oil_gas")]
        biomass_units = network.generators.index[
            network.generators.carrier.eq("bio_power")
        ]
        if not len(biomass_units):
            raise ValueError("Biomass target is configured but no bio_power units exist.")
        retained_snapshots = network.snapshots[
            network.snapshots.normalize().isin(retained_dates)
        ]
        retained_weights = network.snapshot_weightings.generators.reindex(
            retained_snapshots
        )
        biomass_target_mwh = (
            biomass_annual_target_mwh
            * float(retained_weights.sum())
            / full_horizon_weight
        )

        def extra_functionality(
            active_network: pypsa.Network,
            active_snapshots: pd.DatetimeIndex,
        ) -> None:
            add_nuclear_targets(active_network, active_snapshots)
            terminal_snapshot = active_snapshots[
                active_snapshots.normalize() == retained_dates[-1]
            ][-1]
            start_snapshot = active_snapshots[
                active_snapshots.normalize() == retained_dates[0]
            ][0]
            dispatch = active_network.model["Generator-p"]
            generator_dim = generator_dimension(dispatch)
            biomass_weights = xr.DataArray(
                active_network.snapshot_weightings.generators.reindex(retained_snapshots),
                dims=["snapshot"],
                coords={"snapshot": retained_snapshots},
            )
            biomass_dispatch = dispatch.sel(
                {generator_dim: biomass_units, "snapshot": retained_snapshots}
            )
            active_network.model.add_constraints(
                (biomass_dispatch * biomass_weights).sum() == biomass_target_mwh,
                name=f"SelectedWeekBiomassEnergyTarget-{retained_dates[0]:%Y%m%d}",
            )
            for date, target in gas_targets.items():
                day_snapshots = active_snapshots[active_snapshots.normalize() == date]
                expression = dispatch.sel(
                    {generator_dim: gas_units, "snapshot": day_snapshots}
                ).sum()
                active_network.model.add_constraints(
                    expression == float(target),
                    name=f"SelectedWeekOilGasDailyTarget-{date:%Y%m%d}",
                )
            if len(observed_terminal):
                soc = active_network.model["StorageUnit-state_of_charge"]
                storage_dim = next(dim for dim in soc.dims if dim != "snapshot")
                target = xr.DataArray(
                    observed_terminal.to_numpy(),
                    dims=[storage_dim],
                    coords={storage_dim: observed_terminal.index},
                )
                active_network.model.add_constraints(
                    soc.sel({storage_dim: observed_terminal.index, "snapshot": terminal_snapshot}) == target,
                    name=f"ObservedHydroTerminalSOC-{retained_dates[-1]:%Y%m%d}",
                )
            if len(observed_store_terminal):
                store_e = active_network.model["Store-e"]
                store_dim = next(dim for dim in store_e.dims if dim != "snapshot")
                target = xr.DataArray(
                    observed_store_terminal.to_numpy(), dims=[store_dim],
                    coords={store_dim: observed_store_terminal.index},
                )
                active_network.model.add_constraints(
                    store_e.sel({store_dim: observed_store_terminal.index, "snapshot": terminal_snapshot}) == target,
                    name=f"ObservedHydroStoreTerminalSOC-{retained_dates[-1]:%Y%m%d}",
                )
            if len(observed_week_start):
                soc = active_network.model["StorageUnit-state_of_charge"]
                storage_dim = next(dim for dim in soc.dims if dim != "snapshot")
                target = xr.DataArray(observed_week_start.to_numpy(), dims=[storage_dim], coords={storage_dim: observed_week_start.index})
                active_network.model.add_constraints(soc.sel({storage_dim: observed_week_start.index, "snapshot": start_snapshot}) == target, name=f"ObservedHydroWeekStartSOC-{retained_dates[0]:%Y%m%d}")
            if len(observed_store_week_start):
                store_e = active_network.model["Store-e"]
                store_dim = next(dim for dim in store_e.dims if dim != "snapshot")
                target = xr.DataArray(observed_store_week_start.to_numpy(), dims=[store_dim], coords={store_dim: observed_store_week_start.index})
                active_network.model.add_constraints(store_e.sel({store_dim: observed_store_week_start.index, "snapshot": start_snapshot}) == target, name=f"ObservedHydroStoreWeekStartSOC-{retained_dates[0]:%Y%m%d}")
            if len(pondage_units):
                soc = active_network.model["StorageUnit-state_of_charge"]
                storage_dim = next(dim for dim in soc.dims if dim != "snapshot")
                terminal_soc = soc.sel(
                    {storage_dim: pondage_units, "snapshot": terminal_snapshot}
                )
                start_soc = soc.sel(
                    {storage_dim: pondage_units, "snapshot": start_snapshot}
                ).assign_coords(snapshot=terminal_snapshot)
                active_network.model.add_constraints(
                    terminal_soc == start_soc,
                    name=f"WeeklyWaterNeutralStorageUnits-{retained_dates[0]:%Y%m%d}",
                )
            if len(pondage_stores):
                store_e = active_network.model["Store-e"]
                store_dim = next(dim for dim in store_e.dims if dim != "snapshot")
                terminal_e = store_e.sel(
                    {store_dim: pondage_stores, "snapshot": terminal_snapshot}
                )
                start_e = store_e.sel(
                    {store_dim: pondage_stores, "snapshot": start_snapshot}
                ).assign_coords(snapshot=terminal_snapshot)
                active_network.model.add_constraints(
                    terminal_e == start_e,
                    name=f"WeeklyWaterNeutralStores-{retained_dates[0]:%Y%m%d}",
                )
            # Monthly data are interpolated at the two boundaries only. The
            # tolerance is in per-unit e_nom, not an invented hourly profile.
            monthly_units = reservoir_units.intersection(monthly_initial.index)
            if len(monthly_units):
                soc = active_network.model["StorageUnit-state_of_charge"]
                storage_dim = next(dim for dim in soc.dims if dim != "snapshot")
                tolerance = xr.DataArray(
                    soc_boundary_tolerance_pu
                    * active_network.storage_units.loc[monthly_units, "p_nom"].to_numpy()
                    * active_network.storage_units.loc[monthly_units, "max_hours"].to_numpy(),
                    dims=[storage_dim], coords={storage_dim: monthly_units},
                )
                for label, snapshot, targets in [("Initial", start_snapshot, monthly_initial), ("Terminal", terminal_snapshot, monthly_terminal)]:
                    target = xr.DataArray(targets.reindex(monthly_units).to_numpy(), dims=[storage_dim], coords={storage_dim: monthly_units})
                    expression = soc.sel({storage_dim: monthly_units, "snapshot": snapshot})
                    active_network.model.add_constraints(expression >= target - tolerance, name=f"MonthlyHydro{label}Lower-{retained_dates[0]:%Y%m%d}")
                    active_network.model.add_constraints(expression <= target + tolerance, name=f"MonthlyHydro{label}Upper-{retained_dates[0]:%Y%m%d}")
            monthly_stores = network.stores.index.intersection(monthly_initial.index)
            if len(monthly_stores):
                store_e = active_network.model["Store-e"]
                store_dim = next(dim for dim in store_e.dims if dim != "snapshot")
                tolerance = xr.DataArray(soc_boundary_tolerance_pu * active_network.stores.loc[monthly_stores, "e_nom"].to_numpy(), dims=[store_dim], coords={store_dim: monthly_stores})
                for label, snapshot, targets in [("Initial", start_snapshot, monthly_initial), ("Terminal", terminal_snapshot, monthly_terminal)]:
                    target = xr.DataArray(targets.reindex(monthly_stores).to_numpy(), dims=[store_dim], coords={store_dim: monthly_stores})
                    expression = store_e.sel({store_dim: monthly_stores, "snapshot": snapshot})
                    active_network.model.add_constraints(expression >= target - tolerance, name=f"MonthlyHydroStore{label}Lower-{retained_dates[0]:%Y%m%d}")
                    active_network.model.add_constraints(expression <= target + tolerance, name=f"MonthlyHydroStore{label}Upper-{retained_dates[0]:%Y%m%d}")

        print(
            f"Solving selected week {position}/{len(starts)}: {start.date()} to "
            f"{retained_dates[-1].date()} ({warmup_days} warm-up + 7 retained + "
            f"{lookahead_days} look-ahead days)",
            flush=True,
        )
        started = time.perf_counter()
        status, condition = network.optimize(
            solver_name=solver_name,
            solver_options={"mip_rel_gap": mip_rel_gap, "log_to_console": False},
            extra_functionality=extra_functionality,
            include_objective_constant=False,
        )
        runtime = time.perf_counter() - started
        if status != "ok" or condition != "optimal":
            raise RuntimeError(
                f"Selected week {start.date()} failed: status={status}, "
                f"condition={condition}."
            )

        modeled = _carrier_energy_by_day(network).reindex(retained_dates)
        modeled_parts.append(modeled)
        terminal_snapshot = network.snapshots[
            network.snapshots.normalize() == retained_dates[-1]
        ][-1]
        start_snapshot = network.snapshots[
            network.snapshots.normalize() == retained_dates[0]
        ][0]
        residual = float(network.buses_t.p.sum(axis=1).abs().max())
        loading = (
            float(network.links_t.p0.abs().div(network.links.p_nom, axis=1).max().max() * 100)
            if len(network.links)
            else 0.0
        )
        gas_deviation = (
            modeled["oil_gas"].mul(1_000.0)
            - gas_targets.reindex(retained_dates)
        ).abs().max()
        nuclear_deviation = (
            modeled["nuclear"].sub(observed["nuclear"].reindex(retained_dates)).mul(1_000.0)
        ).abs().max()
        biomass_generation_mwh = float(
            network.generators_t.p.loc[retained_snapshots, biomass_units]
            .mul(retained_weights, axis=0)
            .sum()
            .sum()
        )
        pap_store = "Papanasam_reservoir"
        serv_store = "Servalar_reservoir"
        lower_store = "Papanasam_lower_pondage"
        pap_links = network.links.index[
            network.links.index.str.startswith("papanasam_turbine__")
        ]
        serv_links = network.links.index[
            network.links.index.str.startswith("servalar_turbine__")
        ]
        pap_start_soc = float(network.stores_t.e.at[start_snapshot, pap_store])
        pap_end_soc = float(network.stores_t.e.at[terminal_snapshot, pap_store])
        pap_start_target = float(observed_store_week_start.at[pap_store])
        pap_end_target = float(observed_store_terminal.at[pap_store])
        serv_neutral_error = float(
            network.stores_t.e.at[terminal_snapshot, serv_store]
            - network.stores_t.e.at[start_snapshot, serv_store]
        )
        lower_neutral_error = float(
            network.stores_t.e.at[terminal_snapshot, lower_store]
            - network.stores_t.e.at[start_snapshot, lower_store]
        )
        pap_generation_mwh = float(
            -network.links_t.p1.loc[retained_snapshots, pap_links].sum().sum()
        )
        serv_generation_mwh = float(
            -network.links_t.p1.loc[retained_snapshots, serv_links].sum().sum()
        )
        cascade_buses = [
            "water_papanasam_reservoir",
            "water_servalar_reservoir",
            "water_papanasam_lower_pondage",
            "water_papanasam_downstream_river",
        ]
        cascade_residual = float(
            network.buses_t.p.loc[retained_snapshots, cascade_buses].abs().max().max()
        )
        week_validation = {
            "model_version": CHECKPOINT_MODEL_VERSION,
            "formulation": "continuous selected week with warm-up and look-ahead",
            "solver_status": str(status),
            "solver_condition": str(condition),
            "week_start": str(start.date()),
            "week_end": str(retained_dates[-1].date()),
            "warmup_days": warmup_days,
            "lookahead_days": lookahead_days,
            "biomass_target_mwh": biomass_target_mwh,
            "biomass_generation_mwh": biomass_generation_mwh,
            "biomass_target_error_mwh": biomass_generation_mwh - biomass_target_mwh,
            "solver_name": solver_name,
            "mip_rel_gap": mip_rel_gap,
            "reservoir_initial_soc_fraction": reservoir_initial_soc_fraction,
            "soc_boundary_tolerance_pu": soc_boundary_tolerance_pu,
            "observed_hydro_initial_soc_components": int(len(observed_initial)),
            "observed_hydro_terminal_soc_components": int(len(observed_terminal)),
            "observed_hydro_store_initial_soc_components": int(len(observed_store_initial)),
            "observed_hydro_store_terminal_soc_components": int(len(observed_store_terminal)),
            "observed_hydro_week_start_soc_components": int(len(observed_week_start)),
            "observed_hydro_store_week_start_soc_components": int(len(observed_store_week_start)),
            "monthly_interpolated_boundary_components": int(len(monthly_initial)),
            "weekly_water_neutral_storage_units": int(len(pondage_units)),
            "weekly_water_neutral_stores": int(len(pondage_stores)),
            "papanasam_observed_start_soc_mwh": pap_start_soc,
            "papanasam_observed_end_soc_mwh": pap_end_soc,
            "papanasam_observed_soc_change_mwh": pap_end_soc - pap_start_soc,
            "max_abs_papanasam_observed_soc_error_mwh": max(
                abs(pap_start_soc - pap_start_target),
                abs(pap_end_soc - pap_end_target),
            ),
            "servalar_generation_mwh": serv_generation_mwh,
            "papanasam_generation_mwh": pap_generation_mwh,
            "servalar_reservoir_weekly_neutral_error_mwh": serv_neutral_error,
            "papanasam_lower_pondage_weekly_neutral_error_mwh": lower_neutral_error,
            "max_abs_papanasam_cascade_nodal_residual_mw": cascade_residual,
            "max_abs_gas_target_deviation_mwh": float(gas_deviation),
            "max_abs_nuclear_target_deviation_mwh": float(nuclear_deviation),
            "max_abs_nodal_residual_mw": residual,
            "max_link_loading_pct": loading,
            "total_unserved_gwh": float(modeled["unserved_energy"].sum()),
            "solver_runtime_seconds": runtime,
        }
        _write_outputs(
            "selected_week",
            retained_dates,
            modeled,
            observed,
            week_validation,
            week_dir,
            network,
        )
        week_rows.append(week_validation)
        pd.DataFrame(week_rows).to_csv(output_dir / "selected_week_log.csv", index=False)

    week_log = pd.DataFrame(week_rows)
    validation = {
        "formulation": "independent continuous selected weeks",
        "weeks_solved": int(len(week_rows)),
        "days_retained": int(7 * len(week_rows)),
        "warmup_days_per_week": warmup_days,
        "lookahead_days_per_week": lookahead_days,
        "reservoir_initial_soc_fraction": reservoir_initial_soc_fraction,
        "all_solver_statuses_ok": bool(week_log["solver_condition"].eq("optimal").all()),
        "max_abs_gas_target_deviation_mwh": float(
            week_log["max_abs_gas_target_deviation_mwh"].max()
        ),
        "max_abs_nuclear_target_deviation_mwh": float(
            week_log["max_abs_nuclear_target_deviation_mwh"].max()
        ),
        "max_abs_nodal_residual_mw": float(week_log["max_abs_nodal_residual_mw"].max()),
        "max_link_loading_pct": float(week_log["max_link_loading_pct"].max()),
        "total_unserved_gwh": float(week_log["total_unserved_gwh"].sum()),
        "solver_runtime_seconds_sum": float(week_log["solver_runtime_seconds"].sum()),
        "wall_clock_seconds": float(time.perf_counter() - run_started),
    }
    return pd.concat(modeled_parts).sort_index(), validation, None


def _write_outputs(
    mode: str,
    dates: pd.DatetimeIndex,
    modeled: pd.DataFrame,
    observed: pd.DataFrame,
    validation: dict,
    output_dir: Path,
    solved_network: pypsa.Network | None,
) -> tuple[pd.DataFrame, pd.DataFrame, Path, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    modeled_period = modeled.reindex(dates)
    observed_period = observed.reindex(dates)
    modeled_period.index.name = "date"
    observed_period.index.name = "date"
    modeled_period.to_csv(output_dir / "modeled_daily_generation.csv")
    observed_period.to_csv(output_dir / "observed_daily_generation.csv")

    comparison_rows = []
    summary_rows = []
    for carrier in COMPARISON_CATEGORIES:
        modeled_values = modeled_period[carrier]
        observed_values = observed_period[carrier]
        difference = modeled_values - observed_values
        pct = difference.div(observed_values.replace(0.0, np.nan)) * 100.0
        for date in dates:
            comparison_rows.append(
                {
                    "date": date,
                    "carrier": carrier,
                    "modeled_gwh": modeled_values.loc[date],
                    "observed_gwh": observed_values.loc[date],
                    "difference_gwh": difference.loc[date],
                    "difference_pct": pct.loc[date],
                }
            )
        modeled_total = float(modeled_values.sum())
        observed_total = float(observed_values.sum())
        summary_rows.append(
            {
                "carrier": carrier,
                "modeled_gwh": modeled_total,
                "observed_gwh": observed_total,
                "difference_gwh": modeled_total - observed_total,
                "difference_pct": 100.0 * (modeled_total - observed_total) / observed_total
                if observed_total
                else np.nan,
            }
        )
    comparison = pd.DataFrame(comparison_rows)
    summary = pd.DataFrame(summary_rows).set_index("carrier")
    comparison.to_csv(output_dir / "daily_generation_comparison.csv", index=False)
    summary.to_csv(output_dir / "generation_comparison_summary.csv")

    validation.update(
        {
            "run_mode": mode,
            "start_date": str(dates[0].date()),
            "end_date": str(dates[-1].date()),
        }
    )
    (output_dir / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")

    plot_path = output_dir / "daily_generation_comparison.png"
    fig, axes = plt.subplots(4, 2, figsize=(15, 14), sharex=True)
    if mode == "selected_weeks":
        gap_positions = np.flatnonzero(dates.to_series().diff().gt(pd.Timedelta(days=1)))
        plot_blocks = np.split(dates, gap_positions)
    else:
        plot_blocks = [dates]
    for axis, carrier in zip(axes.flat, COMPARISON_CATEGORIES):
        for block_number, block in enumerate(plot_blocks):
            axis.plot(
                block,
                observed_period.loc[block, carrier],
                label="Observed" if block_number == 0 else None,
                linewidth=2.0,
            )
            axis.plot(
                block,
                modeled_period.loc[block, carrier],
                label="Model" if block_number == 0 else None,
                linewidth=1.7,
            )
        axis.set_title(PLOT_LABELS.get(carrier, carrier.replace("_", " ").title()))
        axis.set_ylabel("GWh/day")
        axis.grid(alpha=0.25)
    for axis in axes.flat[len(COMPARISON_CATEGORIES) :]:
        axis.set_visible(False)
    axes[0, 0].legend()
    fig.suptitle(f"9-BA {mode} dispatch: model versus observed", fontsize=15)
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(plot_path, dpi=170, bbox_inches="tight")
    plt.close(fig)

    network_path = None
    if solved_network is not None:
        network_path = output_dir / "solved_network.nc"
        solved_network.export_to_netcdf(network_path)
    return summary, comparison, plot_path, network_path


def run_selection(
    run_mode: str = "daily",
    reference_date: str = "2025-07-11",
    *,
    week_starts: list[str] | tuple[str, ...] | pd.DatetimeIndex | None = None,
    warmup_days: int = 1,
    lookahead_days: int = 1,
    reservoir_initial_soc_fraction: float = 0.0,
    soc_boundary_tolerance_pu: float = DEFAULT_SOC_BOUNDARY_TOLERANCE_PU,
    solver_name: str = "highs",
    mip_rel_gap: float = 0.01,
    network_path: str | Path = DEFAULT_NETWORK,
    results_root: str | Path = DEFAULT_RESULTS_ROOT,
    resume: bool = True,
    max_windows: int | None = None,
) -> dict:
    """Run the 9-BA unit-commitment model for the selected horizon.

    ``peak`` means the continuous seven-day period containing the annual maximum
    hourly demand. ``selected_weeks`` solves user-supplied seven-day periods
    independently with configurable warm-up and look-ahead. Monthly runs use
    independent daily solves. ``rolling`` solves the full year in seven-day steps.
    """
    mode = _normalise_mode(run_mode)
    network_path = Path(network_path)
    results_root = Path(results_root)
    print(f"Loading {network_path} ...", flush=True)
    load_started = time.perf_counter()
    base_network, from_cache = _load_base_network(network_path)
    load_source = "cached copy" if from_cache else "disk"
    print(
        f"Loaded network from {load_source} in {time.perf_counter() - load_started:.2f} s.",
        flush=True,
    )
    biomass_annual_target_mwh: float | None = None
    full_horizon_weight: float | None = None
    if BIOMASS_ENERGY_CONSTRAINT in base_network.global_constraints.index:
        biomass_annual_target_mwh = float(
            base_network.global_constraints.at[BIOMASS_ENERGY_CONSTRAINT, "constant"]
        )
        full_horizon_weight = float(base_network.snapshot_weightings.generators.sum())

    # Daily and peak diagnostic modes deliberately leave biomass unconstrained.
    # Selected weeks replace the native annual constraint with an exact,
    # retained-week proportional target inside each independent optimization.
    if mode != "rolling" and BIOMASS_ENERGY_CONSTRAINT in base_network.global_constraints.index:
        base_network.remove("GlobalConstraint", BIOMASS_ENERGY_CONSTRAINT)
    total_load = base_network.loads_t.p_set.sum(axis=1)
    available = pd.DatetimeIndex(total_load.index).normalize().unique().sort_values()
    if mode == "selected_weeks":
        selected_starts, dates = _selected_week_dates(
            week_starts, available, warmup_days, lookahead_days
        )
        label = "selected_weeks"
    else:
        selected_starts = pd.DatetimeIndex([])
        dates, label = _dates_for_mode(mode, reference_date, total_load)

    observed = read_observed_generation()
    oil_gas_budget = read_oil_gas_budget(observed)
    missing_observed = dates.difference(observed.index)
    if len(missing_observed):
        raise ValueError(f"Observed generation is missing {len(missing_observed)} requested day(s).")

    output_dir = results_root / label
    if mode == "rolling":
        modeled, validation, solved_network = run_rolling_year(
            base_network,
            observed,
            oil_gas_budget,
            solver_name=solver_name,
            mip_rel_gap=mip_rel_gap,
            output_dir=output_dir,
            resume=resume,
            max_windows=max_windows,
        )
    elif mode == "selected_weeks":
        if biomass_annual_target_mwh is None or full_horizon_weight is None:
            raise ValueError(
                f"The model is missing {BIOMASS_ENERGY_CONSTRAINT!r}; rebuild it first."
            )
        modeled, validation, solved_network = _solve_selected_weeks(
            base_network,
            selected_starts,
            observed,
            oil_gas_budget,
            biomass_annual_target_mwh,
            full_horizon_weight,
            solver_name,
            mip_rel_gap,
            output_dir,
            warmup_days,
            lookahead_days,
            reservoir_initial_soc_fraction,
            soc_boundary_tolerance_pu,
            resume,
        )
    elif mode == "peak":
        modeled, validation, solved_network = _solve_peak_week(
            base_network, dates, observed, oil_gas_budget, solver_name, mip_rel_gap
        )
    else:
        modeled, validation, solved_network = _solve_independent_days(
            base_network, dates, observed, oil_gas_budget, solver_name, mip_rel_gap
        )

    summary, comparison, plot_path, solved_network_path = _write_outputs(
        mode, dates, modeled, observed, validation, output_dir, solved_network
    )
    print(f"Finished. Results: {output_dir}", flush=True)
    return {
        "mode": mode,
        "dates": dates,
        "output_dir": output_dir,
        "summary": summary,
        "comparison": comparison,
        "validation": validation,
        "plot_path": plot_path,
        "network_path": solved_network_path,
        "week_dirs": (
            [output_dir / f"week_{start:%Y-%m-%d}" for start in selected_starts]
            if mode == "selected_weeks"
            else []
        ),
    }


if __name__ == "__main__":
    run_selection()
