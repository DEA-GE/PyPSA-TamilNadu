from pathlib import Path
import sys

import pandas as pd
import pypsa
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ists_re_plants as plants  # noqa: E402

SNAPSHOTS = pd.date_range("2025-07-01", periods=3, freq="h")


def inputs(tmp_path: Path, capacity: float = 200.0) -> dict:
    table = tmp_path / "plants.csv"
    pd.DataFrame({"seller": ["Tuticorin_X"], "technology": ["wind"], "pool": ["Tuticorin"], "area": ["A"],
                  "capacity_mw": [capacity]}).to_csv(table, index=False)
    series = tmp_path / "series.csv"
    pd.DataFrame({"snapshot": SNAPSHOTS[:2], "seller": "Tuticorin_X", "output_mw": [100.0, 300.0],
                  "scheduled_mw": [120.0, 150.0]}).to_csv(series, index=False)  # the third hour is missing
    return {"enabled": True, "series": str(series), "plants": str(table)}


def network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    n.add("Bus", "A")
    n.add("Carrier", ["wind", "solar"])
    n.add("Generator", "district_1", bus="A", carrier="wind", p_nom=600.0, marginal_cost=1.0, p_max_pu=[0.5, 0.5, 0.2])
    n.add("Generator", "district_2", bus="A", carrier="wind", p_nom=200.0, marginal_cost=1.0, p_max_pu=[0.1, 0.1, 0.6])
    n.add("Generator", "rooftop_grid_connected", bus="A", carrier="solar", p_nom=50.0)
    return n


def test_plant_runs_on_its_output_and_the_fleet_shrinks(tmp_path):
    n = network()

    report = plants.apply_ists_re_plants(n, inputs(tmp_path))

    assert n.generators.loc[["district_1", "district_2"], "p_nom"].tolist() == pytest.approx([450.0, 150.0])
    plant = n.get_switchable_as_dense("Generator", "p_max_pu")["ists_re_plant__tuticorin_x"]
    # 100/200, 300/200 capped at 1, and the fleet's weighted availability where data is missing
    assert plant.tolist() == pytest.approx([0.5, 1.0, (600 * 0.2 + 200 * 0.6) / 800])
    assert report.hours_from_fleet_profile.tolist() == [1]
    assert n.generators.at["ists_re_plant__tuticorin_x", "p_nom"] == 200.0


def test_scheduled_basis_uses_the_schedule(tmp_path):
    n = network()
    plants.apply_ists_re_plants(n, {**inputs(tmp_path), "basis": "scheduled"})
    plant = n.get_switchable_as_dense("Generator", "p_max_pu")["ists_re_plant__tuticorin_x"]
    assert plant.iloc[:2].tolist() == pytest.approx([0.6, 0.75])


def test_off_by_default_and_capacity_check(tmp_path):
    n = network()
    assert plants.apply_ists_re_plants(n, None).empty and len(n.generators) == 3
    with pytest.raises(plants.IstsRePlantError, match="exceed the area fleet"):
        plants.apply_ists_re_plants(n, inputs(tmp_path, capacity=900.0))
    with pytest.raises(plants.IstsRePlantError, match="basis"):
        plants.validate_section({"basis": "forecast"})


def test_wind_seasonal_reshapes_the_fleet_but_not_the_plants(tmp_path):
    factors = tmp_path / "factors.csv"
    pd.DataFrame({"area": ["A"], "month": ["2025-07"], "factor": [1.5]}).to_csv(factors, index=False)
    n = network()

    report = plants.apply_ists_re_plants(n, {**inputs(tmp_path), "wind_seasonal": True, "wind_factors": str(factors)})

    p_max_pu = n.get_switchable_as_dense("Generator", "p_max_pu")
    assert p_max_pu["district_1"].tolist() == pytest.approx([0.75, 0.75, 0.3])
    assert p_max_pu["district_2"].tolist() == pytest.approx([0.15, 0.15, 0.9])
    assert p_max_pu["ists_re_plant__tuticorin_x"].iloc[0] == pytest.approx(0.5)  # the plant keeps its output
    seasonal = report.attrs["wind_seasonal"].set_index("area")
    assert seasonal.loc["A", "capped_gwh"] == pytest.approx(0.0)


def test_seasonal_factors_keep_the_annual_energy():
    import prepare_wind_seasonal_factors as factors

    hours = pd.date_range("2025-04-01", periods=24 * 61, freq="h")  # April and May
    profiles = pd.DataFrame({"tirunelveli_wind": 0.2, "madurai_wind": 0.3}, index=hours)
    output = pd.Series([10.0] * (24 * 30) + [30.0] * (24 * 31), index=hours)  # plants: May three times April
    series = pd.concat([pd.DataFrame({"snapshot": hours, "seller": seller, "output_mw": output.to_numpy()})
                        for group in factors.GROUPS.values() for seller in group])
    capacity = pd.DataFrame({"capacity_mw": 100.0}, index=pd.Index(series.seller.unique(), name="seller"))

    table = factors.seasonal_factors(series, capacity, profiles)

    madurai = table[table.area.eq("Madurai")].set_index("month").factor
    assert madurai["2025-05"] / madurai["2025-04"] == pytest.approx(3.0)
    assert (madurai["2025-04"] * 30 + madurai["2025-05"] * 31) / 61 == pytest.approx(1.0)
