"""Shared HiGHS parallelism settings for all optimization entry points."""

from __future__ import annotations


def reset_highs_global_scheduler() -> None:
    """Release HiGHS' process-wide thread scheduler before a new solve.

    HiGHS fixes its global scheduler to the thread count of the first solve in
    a Python process. Without a reset, changing ``SOLVER_THREADS`` in a
    notebook kernel makes the next solve return ``Not Set``.  These workflows
    do not run simultaneous solves in one process, so a forced reset is safe.
    """
    import highspy

    highspy.Highs.resetGlobalScheduler(True)


# Per-attempt rolling-window time limits, by unit-commitment mode. Measured
# solve times (FY2025-26 model, Nov 2026): none ~3-8 s (simplex); relaxed
# with coal-only commitment ~70 s per week (interior point; the parallel dual
# simplex fallback ~210-230 s); full is a MIP and keeps its existing limit
# with gap-based acceptance. 700 s is ~10x a normal relaxed week, so a
# stalled interior-point solve falls back to the simplex within ~12 minutes.
DEFAULT_ROLLING_TIME_LIMIT_SECONDS = {"none": 300.0, "relaxed": 700.0, "full": 1800.0}


def default_lp_solver(unit_commitment: str) -> str:
    """Return the LP algorithm used by default for a unit-commitment mode.

    Relaxed unit commitment produces large LPs (~400k rows per week after
    presolve) on which HiGHS' interior-point solver with crossover was 3.9x
    faster than the parallel dual simplex with an identical objective. No-UC
    weeks are small and solve faster with the dual simplex.
    """
    return "ipm" if unit_commitment == "relaxed" else "simplex"


def highs_solver_options(
    unit_commitment: str,
    mip_rel_gap: float | None,
    threads: int,
    simplex_strategy: int | None = None,
    lp_solver: str | None = None,
) -> dict[str, object]:
    """Return explicit, formulation-appropriate HiGHS options.

    HiGHS recommends no more than eight threads for most memory-bound solves.
    Full unit commitment uses parallel MIP tree search. Relaxed unit
    commitment uses the interior-point solver with crossover (basic solution
    and duals); no-UC uses the parallel PAMI dual simplex. ``lp_solver``
    ("ipm" or "simplex") overrides the mode default; an explicit
    ``simplex_strategy`` implies the simplex.
    """
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError("solver_threads must be a positive integer")
    if threads > 8:
        raise ValueError(
            "solver_threads must not exceed 8; HiGHS parallelism is normally "
            "memory-bandwidth-bound and simplex concurrency is capped at 8"
        )
    if unit_commitment not in {"full", "relaxed", "none"}:
        raise ValueError(f"Unknown unit-commitment mode: {unit_commitment!r}")
    if simplex_strategy is not None and (
        isinstance(simplex_strategy, bool)
        or not isinstance(simplex_strategy, int)
        or simplex_strategy not in range(5)
    ):
        raise ValueError("simplex_strategy must be an integer from 0 to 4")

    options: dict[str, object] = {
        "log_to_console": False,
        "threads": threads,
        "parallel": "on" if threads > 1 else "off",
    }
    if unit_commitment == "full":
        if mip_rel_gap is not None:
            options["mip_rel_gap"] = mip_rel_gap
        if simplex_strategy is not None:
            options["simplex_strategy"] = simplex_strategy
    else:
        if lp_solver is None:
            lp_solver = "simplex" if simplex_strategy is not None else default_lp_solver(unit_commitment)
        if lp_solver not in {"ipm", "simplex"}:
            raise ValueError(f"lp_solver must be 'ipm' or 'simplex', not {lp_solver!r}")
        if lp_solver == "ipm":
            options.update({"solver": "ipm", "run_crossover": "on"})
        else:
            options.update(
                {
                    "solver": "simplex",
                    "simplex_strategy": (
                        simplex_strategy if simplex_strategy is not None
                        else 3 if threads > 1 else 1
                    ),
                    "simplex_max_concurrency": threads,
                }
            )
    return options


# HiGHS' default random_seed is 0. A different seed changes pivoting and
# task-scheduling choices, so a retry does not follow the same solver path.
RETRY_RANDOM_SEED = 1
# Termination conditions that a different solver path cannot change.
NON_RETRYABLE_CONDITIONS = {"infeasible", "infeasible_or_unbounded", "unbounded"}


def solve_attempts(
    unit_commitment: str,
    mip_rel_gap: float | None,
    threads: int,
    simplex_strategy: int | None,
    time_limit: float | None,
) -> list[tuple[str, dict[str, object]]]:
    """Return the ordered solver settings to try for one optimization.

    Interior-point runs (relaxed unit commitment by default):
    1. Interior point with crossover.
    2. The dual simplex (parallel when threads > 1): a different algorithm,
       so it cannot repeat the first attempt's failure.

    Simplex and MIP runs:
    1. The configured settings.
    2. The same settings with a different random seed. A parallel solve that
       stalls (for example, a scheduler deadlock that makes no iterations
       before the time limit) usually completes on a different path.
    3. The serial solver, only when the configured run is parallel.

    Retries need a finite time limit; without one a stalled solve never
    returns, so only the configured attempt is listed.
    """
    configured = highs_solver_options(unit_commitment, mip_rel_gap, threads, simplex_strategy)
    if time_limit is None:
        return [("configured", configured)]
    configured["time_limit"] = time_limit
    if configured.get("solver") == "ipm":
        simplex = highs_solver_options(unit_commitment, mip_rel_gap, threads, lp_solver="simplex")
        simplex["time_limit"] = time_limit
        return [
            ("interior point", configured),
            ("parallel dual simplex" if threads > 1 else "dual simplex", simplex),
        ]
    attempts = [
        ("configured", configured),
        ("configured with new random seed", {**configured, "random_seed": RETRY_RANDOM_SEED}),
    ]
    if threads > 1:
        serial = highs_solver_options(unit_commitment, mip_rel_gap, 1)
        serial["time_limit"] = time_limit
        attempts.append(("serial", serial))
    return attempts
