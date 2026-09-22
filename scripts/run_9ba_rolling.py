"""Rolling-horizon full-year unit commitment for the 9-BA model.

Each optimization sees seven committed days plus one look-ahead day.  Only the
committed snapshots are exported; the look-ahead is re-optimized in the next
window after the generator and storage state at the boundary has been passed
forward.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
import xarray as xr

from nuclear_energy_targets import (
    add_nuclear_targets,
    generator_dimension,
    prepare_nuclear_targets,
)
from run_9ba_weekly_days_2025_26 import OIL_GAS_BUDGET_CONSTRAINT

BIOMASS_ENERGY_CONSTRAINT = "cea_resource_adequacy_biomass_generation_target"
CHECKPOINT_MODEL_VERSION = "papanasam-servalar-topology-v2"


def _set_initial_state(
    network: pypsa.Network,
    previous: pypsa.Network | None,
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
    output_dir: Path,
    resume: bool = True,
    commit_days: int = 7,
    lookahead_days: int = 1,
    max_windows: int | None = None,
) -> tuple[pd.DataFrame, dict, None]:
    """Solve the complete network horizon in chronological rolling windows."""
    if commit_days < 1 or lookahead_days < 1:
        raise ValueError("commit_days and lookahead_days must both be positive")
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
            if checkpoint_meta.get("model_version") == CHECKPOINT_MODEL_VERSION:
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
        _set_initial_state(network, previous)
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
        started = time.perf_counter()
        status, condition = network.optimize(
            solver_name=solver_name,
            solver_options={"mip_rel_gap": mip_rel_gap, "log_to_console": False},
            extra_functionality=lambda n, s: (
                _add_daily_targets(n, s, gas_targets),
                _add_committed_biomass_target(
                    n, committed, biomass_annual_target_mwh, full_horizon_weight
                ),
            ),
            include_objective_constant=False,
        )
        runtime = time.perf_counter() - started
        if status != "ok" or condition != "optimal":
            raise RuntimeError(
                f"Window {number} failed: status={status}, condition={condition}. "
                "Completed checkpoints can be resumed."
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
            "window": number,
            "solve_start": str(active[0]),
            "solve_end": str(active[-1]),
            "commit_end": str(committed[-1]),
            "committed_snapshots": len(committed),
            "lookahead_snapshots": len(active) - len(committed),
            "solver_status": str(status),
            "termination_condition": str(condition),
            "runtime_seconds": runtime,
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

    modeled = pd.concat(daily_parts).sort_index() if daily_parts else pd.DataFrame()
    window_log = pd.DataFrame(window_rows)
    completed_snapshots = int(window_log["committed_snapshots"].sum()) if len(window_log) else 0
    validation = {
        "model_version": CHECKPOINT_MODEL_VERSION,
        "formulation": f"rolling {commit_days}-day commitment with {lookahead_days}-day look-ahead",
        "snapshots_solved_and_retained": completed_snapshots,
        "full_horizon_snapshots": len(snapshots),
        "windows_completed": len(window_log),
        "windows_planned": len(starts),
        "complete": completed_snapshots == len(snapshots),
        "all_solver_statuses_ok": bool(
            len(window_log) and window_log["termination_condition"].eq("optimal").all()
        ),
        "total_unserved_gwh": float(modeled.get("unserved_energy", pd.Series(dtype=float)).sum()),
        "solver_runtime_seconds_sum": float(window_log.get("runtime_seconds", pd.Series(dtype=float)).sum()),
        "wall_clock_seconds_this_session": float(time.perf_counter() - run_started),
    }
    modeled.to_csv(output_dir / "modeled_daily_generation.csv")
    window_log.to_csv(output_dir / "window_log.csv", index=False)
    return modeled, validation, None
