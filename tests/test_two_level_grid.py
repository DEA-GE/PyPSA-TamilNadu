from pathlib import Path
import sys

import pandas as pd
import pypsa
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import two_level_grid as grid  # noqa: E402


def small_network() -> pypsa.Network:
    """Two Tamil Nadu areas, one neighbour; one mixed-voltage corridor and one mixed tie."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-04-01", periods=2, freq="h"))
    for name, kind in (("A", grid.TN_KIND), ("B", grid.TN_KIND), ("Neighbour", "neighbouring state")):
        n.add("Bus", name, v_nom=400.0, kind=kind, carrier="AC")
    n.add("Line", "A–B", bus0="A", bus1="B", x=1.0, r=0.1, s_nom=1300.0, s_max_pu=0.7, line_ids="L1 L2")
    for name, bus0, bus1 in (("A–Neighbour (AC tie) →", "A", "Neighbour"), ("A–Neighbour (AC tie) ←", "Neighbour", "A")):
        n.add("Link", name, bus0=bus0, bus1=bus1, p_nom=1300.0, efficiency=0.97, carrier="AC", line_ids="L3 L4")
    n.add("Carrier", ["AC", "coal"])
    n.add("Generator", "big_plant__unit_01", bus="A", p_nom=500.0, carrier="coal")
    n.add("Generator", "small_plant__unit_01", bus="A", p_nom=100.0, carrier="coal")
    n.add("Load", "A load", bus="A", p_set=300.0)
    return n


CIRCUITS = pd.DataFrame({
    "line_id": ["L1", "L2", "L3", "L4"], "kv": [400, 230, 400, 220], "capacity_mw": [1000.0, 300.0, 1000.0, 300.0],
    "x_ohm": [2.0, 4.0, 2.0, 4.0], "r_ohm": [0.2, 0.4, 0.2, 0.4], "b_siemens": [0.0] * 4,
}).set_index("line_id").assign(level=lambda c: ["upper", "lower", "upper", "lower"])

UNITS = pd.DataFrame({"area": ["A", "A", "B"], "kv": ["400/230", "400/110", "400/230"], "mva": [500.0, 200.0, 315.0]})


def test_corridor_split_keeps_rating_and_parallel_reactance():
    n = small_network()
    upper = {"A": "A 400kV", "B": "B 400kV"}
    for area, bus in upper.items():
        n.add("Bus", bus, v_nom=400.0)

    grid.split_corridors(n, CIRCUITS, upper)

    assert n.lines.loc["A–B", ["bus0", "bus1", "s_nom"]].tolist() == ["A 400kV", "B 400kV", 1000.0]
    assert n.lines.loc["A–B (230 kV)", ["bus0", "bus1", "s_nom"]].tolist() == ["A", "B", 300.0]
    assert 1 / (1 / n.lines.x).sum() == pytest.approx(1 / (1 / 2.0 + 1 / 4.0))
    assert n.lines.s_max_pu.eq(0.7).all()


def test_tie_split_moves_the_upper_part_and_keeps_direction():
    n = small_network()
    upper = {"A": "A 400kV", "B": "B 400kV"}
    for bus in upper.values():
        n.add("Bus", bus, v_nom=400.0)

    grid.split_ties(n, CIRCUITS, upper)

    links = n.links
    assert links.loc["A–Neighbour (AC tie) →", ["bus0", "bus1", "p_nom"]].tolist() == ["A 400kV", "Neighbour", 1000.0]
    assert links.loc["A–Neighbour (AC tie, 230 kV) ←", ["bus0", "bus1", "p_nom"]].tolist() == ["Neighbour", "A", 300.0]
    assert links.efficiency.eq(0.97).all()
    assert links.p_nom.sum() == 2 * 1300.0


def test_transformer_branch_rating_and_reactance():
    branches = grid.transformer_branches(UNITS, ["A", "B"], reactance_pu=0.125, power_factor=0.95, n_minus_1=True)

    a = branches.loc["A"]
    assert a.s_nom == pytest.approx(700 * 0.95)
    assert a.s_max_pu == pytest.approx(200 / 700)  # without the 500 MVA unit
    assert a.x == pytest.approx(1 / (500 / (0.125 * 160000) + 200 / (0.125 * 160000)))
    assert branches.loc["B", "s_max_pu"] == 0  # a single unit gives no N-1 capacity


def test_n_minus_1_per_substation_drops_each_substations_largest_unit():
    units = pd.DataFrame({
        "area": ["A"] * 5, "bus": ["S1", "S1", "S1", "S2", "S2"], "kv": ["400/230"] * 3 + ["400/110"] * 2,
        "mva": [500.0, 315.0, 315.0, 200.0, 200.0],
    })

    by_area = grid.transformer_branches(units, ["A"], 0.125, 0.95, "area").loc["A"]
    by_substation = grid.transformer_branches(units, ["A"], 0.125, 0.95, "substation").loc["A"]
    unlimited = grid.transformer_branches(units, ["A"], 0.125, 0.95, "none").loc["A"]

    assert by_area.s_max_pu == pytest.approx(1030 / 1530)
    assert by_substation.s_max_pu == pytest.approx((630 + 200) / 1530)
    assert unlimited.s_max_pu == 1.0
    assert by_substation.s_nom == by_area.s_nom == pytest.approx(1530 * 0.95)


def test_every_area_needs_transformers():
    with pytest.raises(grid.TwoLevelGridError, match="No 400/230"):
        grid.transformer_branches(UNITS, ["A", "B", "C"], 0.125, 0.95, True)


def test_off_by_default_leaves_the_network_unchanged():
    n = small_network()

    report = grid.apply_two_level_grid(n, None)

    assert report.empty and len(n.buses) == 3 and len(n.lines) == 1


def test_settings_are_validated():
    with pytest.raises(grid.TwoLevelGridError, match="Unknown grid keys"):
        grid.validate_section({"two_levels": True})
    with pytest.raises(grid.TwoLevelGridError, match="power_factor"):
        grid.validate_section({"transformer_power_factor": 1.5})
    with pytest.raises(grid.TwoLevelGridError, match="transformer_n_minus_1"):
        grid.validate_section({"transformer_n_minus_1": "feeder"})
    grid.validate_section({"transformer_n_minus_1": True})  # earlier configs: true meant per area
