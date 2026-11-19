"""Save the capacities chosen by a capacity-expansion run.

    python scripts/workflow/extract_capacities.py <run summary.yaml> <capacities.csv>

The run summary is the file ``run_model.py --summary`` wrote for the
capacity-expansion run. The CSV is the format of
``technologies.fixed_capacities``, which fixes these capacities for the
scenario's operational run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pypsa
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenario_technologies import write_optimised_capacities  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("summary", type=Path, help="run summary of the capacity-expansion run")
    parser.add_argument("output", type=Path, help="CSV of optimised capacities to write")
    args = parser.parse_args(argv)

    summary = yaml.safe_load(args.summary.read_text(encoding="utf-8"))
    network_file = Path(summary["output_dir"]) / "solved_network.nc"
    if not network_file.exists():
        raise FileNotFoundError(f"The capacity-expansion run has no solved network: {network_file}")
    table = write_optimised_capacities(pypsa.Network(network_file), args.output)
    if table.empty:
        raise ValueError(f"{network_file} has no optimised scenario capacities")
    print(table.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
