"""Temporary rolling LP diagnostic excluding distributed unresolved REA flows."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from run_9ba_selection import (
    DEFAULT_NETWORK,
    DEFAULT_RESULTS_ROOT,
    _configure_unit_commitment,
    _load_base_network,
)
from run_9ba_weekly_days_2025_26 import read_observed_generation, read_oil_gas_budget
from run_9ba_rolling import run_rolling_year


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-windows", type=int, default=None)
    args = parser.parse_args()
    output = (
        Path(DEFAULT_RESULTS_ROOT)
        / "rolling_2025-04-01_2026-03-31_none_uc"
        / "LP_IE_resolved_only_diagnostic"
    )
    if output.exists():
        raise FileExistsError(f"Diagnostic output already exists: {output}")
    network, _ = _load_base_network(Path(DEFAULT_NETWORK))
    _configure_unit_commitment(network, "none")
    columns = network.loads.index[
        network.loads.index.str.startswith("interstate_unresolved_distributed_")
    ]
    if len(columns) != 8 or not set(columns).issubset(network.loads_t.p_set.columns):
        raise ValueError("Expected eight distributed interstate load profiles")
    original = network.loads_t.p_set.loc[:, columns].copy()
    import_columns = columns[columns.str.contains("_import_")]
    export_columns = columns[columns.str.contains("_export_")]
    removed_import_gwh = -float(original[import_columns].sum().sum()) / 1000
    removed_export_gwh = float(original[export_columns].sum().sum()) / 1000
    if not np.isclose(removed_import_gwh, 20826.9511525, atol=1e-5):
        raise ValueError(f"Unexpected distributed import total: {removed_import_gwh}")
    if not np.isclose(removed_export_gwh, 8000.055805, atol=1e-5):
        raise ValueError(f"Unexpected distributed export total: {removed_export_gwh}")
    network.loads_t.p_set.loc[:, columns] = 0.0
    print(
        f"Zeroed distributed imports {removed_import_gwh:.3f} GWh and "
        f"exports {removed_export_gwh:.3f} GWh; resolved flows retained.",
        flush=True,
    )
    observed = read_observed_generation()
    run_rolling_year(
        network,
        observed,
        read_oil_gas_budget(observed),
        solver_name="highs",
        mip_rel_gap=0.01,
        time_limit=None,
        max_time_limit_mip_gap=0.10,
        solver_threads=3,
        unit_commitment="none",
        output_dir=output,
        resume=False,
        max_windows=args.max_windows,
    )
    print(f"Diagnostic results: {output}", flush=True)


if __name__ == "__main__":
    main()
