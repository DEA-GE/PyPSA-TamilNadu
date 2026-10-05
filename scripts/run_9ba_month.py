"""Solve all 31 days of July 2025 and compare monthly generation.

Each day is an independent 24-hour unit-commitment problem. This covers all
744 July hours while avoiding the very large continuous monthly MIP. Gas and
nuclear use observed daily equality targets.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pypsa

from run_9ba_weekly_days_2025_26 import (
    COMPARISON_CATEGORIES,
    MODEL_DIR,
    PLOT_LABELS,
    read_observed_generation,
    read_oil_gas_budget,
    solve_day,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "9_BA" / "monthly_results" / "2025_07"
MONTH_START = pd.Timestamp("2025-07-01")
MONTH_END = pd.Timestamp("2025-07-31")


def write_outputs(
    modeled: pd.DataFrame,
    observed: pd.DataFrame,
    runtime_seconds: float,
) -> None:
    modeled.to_csv(OUTPUT_DIR / "modeled_daily_generation.csv", date_format="%Y-%m-%d")
    observed.to_csv(OUTPUT_DIR / "observed_daily_generation.csv", date_format="%Y-%m-%d")

    summary = pd.DataFrame(
        {
            "modeled_mu": modeled[COMPARISON_CATEGORIES].sum(),
            "observed_mu": observed[COMPARISON_CATEGORIES].sum(),
        }
    )
    summary["error_mu"] = summary["modeled_mu"] - summary["observed_mu"]
    summary["error_pct"] = 100.0 * summary["error_mu"] / summary["observed_mu"]
    summary.to_csv(OUTPUT_DIR / "generation_comparison_summary.csv")

    daily_rows = []
    for date in modeled.index:
        for technology in COMPARISON_CATEGORIES:
            model_value = float(modeled.at[date, technology])
            observed_value = float(observed.at[date, technology])
            daily_rows.append(
                {
                    "date": date,
                    "technology": technology,
                    "modeled_mu": model_value,
                    "observed_mu": observed_value,
                    "error_mu": model_value - observed_value,
                    "error_pct": 100.0 * (model_value - observed_value) / observed_value,
                }
            )
    pd.DataFrame(daily_rows).to_csv(
        OUTPUT_DIR / "daily_generation_comparison.csv", index=False, date_format="%Y-%m-%d"
    )

    checks = {
        "run_mode": "31 independent 24-hour unit-commitment solves",
        "snapshots": 24 * len(modeled),
        "days": len(modeled),
        "runtime_seconds": runtime_seconds,
        "maximum_gas_daily_target_deviation_mwh": float(
            modeled["oil_gas_target_deviation_mwh"].abs().max()
        ),
        "maximum_nuclear_daily_target_deviation_mwh": float(
            modeled["nuclear_target_deviation_mwh"].abs().max()
        ),
        "maximum_nodal_residual_mw": float(modeled["max_nodal_residual_mw"].max()),
        "maximum_corridor_loading_pct": float(
            modeled["max_corridor_loading_pct"].max()
        ),
        "unserved_energy_mu": float(modeled["unserved_energy"].sum()),
    }
    (OUTPUT_DIR / "validation.json").write_text(
        json.dumps(checks, indent=2), encoding="utf-8"
    )

    figure, axes = plt.subplots(4, 2, figsize=(15, 13), sharex=True)
    for axis, technology in zip(axes.flat, COMPARISON_CATEGORIES):
        axis.plot(modeled.index, modeled[technology], label="Modeled")
        axis.plot(observed.index, observed[technology], label="Observed")
        axis.set_title(PLOT_LABELS[technology])
        axis.set_ylabel("MU/day")
        axis.grid(alpha=0.25)
    axes.flat[0].legend()
    axes.flat[-1].axis("off")
    figure.suptitle(
        "July 2025: modeled vs observed generation\n"
        "Gas and nuclear calibrated with daily equality targets"
    )
    figure.autofmt_xdate()
    figure.tight_layout()
    figure.savefig(OUTPUT_DIR / "daily_generation_comparison.png", dpi=160)
    plt.close(figure)

    print("\nJuly 2025 comparison:")
    print(summary.to_string())
    print("\nValidation:")
    print(json.dumps(checks, indent=2))


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    observed_year = read_observed_generation()
    observed = observed_year.loc[MONTH_START:MONTH_END]
    gas_budget = read_oil_gas_budget(observed_year)
    annual_network = pypsa.Network(MODEL_DIR)

    dates = pd.date_range(MONTH_START, MONTH_END, freq="D")
    results: list[dict[str, object]] = []
    started = time.perf_counter()
    for position, day in enumerate(dates, start=1):
        print(f"[{position:02d}/{len(dates):02d}] solving {day.date()}", flush=True)
        _, result = solve_day(
            annual_network,
            day,
            "highs",
            oil_gas_daily_target_mwh=float(
                gas_budget.at[day, "daily_energy_target_mwh"]
            ),
            oil_gas_annual_share=float(gas_budget.at[day, "annual_share"]),
            mip_rel_gap=0.01,
        )
        results.append(result)
        pd.DataFrame(results).set_index("date").to_csv(
            OUTPUT_DIR / "modeled_daily_generation.partial.csv",
            date_format="%Y-%m-%d",
        )

    modeled = pd.DataFrame(results).set_index("date").sort_index()
    if len(modeled) != 31 or not np.isfinite(modeled.select_dtypes("number")).all().all():
        raise RuntimeError("Monthly result is incomplete or contains non-finite values")
    write_outputs(modeled, observed, time.perf_counter() - started)


if __name__ == "__main__":
    main()
