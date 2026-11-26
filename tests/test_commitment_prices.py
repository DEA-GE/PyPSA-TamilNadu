from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pypsa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import commitment_prices as prices  # noqa: E402


def _network() -> pypsa.Network:
    """One bus: a committable cheap unit (min 50 %) and a flexible expensive one."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-04-01", periods=3, freq="h"))
    n.add("Bus", "bus")
    n.add("Load", "load", bus="bus", p_set=pd.Series([100.0, 60.0, 100.0], index=n.snapshots))
    n.add(
        "Generator", "coal", bus="bus", p_nom=80.0, marginal_cost=10.0,
        committable=True, p_min_pu=0.5, start_up_cost=500.0,
    )
    n.add("Generator", "gas", bus="bus", p_nom=100.0, marginal_cost=50.0)
    return n


def test_fixed_commitment_gives_prices():
    n = _network()
    status, condition = n.optimize(solver_name="highs")
    assert (status, condition) == ("ok", "optimal")
    assert prices.integer_variables(n.model) == ["Generator-status", "Generator-start_up", "Generator-shut_down"]

    summary = prices.price_fixed_commitment(n, solver_name="highs", threads=1)

    assert summary["commitment_prices"] is True
    assert np.isclose(summary["price_lp_objective"], summary["mip_objective"])
    # Gas sets the price while coal is at full output; coal itself in hour 2.
    assert np.allclose(n.buses_t.marginal_price["bus"], [50.0, 10.0, 50.0])
    assert np.allclose(n.generators_t.status["coal"], 1.0)
    assert not prices.integer_variables(n.model)


def test_no_integer_variables_leaves_the_solution():
    n = _network()
    n.generators.loc["coal", "committable"] = False
    n.optimize(solver_name="highs")
    before = n.buses_t.marginal_price.copy()

    summary = prices.price_fixed_commitment(n, solver_name="highs", threads=1)

    assert summary["commitment_prices"] is False
    assert summary["price_lp_condition"] == "no integer variables"
    pd.testing.assert_frame_equal(n.buses_t.marginal_price, before)


def test_attempts_carry_the_time_limit():
    attempts = prices.price_attempts(threads=4, time_limit=120)
    assert [label for label, _ in attempts] == ["dual simplex", "interior point"]
    assert all(options["time_limit"] == 120.0 for _, options in attempts)
    assert attempts[1][1]["solver"] == "ipm"
