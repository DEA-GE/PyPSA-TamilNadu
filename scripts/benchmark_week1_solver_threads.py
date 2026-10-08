"""Benchmark HiGHS thread counts on short rolling-model runs.

Full and relaxed UC cases solve the first rolling window: seven retained days
plus a one-day look-ahead. No-UC cases solve the full rolling year by default,
or several consecutive windows when requested. Cases run one at a time, so
their HiGHS threads never compete with each other. Results are written below
``results/benchmarks`` by default, separate from study runs.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from run_9ba_selection import run_selection


DEFAULT_THREADS = (1, 2, 3, 4, 8)
DEFAULT_MODES = ("full", "relaxed", "none")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threads",
        type=int,
        nargs="+",
        default=DEFAULT_THREADS,
        help="HiGHS thread counts to test (default: 1 2 3 4 8)",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=DEFAULT_MODES,
        default=DEFAULT_MODES,
        help="Unit-commitment formulations to test (default: full relaxed none)",
    )
    parser.add_argument(
        "--simplex-strategies",
        nargs="+",
        choices=("default", "0"),
        default=("default",),
        help=(
            "HiGHS simplex strategies to compare: default keeps the current "
            "strategy (serial dual with 1 thread, PAMI otherwise); 0 lets "
            "HiGHS choose (default: default)"
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/benchmarks/week1_solver_threads"),
        help="Directory for case outputs and the benchmark summary",
    )
    parser.add_argument(
        "--mip-rel-gap",
        type=float,
        default=0.01,
        help="Target MIP gap for full unit commitment (default: 0.01)",
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=None,
        help="Optional per-window limit in seconds; omit for no limit",
    )
    parser.add_argument(
        "--none-max-windows",
        type=int,
        default=None,
        help="Consecutive rolling windows for no-UC cases (default: entire year)",
    )
    return parser.parse_args()


def _validate_args(args: argparse.Namespace) -> None:
    if any(thread < 1 or thread > 8 for thread in args.threads):
        raise ValueError("All --threads values must be integers from 1 to 8")
    if len(set(args.threads)) != len(args.threads):
        raise ValueError("Each --threads value must appear only once")
    if len(set(args.simplex_strategies)) != len(args.simplex_strategies):
        raise ValueError("Each --simplex-strategies value must appear only once")
    if not 0 <= args.mip_rel_gap <= 1:
        raise ValueError("--mip-rel-gap must be between 0 and 1")
    if args.time_limit is not None and args.time_limit <= 0:
        raise ValueError("--time-limit must be positive")
    if args.none_max_windows is not None and args.none_max_windows < 1:
        raise ValueError("--none-max-windows must be a positive integer")


def _case_row(
    result: dict, mode: str, threads: int, strategy_variant: str, elapsed: float
) -> dict:
    output_dir = Path(result["output_dir"])
    validation = result["validation"]
    log = pd.read_csv(output_dir / "window_log.csv")
    window = log.iloc[0]
    return {
        "unit_commitment": mode,
        "solver_threads": threads,
        "strategy_variant": strategy_variant,
        "simplex_strategy": window.get("simplex_strategy"),
        "windows_completed": validation["windows_completed"],
        "status": window["solver_status"],
        "termination_condition": window["termination_condition"],
        "accepted_solution": bool(window["accepted_solution"]),
        "mip_gap": window.get("mip_gap"),
        "solver_runtime_seconds": validation["solver_runtime_seconds_sum"],
        "wall_clock_seconds": elapsed,
        "process_cpu_seconds": window.get("process_cpu_seconds"),
        "average_cpu_cores": window.get("average_cpu_cores"),
        "peak_process_rss_mb": window.get("peak_process_rss_mb_sampled"),
        "peak_process_threads": window.get("peak_process_threads_sampled"),
        "max_nodal_residual_mw": window["max_nodal_residual_mw"],
        "max_corridor_loading_pct": window["max_corridor_loading_pct"],
        "output_dir": str(output_dir),
    }


def _no_uc_week_rows(
    result: dict, threads: int, strategy_variant: str
) -> list[dict]:
    """Return a timing row for every retained no-UC rolling window."""
    output_dir = Path(result["output_dir"])
    windows = pd.read_csv(output_dir / "window_log.csv")
    columns = [
        "window",
        "solve_start",
        "solve_end",
        "committed_snapshots",
        "lookahead_snapshots",
        "solver_status",
        "termination_condition",
        "simplex_strategy",
        "runtime_seconds",
        "process_cpu_seconds",
        "average_cpu_cores",
        "peak_process_rss_mb_sampled",
        "peak_process_threads_sampled",
        "max_nodal_residual_mw",
        "max_corridor_loading_pct",
    ]
    available = windows.reindex(columns=columns)
    available.insert(0, "solver_threads", threads)
    available.insert(0, "strategy_variant", strategy_variant)
    available.insert(0, "unit_commitment", "none")
    available.insert(0, "output_dir", str(output_dir))
    return available.to_dict(orient="records")


def main() -> Path:
    args = parse_args()
    _validate_args(args)
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    no_uc_week_rows: list[dict] = []

    for mode in args.modes:
        for threads in args.threads:
            for strategy_variant in args.simplex_strategies:
                simplex_strategy = 0 if strategy_variant == "0" else None
                max_windows = args.none_max_windows if mode == "none" else 1
                horizon_label = "full_year" if max_windows is None else f"{max_windows}w"
                strategy_label = "_simplex0" if simplex_strategy == 0 else ""
                run_name = f"benchmark_{mode}_{threads}t{strategy_label}_{horizon_label}"
                print(
                    f"\nBenchmarking {mode} UC with {threads} HiGHS thread(s), "
                    f"simplex strategy {strategy_variant}...",
                    flush=True,
                )
                started = time.perf_counter()
                try:
                    result = run_selection(
                        "rolling",
                        unit_commitment=mode,
                        solver_name="highs",
                        solver_threads=threads,
                        simplex_strategy=simplex_strategy,
                        parallel_workers=1,
                        mip_rel_gap=args.mip_rel_gap,
                        rolling_time_limit=args.time_limit,
                        rolling_max_time_limit_mip_gap=args.mip_rel_gap,
                        run_name=run_name,
                        results_root=output_root,
                        resume=False,
                        max_windows=max_windows,
                    )
                    rows.append(
                        _case_row(
                            result, mode, threads, strategy_variant,
                            time.perf_counter() - started,
                        )
                    )
                    if mode == "none":
                        no_uc_week_rows.extend(
                            _no_uc_week_rows(result, threads, strategy_variant)
                        )
                except Exception as error:
                    rows.append(
                        {
                            "unit_commitment": mode,
                            "solver_threads": threads,
                            "strategy_variant": strategy_variant,
                            "simplex_strategy": simplex_strategy,
                            "status": "error",
                            "termination_condition": type(error).__name__,
                            "accepted_solution": False,
                            "error": str(error),
                            "wall_clock_seconds": time.perf_counter() - started,
                        }
                    )
                    print(f"Case failed: {error}", flush=True)

    summary = pd.DataFrame(rows)
    summary_path = output_root / "benchmark_summary.csv"
    summary.to_csv(summary_path, index=False)
    (output_root / "benchmark_summary.json").write_text(
        json.dumps(rows, indent=2, default=str), encoding="utf-8"
    )
    if no_uc_week_rows:
        no_uc_week_path = output_root / "no_uc_week_timings.csv"
        pd.DataFrame(no_uc_week_rows).to_csv(no_uc_week_path, index=False)
        print(f"No-UC weekly timings: {no_uc_week_path}", flush=True)
    columns = [
        "unit_commitment",
        "solver_threads",
        "strategy_variant",
        "simplex_strategy",
        "windows_completed",
        "status",
        "termination_condition",
        "mip_gap",
        "solver_runtime_seconds",
        "wall_clock_seconds",
        "average_cpu_cores",
    ]
    print("\n" + summary.reindex(columns=columns).to_string(index=False), flush=True)
    print(f"\nBenchmark summary: {summary_path}", flush=True)
    return summary_path


if __name__ == "__main__":
    main()
