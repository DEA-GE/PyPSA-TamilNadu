from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pypsa  # noqa: E402

from compare_energy_target_runs import iced_plant_monthly_gwh, month_coverage, operating_cost_by_carrier  # noqa: E402


def test_month_coverage_counts_distinct_days():
    hours = pd.date_range("2025-09-29", "2025-10-02 23:00", freq="h")
    coverage = month_coverage(hours)
    assert coverage.to_dict() == pytest.approx({
        pd.Timestamp("2025-09-01"): 2 / 30,
        pd.Timestamp("2025-10-01"): 2 / 31,
    })


def test_iced_plants_are_scaled_to_the_covered_share():
    coverage = pd.Series({pd.Timestamp("2025-04-01"): 1.0, pd.Timestamp("2025-05-01"): 0.5})
    iced = iced_plant_monthly_gwh(coverage)
    kuttalam = iced.loc[iced["plant"].str.startswith("Kuttalam")].set_index("month")["gwh"]
    full = pd.read_csv(ROOT / "data/model_inputs/niti_iced_tamil_nadu_monthly_generation.csv", parse_dates=["month"])
    full = full.loc[full["plant_name"].str.startswith("Kuttalam", na=False)].set_index("month")["generation_mu"]
    assert kuttalam[pd.Timestamp("2025-04-01")] == pytest.approx(full[pd.Timestamp("2025-04-01")])
    assert kuttalam[pd.Timestamp("2025-05-01")] == pytest.approx(0.5 * full[pd.Timestamp("2025-05-01")])
    assert set(iced["month"]) == set(coverage.index)


def test_operating_cost_uses_time_varying_marginal_costs():
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2025-04-30 23:00", periods=2, freq="h"))
    network.add("Bus", "b")
    network.add("Carrier", "coal")
    network.add("Generator", "monthly", bus="b", carrier="coal", p_nom=10,
                marginal_cost=pd.Series([1e7, 2e7], index=network.snapshots))
    network.add("Generator", "static", bus="b", carrier="coal", p_nom=10, marginal_cost=1e7)
    network.generators_t.p = pd.DataFrame({"monthly": [1.0, 1.0], "static": [1.0, 0.0]}, index=network.snapshots)

    assert operating_cost_by_carrier(network)["coal"] == pytest.approx(4.0)
