"""Run the standardized result analysis without opening Jupyter."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from results_analysis import (
    analyze_run,
    export_analysis,
    load_analysis_run,
    load_observed_generation,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze a solved PyPSA network or rolling-run directory."
    )
    parser.add_argument("result", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--no-observed", action="store_true")
    parser.add_argument("--no-export", action="store_true")
    args = parser.parse_args()

    run = load_analysis_run(args.result)
    observed = None if args.no_observed else load_observed_generation()
    bundle = analyze_run(run, observed)

    print(f"Run type: {run.run_type}")
    print(f"Period: {run.snapshots[0]} to {run.snapshots[-1]}")
    print(f"Snapshots: {len(run.snapshots):,}")
    print(bundle.tables["system_metrics"].to_string())
    if not args.no_export:
        destination = export_analysis(run, bundle, args.output_dir)
        print(f"Analysis written to {destination}")
    plt.close("all")


if __name__ == "__main__":
    main()
