"""Rolling-horizon full-year unit commitment for the 9-BA model.

Each optimization sees seven committed days plus one look-ahead day.  Only the
committed snapshots are exported; the look-ahead is re-optimized in the next
window after the generator and storage state at the boundary has been passed
forward.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psutil
import pypsa
import xarray as xr

from highs_solver_options import highs_solver_options, reset_highs_global_scheduler

from nuclear_energy_targets import (
    add_nuclear_targets,
    generator_dimension,
    prepare_nuclear_targets,
)
from run_9ba_weekly_days_2025_26 import OIL_GAS_BUDGET_CONSTRAINT

BIOMASS_ENERGY_CONSTRAINT = "cea_resource_adequacy_biomass_generation_target"
# Increment when either the model inputs or rolling-solve acceptance policy changes.
# This prevents resume from combining checkpoints made under different objectives
# or solver-quality criteria.
CHECKPOINT_MODEL_VERSION = "relaxed-uc-fractional-boundary-v1"


class _SolveResourceMonitor:
    """Sample this process while PyPSA builds and solves one window."""

    def __init__(self, interval_seconds: float = 0.5) -> None:
        self.process = psutil.Process()
        self.interval_seconds = interval_seconds
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.peak_rss_bytes = 0
        self.peak_threads = 0
        self.cpu_start = 0.0
        self.cpu_seconds = 0.0

    def _sample(self) -> None:
        try:
            self.peak_rss_bytes = max(
                self.peak_rss_bytes, self.process.memory_info().rss
            )
            # Exclude the monitor thread itself from the observed count.
            self.peak_threads = max(
                self.peak_threads, self.process.num_threads() - 1
            )
        except psutil.Error:
            pass

    def _run(self) -> None:
        while not self.stop.wait(self.interval_seconds):
            self._sample()

    def __enter__(self) -> "_SolveResourceMonitor":
        cpu = self.process.cpu_times()
        self.cpu_start = cpu.user + cpu.system
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
                name=f"RollingOilGasDailyTarget-{date:%Y%m%d}",
            )


def _add_committed_biomass_target(
    network: pypsa.Network,
    committed_snapshots: pd.DatetimeIndex,
    annual_target_mwh: float,
    full_horizon_weight: float,
) -> None:
    """Apply a prorated annual biomass target to the accepted part of a window.

    A standard PyPSA operational-limit applies to the whole active window,
    including its look-ahead day.  Constraining only accepted snapshots avoids
    double-counting that look-ahead while making all rolling-window targets sum
    exactly to the annual CEA target.
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
    target_mwh = annual_target_mwh * float(weights.sum()) / full_horizon_weight
    network.model.add_constraints(
        expression == target_mwh,
        name=f"RollingBiomassEnergyTarget-{committed_snapshots[0]:%Y%m%d}",
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
    output["other_res"] = sum(
        (daily.get(c, pd.Series(0.0, index=daily.index)) for c in ["bio_power", "small_hydro"]),
        start=pd.Series(0.0, index=daily.index),
    )
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
    if BIOMASS_ENERGY_CONSTRAINT not in base_network.global_constraints.index:
        raise ValueError(
            f"The model is missing {BIOMASS_ENERGY_CONSTRAINT!r}; rebuild it first"
        )
    biomass_annual_target_mwh = float(
        base_network.global_constraints.at[BIOMASS_ENERGY_CONSTRAINT, "constant"]
    )
    full_horizon_weight = float(base_network.snapshot_weightings.generators.sum())

    step = pd.Timedelta(days=commit_days)
    horizon = pd.Timedelta(days=commit_days + lookahead_days)
    starts = pd.date_range(snapshots[0].normalize(), snapshots[-1].normalize(), freq=step)
    previous: pypsa.Network | None = None
    daily_parts: list[pd.DataFrame] = []
    window_rows: list[dict] = []
    run_started = time.perf_counter()

    for number, start in enumerate(starts, start=1):
        if max_windows is not None and number > max_windows:
            break
        commit_end = min(start + step, snapshots[-1] + pd.Timedelta(hours=1))
        solve_end = min(start + horizon, snapshots[-1] + pd.Timedelta(hours=1))
        active = snapshots[(snapshots >= start) & (snapshots < solve_end)]
        committed = active[active < commit_end]
        checkpoint = chunks_dir / f"window_{number:03d}_{start:%Y-%m-%d}.nc"
        daily_file = checkpoint.with_suffix(".daily.csv")
        meta_file = checkpoint.with_suffix(".json")

        if resume and checkpoint.exists() and daily_file.exists() and meta_file.exists():
            checkpoint_meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if (checkpoint_meta.get("model_version") == CHECKPOINT_MODEL_VERSION
                    and checkpoint_meta.get("unit_commitment", "full") == unit_commitment):
                print(f"Loading completed window {number}/{len(starts)}: {start.date()}", flush=True)
                previous = pypsa.Network(checkpoint)
                daily_parts.append(pd.read_csv(daily_file, index_col="date", parse_dates=True))
                window_rows.append(checkpoint_meta)
                continue
            print(
                f"Re-solving stale checkpoint {number}/{len(starts)}: {start.date()}",
                flush=True,
            )

        network = base_network.copy(snapshots=active)
        # Replace the native global constraint with a committed-snapshot
        # constraint below. Native constraints cover the look-ahead day too.
        network.remove("GlobalConstraint", BIOMASS_ENERGY_CONSTRAINT)
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
                    _add_committed_biomass_target(
                        n, committed, biomass_annual_target_mwh, full_horizon_weight
                    ),
                    _add_relaxed_boundary_constraints(n, previous)
                    if unit_commitment == "relaxed"
                    else None,
                ),
                include_objective_constant=False,
                linearized_unit_commitment=unit_commitment == "relaxed",
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

        # The checkpoint contains only accepted hours. It is also the complete
        # state source for the next window and for restart/resume.
        # Release the large Linopy/HiGHS model before checkpointing. Copying a
        # solved 192-hour network here can briefly double RAM and kill a Jupyter
        # kernel even though optimization itself completed successfully.
        del network.model
        network.set_snapshots(committed)
        previous = network
        daily = _window_metrics(previous, committed)
        residual = float(previous.buses_t.p.sum(axis=1).abs().max())
        loading = (
            float(previous.links_t.p0.abs().div(previous.links.p_nom, axis=1).max().max() * 100)
            if len(previous.links)
            else 0.0
        )
        row = {
            "model_version": CHECKPOINT_MODEL_VERSION,
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
            "peak_process_rss_mb_sampled": resources.peak_rss_bytes / 1_000_000,
            "peak_process_threads_sampled": resources.peak_threads,
            "max_nodal_residual_mw": residual,
            "max_corridor_loading_pct": loading,
        }
        previous.export_to_netcdf(checkpoint)
        daily.to_csv(daily_file)
        meta_file.write_text(json.dumps(row, indent=2), encoding="utf-8")
        daily_parts.append(daily)
        window_rows.append(row)

        pd.concat(daily_parts).sort_index().to_csv(output_dir / "modeled_daily_generation.partial.csv")
        pd.DataFrame(window_rows).to_csv(output_dir / "window_log.partial.csv", index=False)
        print(
            f"Window {number}/{len(starts)}: {runtime:.1f} s wall, "
            f"{resources.cpu_seconds:.1f} s CPU "
            f"({resources.cpu_seconds / runtime:.2f} cores average), "
            f"{resources.peak_rss_bytes / 1_000_000:.0f} MB peak RSS, "
            f"{resources.peak_threads} peak process threads. "
            f"Metrics: {output_dir / 'window_log.partial.csv'}",
            flush=True,
        )

    modeled = pd.concat(daily_parts).sort_index() if daily_parts else pd.DataFrame()
    window_log = pd.DataFrame(window_rows)
    completed_snapshots = int(window_log["committed_snapshots"].sum()) if len(window_log) else 0
    validation = {
        "model_version": CHECKPOINT_MODEL_VERSION,
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
        "time_limit_seconds": time_limit,
        "max_time_limit_mip_gap": (
            max_time_limit_mip_gap if unit_commitment == "full" else None
        ),
        "solver_threads": solver_threads,
        "simplex_strategy": simplex_strategy,
        "total_unserved_gwh": float(modeled.get("unserved_energy", pd.Series(dtype=float)).sum()),
        "solver_runtime_seconds_sum": float(window_log.get("runtime_seconds", pd.Series(dtype=float)).sum()),
        "wall_clock_seconds_this_session": float(time.perf_counter() - run_started),
    }
    modeled.to_csv(output_dir / "modeled_daily_generation.csv")
    window_log.to_csv(output_dir / "window_log.csv", index=False)
    return modeled, validation, None
