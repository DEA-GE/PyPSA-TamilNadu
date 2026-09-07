"""Unified daily, monthly, and peak-week runner for the 9-BA model."""

from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa

from nuclear_energy_targets import add_nuclear_targets, prepare_nuclear_targets
from run_9ba_weekly_days_2025_26 import (
    COMPARISON_CATEGORIES,
    MODEL_DIR,
    OIL_GAS_BUDGET_CONSTRAINT,
    PLOT_LABELS,
    read_observed_generation,
    read_oil_gas_budget,
    solve_day,
)


DEFAULT_NETWORK = MODEL_DIR
DEFAULT_RESULTS_ROOT = MODEL_DIR.parent / "run_results"


def _normalise_mode(run_mode: str) -> str:
    aliases = {
        "day": "daily",
        "daily": "daily",
        "month": "monthly",
        "monthly": "monthly",
        "peak": "peak",
        "peak_week": "peak",
    }
    mode = aliases.get(str(run_mode).strip().lower())
    if mode is None:
        raise ValueError("RUN_MODE must be 'daily', 'monthly', or 'peak'.")
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


def _carrier_energy_by_day(network: pypsa.Network) -> pd.DataFrame:
    weights = network.snapshot_weightings.generators.reindex(network.snapshots)
    weighted = network.generators_t.p.mul(weights, axis=0)
    by_carrier_hourly = weighted.T.groupby(network.generators.carrier).sum().T
    daily = by_carrier_hourly.groupby(by_carrier_hourly.index.normalize()).sum() / 1000.0

    storage_discharge = network.storage_units_t.p.clip(lower=0).mul(weights, axis=0)
    storage_daily = storage_discharge.groupby(storage_discharge.index.normalize()).sum().sum(axis=1) / 1000.0

    output = pd.DataFrame(index=daily.index)
    for carrier in ["coal", "oil_gas", "nuclear", "hydro", "solar", "wind", "diesel"]:
        output[carrier] = daily.get(carrier, pd.Series(0.0, index=daily.index))
    output["hydro"] = output["hydro"].add(storage_daily, fill_value=0.0)
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
        for date, target in gas_targets.items():
            day_snapshots = active_snapshots[active_snapshots.normalize() == date]
            if not len(day_snapshots):
                continue
            active_network.model.add_constraints(
                dispatch.sel(name=gas_units, snapshot=day_snapshots).sum() == float(target),
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
    for axis, carrier in zip(axes.flat, COMPARISON_CATEGORIES):
        axis.plot(dates, observed_period[carrier], label="Observed", linewidth=2.0)
        axis.plot(dates, modeled_period[carrier], label="Model", linewidth=1.7)
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
    solver_name: str = "highs",
    mip_rel_gap: float = 0.01,
    network_path: str | Path = DEFAULT_NETWORK,
    results_root: str | Path = DEFAULT_RESULTS_ROOT,
) -> dict:
    """Run the 9-BA unit-commitment model for the selected horizon.

    ``peak`` means the continuous seven-day period containing the annual maximum
    hourly demand. Monthly runs use independent daily solves to remain tractable.
    """
    mode = _normalise_mode(run_mode)
    network_path = Path(network_path)
    results_root = Path(results_root)
    print(f"Loading {network_path} ...", flush=True)
    base_network = pypsa.Network(network_path)
    total_load = base_network.loads_t.p_set.sum(axis=1)
    dates, label = _dates_for_mode(mode, reference_date, total_load)

    observed = read_observed_generation()
    oil_gas_budget = read_oil_gas_budget(observed)
    missing_observed = dates.difference(observed.index)
    if len(missing_observed):
        raise ValueError(f"Observed generation is missing {len(missing_observed)} requested day(s).")

    if mode == "peak":
        modeled, validation, solved_network = _solve_peak_week(
            base_network, dates, observed, oil_gas_budget, solver_name, mip_rel_gap
        )
    else:
        modeled, validation, solved_network = _solve_independent_days(
            base_network, dates, observed, oil_gas_budget, solver_name, mip_rel_gap
        )

    output_dir = results_root / label
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
    }


if __name__ == "__main__":
    run_selection()
