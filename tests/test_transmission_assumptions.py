from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pypsa


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from transmission_assumptions import (  # noqa: E402
    INTERCONNECTOR_EFFICIENCY,
    INTERSTATE_FLOW_COST_PER_MWH,
    TRANSMISSION_FLOW_COST_PER_MWH,
    TRANSMISSION_LOSSES,
    add_transmission_flow_cost,
    interconnector_efficiency,
)


def test_interconnector_assumptions_match_indian_regulatory_sources() -> None:
    lengths = pd.Series([0.0, 165.0, 1765.0])
    assert np.allclose(interconnector_efficiency(lengths), INTERCONNECTOR_EFFICIENCY)
    assert INTERCONNECTOR_EFFICIENCY == 0.97
    assert INTERSTATE_FLOW_COST_PER_MWH == 241.81
    assert TRANSMISSION_FLOW_COST_PER_MWH == 1.0


def test_built_grid_contains_interstate_cost_and_losses() -> None:
    links = pd.read_csv(ROOT / "model" / "links.csv")
    grid = links.loc[links["kind"].isin(["HVDC", "interstate AC tie"])]
    assert len(grid) == 24
    assert np.allclose(grid["efficiency"], INTERCONNECTOR_EFFICIENCY)
    assert np.allclose(grid["marginal_cost"], INTERSTATE_FLOW_COST_PER_MWH)


def test_ac_line_losses_and_flow_cost_are_added_to_optimization() -> None:
    network = pypsa.Network()
    snapshots = pd.date_range("2025-04-01", periods=2, freq="h")
    network.set_snapshots(snapshots)
    network.add("Bus", "source", v_nom=230.0)
    network.add("Bus", "sink", v_nom=230.0)
    network.add(
        "Generator", "generator", bus="source", p_nom=100.0, marginal_cost=10.0
    )
    network.add("Load", "load", bus="sink", p_set=50.0)
    network.add(
        "Line",
        "line",
        bus0="source",
        bus1="sink",
        x=0.1,
        r=1.0,
        s_nom=100.0,
    )

    status, condition = network.optimize(
        solver_name="highs",
        transmission_losses=TRANSMISSION_LOSSES,
        extra_functionality=add_transmission_flow_cost,
        include_objective_constant=False,
    )

    assert (status, condition) == ("ok", "optimal")
    assert "Line-absolute-flow" in network.model.variables
    assert network.generators_t.p.to_numpy().sum() > network.loads_t.p.to_numpy().sum()
