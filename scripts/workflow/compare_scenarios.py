"""Collect the headline results of several scenario runs side by side.

    python scripts/workflow/compare_scenarios.py <output folder> <name>=<run summary.yaml> ...

Each run summary is the file ``run_model.py --summary`` wrote for a
scenario's operational run. The output folder receives one CSV per table,
with one column per scenario:

- ``system_metrics.csv``: demand, unserved energy, costs, prices, ...
- ``generation_gwh.csv``: generation by carrier
- ``installed_capacity_mw.csv``: modelled capacity by carrier
- ``operating_costs_million_inr.csv``: operating cost by carrier and the
  penalty total
- ``scenario_technologies.csv``: the batteries and offshore wind each scenario
  added, with fixed or optimised capacities
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml


def _table(output_dir: Path, name: str) -> pd.DataFrame | None:
    path = output_dir / "analysis" / "tables" / f"{name}.csv"
    return pd.read_csv(path, index_col=0) if path.exists() else None


def _column(frame: pd.DataFrame | None, column: str) -> pd.Series:
    if frame is None or column not in frame:
        return pd.Series(dtype=float)
    return frame[column]


def collect(runs: dict[str, Path]) -> dict[str, pd.DataFrame]:
    """Return the comparison tables for ``{scenario: run summary}``."""
    metrics, units, generation, capacity, costs, technologies = {}, {}, {}, {}, {}, []
    for scenario, summary_path in runs.items():
        summary = yaml.safe_load(Path(summary_path).read_text(encoding="utf-8"))
        output_dir = Path(summary["output_dir"])
        system = _table(output_dir, "system_metrics")
        metrics[scenario] = _column(system, "value")
        if system is not None:
            units.update(system["unit"].to_dict())
        generation[scenario] = _column(_table(output_dir, "generation_by_carrier"), "generation_gwh")
        capacity[scenario] = _column(_table(output_dir, "generation_by_carrier"), "capacity_mw")
        cost_table = _table(output_dir, "operating_costs")
        costs[scenario] = (
            cost_table.filter(like="total_").iloc[:, 0] if cost_table is not None else pd.Series(dtype=float)
        )
        added = output_dir / "scenario_technologies.csv"
        if added.exists():
            technologies.append(pd.read_csv(added).assign(scenario=scenario))

    system_metrics = pd.DataFrame(metrics)
    system_metrics.insert(0, "unit", pd.Series(units).reindex(system_metrics.index))
    system_metrics.index.name = "metric"
    tables = {
        "system_metrics": system_metrics,
        "generation_gwh": pd.DataFrame(generation).rename_axis("carrier"),
        "installed_capacity_mw": pd.DataFrame(capacity).rename_axis("carrier"),
        "operating_costs_million_inr": pd.DataFrame(costs).rename_axis("carrier"),
        "scenario_technologies": (
            pd.concat(technologies, ignore_index=True).set_index("scenario")
            if technologies
            else pd.DataFrame(columns=["technology", "site", "bus", "capacity_mw"])
        ),
    }
    return tables


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("runs", nargs="+", metavar="NAME=SUMMARY")
    args = parser.parse_args(argv)
    runs = {}
    for item in args.runs:
        name, _, path = item.partition("=")
        if not name or not path:
            parser.error(f"expected NAME=SUMMARY, got {item!r}")
        runs[name] = Path(path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, table in collect(runs).items():
        table.to_csv(args.output_dir / f"{name}.csv")
    print(f"Scenario comparison written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
