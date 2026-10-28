"""Export an accessible resolved-only Tamil Nadu interstate transaction dataset.

The approved deduplicated ledger supplies ordinary imports and exports. Verified
Tamil Nadu allocations to Puducherry are added from the excluded ledger as an
explicit user-requested exception; no other excluded or unresolved rows enter.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


TN = "Tamil Nadu"
PUDUCHERRY = "Puducherry"
UNKNOWN_STATES = {"", "Unknown"}
EXPECTED_GWH = {
    "resolved_import": 27_765.9794200001,
    "resolved_export_external": 15_504.698395,
    "verified_puducherry_exception": 1_462.72162,
    "resolved_tn_export_register": 765.02881,
}

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

TRANSACTION_COLUMNS = [
    "canonical_id", "date", "time_block", "month", "financial_year",
    "direction", "raw_schedule_MW", "energy_GWh", "signed_GWh", "channel",
    "generator_id", "generator_name", "plant_location_state", "plant_district",
    "tn_BA", "seller_entity", "contract_seller_state", "trader", "buyer_entity",
    "beneficiary_state", "contract_application_id", "source_entity",
    "counterparty_entity", "schedule_basis", "geographical_class",
    "source_archive", "source_path", "source_sha256", "source_column",
    "raw_record_key", "dedup_key", "overlap_parent", "evidence", "confidence",
    "exception_flag", "source_family", "included_in_approved", "exclusion_reason",
]


def select_resolved_transactions(
    approved_ledger: pd.DataFrame, excluded_ledger: pd.DataFrame
) -> pd.DataFrame:
    """Select verified TN interstate rows and the explicit Puducherry exception."""
    imports, exports, register_exports = _select_approved(approved_ledger)
    puducherry = _select_puducherry(excluded_ledger)
    return _decorate(pd.concat([imports, exports, register_exports, puducherry], ignore_index=True))


def _select_approved(approved_ledger: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    approved = approved_ledger.loc[
        approved_ledger["included_in_approved"].astype(bool)
        & approved_ledger["direction"].isin(["import", "export"])
    ].copy()
    verified = approved.loc[approved["confidence"].eq("verified")]

    imports = verified.loc[
        verified["direction"].eq("import")
        & verified["beneficiary_state"].eq(TN)
        & ~verified["plant_location_state"].isin({TN, *UNKNOWN_STATES})
    ].copy()
    imports["inclusion_basis"] = "resolved_import"

    exports = verified.loc[
        verified["direction"].eq("export")
        & verified["plant_location_state"].eq(TN)
        & ~verified["beneficiary_state"].isin({TN, PUDUCHERRY, *UNKNOWN_STATES})
    ].copy()
    exports["inclusion_basis"] = "resolved_export_external"
    register_exports = approved.loc[
        approved["direction"].eq("export")
        & approved["confidence"].eq("probable_not_verified")
        & approved["source_family"].eq("GNA_TGNA_STATE_VIEW")
        & approved["channel"].isin(["GNA", "TGNA"])
        & approved["source_entity"].astype(str).str.lower().eq("tamilnadu")
    ].copy()
    register_exports["inclusion_basis"] = "resolved_tn_export_register"
    unknown = register_exports["beneficiary_state"].isin(UNKNOWN_STATES)
    buyer = (register_exports["counterparty_entity"].fillna("").astype(str) + " " + register_exports["buyer_entity"].fillna("").astype(str)).str.lower()
    rules = {
        "greenko|kurnool": "Andhra Pradesh",
        "sks_raigarh|sks raigarh": "Chhattisgarh",
        "amnsil": "Gujarat",
        "dvc": "DVC Territory (Jharkhand/West Bengal)",
    }
    for pattern, state in rules.items():
        register_exports.loc[unknown & buyer.str.contains(pattern, regex=True), "beneficiary_state"] = state
    return imports, exports, register_exports


def _select_puducherry(excluded_ledger: pd.DataFrame) -> pd.DataFrame:
    puducherry = excluded_ledger.loc[
        excluded_ledger["direction"].eq("export")
        & excluded_ledger["plant_location_state"].eq(TN)
        & excluded_ledger["beneficiary_state"].eq(PUDUCHERRY)
        & excluded_ledger["confidence"].eq("verified")
        & excluded_ledger["exclusion_reason"].eq(
            "PUDUCHERRY_ENCLAVE_BOUNDARY_UNRESOLVED"
        )
    ].copy()
    puducherry["inclusion_basis"] = "verified_puducherry_exception"
    return puducherry


def _decorate(selected: pd.DataFrame) -> pd.DataFrame:
    selected["counterparty_state"] = np.where(
        selected["direction"].eq("import"),
        selected["plant_location_state"],
        selected["beneficiary_state"],
    )
    selected["external_gateway"] = selected["counterparty_state"].map(
        STATE_TO_GATEWAY
    )
    selected.loc[
        selected["counterparty_state"].eq(PUDUCHERRY), "external_gateway"
    ] = PUDUCHERRY
    selected.loc[selected["counterparty_state"].eq("DVC Territory (Jharkhand/West Bengal)"), "external_gateway"] = "UNALLOCATED_EXTERNAL"
    selected["tn_BA"] = selected["tn_BA"].fillna("").replace("", "UNALLOCATED_TN")
    selected["dataset_status"] = "verified_resolved"
    selected.loc[
        selected["inclusion_basis"].eq("verified_puducherry_exception"),
        "dataset_status",
    ] = "verified_puducherry_exception"
    selected.loc[selected["inclusion_basis"].eq("resolved_tn_export_register"), "dataset_status"] = "resolved_flow_plant_unattributed"
    return selected


def validate(selected: pd.DataFrame) -> dict[str, object]:
    if selected.empty:
        raise ValueError("Resolved transaction selection is empty")
    if selected["dedup_key"].duplicated().any():
        raise AssertionError("Resolved transaction dataset contains duplicate dedup_key values")
    promoted = selected["inclusion_basis"].eq("resolved_tn_export_register")
    if not selected.loc[~promoted, "confidence"].eq("verified").all():
        raise AssertionError("Non-verified rows entered without an explicit resolution rule")
    if selected["external_gateway"].isna().any():
        states = sorted(selected.loc[selected["external_gateway"].isna(), "counterparty_state"].unique())
        raise AssertionError(f"Resolved counterparties lack gateway mappings: {states}")
    ordinary = ~selected["inclusion_basis"].eq("verified_puducherry_exception")
    if not selected.loc[ordinary, "included_in_approved"].astype(bool).all():
        raise AssertionError("Ordinary resolved rows must come from the approved ledger")
    puducherry = ~ordinary
    if not (
        selected.loc[puducherry, "beneficiary_state"].eq(PUDUCHERRY).all()
        and selected.loc[puducherry, "exclusion_reason"].eq(
            "PUDUCHERRY_ENCLAVE_BOUNDARY_UNRESOLVED"
        ).all()
    ):
        raise AssertionError("The excluded-ledger exception must contain only Puducherry")

    totals = selected.groupby("inclusion_basis")["energy_GWh"].sum()
    for category, expected in EXPECTED_GWH.items():
        actual = float(totals.get(category, 0.0))
        if not np.isclose(actual, expected, atol=1e-6):
            raise AssertionError(
                f"{category} changed: expected {expected:.9f} GWh, got {actual:.9f} GWh"
            )
    expected_sign = selected["direction"].map({"import": 1.0, "export": -1.0})
    if not np.allclose(selected["signed_GWh"], selected["energy_GWh"] * expected_sign):
        raise AssertionError("Signed energy does not match the import/export convention")

    return {
        "status": "PASS",
        "transaction_rows": int(len(selected)),
        "duplicate_dedup_keys": 0,
        "resolved_import_GWh": float(totals["resolved_import"]),
        "resolved_export_external_GWh": float(totals["resolved_export_external"]),
        "verified_puducherry_exception_GWh": float(
            totals["verified_puducherry_exception"]
        ),
        "total_export_including_puducherry_GWh": float(
            totals["resolved_export_external"]
            + totals["verified_puducherry_exception"]
        ),
        "unresolved_rows_included_without_resolution_rule": 0,
    }


def hourly_summary(selected: pd.DataFrame) -> pd.DataFrame:
    blocks = pd.to_numeric(selected["time_block"], errors="raise").astype(int)
    if not blocks.between(1, 96).all():
        raise ValueError("REA time blocks must be between 1 and 96")
    frame = selected.copy()
    frame["snapshot"] = pd.to_datetime(frame["date"], errors="raise") + pd.to_timedelta(
        (blocks - 1) // 4, unit="h"
    )
    grouping = [
        "snapshot", "direction", "inclusion_basis", "dataset_status",
        "counterparty_state", "external_gateway", "tn_BA", "channel",
    ]
    hourly = frame.groupby(grouping, as_index=False, dropna=False).agg(
        flow_MW=("energy_GWh", lambda values: values.sum() * 1_000.0),
        energy_MWh=("energy_GWh", lambda values: values.sum() * 1_000.0),
        transaction_rows=("energy_GWh", "size"),
    )
    return hourly.sort_values(grouping, kind="stable").reset_index(drop=True)


def annual_summary(selected: pd.DataFrame) -> pd.DataFrame:
    grouping = [
        "direction", "inclusion_basis", "dataset_status", "counterparty_state",
        "external_gateway", "tn_BA", "channel",
    ]
    return (
        selected.groupby(grouping, as_index=False, dropna=False)
        .agg(energy_GWh=("energy_GWh", "sum"), transaction_rows=("energy_GWh", "size"))
        .sort_values(grouping, kind="stable")
        .reset_index(drop=True)
    )


def export(approved_path: Path, excluded_path: Path, output_dir: Path) -> None:
    ordered = [
        "canonical_id", "date", "time_block", "month", "financial_year",
        "direction", "inclusion_basis", "dataset_status", "counterparty_state",
        "external_gateway", "raw_schedule_MW", "energy_GWh", "signed_GWh",
        "channel", "generator_id", "generator_name", "plant_location_state",
        "plant_district", "tn_BA", "seller_entity", "contract_seller_state",
        "trader", "buyer_entity", "beneficiary_state", "contract_application_id",
        "source_entity", "counterparty_entity", "schedule_basis",
        "geographical_class", "source_archive", "source_path", "source_sha256",
        "source_column", "raw_record_key", "dedup_key", "overlap_parent",
        "evidence", "confidence", "exception_flag", "source_family",
        "included_in_approved", "exclusion_reason",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    transaction_path = output_dir / "TN_resolved_interstate_transactions_FY2025_26.parquet"
    writer: pq.ParquetWriter | None = None
    hourly_parts: list[pd.DataFrame] = []
    annual_parts: list[pd.DataFrame] = []
    totals: dict[str, float] = {key: 0.0 for key in EXPECTED_GWH}
    transaction_rows = 0

    def consume(path: Path, source_kind: str) -> None:
        nonlocal writer, transaction_rows
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=75_000, columns=TRANSACTION_COLUMNS):
            frame = batch.to_pandas()
            if source_kind == "approved":
                imports, exports, register_exports = _select_approved(frame)
                selected = _decorate(pd.concat([imports, exports, register_exports], ignore_index=True))
            else:
                selected = _decorate(_select_puducherry(frame))
            if selected.empty:
                continue
            if selected["dedup_key"].duplicated().any():
                raise AssertionError("A source batch contains duplicate dedup_key values")
            promoted = selected["inclusion_basis"].eq("resolved_tn_export_register")
            if not selected.loc[~promoted, "confidence"].eq("verified").all():
                raise AssertionError("Non-verified rows entered without an explicit resolution rule")
            if selected["external_gateway"].isna().any():
                states = sorted(
                    selected.loc[selected["external_gateway"].isna(), "counterparty_state"].unique()
                )
                raise AssertionError(f"Resolved counterparties lack gateway mappings: {states}")
            expected_sign = selected["direction"].map({"import": 1.0, "export": -1.0})
            if not np.allclose(
                selected["signed_GWh"], selected["energy_GWh"] * expected_sign
            ):
                raise AssertionError("Signed energy does not match direction")
            for category, value in selected.groupby("inclusion_basis")["energy_GWh"].sum().items():
                totals[str(category)] += float(value)
            transaction_rows += len(selected)
            hourly_parts.append(hourly_summary(selected))
            annual_parts.append(annual_summary(selected))
            table = pa.Table.from_pandas(selected.loc[:, ordered], preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(transaction_path, table.schema, compression="zstd")
            writer.write_table(table)

    try:
        consume(approved_path, "approved")
        consume(excluded_path, "excluded")
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise ValueError("Resolved transaction selection is empty")
    dedup_keys = pd.read_parquet(transaction_path, columns=["dedup_key"])["dedup_key"]
    duplicate_dedup_keys = int(dedup_keys.duplicated().sum())
    if duplicate_dedup_keys:
        raise AssertionError(
            f"Resolved transaction dataset contains {duplicate_dedup_keys} duplicate dedup_key values"
        )

    for category, expected in EXPECTED_GWH.items():
        if not np.isclose(totals[category], expected, atol=1e-6):
            raise AssertionError(
                f"{category} changed: expected {expected:.9f}, got {totals[category]:.9f} GWh"
            )
    hourly = pd.concat(hourly_parts, ignore_index=True)
    hourly_grouping = [
        "snapshot", "direction", "inclusion_basis", "dataset_status",
        "counterparty_state", "external_gateway", "tn_BA", "channel",
    ]
    hourly = hourly.groupby(hourly_grouping, as_index=False, dropna=False).agg(
        flow_MW=("flow_MW", "sum"), energy_MWh=("energy_MWh", "sum"),
        transaction_rows=("transaction_rows", "sum"),
    ).sort_values(hourly_grouping, kind="stable")
    annual = pd.concat(annual_parts, ignore_index=True)
    annual_grouping = [
        "direction", "inclusion_basis", "dataset_status", "counterparty_state",
        "external_gateway", "tn_BA", "channel",
    ]
    annual = annual.groupby(annual_grouping, as_index=False, dropna=False).agg(
        energy_GWh=("energy_GWh", "sum"), transaction_rows=("transaction_rows", "sum")
    ).sort_values(annual_grouping, kind="stable")
    hourly_totals = hourly.groupby("inclusion_basis")["energy_MWh"].sum() / 1_000.0
    annual_totals = annual.groupby("inclusion_basis")["energy_GWh"].sum()
    for category, expected in totals.items():
        if not np.isclose(float(hourly_totals[category]), expected, atol=1e-6):
            raise AssertionError(f"Hourly output does not reconcile for {category}")
        if not np.isclose(float(annual_totals[category]), expected, atol=1e-6):
            raise AssertionError(f"Annual output does not reconcile for {category}")
    report = {
        "status": "PASS",
        "transaction_rows": int(transaction_rows),
        "duplicate_dedup_keys": duplicate_dedup_keys,
        "resolved_import_GWh": totals["resolved_import"],
        "resolved_export_external_GWh": totals["resolved_export_external"],
        "verified_puducherry_exception_GWh": totals["verified_puducherry_exception"],
        "newly_resolved_tn_export_register_GWh": totals["resolved_tn_export_register"],
        "total_export_including_puducherry_GWh": (
            totals["resolved_export_external"] + totals["verified_puducherry_exception"]
            + totals["resolved_tn_export_register"]
        ),
        "remaining_unresolved_export_GWh": 7_235.026995,
        "unresolved_rows_included_without_resolution_rule": 0,
    }
    hourly.to_csv(
        output_dir / "TN_resolved_interstate_hourly_FY2025_26.csv", index=False
    )
    annual.to_csv(
        output_dir / "TN_resolved_interstate_summary_FY2025_26.csv", index=False
    )
    pd.DataFrame([
        {"category": "GNA/TGNA TN export register", "decision": "PROMOTED",
         "energy_GWh": totals["resolved_tn_export_register"],
         "reason": "Final tneb_exp.csv identifies TamilNadu as source and a named external buyer"},
        {"category": "DAM/RTM pooled market position", "decision": "REMAINS_UNRESOLVED",
         "energy_GWh": 7_235.026995,
         "reason": "Power-exchange position supplies no buyer or physical destination"},
    ]).to_csv(output_dir / "TN_unresolved_export_resolution_audit_FY2025_26.csv", index=False)
    (output_dir / "TN_resolved_interstate_validation_FY2025_26.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-ledger", type=Path, required=True)
    parser.add_argument("--excluded-ledger", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("outputs/rea_fy2025_26")
    )
    args = parser.parse_args()
    export(args.approved_ledger, args.excluded_ledger, args.output_dir)


if __name__ == "__main__":
    main()
