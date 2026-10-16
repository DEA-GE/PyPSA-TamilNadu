"""Rolling-horizon full-year unit commitment for the 9-BA model.

Each optimization sees seven committed days plus one look-ahead day.  Only the
committed snapshots are exported; the look-ahead is re-optimized in the next
window after the generator and storage state at the boundary has been passed
forward.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import psutil
import pypsa
import xarray as xr

from highs_solver_options import highs_solver_options, reset_highs_global_scheduler
from bio_power_targets import bio_power_energy_target_mwh, read_bio_power_monthly_targets
from small_hydro_targets import small_hydro_energy_target_mwh, read_small_hydro_monthly_targets

from nuclear_energy_targets import (
    add_nuclear_targets,
    generator_dimension,
    prepare_nuclear_targets,
)
from run_9ba_weekly_days_2025_26 import MODEL_DIR, OIL_GAS_BUDGET_CONSTRAINT

BIOMASS_ENERGY_CONSTRAINT = "observed_fy2025_26_bio_power_generation_target"
# Increment when either the model inputs or rolling-solve acceptance policy changes.
# This prevents resume from combining checkpoints made under different objectives
# or solver-quality criteria.
CHECKPOINT_MODEL_VERSION = "pap-aliyar-feeder-v4"
SMALL_HYDRO_ENERGY_CONSTRAINT = "observed_fy2025_26_small_hydro_generation_target"

# Widths are fractions of usable storage capacity. Proxy observations receive
# wider bands than direct Agriculture measurements. Slack costs use the same
# currency per MWh as generator marginal costs in the network objective.
HYDRO_BAND_PU = {"high": 0.05, "medium": 0.15, "low": 0.25}
HYDRO_SLACK_COST = {"high": 6000.0, "medium": 1500.0, "low": 500.0}
WATER_BALANCE_ADJUSTMENT_COST = 1_000_000.0
WATER_BALANCE_ADJUSTMENT_CAPACITY_MW = 10_000.0
WATER_BALANCE_SINK = "measured_storage_balance_sink"
KODAYAR_WATER_MWH_PER_MCFT = 1000.0 / 16.5
CASCADE_TARGET_FILE = MODEL_DIR / "hydro_cascade_generation_targets.csv"


def _cascade_generation_targets() -> pd.DataFrame:
    """Read the rolling-week targets created by the model build step."""
    targets = pd.read_csv(CASCADE_TARGET_FILE, parse_dates=["week_start", "week_end"])
    required = {
        "week_start", "week_end", "week_days", "asset_id",
        "weekly_generation_target_mwh", "constraint_kind",
    }
    if required - set(targets):
        raise ValueError("Cascade generation target file is missing required columns")
    targets["week_start"] = targets["week_start"].dt.normalize()
    targets["week_end"] = targets["week_end"].dt.normalize()
    if targets.duplicated(["week_start", "asset_id"]).any():
        raise ValueError("Cascade generation targets contain duplicates")
    return targets.set_index(["week_start", "asset_id"]).sort_index()


def _add_weekly_cascade_targets(
    network: pypsa.Network, snapshots: pd.DatetimeIndex, targets: pd.DataFrame,
) -> None:
    """Apply CEA-month-derived weekly hydro objectives and Agriculture-flow caps."""
    audit = pd.read_csv(MODEL_DIR / "hydro_capacity_validation.csv")
    if {"asset_id", "model_component", "model_component_type"} - set(audit):
        raise ValueError("Hydro component audit is missing target-mapping columns")
    link_dispatch = network.model["Link-p"]
    link_dim = next(dim for dim in link_dispatch.dims if dim != "snapshot")
    generator_dispatch = network.model["Generator-p"]
    generator_dim = generator_dimension(generator_dispatch)
    storage_dispatch = network.model["StorageUnit-p_dispatch"]
    storage_dim = next(dim for dim in storage_dispatch.dims if dim != "snapshot")
    weights = network.snapshot_weightings.generators
    week_start = snapshots[0].normalize()
    assets = targets.index.get_level_values("asset_id").unique()
    weekly = targets.reindex(pd.MultiIndex.from_product([[week_start], assets]))
    if weekly.weekly_generation_target_mwh.isna().any():
        raise ValueError(f"Missing CEA hydro target for week {week_start.date()}")
    if weekly.week_days.nunique() != 1:
        raise ValueError(f"Inconsistent CEA target week length for {week_start.date()}")
    expected_days = int(weekly.week_days.iloc[0])
    if snapshots.normalize().nunique() != expected_days:
        raise ValueError(f"CEA target week length mismatch for {week_start.date()}")
    weight = xr.DataArray(
        weights.reindex(snapshots), dims="snapshot", coords={"snapshot": snapshots}
    )
    for asset in assets:
        row = weekly.loc[(week_start, asset)]
        components = audit.loc[
            audit.asset_id.eq(asset)
            & audit.model_component_type.str.contains("turbine|run-of-river", case=False, regex=True)
        ]
        if len(components) == 0:
            raise ValueError(f"No model components found for CEA target {asset}")
        component_type = components.model_component_type.iloc[0]
        names = components.model_component.to_list()
        if component_type.startswith("Link"):
            expression = link_dispatch.sel({link_dim: names, "snapshot": snapshots})
        elif component_type.startswith("Generator"):
            expression = generator_dispatch.sel({generator_dim: names, "snapshot": snapshots})
        elif component_type.startswith("StorageUnit"):
            expression = storage_dispatch.sel({storage_dim: names, "snapshot": snapshots})
        else:
            raise ValueError(f"Unsupported CEA target component type for {asset}: {component_type}")
        expression = (expression * weight).sum()
        target = float(row.weekly_generation_target_mwh)
        name = f"GlobalConstraint-CEAHydro-{asset}-{week_start:%Y%m%d}"
        if row.constraint_kind == "equality":
            network.model.add_constraints(expression == target, name=name)
        elif row.constraint_kind == "maximum":
            network.model.add_constraints(expression <= target, name=name)
        else:
            raise ValueError(f"Unknown CEA hydro target kind for {asset}: {row.constraint_kind}")


def _measured_store_targets(
    observed: pd.DataFrame, parameters: pd.DataFrame, network: pypsa.Network,
) -> pd.DataFrame:
    """Return dated, directly measured Store levels; never promote proxies to measurements."""
    direct = parameters.index[
        parameters.soc_source.eq("observed_agriculture")
        & parameters.boundary_data_resolution.eq("daily")
    ].intersection(network.stores.index)
    rows = observed.loc[
        observed.index.get_level_values("pypsa_component").isin(direct)
        & observed.soc_source.eq("observed_agriculture"), "soc_mwh"
    ]
    targets = rows.unstack("pypsa_component").reindex(columns=direct)
    dates = pd.DatetimeIndex(network.snapshots).normalize().unique()
    if targets.reindex(dates).isna().any().any():
        raise ValueError("Directly measured Stores need one storage value for every model date")
    for component in direct:
        capacity = float(network.stores.at[component, "e_nom"])
        if not targets[component].between(0, capacity * (1 + 1e-6)).all():
            raise ValueError(f"Measured storage for {component} exceeds its modeled capacity")
    return targets.reindex(dates)


def _add_measured_water_balance(network: pypsa.Network, measured: pd.DataFrame) -> None:
    """Admit reported inflow and expose any water needed to reconcile exact storage."""
    if measured.empty:
        return
    if "water_balance_adjustment" not in network.carriers.index:
        network.add("Carrier", "water_balance_adjustment")
    network.add("Bus", WATER_BALANCE_SINK, carrier="water_energy")
    network.add("Store", WATER_BALANCE_SINK, bus=WATER_BALANCE_SINK,
                e_nom=1_000_000_000.0, e_initial=0.0, e_cyclic=False,
                carrier="water_energy")
    for component in measured.columns:
        bus = str(network.stores.at[component, "bus"])
        inflow = network.generators.index[
            network.generators.carrier.eq("water_inflow")
            & network.generators.bus.eq(bus)
        ]
        if len(inflow) != 1 or inflow[0] not in network.generators_t.p_max_pu:
            raise ValueError(f"Expected one observed inflow profile for {component}")
        network.generators_t.p_min_pu[inflow[0]] = network.generators_t.p_max_pu[inflow[0]].to_numpy()
        network.add("Generator", f"{component}_balance_inflow", bus=bus,
                    p_nom=WATER_BALANCE_ADJUSTMENT_CAPACITY_MW,
                    carrier="water_balance_adjustment",
                    marginal_cost=WATER_BALANCE_ADJUSTMENT_COST)
        network.add("Link", f"{component}_balance_outflow", bus0=bus,
                    bus1=WATER_BALANCE_SINK,
                    p_nom=WATER_BALANCE_ADJUSTMENT_CAPACITY_MW,
                    efficiency=1.0, carrier="water_balance_adjustment",
                    marginal_cost=WATER_BALANCE_ADJUSTMENT_COST)


def _add_exact_measured_storage(
    network: pypsa.Network, snapshots: pd.DatetimeIndex, measured: pd.DataFrame,
) -> None:
    """Match each next-day reading at the end of the preceding model day."""
    if measured.empty:
        return
    states = network.model["Store-e"]
    store_dim = next(dim for dim in states.dims if dim != "snapshot")
    for day in snapshots.normalize().unique():
        next_day = day + pd.Timedelta(days=1)
        if next_day not in measured.index:
            continue
        terminal = snapshots[snapshots.normalize() == day][-1]
        for component in measured.columns:
            network.model.add_constraints(
                states.sel({store_dim: component, "snapshot": terminal})
                == float(measured.at[next_day, component]),
                name=f"GlobalConstraint-MeasuredStore-{component}-{day:%Y%m%d}",
            )


def _measured_water_balance_daily(
    network: pypsa.Network, measured: pd.DataFrame,
) -> pd.DataFrame:
    """Keep reported inflow and explicit reconciliation separate in the results."""
    rows = []
    for component in measured.columns:
        bus = str(network.stores.at[component, "bus"])
        inflow = network.generators.index[
            network.generators.carrier.eq("water_inflow")
            & network.generators.bus.eq(bus)
        ][0]
        outgoing = network.links.index[network.links.bus0.eq(bus)]
        state = network.stores_t.e[component]
        opening = state.shift(1)
        opening.iloc[0] = float(network.stores.at[component, "e_initial"])
        daily = pd.DataFrame({
            "date": opening.resample("D").first().index,
            "store": component,
            "measured_opening_mwh": measured[component].reindex(opening.resample("D").first().index).to_numpy(),
            "model_opening_mwh": opening.resample("D").first().to_numpy(),
            "reported_inflow_mwh": network.generators_t.p[inflow].resample("D").sum().to_numpy(),
            "balancing_inflow_mwh": network.generators_t.p[f"{component}_balance_inflow"].resample("D").sum().to_numpy(),
            "balancing_outflow_mwh": network.links_t.p0[f"{component}_balance_outflow"].resample("D").sum().to_numpy(),
            "total_outflow_mwh": network.links_t.p0[outgoing].sum(axis=1).resample("D").sum().to_numpy(),
            "model_closing_mwh": state.resample("D").last().to_numpy(),
        })
        daily["opening_error_mwh"] = daily.model_opening_mwh - daily.measured_opening_mwh
        rows.append(daily)
    return pd.concat(rows, ignore_index=True)


def _upper_kodayar_daily_balance(network: pypsa.Network) -> pd.DataFrame:
    """Audit Upper Kodayar inflow, turbine withdrawal, and explicit spill."""
    component = "Upper_Kodayar"
    inflow = "upper_kodayar_estimated_inflow"
    if component not in network.stores.index or inflow not in network.generators.index:
        raise ValueError("Upper Kodayar Store or estimated inflow is missing")
    bus = str(network.stores.at[component, "bus"])
    turbines = network.links.index[
        network.links.bus0.eq(bus)
        & network.links.index.str.startswith("kodayar_I__")
    ]
    spill = network.links.index[
        network.links.bus0.eq(bus)
        & (network.links.index == "upper_kodayar_spill")
        & network.links.carrier.eq("water_spill")
    ]
    other_outlets = network.links.index[
        network.links.bus0.eq(bus)
        & ~network.links.index.isin(turbines.union(spill))
    ]
    other_inflow = network.generators.index[
        network.generators.bus.eq(bus) & (network.generators.index != inflow)
    ]
    incoming_links = network.links.index[
        network.links.bus1.eq(bus) | network.links.bus2.eq(bus)
    ]
    if (len(turbines) != 1 or len(spill) != 1 or len(other_outlets)
            or len(other_inflow) or len(incoming_links)):
        raise ValueError(
            "Upper Kodayar must have only estimated inflow, Kodayar-I withdrawal, "
            "and its explicit spill outlet"
        )
    available_inflow = (
        network.generators_t.p_max_pu[inflow]
        * float(network.generators.at[inflow, "p_nom"])
    )
    if (network.generators_t.p[inflow] - available_inflow).abs().max() > 1e-4:
        raise ValueError("Estimated Upper Kodayar inflow was not fully admitted")
    state = network.stores_t.e[component]
    opening = state.shift(1)
    opening.iloc[0] = float(network.stores.at[component, "e_initial"])
    frame = pd.DataFrame({
        "date": state.resample("D").last().index,
        "opening_storage_mwh_equiv": opening.resample("D").first().to_numpy(),
        "estimated_natural_inflow_mwh_equiv": network.generators_t.p[inflow].resample("D").sum().to_numpy(),
        "kodayar_i_water_use_mwh_equiv": network.links_t.p0[turbines[0]].resample("D").sum().to_numpy(),
        "spill_mwh_equiv": network.links_t.p0[spill[0]].resample("D").sum().to_numpy(),
        "other_release_mwh_equiv": 0.0,
        "closing_storage_mwh_equiv": state.resample("D").last().to_numpy(),
    })
    frame["balance_residual_mwh_equiv"] = (
        frame.closing_storage_mwh_equiv
        - frame.opening_storage_mwh_equiv
        - frame.estimated_natural_inflow_mwh_equiv
        + frame.kodayar_i_water_use_mwh_equiv
        + frame.spill_mwh_equiv
    )
    for column in [
        "opening_storage", "estimated_natural_inflow", "kodayar_i_water_use",
        "spill", "other_release", "closing_storage", "balance_residual",
    ]:
        frame[f"{column}_mcft"] = (
            frame[f"{column}_mwh_equiv"] / KODAYAR_WATER_MWH_PER_MCFT
        )
    return frame


def _hydro_storage_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    """Load storage evidence and hash the hydro inputs used by checkpoints."""
    paths = [
        MODEL_DIR / "hydro_observed_soc.csv",
        MODEL_DIR / "hydro_reservoir_parameters.csv",
        MODEL_DIR / "hydro_monthly_stored_energy.csv",
        MODEL_DIR / "upper_kodayar_inflow_estimate.csv",
        MODEL_DIR / "generators.csv",
        MODEL_DIR / "generators-p_max_pu.csv",
        MODEL_DIR / "generators-p_min_pu.csv",
        MODEL_DIR / "storage_units-inflow.csv",
        MODEL_DIR / "hydro_cascade_generation_targets.csv",
        MODEL_DIR / "stores.csv",
        MODEL_DIR / "links.csv",
    ]
    if not paths[0].exists() or not paths[1].exists():
        raise FileNotFoundError("Rolling hydro storage needs observed SOC and reservoir parameters")
    missing_kodayar = [path.name for path in paths[3:] if not path.exists()]
    if missing_kodayar:
        raise FileNotFoundError(f"Rolling hydro inputs are missing: {missing_kodayar}")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes() if path.exists() else b"<absent>")
    observed = pd.read_csv(paths[0], parse_dates=["date"])
    parameters = pd.read_csv(paths[1]).drop_duplicates("pypsa_component", keep="last")
    required_observed = {"date", "pypsa_component", "soc_mwh", "data_quality"}
    required_parameters = {
        "pypsa_component", "initial_soc_method", "terminal_soc_method",
        "boundary_data_resolution", "e_nom_mwh", "data_quality",
    }
    if required_observed - set(observed) or required_parameters - set(parameters):
        raise ValueError("Hydro storage input files are missing required columns")
    observed["date"] = observed["date"].dt.normalize()
    if observed.duplicated(["date", "pypsa_component"]).any():
        raise ValueError("Hydro SOC has duplicate date/component targets")
    observed = observed.set_index(["date", "pypsa_component"]).sort_index()
    parameters = parameters.set_index("pypsa_component")
    monthly = pd.DataFrame(columns=["date", "pypsa_component", "stored_energy_mwh"])
    if paths[2].exists():
        monthly = pd.read_csv(paths[2], parse_dates=["date"])
        if {"date", "pypsa_component", "stored_energy_mwh"} - set(monthly):
            raise ValueError("Monthly stored-energy file is missing required columns")
        monthly["date"] = monthly["date"].dt.normalize()
        if monthly.duplicated(["date", "pypsa_component"]).any():
            raise ValueError("Monthly stored energy has duplicate date/component targets")
    return observed, parameters, monthly, digest.hexdigest()


def _hydro_targets_at(
    date: pd.Timestamp, observed: pd.DataFrame, parameters: pd.DataFrame,
    monthly: pd.DataFrame, *, initial: bool = False,
) -> pd.DataFrame:
    """Use only actual dated evidence; interpolate the optional monthly series."""
    day = pd.Timestamp(date).normalize()
    rows = []
    if day in observed.index.get_level_values("date"):
        daily = observed.xs(day, level="date")
        for component, record in daily.iterrows():
            if component not in parameters.index:
                continue
            method = parameters.at[
                component, "initial_soc_method" if initial else "terminal_soc_method"
            ]
            allowed = ({"observed_daily", "proxy_fraction"} if initial
                       else {"observed_daily_target", "proxy_target"})
            if method not in allowed:
                continue
            rows.append((component, float(record.soc_mwh), str(record.data_quality)))
    if not monthly.empty:
        monthly_components = parameters.index[
            parameters["boundary_data_resolution"].eq("monthly")
        ]
        for component in monthly_components:
            series = monthly.loc[monthly.pypsa_component.eq(component)].set_index("date")["stored_energy_mwh"].sort_index()
            if series.empty or not series.index.min() <= day <= series.index.max():
                continue
            points = series.index.union(pd.DatetimeIndex([day]))
            target = series.reindex(points).interpolate(method="time").at[day]
            rows.append((component, float(target), str(parameters.at[component, "data_quality"])))
    targets = pd.DataFrame(rows, columns=["component", "target_mwh", "quality"])
    if targets.empty:
        return targets.set_index("component")
    if not np.isfinite(targets.target_mwh).all():
        raise ValueError(f"Non-finite hydro storage target at {day.date()}")
    return targets.set_index("component")


def _add_hydro_terminal_targets(
    network: pypsa.Network, committed: pd.DatetimeIndex, targets: pd.DataFrame,
) -> None:
    """Price violations outside quality-based terminal storage bands."""
    if targets.empty:
        return
    model = network.model
    terminal = committed[-1]
    penalty = 0
    for kind, frame, variable_name in [
        ("StorageUnit", network.storage_units, "StorageUnit-state_of_charge"),
        ("Store", network.stores, "Store-e"),
    ]:
        if variable_name not in model.variables:
            continue
        variables = model[variable_name]
        dimension = next(dim for dim in variables.dims if dim != "snapshot")
        component_targets = targets.loc[targets.index.intersection(frame.index)]
        if component_targets.empty:
            continue
        component_index = pd.Index(component_targets.index, name=dimension)
        shortfalls = model.add_variables(
            lower=0,
            coords=[component_index],
            name=f"{kind}-hydro_shortfall",
        )
        excesses = model.add_variables(
            lower=0,
            coords=[component_index],
            name=f"{kind}-hydro_excess",
        )
        for component, row in component_targets.iterrows():
            capacity = (float(frame.at[component, "p_nom"]) * float(frame.at[component, "max_hours"])
                        if kind == "StorageUnit" else float(frame.at[component, "e_nom"]))
            if not np.isfinite(capacity) or capacity <= 0:
                raise ValueError(f"Invalid storage capacity for {component}")
            quality = row.quality if row.quality in HYDRO_BAND_PU else "low"
            target = float(row.target_mwh)
            if not 0 <= target <= capacity * (1 + 1e-6):
                raise ValueError(f"Hydro target outside capacity for {component}: {target}")
            band = HYDRO_BAND_PU[quality] * capacity
            lower = max(0.0, target - band)
            upper = min(capacity, target + band)
            shortfall = shortfalls.sel({dimension: component})
            excess = excesses.sel({dimension: component})
            state = variables.sel({dimension: component, "snapshot": terminal})
            model.add_constraints(
                state + shortfall >= lower,
                name=f"GlobalConstraint-HydroLower-{component}",
            )
            model.add_constraints(
                state - excess <= upper,
                name=f"GlobalConstraint-HydroUpper-{component}",
            )
            penalty = penalty + HYDRO_SLACK_COST[quality] * (shortfall + excess)
    if not isinstance(penalty, int):
        model.add_objective(model.objective.expression + penalty, overwrite=True)


def _hydro_terminal_error(network: pypsa.Network, targets: pd.DataFrame) -> tuple[float, float]:
    """Return the total and largest MWh violation of committed-end bands."""
    violations = []
    for component, row in targets.iterrows():
        if component in network.storage_units.index:
            state = float(network.storage_units_t.state_of_charge.iloc[-1][component])
            capacity = float(network.storage_units.at[component, "p_nom"] * network.storage_units.at[component, "max_hours"])
        elif component in network.stores.index:
            state = float(network.stores_t.e.iloc[-1][component])
            capacity = float(network.stores.at[component, "e_nom"])
        else:
            continue
        band = HYDRO_BAND_PU.get(row.quality, HYDRO_BAND_PU["low"]) * capacity
        violations.append(max(0.0, abs(state - row.target_mwh) - band))
    return float(sum(violations)), float(max(violations, default=0.0))


def _write_shadow_prices(network: pypsa.Network, path: Path) -> int:
    """Persist every Linopy constraint dual without relying on PyPSA mapping."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Coordinates across variables not equal. Perform outer join.",
            category=UserWarning,
        )
        duals = network.model.dual
    if not len(duals.data_vars):
        raise RuntimeError("The accepted solution does not contain shadow prices")
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoding = {
        name: {"zlib": True, "complevel": 4, "shuffle": True}
        for name in duals.data_vars
    }
    duals.to_netcdf(temporary, engine="netcdf4", encoding=encoding)
    temporary.replace(path)
    return len(duals.data_vars)


def _hydro_terminal_slacks(network: pypsa.Network) -> pd.DataFrame:
    """Return solved terminal-band slack variables in a portable tabular form."""
    rows = []
    for component_type in ("StorageUnit", "Store"):
        values: dict[str, pd.Series] = {}
        for direction in ("shortfall", "excess"):
            name = f"{component_type}-hydro_{direction}"
            if name not in network.model.variables:
                continue
            variable = network.model[name]
            dimensions = [dim for dim in variable.dims if dim != "snapshot"]
            if len(dimensions) != 1:
                raise ValueError(f"Unexpected dimensions for {name}: {variable.dims}")
            values[direction] = variable.solution.to_series()
        if not values:
            continue
        table = pd.DataFrame(values).fillna(0.0)
        table.index.name = "component"
        table.insert(0, "component_type", component_type)
        rows.append(table.reset_index())
    if not rows:
        return pd.DataFrame(
            columns=["component_type", "component", "shortfall", "excess"]
        )
    return pd.concat(rows, ignore_index=True)


class _SolveResourceMonitor:
    """Sample this process while PyPSA builds and solves one window."""

    def __init__(self, interval_seconds: float = 0.5) -> None:
        self.process = psutil.Process()
        self.interval_seconds = interval_seconds
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.peak_rss_bytes = 0
        self.peak_threads = 0
        self.peak_cpu_cores_sampled = 0.0
        self.cpu_start = 0.0
        self.cpu_seconds = 0.0
        self.last_cpu_seconds = 0.0
        self.last_sample_time = 0.0
        self.started_at = 0.0

    def _sample(self) -> None:
        try:
            self.peak_rss_bytes = max(
                self.peak_rss_bytes, self.process.memory_info().rss
            )
            # Exclude the monitor thread only while it is running.
            monitor_running = self.thread is not None and self.thread.is_alive()
            self.peak_threads = max(
                self.peak_threads, self.process.num_threads() - int(monitor_running)
            )
            cpu = self.process.cpu_times()
            cpu_seconds = cpu.user + cpu.system
            sampled_at = time.perf_counter()
            elapsed = sampled_at - self.last_sample_time
            if elapsed >= 0.1:
                self.peak_cpu_cores_sampled = max(
                    self.peak_cpu_cores_sampled,
                    max(0.0, cpu_seconds - self.last_cpu_seconds) / elapsed,
                )
                self.last_cpu_seconds = cpu_seconds
                self.last_sample_time = sampled_at
        except psutil.Error:
            pass

    def _run(self) -> None:
        while not self.stop.wait(self.interval_seconds):
            self._sample()

    def __enter__(self) -> "_SolveResourceMonitor":
        cpu = self.process.cpu_times()
        self.cpu_start = cpu.user + cpu.system
        self.last_cpu_seconds = self.cpu_start
        self.started_at = time.perf_counter()
        self.last_sample_time = self.started_at
        self._sample()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        self._sample()
        cpu = self.process.cpu_times()
        self.cpu_seconds = cpu.user + cpu.system - self.cpu_start
        elapsed = time.perf_counter() - self.started_at
        if elapsed > 0:
            self.peak_cpu_cores_sampled = max(
                self.peak_cpu_cores_sampled, self.cpu_seconds / elapsed
            )


def _set_initial_state(
    network: pypsa.Network,
    previous: pypsa.Network | None,
    unit_commitment: str = "full",
) -> None:
    """Set the state immediately before this window's first snapshot."""
    if previous is None:
        initially_down = (
            network.generators.committable
            & network.generators.down_time_before.fillna(0).gt(0)
        )
        network.generators.loc[initially_down, "p_init"] = np.nan
        return

    committable = network.generators.index[network.generators.committable]
    if unit_commitment == "relaxed" and len(committable):
        # PyPSA's static pre-horizon fields describe an integer on/off state.
        # The relaxed boundary is imposed directly on the Linopy model below,
        # so disable those binary-history assumptions here.
        network.generators.loc[committable, "up_time_before"] = 0
        network.generators.loc[committable, "down_time_before"] = 0
        network.generators.loc[committable, "p_init"] = np.nan
    elif len(committable):
        status = previous.generators_t.status.reindex(columns=committable)
        last_status = status.iloc[-1].round().astype(int)

    # PyPSA needs the number of consecutive hours already on/off before the
    # first snapshot to enforce minimum up/down times across the boundary.
        for generator in committable:
            values = status[generator].round().astype(int).to_numpy()
            final_value = int(values[-1])
            run_length = 0
            for value in values[::-1]:
                if int(value) != final_value:
                    break
                run_length += 1
            network.generators.at[generator, "up_time_before"] = run_length if final_value else 0
            network.generators.at[generator, "down_time_before"] = run_length if not final_value else 0

    # p_init supplies the preceding dispatch for the first ramp constraint.
        network.generators.loc[committable, "p_init"] = (
            previous.generators_t.p.iloc[-1].reindex(committable).fillna(0.0)
        )
        network.generators.loc[committable[last_status.eq(0)], "p_init"] = np.nan
    elif unit_commitment == "none":
        # Preserve dispatch across windows for ramp-constrained generators.
        network.generators.loc[:, "p_init"] = previous.generators_t.p.iloc[-1].reindex(
            network.generators.index
        ).fillna(0.0)
        network.generators.loc[:, "up_time_before"] = 1

    if len(network.storage_units):
        network.storage_units.loc[:, "state_of_charge_initial"] = (
            previous.storage_units_t.state_of_charge.iloc[-1]
            .reindex(network.storage_units.index)
            .fillna(0.0)
        )
        # A cyclic state would force every weekly window back to its starting
        # SOC and defeat chronological state transfer.
        network.storage_units.loc[:, "cyclic_state_of_charge"] = False
    if len(network.stores):
        network.stores.loc[:, "e_initial"] = (
            previous.stores_t.e.iloc[-1].reindex(network.stores.index).fillna(0.0)
        )
        network.stores.loc[:, "e_cyclic"] = False


def _disable_first_snapshot_constraints(
    network: pypsa.Network,
    names: list[str],
    snapshot: pd.Timestamp,
    generators: pd.Index,
) -> None:
    """Mask native constraints whose pre-horizon state is necessarily binary."""
    for name in names:
        if name not in network.model.constraints:
            continue
        labels = network.model.constraints[name].labels
        generator_dim = generator_dimension(labels)
        constrained = generators.intersection(labels.indexes[generator_dim])
        if len(constrained):
            labels.loc[{"snapshot": snapshot, generator_dim: constrained}] = -1


def _add_relaxed_boundary_constraints(
    network: pypsa.Network,
    previous: pypsa.Network | None,
) -> None:
    """Carry fractional commitment, transitions, and ramps between windows."""
    if previous is None:
        return
    generators = network.generators.index[network.generators.committable]
    if not len(generators):
        return

    first = pd.Timestamp(network.snapshots[0])
    model = network.model
    _disable_first_snapshot_constraints(
        network,
        [
            "Generator-com-transition-start-up",
            "Generator-com-transition-shut-down",
            "Generator-p-ramp_limit_up",
            "Generator-p-ramp_limit_down",
        ],
        first,
        generators,
    )

    def select_generators(variable):
        generator_dim = generator_dimension(variable)
        selected = variable.sel({generator_dim: generators})
        if generator_dim != "name":
            selected = selected.rename({generator_dim: "name"})
        return selected

    status = select_generators(model["Generator-status"])
    start_up = select_generators(model["Generator-start_up"])
    shut_down = select_generators(model["Generator-shut_down"])
    dispatch = select_generators(model["Generator-p"])
    status_now = status.sel(snapshot=first)
    start_up_now = start_up.sel(snapshot=first)
    shut_down_now = shut_down.sel(snapshot=first)
    dispatch_now = dispatch.sel(snapshot=first)

    def as_array(values: pd.Series) -> xr.DataArray:
        values = values.reindex(generators).astype(float)
        return xr.DataArray(
            values.to_numpy(), dims="name", coords={"name": generators.to_numpy()}
        )

    status_before = as_array(
        previous.generators_t.status.iloc[-1].clip(lower=0.0, upper=1.0)
    )
    dispatch_before = as_array(previous.generators_t.p.iloc[-1])
    nominal = as_array(network.generators.loc[generators, "p_nom"])
    lower_pu = as_array(
        network.get_switchable_as_dense("Generator", "p_min_pu")
        .loc[first, generators]
    )
    upper_pu = as_array(
        network.get_switchable_as_dense("Generator", "p_max_pu")
        .loc[first, generators]
    )
    lower = nominal * lower_pu
    upper = nominal * upper_pu
    ramp_up = nominal * as_array(
        network.generators.loc[generators, "ramp_limit_up"].fillna(1.0)
    )
    ramp_down = nominal * as_array(
        network.generators.loc[generators, "ramp_limit_down"].fillna(1.0)
    )
    ramp_start = nominal * as_array(
        network.generators.loc[generators, "ramp_limit_start_up"].fillna(1.0)
    )
    ramp_shut = nominal * as_array(
        network.generators.loc[generators, "ramp_limit_shut_down"].fillna(1.0)
    )

    model.add_constraints(
        start_up_now >= status_now - status_before,
        name="Generator-relaxed-boundary-start-up",
    )
    model.add_constraints(
        shut_down_now + status_now >= status_before,
        name="Generator-relaxed-boundary-shut-down",
    )
    model.add_constraints(
        dispatch_now - ramp_start * status_now - dispatch_before
        <= (ramp_up - ramp_start) * status_before,
        name="Generator-relaxed-boundary-ramp-up",
    )
    model.add_constraints(
        dispatch_now + (ramp_down - ramp_shut) * status_now
        >= dispatch_before - ramp_shut * status_before,
        name="Generator-relaxed-boundary-ramp-down",
    )

    # PyPSA's tightened Hua relaxation starts at the second snapshot. Add the
    # same four inequalities across the rolling boundary.
    model.add_constraints(
        (upper - ramp_shut) * (status_now - start_up_now)
        >= dispatch_before - ramp_shut * status_before,
        name="Generator-relaxed-boundary-p-before",
    )
    model.add_constraints(
        dispatch_now
        - upper * status_now
        + (upper - ramp_start) * start_up_now
        <= 0,
        name="Generator-relaxed-boundary-p-current",
    )
    model.add_constraints(
        dispatch_now
        - dispatch_before
        - (lower + ramp_up) * status_now
        + lower * status_before
        + (lower + ramp_up - ramp_start) * start_up_now
        <= 0,
        name="Generator-relaxed-boundary-partly-start-up",
    )
    model.add_constraints(
        dispatch_now
        - (ramp_shut - ramp_down) * status_now
        + (lower + ramp_down - ramp_shut) * start_up_now
        >= dispatch_before - ramp_shut * status_before,
        name="Generator-relaxed-boundary-partly-shut-down",
    )

    # Complete minimum up/down rolling sums with fractional transitions from
    # the preceding retained window. Native within-window constraints remain.
    prior_start_up = previous.generators_t.start_up.reindex(columns=generators)
    prior_shut_down = previous.generators_t.shut_down.reindex(columns=generators)
    snapshots = pd.DatetimeIndex(network.snapshots)
    min_up_times = network.generators.loc[generators, "min_up_time"].astype(int)
    for duration in sorted(min_up_times.unique()):
        if duration <= 1:
            continue
        units = min_up_times.index[min_up_times.eq(duration)]
        for position in range(min(duration - 1, len(snapshots))):
            missing = duration - position - 1
            prior = prior_start_up.loc[:, units].iloc[-missing:].sum()
            prior_array = xr.DataArray(
                prior.to_numpy(), dims="name", coords={"name": units.to_numpy()}
            )
            current = start_up.sel(
                snapshot=snapshots[: position + 1], name=units
            ).sum("snapshot")
            model.add_constraints(
                current + prior_array
                <= status.sel(snapshot=snapshots[position], name=units),
                name=f"Generator-relaxed-boundary-up-time-{duration}h-{position}",
            )

    min_down_times = network.generators.loc[generators, "min_down_time"].astype(int)
    for duration in sorted(min_down_times.unique()):
        if duration <= 1:
            continue
        units = min_down_times.index[min_down_times.eq(duration)]
        for position in range(min(duration - 1, len(snapshots))):
            missing = duration - position - 1
            prior = prior_shut_down.loc[:, units].iloc[-missing:].sum()
            prior_array = xr.DataArray(
                prior.to_numpy(), dims="name", coords={"name": units.to_numpy()}
            )
            current = shut_down.sel(
                snapshot=snapshots[: position + 1], name=units
            ).sum("snapshot")
            model.add_constraints(
                status.sel(snapshot=snapshots[position], name=units)
                + current
                + prior_array
                <= 1,
                name=f"Generator-relaxed-boundary-down-time-{duration}h-{position}",
            )


def _prepare_topology_for_pandas3(network: pypsa.Network) -> None:
    """Build topology without triggering PyPSA 1.2/Pandas 3 read-only views."""
    # Network.copy(snapshots=...) can leave these object columns backed by
    # read-only arrays under pandas 3. PyPSA mutates them while selecting slack
    # generators and buses, so replace the complete columns with writable data.
    for frame, columns in [
        (network.generators, ["control"]),
        (network.buses, ["control", "generator"]),
    ]:
        for column in columns:
            frame[column] = frame[column].astype(object).to_numpy(copy=True)
    network.determine_network_topology()


def _add_daily_targets(
    network: pypsa.Network,
    snapshots: pd.DatetimeIndex,
    gas_targets: pd.Series,
) -> None:
    add_nuclear_targets(network, snapshots)
    gas_units = network.generators.index[network.generators.carrier == "oil_gas"]
    dispatch = network.model["Generator-p"]
    generator_dim = generator_dimension(dispatch)
    for date, target in gas_targets.items():
        hours = snapshots[snapshots.normalize() == date]
        if len(hours):
            weights = xr.DataArray(
                network.snapshot_weightings.generators.reindex(hours),
                dims="snapshot",
                coords={"snapshot": hours},
            )
            expression = dispatch.sel({generator_dim: gas_units, "snapshot": hours})
            expression = (expression * weights).sum()
            network.model.add_constraints(
                expression == float(target),
                name=f"GlobalConstraint-RollingOilGasDailyTarget-{date:%Y%m%d}",
            )


def _add_committed_biomass_target(
    network: pypsa.Network,
    committed_snapshots: pd.DatetimeIndex,
    monthly_targets_mwh: pd.Series,
) -> None:
    """Apply the month-prorated bio-power target to the accepted window.

    A standard PyPSA operational-limit applies to the whole active window,
    including its look-ahead day.  Constraining only accepted snapshots avoids
    double-counting that look-ahead. Month-crossing windows receive a prorated
    share from each month they cover.
    """
    biomass_units = network.generators.index[
        network.generators.carrier.eq("bio_power")
    ]
    if not len(biomass_units):
        raise ValueError("Biomass target is configured but no bio_power units exist")
    weights = xr.DataArray(
        network.snapshot_weightings.generators.reindex(committed_snapshots),
        dims="snapshot",
        coords={"snapshot": committed_snapshots},
    )
    dispatch = network.model["Generator-p"]
    generator_dim = generator_dimension(dispatch)
    expression = dispatch.sel(
        {generator_dim: biomass_units, "snapshot": committed_snapshots}
    )
    expression = (expression * weights).sum()
    target_mwh = bio_power_energy_target_mwh(
        committed_snapshots, network.snapshot_weightings.generators, monthly_targets_mwh
    )
    network.model.add_constraints(
        expression == target_mwh,
        name=(
            "GlobalConstraint-RollingBiomassEnergyTarget-"
            f"{committed_snapshots[0]:%Y%m%d}"
        ),
    )


def _add_committed_small_hydro_target(
    network: pypsa.Network,
    committed_snapshots: pd.DatetimeIndex,
    monthly_targets_mwh: pd.Series,
) -> None:
    """Fix weekly small-hydro energy while leaving its hourly dispatch free."""
    units = network.generators.index[network.generators.carrier.eq("small_hydro")]
    if not len(units):
        raise ValueError("Small-hydro target is configured but no small_hydro units exist")
    weights = xr.DataArray(
        network.snapshot_weightings.generators.reindex(committed_snapshots),
        dims="snapshot",
        coords={"snapshot": committed_snapshots},
    )
    dispatch = network.model["Generator-p"]
    generator_dim = generator_dimension(dispatch)
    expression = dispatch.sel({generator_dim: units, "snapshot": committed_snapshots})
    target_mwh = small_hydro_energy_target_mwh(
        committed_snapshots, network.snapshot_weightings.generators, monthly_targets_mwh
    )
    network.model.add_constraints(
        (expression * weights).sum() == target_mwh,
        name=(
            "GlobalConstraint-RollingSmallHydroEnergyTarget-"
            f"{committed_snapshots[0]:%Y%m%d}"
        ),
    )


def _window_metrics(network: pypsa.Network, snapshots: pd.DatetimeIndex) -> dict:
    weights = network.snapshot_weightings.generators.reindex(snapshots)
    generator_energy = network.generators_t.p.reindex(snapshots).mul(weights, axis=0)
    energy = generator_energy.T.groupby(network.generators.carrier).sum().T
    storage = (
        network.storage_units_t.p.reindex(snapshots).clip(lower=0).mul(weights, axis=0)
        if len(network.storage_units)
        else pd.DataFrame(index=snapshots)
    )
    daily = energy.groupby(energy.index.normalize()).sum() / 1000.0
    storage_by_carrier = storage.T.groupby(network.storage_units.carrier).sum().T
    storage_daily = storage_by_carrier.groupby(storage.index.normalize()).sum() / 1000.0
    output = pd.DataFrame(index=daily.index)
    for carrier in ["coal", "oil_gas", "nuclear", "hydro", "solar", "wind", "diesel"]:
        output[carrier] = daily.get(carrier, pd.Series(0.0, index=daily.index))
    output["hydro"] = output["hydro"].add(
        storage_daily.get("hydro", pd.Series(0.0, index=daily.index)), fill_value=0.0
    )
    output["hydro"] = output["hydro"].add(
        daily.get("hydro_run_of_river", pd.Series(0.0, index=daily.index)), fill_value=0.0
    )
    hydro_links = network.links.index[network.links.carrier.eq("hydro_turbine")]
    if len(hydro_links):
        link_daily = (-network.links_t.p1.reindex(snapshots)[hydro_links]).mul(weights, axis=0).sum(axis=1)
        link_daily = link_daily.groupby(link_daily.index.normalize()).sum() / 1_000.0
        output["hydro"] = output["hydro"].add(link_daily, fill_value=0.0)
    output["bio_power"] = daily.get("bio_power", pd.Series(0.0, index=daily.index))
    output["small_hydro"] = daily.get("small_hydro", pd.Series(0.0, index=daily.index))
    output["other_res"] = output["bio_power"] + output["small_hydro"]
    output["market_import"] = daily.get("market_import", 0.0)
    output["unserved_energy"] = daily.get("unserved_energy", 0.0)
    output["system_plant_generation"] = output[
        ["coal", "oil_gas", "nuclear", "hydro", "solar", "wind", "diesel", "other_res"]
    ].sum(axis=1)
    output["comparable_total"] = output[
        ["coal", "oil_gas", "nuclear", "hydro", "solar", "wind"]
    ].sum(axis=1)
    output.index.name = "date"
    return output


def run_rolling_year(
    base_network: pypsa.Network,
    observed: pd.DataFrame,
    oil_gas_budget: pd.DataFrame,
    *,
    solver_name: str,
    mip_rel_gap: float,
    time_limit: float | None,
    max_time_limit_mip_gap: float,
    solver_threads: int,
    simplex_strategy: int | None = None,
    unit_commitment: str,
    output_dir: Path,
    resume: bool = True,
    commit_days: int = 7,
    lookahead_days: int = 1,
    max_windows: int | None = None,
) -> tuple[pd.DataFrame, dict, None]:
    """Solve the complete network horizon in chronological rolling windows."""
    if commit_days < 1 or lookahead_days < 1:
        raise ValueError("commit_days and lookahead_days must both be positive")
    if time_limit is not None and time_limit <= 0:
        raise ValueError("time_limit must be positive or None")
    if not 0 <= max_time_limit_mip_gap <= 1:
        raise ValueError("max_time_limit_mip_gap must be between 0 and 1")
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = output_dir / "weekly_networks"
    chunks_dir.mkdir(exist_ok=True)
    snapshots = pd.DatetimeIndex(base_network.snapshots)
    if not snapshots.is_monotonic_increasing or snapshots.has_duplicates:
        raise ValueError("Network snapshots must be unique and chronological")
    cascade_targets = _cascade_generation_targets()
    if BIOMASS_ENERGY_CONSTRAINT not in base_network.global_constraints.index:
        raise ValueError(
            f"The model is missing {BIOMASS_ENERGY_CONSTRAINT!r}; rebuild it first"
        )
    biomass_monthly_targets_mwh = read_bio_power_monthly_targets()
    small_hydro_monthly_targets_mwh = read_small_hydro_monthly_targets()
    small_hydro_units = base_network.generators.index[
        base_network.generators.carrier.eq("small_hydro")
    ]
    if not len(small_hydro_units):
        raise ValueError("Small-hydro targets require small_hydro generators")
    # The inherited placeholder profile cannot supply the observed monthly
    # energy. Weekly energy equalities replace that profile as the operating
    # evidence, so availability is nameplate and timing is optimized.
    base_network.generators_t.p_max_pu.loc[:, small_hydro_units] = 1.0
    observed_soc, hydro_parameters, monthly_soc, hydro_input_hash = _hydro_storage_inputs()
    measured_store_targets = _measured_store_targets(
        observed_soc, hydro_parameters, base_network
    )

    step = pd.Timedelta(days=commit_days)
    horizon = pd.Timedelta(days=commit_days + lookahead_days)
    starts = pd.date_range(snapshots[0].normalize(), snapshots[-1].normalize(), freq=step)
    previous: pypsa.Network | None = None
    daily_parts: list[pd.DataFrame] = []
    water_balance_parts: list[pd.DataFrame] = []
    kodayar_balance_parts: list[pd.DataFrame] = []
    window_rows: list[dict] = []
    run_started = time.perf_counter()

    for number, start in enumerate(starts, start=1):
        if max_windows is not None and number > max_windows:
            break
        commit_end = min(start + step, snapshots[-1] + pd.Timedelta(hours=1))
        solve_end = min(start + horizon, snapshots[-1] + pd.Timedelta(hours=1))
        active = snapshots[(snapshots >= start) & (snapshots < solve_end)]
        committed = active[active < commit_end]
        expected_bio_power_target_mwh = bio_power_energy_target_mwh(
            committed,
            base_network.snapshot_weightings.generators,
            biomass_monthly_targets_mwh,
        )
        expected_small_hydro_target_mwh = small_hydro_energy_target_mwh(
            committed,
            base_network.snapshot_weightings.generators,
            small_hydro_monthly_targets_mwh,
        )
        terminal_targets = _hydro_targets_at(
            committed[-1], observed_soc, hydro_parameters, monthly_soc
        ).drop(index=measured_store_targets.columns, errors="ignore")
        checkpoint = chunks_dir / f"window_{number:03d}_{start:%Y-%m-%d}.nc"
        daily_file = checkpoint.with_suffix(".daily.csv")
        dual_file = checkpoint.with_suffix(".duals.nc")
        slack_file = checkpoint.with_suffix(".hydro_terminal_slacks.csv")
        meta_file = checkpoint.with_suffix(".json")

        if (
            resume
            and checkpoint.exists()
            and daily_file.exists()
            and dual_file.exists()
            and slack_file.exists()
            and meta_file.exists()
        ):
            checkpoint_meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if (checkpoint_meta.get("model_version") == CHECKPOINT_MODEL_VERSION
                    and checkpoint_meta.get("hydro_input_hash") == hydro_input_hash
                    and checkpoint_meta.get("unit_commitment", "full") == unit_commitment
                    and np.isclose(
                        checkpoint_meta.get("bio_power_target_mwh", np.nan),
                        expected_bio_power_target_mwh,
                    )
                    and np.isclose(
                        checkpoint_meta.get("small_hydro_target_mwh", np.nan),
                        expected_small_hydro_target_mwh,
                    )):
                print(f"Loading completed window {number}/{len(starts)}: {start.date()}", flush=True)
                previous = pypsa.Network(checkpoint)
                checkpoint_daily = pd.read_csv(
                    daily_file, index_col="date", parse_dates=True
                )
                if {"bio_power", "small_hydro"} - set(checkpoint_daily.columns):
                    checkpoint_daily = _window_metrics(previous, previous.snapshots)
                    checkpoint_daily.to_csv(daily_file)
                daily_parts.append(checkpoint_daily)
                water_balance_parts.append(
                    _measured_water_balance_daily(previous, measured_store_targets)
                )
                kodayar_balance_parts.append(_upper_kodayar_daily_balance(previous))
                window_rows.append(checkpoint_meta)
                continue
            print(
                f"Re-solving stale checkpoint {number}/{len(starts)}: {start.date()}",
                flush=True,
            )

        network = base_network.copy(snapshots=active)
        _add_measured_water_balance(network, measured_store_targets)
        # Replace the native global constraint with a committed-snapshot
        # constraint below. Native constraints cover the look-ahead day too.
        network.remove("GlobalConstraint", BIOMASS_ENERGY_CONSTRAINT)
        if SMALL_HYDRO_ENERGY_CONSTRAINT in network.global_constraints.index:
            network.remove("GlobalConstraint", SMALL_HYDRO_ENERGY_CONSTRAINT)
        # PyPSA 1.2.x with pandas 3 can expose imported static columns through
        # read-only NumPy views.  If topology is left until optimize() post-
        # processing, slack-bus assignment then fails after a successful solve
        # with "assignment destination is read-only".  Building topology here
        # avoids that incompatible late mutation.
        _prepare_topology_for_pandas3(network)
        network.storage_units.loc[:, "cyclic_state_of_charge"] = False
        if len(network.stores):
            network.stores.loc[:, "e_cyclic"] = False
        _set_initial_state(network, previous, unit_commitment)
        if previous is None:
            opening = _hydro_targets_at(
                active[0], observed_soc, hydro_parameters, monthly_soc, initial=True
            )
            for component, row in opening.iterrows():
                if component in network.storage_units.index:
                    network.storage_units.at[component, "state_of_charge_initial"] = row.target_mwh
                elif component in network.stores.index:
                    network.stores.at[component, "e_initial"] = row.target_mwh
        dates = active.normalize().unique()
        gas_targets = oil_gas_budget["daily_energy_target_mwh"].reindex(dates)
        if gas_targets.isna().any():
            raise ValueError(f"Missing oil/gas target in window starting {start.date()}")
        if OIL_GAS_BUDGET_CONSTRAINT in network.global_constraints.index:
            network.global_constraints.at[OIL_GAS_BUDGET_CONSTRAINT, "constant"] = float(gas_targets.sum())
        prepare_nuclear_targets(network)

        print(
            f"Solving window {number}/{len(starts)}: {start.date()} to "
            f"{active[-1].date()} ({len(committed)} committed + "
            f"{len(active) - len(committed)} look-ahead hours)",
            flush=True,
        )
        solver_options = highs_solver_options(
            unit_commitment, mip_rel_gap, solver_threads, simplex_strategy
        )
        if time_limit is not None:
            solver_options["time_limit"] = time_limit
        reset_highs_global_scheduler()
        with _SolveResourceMonitor() as resources:
            started = time.perf_counter()
            status, condition = network.optimize(
                solver_name=solver_name,
                solver_options=solver_options,
                extra_functionality=lambda n, s: (
                    _add_daily_targets(n, s, gas_targets),
                    _add_weekly_cascade_targets(n, committed, cascade_targets),
                    _add_committed_biomass_target(
                        n, committed, biomass_monthly_targets_mwh
                    ),
                    _add_committed_small_hydro_target(
                        n, committed, small_hydro_monthly_targets_mwh
                    ),
                    _add_hydro_terminal_targets(n, committed, terminal_targets),
                    _add_exact_measured_storage(n, s, measured_store_targets),
                    _add_relaxed_boundary_constraints(n, previous)
                    if unit_commitment == "relaxed"
                    else None,
                ),
                include_objective_constant=False,
                linearized_unit_commitment=unit_commitment == "relaxed",
                assign_all_duals=True,
            )
            runtime = time.perf_counter() - started
        report = getattr(getattr(network.model, "solver", None), "report", None)
        reported_mip_gap = getattr(report, "mip_gap", None)
        # Linopy normally exposes this via ``solver.report``. Query the native
        # HiGHS model as a fallback, since a time-limited solve can leave the
        # wrapper report empty in some PyPSA/Linopy combinations.
        if unit_commitment == "full" and reported_mip_gap is None:
            solver_model = getattr(network.model, "solver_model", None)
            try:
                reported_mip_gap = solver_model.getInfo().mip_gap
            except (AttributeError, RuntimeError):
                pass
        mip_gap = (
            float(reported_mip_gap)
            if unit_commitment == "full"
            and reported_mip_gap is not None
            and np.isfinite(reported_mip_gap)
            else None
        )
        accepted_time_limit = (
            unit_commitment == "full"
            and status == "ok"
            and condition == "time_limit"
            and mip_gap is not None
            and mip_gap <= max_time_limit_mip_gap
        )
        accepted_solution = status == "ok" and (
            condition == "optimal" or accepted_time_limit
        )
        if not accepted_solution:
            solver_model = getattr(network.model, "solver_model", None)
            native_status = None
            native_info = None
            if solver_model is not None:
                try:
                    native_status = solver_model.modelStatusToString(
                        solver_model.getModelStatus()
                    )
                    info = solver_model.getInfo()
                    native_info = {
                        "primal_solution_status": str(info.primal_solution_status),
                        "dual_solution_status": str(info.dual_solution_status),
                        "simplex_iteration_count": info.simplex_iteration_count,
                        "ipm_iteration_count": info.ipm_iteration_count,
                    }
                except (AttributeError, RuntimeError):
                    pass
            raise RuntimeError(
                f"Window {number} failed: status={status}, condition={condition}, "
                f"HiGHS status={native_status}, HiGHS info={native_info}, "
                f"reported_mip_gap={reported_mip_gap}, accepted_mip_gap={mip_gap}. "
                "Time-limited integer windows require a finite MIP gap "
                f"no greater than {max_time_limit_mip_gap:.1%}; LP windows "
                "must reach optimality. "
                "Completed checkpoints can be resumed."
            )
        if accepted_time_limit:
            print(
                f"Accepting time-limited window {number}/{len(starts)} with "
                f"MIP gap {mip_gap:.2%} (limit {max_time_limit_mip_gap:.2%}).",
                flush=True,
            )

        shadow_price_constraints = _write_shadow_prices(network, dual_file)
        hydro_slacks = _hydro_terminal_slacks(network)
        hydro_slacks.to_csv(slack_file, index=False)

        # The checkpoint contains only accepted hours. It is also the complete
        # state source for the next window and for restart/resume.
        # Release the large Linopy/HiGHS model before checkpointing. Copying a
        # solved 192-hour network here can briefly double RAM and kill a Jupyter
        # kernel even though optimization itself completed successfully.
        del network.model
        network.set_snapshots(committed)
        previous = network
        daily = _window_metrics(previous, committed)
        biomass_target_mwh = expected_bio_power_target_mwh
        biomass_generation_mwh = float(daily["bio_power"].sum() * 1_000.0)
        small_hydro_generation_mwh = float(daily["small_hydro"].sum() * 1_000.0)
        water_balance_daily = _measured_water_balance_daily(
            previous, measured_store_targets
        )
        kodayar_balance_daily = _upper_kodayar_daily_balance(previous)
        kodayar_residual = float(kodayar_balance_daily.balance_residual_mwh_equiv.abs().max())
        if kodayar_residual > 1e-3:
            raise RuntimeError(f"Upper Kodayar water-balance residual is {kodayar_residual:.6g} MWh")
        max_measured_error = float(water_balance_daily.opening_error_mwh.abs().max())
        if max_measured_error > 1e-3:
            raise RuntimeError(
                f"Measured storage opening error is {max_measured_error:.6g} MWh"
            )
        hydro_total_error, hydro_max_error = _hydro_terminal_error(previous, terminal_targets)
        residual = float(previous.buses_t.p.sum(axis=1).abs().max())
        loading = (
            float(previous.links_t.p0.abs().div(previous.links.p_nom, axis=1).max().max() * 100)
            if len(previous.links)
            else 0.0
        )
        row = {
            "model_version": CHECKPOINT_MODEL_VERSION,
            "hydro_input_hash": hydro_input_hash,
            "hydro_terminal_targets": len(terminal_targets),
            "hydro_terminal_band_violation_mwh": hydro_total_error,
            "hydro_max_terminal_band_violation_mwh": hydro_max_error,
            "measured_storage_max_error_mwh": max_measured_error,
            "balancing_inflow_mwh": float(water_balance_daily.balancing_inflow_mwh.sum()),
            "balancing_outflow_mwh": float(water_balance_daily.balancing_outflow_mwh.sum()),
            "upper_kodayar_balance_max_residual_mwh": kodayar_residual,
            "upper_kodayar_inflow_mwh": float(kodayar_balance_daily.estimated_natural_inflow_mwh_equiv.sum()),
            "kodayar_i_water_use_mwh": float(kodayar_balance_daily.kodayar_i_water_use_mwh_equiv.sum()),
            "upper_kodayar_spill_mwh": float(kodayar_balance_daily.spill_mwh_equiv.sum()),
            "bio_power_target_mwh": biomass_target_mwh,
            "bio_power_generation_mwh": biomass_generation_mwh,
            "bio_power_target_error_mwh": biomass_generation_mwh - biomass_target_mwh,
            "small_hydro_target_mwh": expected_small_hydro_target_mwh,
            "small_hydro_generation_mwh": small_hydro_generation_mwh,
            "small_hydro_target_error_mwh": (
                small_hydro_generation_mwh - expected_small_hydro_target_mwh
            ),
            "unit_commitment": unit_commitment,
            "window": number,
            "solve_start": str(active[0]),
            "solve_end": str(active[-1]),
            "commit_end": str(committed[-1]),
            "committed_snapshots": len(committed),
            "lookahead_snapshots": len(active) - len(committed),
            "solver_status": str(status),
            "termination_condition": str(condition),
            "accepted_solution": accepted_solution,
            "shadow_prices_recorded": True,
            "shadow_price_constraints": shadow_price_constraints,
            "shadow_price_file": dual_file.name,
            "hydro_terminal_slack_file": slack_file.name,
            "mip_gap": mip_gap,
            "time_limit_seconds": time_limit,
            "max_time_limit_mip_gap": (
                max_time_limit_mip_gap if unit_commitment == "full" else None
            ),
            "solver_threads": solver_threads,
            "simplex_strategy": solver_options.get("simplex_strategy"),
            "runtime_seconds": runtime,
            "process_cpu_seconds": resources.cpu_seconds,
            "average_cpu_cores": resources.cpu_seconds / runtime if runtime > 0 else None,
            "peak_cpu_cores_sampled": resources.peak_cpu_cores_sampled,
            "peak_process_rss_mb_sampled": resources.peak_rss_bytes / 1_000_000,
            "peak_process_threads_sampled": resources.peak_threads,
            "max_nodal_residual_mw": residual,
            "max_corridor_loading_pct": loading,
        }
        previous.export_to_netcdf(checkpoint)
        daily.to_csv(daily_file)
        meta_file.write_text(json.dumps(row, indent=2), encoding="utf-8")
        daily_parts.append(daily)
        water_balance_parts.append(water_balance_daily)
        kodayar_balance_parts.append(kodayar_balance_daily)
        window_rows.append(row)

        pd.concat(daily_parts).sort_index().to_csv(output_dir / "modeled_daily_generation.partial.csv")
        pd.concat(water_balance_parts).to_csv(
            output_dir / "measured_water_balance.partial.csv", index=False
        )
        pd.concat(kodayar_balance_parts).to_csv(
            output_dir / "upper_kodayar_balance.partial.csv", index=False
        )
        pd.DataFrame(window_rows).to_csv(output_dir / "window_log.partial.csv", index=False)
        print(
            f"Window {number}/{len(starts)}: {runtime:.1f} s wall, "
            f"{resources.cpu_seconds:.1f} s CPU "
            f"({resources.cpu_seconds / runtime:.2f} cores average), "
            f"{resources.peak_cpu_cores_sampled:.2f} peak sampled cores, "
            f"{resources.peak_rss_bytes / 1_000_000:.0f} MB peak RSS, "
            f"{resources.peak_threads} peak process threads. "
            f"Metrics: {output_dir / 'window_log.partial.csv'}",
            flush=True,
        )

    modeled = pd.concat(daily_parts).sort_index() if daily_parts else pd.DataFrame()
    water_balance = (
        pd.concat(water_balance_parts, ignore_index=True)
        if water_balance_parts else pd.DataFrame()
    )
    kodayar_balance = (
        pd.concat(kodayar_balance_parts, ignore_index=True)
        if kodayar_balance_parts else pd.DataFrame()
    )
    window_log = pd.DataFrame(window_rows)
    completed_snapshots = int(window_log["committed_snapshots"].sum()) if len(window_log) else 0
    validation = {
        "model_version": CHECKPOINT_MODEL_VERSION,
        "hydro_input_hash": hydro_input_hash,
        "unit_commitment": unit_commitment,
        "formulation": f"rolling {commit_days}-day commitment with {lookahead_days}-day look-ahead",
        "snapshots_solved_and_retained": completed_snapshots,
        "full_horizon_snapshots": len(snapshots),
        "windows_completed": len(window_log),
        "windows_planned": len(starts),
        "complete": completed_snapshots == len(snapshots),
        "all_solver_statuses_ok": bool(
            len(window_log) and window_log["accepted_solution"].all()
        ),
        "shadow_prices_recorded": bool(
            len(window_log) and window_log["shadow_prices_recorded"].all()
        ),
        "time_limit_seconds": time_limit,
        "max_time_limit_mip_gap": (
            max_time_limit_mip_gap if unit_commitment == "full" else None
        ),
        "solver_threads": solver_threads,
        "simplex_strategy": simplex_strategy,
        "total_unserved_gwh": float(modeled.get("unserved_energy", pd.Series(dtype=float)).sum()),
        "max_abs_bio_power_target_deviation_mwh": float(
            window_log["bio_power_target_error_mwh"].abs().max()
        ) if len(window_log) else None,
        "max_abs_small_hydro_target_deviation_mwh": float(
            window_log["small_hydro_target_error_mwh"].abs().max()
        ) if len(window_log) else None,
        "measured_storage_max_error_mwh": float(
            water_balance.opening_error_mwh.abs().max()
        ) if len(water_balance) else None,
        "balancing_inflow_gwh_equiv": float(
            water_balance.balancing_inflow_mwh.sum() / 1000
        ) if len(water_balance) else None,
        "balancing_outflow_gwh_equiv": float(
            water_balance.balancing_outflow_mwh.sum() / 1000
        ) if len(water_balance) else None,
        "upper_kodayar_balance_max_residual_mwh": float(
            kodayar_balance.balance_residual_mwh_equiv.abs().max()
        ) if len(kodayar_balance) else None,
        "upper_kodayar_estimated_inflow_gwh_equiv": float(
            kodayar_balance.estimated_natural_inflow_mwh_equiv.sum() / 1000
        ) if len(kodayar_balance) else None,
        "kodayar_i_water_use_gwh_equiv": float(
            kodayar_balance.kodayar_i_water_use_mwh_equiv.sum() / 1000
        ) if len(kodayar_balance) else None,
        "upper_kodayar_spill_gwh_equiv": float(
            kodayar_balance.spill_mwh_equiv.sum() / 1000
        ) if len(kodayar_balance) else None,
        "solver_runtime_seconds_sum": float(window_log.get("runtime_seconds", pd.Series(dtype=float)).sum()),
        "wall_clock_seconds_this_session": float(time.perf_counter() - run_started),
    }
    modeled.to_csv(output_dir / "modeled_daily_generation.csv")
    water_balance.to_csv(output_dir / "measured_water_balance.csv", index=False)
    kodayar_balance.to_csv(output_dir / "upper_kodayar_balance.csv", index=False)
    window_log.to_csv(output_dir / "window_log.csv", index=False)
    return modeled, validation, None
