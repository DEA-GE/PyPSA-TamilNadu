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


def highs_solver_options(
    unit_commitment: str,
    mip_rel_gap: float | None,
    threads: int,
    simplex_strategy: int | None = None,
) -> dict[str, object]:
    """Return explicit, formulation-appropriate HiGHS options.

    HiGHS recommends no more than eight threads for most memory-bound solves.
    Full unit commitment uses parallel MIP tree search. Relaxed and no-UC
    formulations use the parallel PAMI dual-simplex implementation.
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
