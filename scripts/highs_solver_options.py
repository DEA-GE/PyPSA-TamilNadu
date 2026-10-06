"""Shared HiGHS parallelism settings for all optimization entry points."""

from __future__ import annotations


def highs_solver_options(
    unit_commitment: str,
    mip_rel_gap: float | None,
    threads: int,
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

    options: dict[str, object] = {
        "log_to_console": False,
        "threads": threads,
        "parallel": "on" if threads > 1 else "off",
    }
    if unit_commitment == "full":
        if mip_rel_gap is not None:
            options["mip_rel_gap"] = mip_rel_gap
    else:
        options.update(
            {
                "solver": "simplex",
                "simplex_strategy": 3 if threads > 1 else 1,
                "simplex_max_concurrency": threads,
            }
        )
    return options
