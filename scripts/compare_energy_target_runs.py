"""Compare solved runs that differ in their energy-target policy.

Each run is a results folder written by ``scripts/run_model.py`` (it must hold
``solved_network.nc`` and ``modeled_daily_generation.csv``). For every run the
script reports, by month:

- energy by carrier against observed generation (the NITI daily workbook,
  with bio-power and small hydro from the Tamil Nadu Outlook monthly values);
- oil/gas and nuclear energy by plant against the NITI ICED plant data;
- the load-weighted mean marginal price over the nine Tamil Nadu areas;
- operating cost by carrier (dispatch times marginal cost).

    python scripts/compare_energy_target_runs.py \
        targets=results/run_results/annual_..._LP/annual_targets \
        released=results/run_results/annual_..._LP/annual_released \
        --output results/energy_target_comparison

Writes one CSV per table to ``--output`` and prints a summary.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pypsa

from bio_power_targets import read_bio_power_monthly_targets
from energy_target_policy import PLANT_GENERATION_FILE, PLANT_LEVEL_CARRIERS, plant_groups
from prepare_renewable_profiles import AREAS
from small_hydro_targets import read_small_hydro_monthly_targets

CARRIERS = ("coal", "oil_gas", "nuclear", "hydro", "small_hydro", "bio_power", "solar", "wind")
ICED_SOURCE = {"oil_gas": "oil-gas", "nuclear": "nuclear"}


def _month(index) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(index).to_period("M").to_timestamp()


def month_coverage(dates) -> pd.Series:
    """Share of each calendar month covered by ``dates`` (for monthly-only observations)."""
    days = pd.DatetimeIndex(dates).normalize().unique()
    months = _month(days)
    counts = pd.Series(1, index=days).groupby(months).sum()
    return counts / counts.index.days_in_month


def observed_monthly_gwh(run_dir: Path) -> pd.DataFrame:
    """Observed monthly energy by carrier (GWh) for the run's dates."""
    daily = pd.read_csv(run_dir / "observed_daily_generation.csv", index_col="date", parse_dates=True)
    monthly = daily.groupby(_month(daily.index)).sum(min_count=1)
    coverage = month_coverage(daily.index)
    for carrier, reader in (("bio_power", read_bio_power_monthly_targets),
                            ("small_hydro", read_small_hydro_monthly_targets)):
        values = reader() / 1_000.0  # MWh -> GWh
        values.index = values.index.to_timestamp()
        monthly[carrier] = values.reindex(monthly.index) * coverage
    return monthly.reindex(columns=list(CARRIERS))


def modeled_monthly_gwh(run_dir: Path) -> pd.DataFrame:
    daily = pd.read_csv(run_dir / "modeled_daily_generation.csv", index_col="date", parse_dates=True)
    return daily.groupby(_month(daily.index))[list(CARRIERS)].sum()


def plant_monthly_gwh(network: pypsa.Network) -> pd.DataFrame:
    """Modeled energy per ICED plant (GWh), long format."""
    weights = network.snapshot_weightings.generators
    months = _month(network.snapshots)
    rows = []
    for carrier in PLANT_LEVEL_CARRIERS:
        for plant, units in plant_groups(network, carrier).items():
            if len(units):
                energy = network.generators_t.p[units].sum(axis=1).mul(weights)
            else:
                energy = pd.Series(0.0, index=network.snapshots)
            for month, value in energy.groupby(months).sum().items():
                rows.append({"carrier": carrier, "plant": plant, "month": month, "gwh": value / 1_000.0})
    return pd.DataFrame(rows)


def iced_plant_monthly_gwh(coverage: pd.Series) -> pd.DataFrame:
    """ICED plant energy (GWh) for the covered months, scaled to the covered share."""
    data = pd.read_csv(PLANT_GENERATION_FILE, parse_dates=["month"])
    data = data.loc[data["source"].isin(ICED_SOURCE.values()) & data["month"].isin(coverage.index)]
    data["generation_mu"] = data["generation_mu"] * data["month"].map(coverage)
    data["carrier"] = data["source"].map({v: k for k, v in ICED_SOURCE.items()})
    return data.rename(columns={"plant_name": "plant", "generation_mu": "gwh"})[
        ["carrier", "plant", "month", "gwh"]
    ]


def monthly_prices(network: pypsa.Network) -> pd.Series:
    """Load-weighted mean marginal price over the Tamil Nadu areas (INR/MWh)."""
    loads = network.loads.loc[network.loads.bus.isin(AREAS)]
    load_by_bus = network.loads_t.p_set[loads.index].T.groupby(loads.bus).sum().T
    prices = network.buses_t.marginal_price[load_by_bus.columns]
    months = _month(network.snapshots)
    weighted = prices.mul(load_by_bus).sum(axis=1).groupby(months).sum()
    return weighted / load_by_bus.sum(axis=1).groupby(months).sum()


def operating_cost_by_carrier(network: pypsa.Network) -> pd.Series:
    """Dispatch times marginal cost, by generator carrier (INR crore)."""
    weights = network.snapshot_weightings.generators
    cost = network.generators_t.p.mul(weights, axis=0).sum() * network.generators.marginal_cost
    return cost.groupby(network.generators.carrier).sum() / 1e7


def compare(runs: dict[str, Path], output: Path) -> dict[str, pd.DataFrame]:
    output.mkdir(parents=True, exist_ok=True)
    first = next(iter(runs.values()))
    observed = observed_monthly_gwh(first)
    carrier_rows, plant_frames, price_rows, cost_rows = [], [], {}, {}
    for name, run_dir in runs.items():
        modeled = modeled_monthly_gwh(run_dir)
        stacked = modeled.stack().rename("gwh").reset_index()
        stacked.columns = ["month", "carrier", "gwh"]
        stacked["run"] = name
        carrier_rows.append(stacked)
        network = pypsa.Network(str(run_dir / "solved_network.nc"))
        plants = plant_monthly_gwh(network)
        plants["run"] = name
        plant_frames.append(plants)
        price_rows[name] = monthly_prices(network)
        cost_rows[name] = operating_cost_by_carrier(network)
    obs = observed.stack().rename("gwh").reset_index()
    obs.columns = ["month", "carrier", "gwh"]
    obs["run"] = "observed"
    carriers = pd.concat(carrier_rows + [obs]).pivot_table(
        index=["carrier", "month"], columns="run", values="gwh"
    )
    dates = pd.read_csv(first / "modeled_daily_generation.csv", index_col="date", parse_dates=True).index
    iced = iced_plant_monthly_gwh(month_coverage(dates)).assign(run="observed (ICED)")
    plants = pd.concat(plant_frames + [iced]).pivot_table(
        index=["carrier", "plant", "month"], columns="run", values="gwh", fill_value=0.0
    )
    prices = pd.DataFrame(price_rows)
    costs = pd.DataFrame(cost_rows).fillna(0.0)
    tables = {"carrier_monthly_gwh": carriers, "plant_monthly_gwh": plants,
              "price_monthly_inr_per_mwh": prices, "operating_cost_inr_crore": costs}
    for name, table in tables.items():
        table.round(3).to_csv(output / f"{name}.csv")
    return tables


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs", nargs="+", metavar="NAME=RUN_DIR")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    runs = {}
    for item in args.runs:
        name, _, path = item.partition("=")
        if not path:
            parser.error(f"Expected NAME=RUN_DIR, got {item!r}")
        runs[name] = Path(path)
    tables = compare(runs, args.output)
    pd.set_option("display.width", 200)
    carriers = tables["carrier_monthly_gwh"]
    print("Annual energy by carrier (GWh):")
    print(carriers.groupby(level="carrier").sum(min_count=1).round(0).to_string())
    plants = tables["plant_monthly_gwh"]
    print("\nOil/gas and nuclear by plant (GWh):")
    print(plants.groupby(level=["carrier", "plant"]).sum().round(1).to_string())
    print("\nMean Tamil Nadu price (INR/MWh, load-weighted):")
    print(tables["price_monthly_inr_per_mwh"].round(0).to_string())
    costs = tables["operating_cost_inr_crore"]
    print("\nOperating cost (INR crore):")
    print(pd.concat([costs, costs.sum().to_frame("total").T]).round(1).to_string())
    print(f"\nTables written to {args.output}")


if __name__ == "__main__":
    main()
