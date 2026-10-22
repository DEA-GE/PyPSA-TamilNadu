from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "model_inputs"
MODEL = ROOT / "model"
EXPECTED_GWH = {
    ("import", "resolved"): 27_765.9794200001,
    ("import", "unresolved_distributed"): 20_826.9511525,
    ("export", "resolved"): 15_504.698395,
    ("export", "unresolved_distributed"): 8_000.055805,
}


def test_prepared_rea_totals_and_shares() -> None:
    hourly = pd.read_csv(INPUT / "rea_interstate_hourly_flows_FY2025_26.csv")
    totals = hourly.groupby(["direction", "measure"])["energy_MWh"].sum() / 1_000.0
    assert np.isclose(totals.loc[("import", "resolved")], EXPECTED_GWH[("import", "resolved")])
    assert np.isclose(totals.loc[("import", "unresolved_total")], EXPECTED_GWH[("import", "unresolved_distributed")])
    assert np.isclose(totals.loc[("export", "resolved")], EXPECTED_GWH[("export", "resolved")])
    assert np.isclose(totals.loc[("export", "unresolved_total")], EXPECTED_GWH[("export", "unresolved_distributed")])
    shares = pd.read_csv(INPUT / "rea_interstate_gateway_shares_FY2025_26.csv")
    assert set(shares["gateway"]) == {"Andhra Pradesh", "Karnataka", "Kerala", "Western Region"}
    assert np.allclose(shares.groupby("direction")["allocation_share"].sum(), 1.0)


def test_model_keeps_resolved_and_distributed_measures_separate() -> None:
    audit = pd.read_csv(MODEL / "interstate_flow_audit.csv")
    totals = audit.groupby(["direction", "measure"])["flow_MW"].sum() / 1_000.0
    for key, expected in EXPECTED_GWH.items():
        assert np.isclose(totals.loc[key], expected, atol=1e-6)
    assert set(audit.loc[audit["measure"].eq("resolved"), "status"]) == {"audited_resolved"}
    assert set(audit.loc[audit["measure"].eq("unresolved_distributed"), "status"]) == {
        "unresolved_distributed_not_verified"
    }

    loads = pd.read_csv(MODEL / "loads.csv")
    interstate = loads.loc[loads["name"].str.startswith("interstate_")]
    assert len(interstate) == 16
    assert set(interstate["carrier"]) == {
        "interstate_exchange_resolved",
        "interstate_exchange_unresolved_distributed",
    }
    reconciliation = pd.read_csv(MODEL / "interstate_flow_reconciliation.csv")
    reconciled = reconciliation.groupby(["direction", "measure"])["energy_GWh"].sum()
    for key, expected in EXPECTED_GWH.items():
        assert np.isclose(reconciled.loc[key], expected, atol=1e-6)


def test_hourly_conservation_signs_and_gateway_capacity() -> None:
    source = pd.read_csv(
        INPUT / "rea_interstate_hourly_flows_FY2025_26.csv", parse_dates=["snapshot"]
    )
    audit = pd.read_csv(MODEL / "interstate_flow_audit.csv", parse_dates=["snapshot"])
    profiles = pd.read_csv(MODEL / "loads-p_set.csv", index_col=0)

    unresolved_source = source.loc[source["measure"].eq("unresolved_total")].pivot_table(
        index="snapshot", columns="direction", values="flow_MW", aggfunc="sum"
    )
    unresolved_model = audit.loc[audit["measure"].eq("unresolved_distributed")].pivot_table(
        index="snapshot", columns="direction", values="flow_MW", aggfunc="sum"
    )
    assert np.allclose(unresolved_source, unresolved_model)

    combined = audit.groupby(["snapshot", "direction", "gateway"], as_index=False).agg(
        flow_MW=("flow_MW", "sum"), capacity_MW=("gateway_capacity_MW", "first")
    )
    assert (combined["flow_MW"] <= combined["capacity_MW"] + 1e-6).all()

    for row in audit[["load_name", "direction"]].drop_duplicates().itertuples(index=False):
        expected_sign = -1.0 if row.direction == "import" else 1.0
        nonzero = profiles[row.load_name].loc[profiles[row.load_name].ne(0.0)]
        assert (np.sign(nonzero) == expected_sign).all()

    assert not (MODEL / "links-p_min_pu.csv").exists()
    assert not (MODEL / "links-p_max_pu.csv").exists()
