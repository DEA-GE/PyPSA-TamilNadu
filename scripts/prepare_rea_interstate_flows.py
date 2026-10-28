"""Prepare hourly FY2025-26 interstate-flow inputs from the audited REA ledger."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


FY_START = pd.Timestamp("2025-04-01 00:00:00")
FY_END = pd.Timestamp("2026-03-31 23:00:00")
SNAPSHOTS = pd.date_range(FY_START, FY_END, freq="h")
GATEWAYS = ("Andhra Pradesh", "Karnataka", "Kerala", "Western Region")

STATE_TO_GATEWAY = {
    "Andhra Pradesh": "Andhra Pradesh",
    "Telangana": "Andhra Pradesh",
    "Karnataka": "Karnataka",
    "Odisha": "Karnataka",
    "Bihar": "Karnataka",
    "West Bengal": "Karnataka",
    "Kerala": "Kerala",
    "Chhattisgarh": "Western Region",
    "Maharashtra": "Western Region",
    "Madhya Pradesh": "Western Region",
    "Gujarat": "Western Region",
    "Rajasthan": "Western Region",
    "Haryana": "Western Region",
    "Punjab": "Western Region",
    "Goa": "Western Region",
    "Uttarakhand": "Western Region",
    "Uttar Pradesh": "Western Region",
    "Delhi": "Western Region",
    "Jharkhand": "Western Region",
    "Assam": "Western Region",
}

EXPECTED_GWH = {
    ("import", "resolved"): 27_765.9794200001,
    ("import", "unresolved_total"): 20_826.9511525,
    ("export", "resolved"): 15_504.698395,
    ("export", "unresolved_total"): 8_000.055805,
}


def _snapshot(frame: pd.DataFrame) -> pd.Series:
    blocks = pd.to_numeric(frame["time_block"], errors="raise").astype(int)
    if not blocks.between(1, 96).all():
        raise ValueError("REA time blocks must be between 1 and 96")
    return pd.to_datetime(frame["date"], errors="raise") + pd.to_timedelta(
        (blocks - 1) // 4, unit="h"
    )


def prepare(ledger_path: Path, output_dir: Path) -> None:
    columns = [
        "date", "time_block", "direction", "energy_GWh", "confidence",
        "plant_location_state", "beneficiary_state", "channel",
        "generator_id", "generator_name", "included_in_approved",
    ]
    ledger = pd.read_parquet(ledger_path, columns=columns)
    ledger = ledger.loc[
        ledger["included_in_approved"].astype(bool)
        & ledger["direction"].isin(["import", "export"])
    ].copy()
    ledger["energy_GWh"] = pd.to_numeric(ledger["energy_GWh"], errors="raise")
    if ledger["energy_GWh"].lt(0).any():
        raise ValueError("Approved REA energy must be non-negative")
    ledger["snapshot"] = _snapshot(ledger)
    if not ledger["snapshot"].between(FY_START, FY_END).all():
        raise ValueError("REA ledger contains snapshots outside FY2025-26")

    ledger["measure"] = np.where(
        ledger["confidence"].eq("verified"), "resolved", "unresolved_total"
    )
    ledger["counterparty_state"] = np.where(
        ledger["direction"].eq("import"),
        ledger["plant_location_state"],
        ledger["beneficiary_state"],
    )
    resolved = ledger["measure"].eq("resolved")
    ledger["gateway"] = "UNALLOCATED"
    ledger.loc[resolved, "gateway"] = ledger.loc[resolved, "counterparty_state"].map(
        STATE_TO_GATEWAY
    ).fillna("UNMAPPED")
    unmapped = sorted(ledger.loc[resolved & ledger["gateway"].eq("UNMAPPED"), "counterparty_state"].unique())
    if unmapped:
        raise ValueError(f"Verified counterpart states lack gateway mappings: {unmapped}")

    annual = ledger.groupby(["direction", "measure"], observed=True)["energy_GWh"].sum()
    for key, expected in EXPECTED_GWH.items():
        actual = float(annual.get(key, 0.0))
        if not np.isclose(actual, expected, atol=1e-6):
            raise AssertionError(f"{key} energy changed: expected {expected}, got {actual}")

    resolved_hourly = (
        ledger.loc[resolved]
        .groupby(["snapshot", "direction", "gateway"], observed=True)["energy_GWh"]
        .sum()
        .rename("energy_GWh")
        .reset_index()
    )
    resolved_hourly["measure"] = "resolved"
    unresolved_hourly = (
        ledger.loc[~resolved]
        .groupby(["snapshot", "direction"], observed=True)["energy_GWh"]
        .sum()
        .rename("energy_GWh")
        .reset_index()
    )
    unresolved_hourly["gateway"] = "UNALLOCATED"
    unresolved_hourly["measure"] = "unresolved_total"

    rows = []
    for direction in ("import", "export"):
        for gateway in GATEWAYS:
            series = (
                resolved_hourly.loc[
                    resolved_hourly["direction"].eq(direction)
                    & resolved_hourly["gateway"].eq(gateway)
                ].set_index("snapshot")["energy_GWh"].reindex(SNAPSHOTS, fill_value=0.0)
            )
            rows.append(pd.DataFrame({
                "snapshot": SNAPSHOTS, "direction": direction,
                "measure": "resolved", "gateway": gateway,
                "flow_MW": series.to_numpy() * 1_000.0,
                "energy_MWh": series.to_numpy() * 1_000.0,
            }))
        series = (
            unresolved_hourly.loc[unresolved_hourly["direction"].eq(direction)]
            .set_index("snapshot")["energy_GWh"].reindex(SNAPSHOTS, fill_value=0.0)
        )
        rows.append(pd.DataFrame({
            "snapshot": SNAPSHOTS, "direction": direction,
            "measure": "unresolved_total", "gateway": "UNALLOCATED",
            "flow_MW": series.to_numpy() * 1_000.0,
            "energy_MWh": series.to_numpy() * 1_000.0,
        }))
    hourly = pd.concat(rows, ignore_index=True)

    share_basis = (
        ledger.loc[resolved]
        .groupby(["direction", "gateway"], observed=True)["energy_GWh"]
        .sum().rename("resolved_GWh").reset_index()
    )
    full_index = pd.MultiIndex.from_product(
        [("import", "export"), GATEWAYS], names=["direction", "gateway"]
    )
    shares = share_basis.set_index(["direction", "gateway"]).reindex(
        full_index, fill_value=0.0
    ).reset_index()
    totals = shares.groupby("direction")["resolved_GWh"].transform("sum")
    shares["allocation_share"] = shares["resolved_GWh"] / totals
    shares["routing_basis"] = shares["gateway"].map({
        "Andhra Pradesh": "Andhra Pradesh/Telangana southern gateway",
        "Karnataka": "Karnataka direct plus Talcher-Kolar eastern proxy",
        "Kerala": "Kerala direct gateway",
        "Western Region": "Raigarh-Pugalur western/northern gateway proxy",
    })
    if not np.allclose(shares.groupby("direction")["allocation_share"].sum(), 1.0):
        raise AssertionError("Direction-specific unresolved allocation shares must sum to one")

    source_audit = (
        ledger.groupby(
            ["direction", "measure", "counterparty_state", "gateway", "confidence", "channel"],
            observed=True, dropna=False,
        ).agg(
            energy_GWh=("energy_GWh", "sum"),
            transaction_rows=("energy_GWh", "size"),
        ).reset_index()
    )

    mapping_path = output_dir / "rea_plant_export_mapping.csv"
    if not mapping_path.exists():
        raise FileNotFoundError(f"Missing REA plant mapping: {mapping_path}")
    plant_mapping = pd.read_csv(mapping_path)
    required_mapping = {"rea_generator_name", "component_ids", "mapping_status"}
    if missing := sorted(required_mapping - set(plant_mapping.columns)):
        raise ValueError(f"REA plant mapping is missing columns: {missing}")
    if plant_mapping["rea_generator_name"].duplicated().any():
        raise ValueError("REA plant mapping names must be unique")

    plant_exports = ledger.loc[
        ledger["direction"].eq("export")
        & ledger["measure"].eq("resolved")
        & ledger["plant_location_state"].eq("Tamil Nadu")
    ].copy()
    mapped_names = set(plant_mapping["rea_generator_name"])
    plant_exports["reservation_status"] = np.where(
        plant_exports["generator_name"].isin(mapped_names),
        "mapped_to_model_generator",
        "unmatched_model_generator",
    )
    mapped_exports = plant_exports.loc[
        plant_exports["reservation_status"].eq("mapped_to_model_generator")
    ].merge(
        plant_mapping,
        left_on="generator_name",
        right_on="rea_generator_name",
        how="left",
        validate="many_to_one",
    )
    plant_hourly = (
        mapped_exports.groupby(
            ["snapshot", "generator_name"],
            observed=True,
        )["energy_GWh"].sum().reset_index()
    )
    plant_hourly["flow_MW"] = plant_hourly["energy_GWh"] * 1_000.0
    plant_hourly["energy_MWh"] = plant_hourly["energy_GWh"] * 1_000.0
    plant_hourly = plant_hourly.drop(columns="energy_GWh")
    plant_audit = (
        plant_exports.groupby(
            ["generator_name", "beneficiary_state", "reservation_status"],
            observed=True,
        ).agg(
            energy_GWh=("energy_GWh", "sum"),
            transaction_rows=("energy_GWh", "size"),
        ).reset_index()
    ).merge(
        plant_mapping,
        left_on="generator_name",
        right_on="rea_generator_name",
        how="left",
        validate="many_to_one",
    )
    mapped_gwh = float(plant_audit.loc[
        plant_audit["reservation_status"].eq("mapped_to_model_generator"), "energy_GWh"
    ].sum())
    if not np.isclose(float(plant_audit["energy_GWh"].sum()), EXPECTED_GWH[("export", "resolved")]):
        raise AssertionError("Plant-level REA exports do not reconcile to resolved exports")
    if mapped_gwh <= 0.99 * EXPECTED_GWH[("export", "resolved")]:
        raise AssertionError("Less than 99% of resolved REA exports map to model generators")
    output_dir.mkdir(parents=True, exist_ok=True)
    hourly.to_csv(output_dir / "rea_interstate_hourly_flows_FY2025_26.csv", index=False)
    shares.to_csv(output_dir / "rea_interstate_gateway_shares_FY2025_26.csv", index=False)
    source_audit.to_csv(output_dir / "rea_interstate_source_audit_FY2025_26.csv", index=False)
    plant_hourly.to_csv(output_dir / "rea_plant_export_hourly_FY2025_26.csv", index=False)
    plant_audit.to_csv(output_dir / "rea_plant_export_mapping_audit_FY2025_26.csv", index=False)
    print(f"Prepared {len(hourly):,} hourly flow rows in {output_dir.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/model_inputs"))
    args = parser.parse_args()
    prepare(args.ledger, args.output_dir)


if __name__ == "__main__":
    main()
