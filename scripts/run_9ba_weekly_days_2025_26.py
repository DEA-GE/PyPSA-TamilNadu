"""Solve one peak-demand day from each FY2025-26 week and compare generation.

The intact nine-balancing-area model is solved independently for the highest-
energy-demand day in each Monday-Sunday week. Partial weeks at the beginning
and end of the financial year are included, giving 53 daily optimizations.

Daily modeled generation is compared with the supplied NITI Aayog ICED daily
generation workbook. One MU is treated as one GWh (1,000 MWh). The source's
Other RES row is entirely blank, so bio-power and small-hydro output is
reported but is not included in comparison errors or the like-for-like total.
Diesel is also reported separately and excluded from the like-for-like total
because the observed workbook has no diesel row.

Each daily solve applies the observed oil-and-gas value as an aggregate energy
equality target, so the comparison is calibrated rather than independent for
that technology.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa
from nuclear_energy_targets import prepare_nuclear_targets, add_nuclear_targets, validate_nuclear_targets


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "9_BA" / "model"
OBSERVED_FILE = (
    ROOT
    / "additional_data"
    / "TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx"
)
DEFAULT_OUTPUT_DIR = ROOT / "9_BA" / "weekly_day_results"
OIL_GAS_BUDGET_FILE = MODEL_DIR / "oil_gas_daily_energy_budget.csv"
OIL_GAS_BUDGET_CONSTRAINT = "observed_fy2025_26_oil_gas_generation_target"
FY_START = pd.Timestamp("2025-04-01")
FY_END = pd.Timestamp("2026-03-31")

OBSERVED_ROWS = {
    "Coal - Generation (in MU)": "coal",
    "Oil & Gas - Generation (in MU)": "oil_gas",
    "Nuclear - Generation (in MU)": "nuclear",
    "Hydro - Generation (in MU)": "hydro",
    "Solar - Generation (in MU)": "solar",
    "Wind - Generation (in MU)": "wind",
    "Other RES - Generation (in MU)": "other_res",
    "Total": "comparable_total",
}
COMPARISON_CATEGORIES = [
    "coal",
    "oil_gas",
    "nuclear",
    "hydro",
    "solar",
    "wind",
    "comparable_total",
]
PLOT_LABELS = {
    "coal": "Coal",
    "oil_gas": "Oil & Gas",
    "nuclear": "Nuclear",
    "hydro": "Hydro incl. pumped-storage discharge",
    "solar": "Solar",
    "wind": "Wind",
    "comparable_total": "Comparable total",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver", default="highs", help="PyPSA solver name")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for CSV comparison tables and figures",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=None,
        help="Solve only the first N selected days (useful for a smoke test)",
    )
    parser.add_argument(
        "--save-networks",
        action="store_true",
        help="Also export one solved NetCDF file per selected day",
    )
    return parser.parse_args()


def read_observed_generation() -> pd.DataFrame:
    raw = pd.read_excel(OBSERVED_FILE, sheet_name="Electricity_Power_Generation_")
    raw = raw.loc[raw["State"].eq("Tamil Nadu")].set_index("Parameter")
    missing_rows = set(OBSERVED_ROWS) - set(raw.index)
    if missing_rows:
        raise ValueError(f"Observed workbook is missing rows: {sorted(missing_rows)}")

    date_columns = []
    parsed_dates = []
    for column in raw.columns:
        try:
            date = pd.Timestamp(column)
        except (TypeError, ValueError):
            continue
        if FY_START <= date <= FY_END:
            date_columns.append(column)
            parsed_dates.append(date)

    observed = raw.loc[list(OBSERVED_ROWS), date_columns].T
    observed.index = pd.DatetimeIndex(parsed_dates, name="date")
    observed = observed.apply(pd.to_numeric, errors="coerce")
    observed = observed.rename(columns=OBSERVED_ROWS).sort_index()

    expected_dates = pd.date_range(FY_START, FY_END, freq="D", name="date")
    if not observed.index.equals(expected_dates):
        raise ValueError("Observed workbook does not contain every FY2025-26 date")
    if observed[COMPARISON_CATEGORIES].isna().any().any():
        raise ValueError("A populated comparison technology contains missing values")
    if observed["other_res"].notna().any():
        raise ValueError("Expected the supplied Other RES series to be entirely blank")
    source_sum = observed[["coal", "oil_gas", "nuclear", "hydro", "solar", "wind"]].sum(axis=1)
    if not np.allclose(source_sum, observed["comparable_total"], atol=1e-8):
        raise ValueError("Observed Total does not reconcile to its six populated technologies")
    return observed


def read_oil_gas_budget(observed: pd.DataFrame) -> pd.DataFrame:
    budget = pd.read_csv(OIL_GAS_BUDGET_FILE, parse_dates=["date"]).set_index("date")
    expected_dates = pd.date_range(FY_START, FY_END, freq="D", name="date")
    if not budget.index.equals(expected_dates):
        raise ValueError("Oil-and-gas daily budget does not cover every FY2025-26 date")
    required = {
        "observed_generation_mu",
        "annual_share",
        "daily_energy_target_mwh",
    }
    missing = sorted(required - set(budget.columns))
    if missing:
        raise ValueError(f"Oil-and-gas daily budget is missing columns: {missing}")
    if not np.isclose(budget["annual_share"].sum(), 1.0):
        raise ValueError("Oil-and-gas daily budget shares do not sum to one")
    if not np.allclose(
        budget["observed_generation_mu"], observed["oil_gas"], atol=1e-9
    ):
        raise ValueError("Oil-and-gas budget differs from the observed workbook")
    if not np.allclose(
        budget["daily_energy_target_mwh"],
        1_000.0 * budget["observed_generation_mu"],
        atol=1e-9,
    ):
        raise ValueError("Oil-and-gas daily MWh targets do not match the MU observations")
    return budget


def select_weekly_days(network: pypsa.Network) -> pd.DataFrame:
    demand = network.loads_t.p_set.sum(axis=1)
    daily_demand = demand.groupby(demand.index.normalize()).sum()
    daily_demand.index = pd.DatetimeIndex(daily_demand.index, name="date")
    expected_dates = pd.date_range(FY_START, FY_END, freq="D", name="date")
    if not daily_demand.index.equals(expected_dates):
        raise ValueError("Model demand does not cover every FY2025-26 day")

    records = []
    for period, values in daily_demand.groupby(daily_demand.index.to_period("W-SUN")):
        selected_day = values.idxmax()
        records.append(
            {
                "week_start": max(period.start_time.normalize(), FY_START),
                "week_end": min(period.end_time.normalize(), FY_END),
                "selected_day": selected_day,
                "selection_rule": "highest modeled daily demand in Monday-Sunday week",
                "selected_demand_mwh": float(values.loc[selected_day]),
            }
        )
    selected = pd.DataFrame(records)
    selected.index = pd.RangeIndex(1, len(selected) + 1, name="fiscal_week")
    if len(selected) != 53:
        raise ValueError(f"Expected 53 partial/full FY weeks, found {len(selected)}")
    return selected


def carrier_energy_mu(network: pypsa.Network) -> pd.Series:
    weights = network.snapshot_weightings.generators
    generator_mwh = network.generators_t.p.mul(weights, axis=0).sum(axis=0)
    by_carrier = generator_mwh.groupby(network.generators.carrier).sum() / 1_000.0

    storage_discharge_mwh = (
        network.storage_units_t.p.clip(lower=0.0).mul(weights, axis=0).sum(axis=0)
    )
    storage_by_carrier = (
        storage_discharge_mwh.groupby(network.storage_units.carrier).sum() / 1_000.0
    )

    values = pd.Series(0.0, index=COMPARISON_CATEGORIES, dtype=float)
    for carrier in ["coal", "oil_gas", "nuclear", "solar", "wind"]:
        values[carrier] = float(by_carrier.get(carrier, 0.0))
    values["diesel"] = float(by_carrier.get("diesel", 0.0))
    values["hydro"] = float(by_carrier.get("hydro", 0.0)) + float(
        storage_by_carrier.get("hydro", 0.0)
    )
    values["comparable_total"] = values[
        ["coal", "oil_gas", "nuclear", "hydro", "solar", "wind"]
    ].sum()
    values["other_res"] = float(by_carrier.get("bio_power", 0.0)) + float(
        by_carrier.get("small_hydro", 0.0)
    )
    values["system_plant_generation"] = sum(
        value
        for carrier, value in by_carrier.items()
        if carrier not in {"market_import", "unserved_energy"}
    ) + float(storage_by_carrier.sum())
    values["market_import"] = float(by_carrier.get("market_import", 0.0))
    values["unserved_energy"] = float(by_carrier.get("unserved_energy", 0.0))
    return values


def solve_day(
    annual_network: pypsa.Network,
    day: pd.Timestamp,
    solver: str,
    oil_gas_daily_target_mwh: float,
    oil_gas_annual_share: float,
    mip_rel_gap: float | None = None,
) -> tuple[pypsa.Network, dict[str, object]]:
    end = day + pd.Timedelta(days=1)
    snapshots = annual_network.snapshots[
        (annual_network.snapshots >= day) & (annual_network.snapshots < end)
    ]
    if len(snapshots) != 24:
        raise ValueError(f"Expected 24 snapshots for {day.date()}, found {len(snapshots)}")

    network = annual_network.copy(snapshots=snapshots)
    prepare_nuclear_targets(network)
    if OIL_GAS_BUDGET_CONSTRAINT not in network.global_constraints.index:
        raise ValueError(
            f"The model is missing {OIL_GAS_BUDGET_CONSTRAINT!r}"
        )
    network.global_constraints.at[
        OIL_GAS_BUDGET_CONSTRAINT, "constant"
    ] = oil_gas_daily_target_mwh
    # A positive down_time_before already defines these units as initially off;
    # p_init is redundant in that case and PyPSA explicitly ignores it.
    initially_down = (
        network.generators["committable"]
        & network.generators["down_time_before"].gt(0)
    )
    network.generators.loc[initially_down, "p_init"] = np.nan
    started = time.perf_counter()
    solver_options: dict[str, object] = {"log_to_console": False}
    if mip_rel_gap is not None:
        solver_options["mip_rel_gap"] = mip_rel_gap
    status, condition = network.optimize(
        solver_name=solver,
        extra_functionality=add_nuclear_targets,
        solver_options=solver_options,
        include_objective_constant=False,
    )
    runtime_seconds = time.perf_counter() - started
    if condition != "optimal":
        raise RuntimeError(f"Optimization failed for {day.date()}: {status}, {condition}")

    energy = carrier_energy_mu(network)
    nuclear_target_deviation_mwh = validate_nuclear_targets(network)
    link_loading = network.links_t.p0.abs().div(network.links.p_nom, axis=1)
    result = {
        "date": day,
        **energy.to_dict(),
        "oil_gas_daily_target_mu": oil_gas_daily_target_mwh / 1_000.0,
        "oil_gas_annual_share": oil_gas_annual_share,
        "oil_gas_target_deviation_mwh": (
            1_000.0 * float(energy["oil_gas"]) - oil_gas_daily_target_mwh
        ),
        "nuclear_target_deviation_mwh": nuclear_target_deviation_mwh,
        "objective": float(network.objective),
        "solver_status": status,
        "termination_condition": condition,
        "runtime_seconds": runtime_seconds,
        "max_nodal_residual_mw": float(network.buses_t.p.sum(axis=1).abs().max()),
        "max_corridor_loading_pct": float(100.0 * link_loading.max().max()),
        "most_loaded_corridor": str(link_loading.max().idxmax()),
    }
    if result["max_nodal_residual_mw"] >= 1e-5:
        raise RuntimeError(f"Nodal balance validation failed for {day.date()}")
    if result["max_corridor_loading_pct"] > 100.0001:
        raise RuntimeError(f"Corridor capacity validation failed for {day.date()}")
    if not np.isclose(
        1_000.0 * float(energy["oil_gas"]),
        oil_gas_daily_target_mwh,
        atol=1e-5,
        rtol=0.0,
    ):
        raise RuntimeError(f"Oil-and-gas daily energy target failed for {day.date()}")
    return network, result


def build_comparison(
    modeled: pd.DataFrame, observed: pd.DataFrame, selected: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected_dates = pd.DatetimeIndex(modeled.index)
    observed_selected = observed.loc[selected_dates]
    rows = []
    for date in selected_dates:
        fiscal_week = int(
            selected.index[selected["selected_day"].eq(date)][0]
        )
        for category in COMPARISON_CATEGORIES:
            observed_mu = float(observed_selected.at[date, category])
            modeled_mu = float(modeled.at[date, category])
            error_mu = modeled_mu - observed_mu
            rows.append(
                {
                    "fiscal_week": fiscal_week,
                    "date": date,
                    "technology": category,
                    "observed_mu": observed_mu,
                    "modeled_mu": modeled_mu,
                    "error_mu": error_mu,
                    "absolute_error_mu": abs(error_mu),
                    "percentage_error": 100.0 * error_mu / observed_mu,
                }
            )
    comparison = pd.DataFrame(rows)
    summary = (
        comparison.groupby("technology", sort=False)
        .agg(
            days=("date", "count"),
            observed_mu=("observed_mu", "sum"),
            modeled_mu=("modeled_mu", "sum"),
            mean_error_mu=("error_mu", "mean"),
            mae_mu=("absolute_error_mu", "mean"),
            mean_percentage_error_pct=("percentage_error", "mean"),
            mape_pct=("percentage_error", lambda values: values.abs().mean()),
            rmse_mu=("error_mu", lambda values: np.sqrt(np.mean(np.square(values)))),
        )
        .reset_index()
    )
    summary["aggregate_error_pct"] = 100.0 * (
        summary["modeled_mu"] - summary["observed_mu"]
    ) / summary["observed_mu"]
    return comparison, summary


def make_plots(comparison: pd.DataFrame, summary: pd.DataFrame, output_dir: Path) -> None:
    figure, axes = plt.subplots(4, 2, figsize=(16, 15), sharex=True)
    for axis, category in zip(axes.flat, COMPARISON_CATEGORIES):
        data = comparison.loc[comparison["technology"].eq(category)]
        axis.plot(data["date"], data["observed_mu"], marker="o", ms=3, label="Observed")
        axis.plot(data["date"], data["modeled_mu"], marker="o", ms=3, label="Modeled")
        axis.set_title(PLOT_LABELS[category])
        axis.set_ylabel("MU/day")
        axis.grid(alpha=0.25)
    axes.flat[0].legend()
    axes.flat[-1].axis("off")
    figure.suptitle("FY2025-26 weekly peak-demand days: modeled vs observed generation")
    figure.tight_layout()
    figure.savefig(output_dir / "daily_generation_comparison.png", dpi=160)
    plt.close(figure)

    plot_summary = summary.set_index("technology").loc[COMPARISON_CATEGORIES]
    figure, axes = plt.subplots(1, 2, figsize=(15, 5))
    plot_summary[["observed_mu", "modeled_mu"]].rename(
        columns={"observed_mu": "Observed", "modeled_mu": "Modeled"}
    ).plot.bar(ax=axes[0])
    axes[0].set_ylabel("Aggregate generation over selected days (MU)")
    axes[0].set_xticklabels([PLOT_LABELS[x] for x in COMPARISON_CATEGORIES], rotation=35, ha="right")
    axes[0].grid(axis="y", alpha=0.25)
    plot_summary["aggregate_error_pct"].plot.bar(ax=axes[1], color="#d95f02")
    axes[1].axhline(0.0, color="black", linewidth=0.8)
    axes[1].set_ylabel("Aggregate model error (%)")
    axes[1].set_xticklabels([PLOT_LABELS[x] for x in COMPARISON_CATEGORIES], rotation=35, ha="right")
    axes[1].grid(axis="y", alpha=0.25)
    figure.suptitle("Generation comparison summary")
    figure.tight_layout()
    figure.savefig(output_dir / "generation_comparison_summary.png", dpi=160)
    plt.close(figure)


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    networks_dir = output_dir / "solved_networks"
    if args.save_networks:
        networks_dir.mkdir(parents=True, exist_ok=True)

    observed = read_observed_generation()
    oil_gas_budget = read_oil_gas_budget(observed)
    annual_network = pypsa.Network(MODEL_DIR)
    if len(annual_network.buses) != 9 or len(annual_network.links) != 22:
        raise ValueError("The input is not the expected intact 9BA network")
    if not annual_network.generators.committable.any():
        raise ValueError("The 9BA model contains no unit commitment")

    selected = select_weekly_days(annual_network)
    if args.max_days is not None:
        if args.max_days < 1:
            raise ValueError("--max-days must be positive")
        selected = selected.head(args.max_days)
    selected.to_csv(output_dir / "selected_days.csv", date_format="%Y-%m-%d")
    oil_gas_budget.to_csv(
        output_dir / "oil_gas_daily_energy_budget.csv", date_format="%Y-%m-%d"
    )

    results = []
    total_started = time.perf_counter()
    for position, (fiscal_week, row) in enumerate(selected.iterrows(), start=1):
        day = pd.Timestamp(row["selected_day"])
        print(
            f"[{position:02d}/{len(selected):02d}] FY week {fiscal_week:02d}: "
            f"solving {day.date()}"
        )
        solved, result = solve_day(
            annual_network,
            day,
            args.solver,
            oil_gas_daily_target_mwh=float(
                oil_gas_budget.at[day, "daily_energy_target_mwh"]
            ),
            oil_gas_annual_share=float(oil_gas_budget.at[day, "annual_share"]),
        )
        result["fiscal_week"] = fiscal_week
        results.append(result)
        if args.save_networks:
            solved.export_to_netcdf(networks_dir / f"9ba_{day:%Y-%m-%d}.nc")

        pd.DataFrame(results).set_index("date").sort_index().to_csv(
            output_dir / "modeled_daily_generation.csv",
            date_format="%Y-%m-%d",
        )

    modeled = pd.DataFrame(results).set_index("date").sort_index()
    comparison, summary = build_comparison(modeled, observed, selected)
    comparison.to_csv(output_dir / "daily_generation_comparison.csv", index=False, date_format="%Y-%m-%d")
    summary.to_csv(output_dir / "generation_comparison_summary.csv", index=False)
    observed.loc[modeled.index].to_csv(
        output_dir / "observed_daily_generation.csv", date_format="%Y-%m-%d"
    )
    make_plots(comparison, summary, output_dir)

    total_runtime = time.perf_counter() - total_started
    print(f"Completed {len(modeled)} independent daily solves in {total_runtime:,.1f} seconds")
    print(f"Unserved energy across selected days: {modeled['unserved_energy'].sum():,.6f} MU")
    print("\nComparison summary:")
    print(summary.to_string(index=False))
    print(f"\nOutputs: {output_dir}")


if __name__ == "__main__":
    main()
