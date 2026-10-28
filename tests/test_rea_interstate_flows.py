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


def test_model_uses_only_confirmed_imports() -> None:
    audit = pd.read_csv(MODEL / "interstate_flow_audit.csv")
    totals = audit.groupby(["direction", "measure"])["flow_MW"].sum() / 1_000.0
    for key, expected in EXPECTED_GWH.items():
        assert np.isclose(totals.loc[key], expected, atol=1e-6)
    assert set(audit.loc[audit["measure"].eq("resolved"), "status"]) == {"audited_resolved"}
    assert set(audit.loc[
        audit["direction"].eq("export") & audit["measure"].eq("unresolved_distributed"),
        "status",
    ]) == {
        "unresolved_distributed_not_verified"
    }
    excluded_imports = audit.loc[
        audit["direction"].eq("import") & audit["measure"].eq("unresolved_distributed")
    ]
    assert set(excluded_imports["status"]) == {
        "unresolved_distributed_excluded_from_model"
    }
    assert not excluded_imports["included_in_model"].any()
    assert np.allclose(excluded_imports["modeled_flow_MW"], 0.0)
    assert np.allclose(excluded_imports["external_load_p_set_MW"], 0.0)

    loads = pd.read_csv(MODEL / "loads.csv")
    interstate = loads.loc[loads["name"].str.startswith("interstate_")]
    assert len(interstate) == 12
    assert not interstate["name"].str.startswith(
        "interstate_unresolved_distributed_import_"
    ).any()
    assert set(interstate["carrier"]) == {"electricity"}
    reconciliation = pd.read_csv(MODEL / "interstate_flow_reconciliation.csv")
    reconciled = reconciliation.groupby(["direction", "measure"])["energy_GWh"].sum()
    for key, expected in EXPECTED_GWH.items():
        assert np.isclose(reconciled.loc[key], expected, atol=1e-6)
    modeled = reconciliation.groupby(["direction", "measure"])["modeled_energy_GWh"].sum()
    assert np.isclose(
        modeled.loc[("import", "resolved")], EXPECTED_GWH[("import", "resolved")]
    )
    assert np.isclose(modeled.loc[("import", "unresolved_distributed")], 0.0)


def test_hourly_conservation_signs_and_gateway_capacity() -> None:
    source = pd.read_csv(
        INPUT / "rea_interstate_hourly_flows_FY2025_26.csv", parse_dates=["snapshot"]
    )
    audit = pd.read_csv(MODEL / "interstate_flow_audit.csv", parse_dates=["snapshot"])
    profiles = pd.read_csv(MODEL / "loads-p_set.csv", index_col=0)

    unresolved_source = source.loc[source["measure"].eq("unresolved_total")].pivot_table(
        index="snapshot", columns="direction", values="flow_MW", aggfunc="sum"
    )
    unresolved_audit = audit.loc[audit["measure"].eq("unresolved_distributed")].pivot_table(
        index="snapshot", columns="direction", values="flow_MW", aggfunc="sum"
    )
    assert np.allclose(unresolved_source, unresolved_audit)

    combined = audit.groupby(["snapshot", "direction", "gateway"], as_index=False).agg(
        flow_MW=("modeled_flow_MW", "sum"), capacity_MW=("gateway_capacity_MW", "first")
    )
    assert (combined["flow_MW"] <= combined["capacity_MW"] + 1e-6).all()

    modeled_loads = audit.loc[
        audit["included_in_model"], ["load_name", "direction"]
    ].drop_duplicates()
    for row in modeled_loads.itertuples(index=False):
        expected_sign = -1.0 if row.direction == "import" else 1.0
        nonzero = profiles[row.load_name].loc[profiles[row.load_name].ne(0.0)]
        assert (np.sign(nonzero) == expected_sign).all()

    assert not any(
        column.startswith("interstate_unresolved_distributed_import_")
        for column in profiles.columns
    )

    assert not (MODEL / "links-p_min_pu.csv").exists()
    assert not (MODEL / "links-p_max_pu.csv").exists()


def test_verified_rea_plant_exports_are_reserved_without_changing_flow_totals() -> None:
    source_audit = pd.read_csv(
        INPUT / "rea_plant_export_mapping_audit_FY2025_26.csv"
    )
    source_totals = source_audit.groupby("reservation_status")["energy_GWh"].sum()
    assert np.isclose(source_totals["mapped_to_model_generator"], 15_496.913395)
    assert np.isclose(source_totals["unmatched_model_generator"], 7.785)
    assert np.isclose(source_totals.sum(), EXPECTED_GWH[("export", "resolved")])

    audit = pd.read_csv(MODEL / "rea_plant_export_reservation_audit.csv")
    assert len(audit) == 15
    assert set(audit["status"]) == {"fixed_from_verified_rea_schedule"}
    assert np.isclose(audit["scheduled_export_GWh"].sum(), 15_496.913395)
    assert (audit["reserved_capacity_mw"] <= audit["physical_capacity_mw"]).all()
    assert (audit["remaining_tn_dispatchable_capacity_mw"] >= 0.0).all()

    generators = pd.read_csv(MODEL / "generators.csv").set_index("name")
    minimum = pd.read_csv(MODEL / "generators-p_min_pu.csv", index_col=0)
    maximum = pd.read_csv(MODEL / "generators-p_max_pu.csv", index_col=0)
    reservation_names = audit["reservation_generator"]
    assert set(reservation_names).issubset(generators.index)
    assert np.allclose(minimum[reservation_names], maximum[reservation_names])
    scheduled_gwh = minimum[reservation_names].mul(
        generators.loc[reservation_names, "p_nom"].to_numpy(), axis=1
    ).sum().sum() / 1_000.0
    assert np.isclose(scheduled_gwh, 15_496.913395)

    legacy = pd.read_csv(MODEL / "tn_export_capacity_allocations.csv")
    assert set(legacy["application_status"]) == {
        "reference_only_superseded_by_verified_rea_plant_schedules"
    }
