"""Marginal prices for unit-commitment solves.

A unit-commitment problem with binary on/off decisions is a MIP, and a MIP has
no shadow prices: HiGHS returns none, so the buses get no marginal price. The
usual remedy, also used in market clearing, is a second solve: every binary
(on/off status, start-up, shut-down) is fixed at its value in the MIP solution,
integrality is dropped and the remaining dispatch LP is solved again. Its duals
are the prices of the dispatch given the commitment.

What these prices mean:

- In each hour the price is the cost of one more MWh from the units that are
  online and not at a limit, with the grid, storage and energy targets as in
  the MIP.
- Start-up costs are fixed by then, so they never set a price.
- A unit held at its minimum stable output does not set the price either. In
  hours when online units sit at their minimum, the price can fall to the cost
  of whatever is still flexible (curtailment, storage, the transmission flow
  cost), or below zero.

The LP solution also replaces the MIP's dispatch. The commitment is the same;
the dispatch is the cheapest one for that commitment, so it costs at most as
much as the MIP's, which was accepted within its gap.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pypsa

from highs_solver_options import highs_solver_options


def integer_variables(model: Any) -> list[str]:
    """Names of the model's binary and integer variables."""
    return [
        name
        for name, variable in model.variables.items()
        if variable.attrs.get("binary") or variable.attrs.get("integer")
    ]


def price_attempts(threads: int, time_limit: float | None) -> list[tuple[str, dict[str, object]]]:
    """LP settings for the fixed-commitment solve: dual simplex, then interior point."""
    attempts = [
        ("dual simplex", highs_solver_options("none", None, threads)),
        ("interior point", highs_solver_options("none", None, threads, lp_solver="ipm")),
    ]
    if time_limit is not None:
        for _, options in attempts:
            options["time_limit"] = float(time_limit)
    return attempts


def price_fixed_commitment(
    network: pypsa.Network,
    *,
    solver_name: str,
    threads: int,
    time_limit: float | None = None,
    assign_all_duals: bool = False,
) -> dict[str, object]:
    """Re-solve a solved unit-commitment MIP as an LP with the commitment fixed.

    ``network.model`` must hold the accepted MIP solution. On success the LP's
    dispatch and duals (``buses_t.marginal_price`` among them) are assigned to
    the network. When no attempt reaches optimality the MIP dispatch is kept
    and the buses' marginal prices are NaN. Returns a summary for the run log.
    """
    model = network.model
    names = integer_variables(model)
    summary: dict[str, object] = {
        "commitment_prices": False,
        "price_lp_attempts": "",
        "price_lp_seconds": 0.0,
        "price_lp_condition": None,
        "mip_objective": float(model.objective.value),
        "price_lp_objective": None,
    }
    if not names:
        summary["price_lp_condition"] = "no integer variables"
        return summary
    for name in names:
        variable = model.variables[name]
        # Masked entries have no solution; their bounds are never used.
        variable.fix(variable.solution.fillna(0.0))
        variable.relax()

    log: list[str] = []
    started = time.perf_counter()
    status = condition = None
    for label, options in price_attempts(threads, time_limit):
        attempt_started = time.perf_counter()
        status, condition = model.solve(solver_name=solver_name, **options)
        log.append(f"{label}: {condition} in {time.perf_counter() - attempt_started:.0f} s")
        if status == "ok" and condition == "optimal":
            break
    summary["price_lp_seconds"] = time.perf_counter() - started
    summary["price_lp_attempts"] = "; ".join(log)
    summary["price_lp_condition"] = str(condition)
    if status == "ok" and condition == "optimal":
        network.optimize.assign_solution()
        network.optimize.assign_duals(assign_all_duals)
        network.optimize.post_processing()
        summary["commitment_prices"] = True
        summary["price_lp_objective"] = float(model.objective.value)
    else:
        network.buses_t.marginal_price = network.buses_t.marginal_price.reindex(
            index=network.snapshots, columns=network.buses.index
        ).astype(float) * np.nan
    return summary
