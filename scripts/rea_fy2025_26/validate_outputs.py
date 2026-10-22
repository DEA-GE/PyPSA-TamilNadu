"""Validate a completed FY2025-26 REA output directory and write JSON evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .mappings import MONTH_ARCHIVES


def validate(output: Path) -> dict[str, object]:
    monthly = pd.read_csv(output / "TN_interstate_monthly.csv", keep_default_na=False)
    annual = pd.read_csv(output / "TN_interstate_annual.csv", keep_default_na=False)
    coverage = pd.read_csv(output / "coverage_report.csv", keep_default_na=False)
    validation = pd.read_csv(output / "validation_results.csv", keep_default_na=False)
    unresolved = pd.read_csv(output / "unresolved_cases.csv", keep_default_na=False)
    duplicates = pd.read_csv(output / "duplicate_matches.csv", keep_default_na=False)

    for frame in (monthly, annual):
        frame["energy_GWh"] = pd.to_numeric(frame["energy_GWh"], errors="coerce")
        frame["signed_GWh"] = pd.to_numeric(frame["signed_GWh"], errors="coerce")

    parquet_rows = {
        name: pq.ParquetFile(output / name).metadata.num_rows
        for name in ("canonical_schedules.parquet", "deduplicated_ledger.parquet", "excluded_ledger_rows.parquet")
    }
    expected_months = {month for month, _ in MONTH_ARCHIVES}
    actual_months = set(monthly["month"].unique())
    monthly_direction = monthly.groupby("direction", dropna=False)["energy_GWh"].sum().to_dict()
    annual_direction = annual.groupby("direction", dropna=False)["energy_GWh"].sum().to_dict()
    march = validation.loc[validation["test"].str.startswith("March_regression:")]
    source_errors = unresolved.loc[unresolved["category"].eq("source_parse_error")]

    checks = {
        "all_12_months_present": actual_months == expected_months,
        "coverage_has_72_critical_rows": len(coverage) == 72,
        "all_critical_coverage_passes": bool(coverage["status"].eq("PASS").all()),
        "no_validation_failures": not validation["status"].eq("FAIL").any(),
        "all_12_march_regressions_pass": len(march) == 12 and bool(march["status"].eq("PASS").all()),
        "no_source_parse_errors": source_errors.empty,
        "canonical_rows_partition": parquet_rows["canonical_schedules.parquet"]
            == parquet_rows["deduplicated_ledger.parquet"] + parquet_rows["excluded_ledger_rows.parquet"],
        "monthly_annual_import_conservation": abs(monthly_direction.get("import", 0) - annual_direction.get("import", 0)) < 1e-8,
        "monthly_annual_export_conservation": abs(monthly_direction.get("export", 0) - annual_direction.get("export", 0)) < 1e-8,
        "import_sign_positive": bool(monthly.loc[monthly.direction.eq("import"), "signed_GWh"].ge(0).all()),
        "export_sign_negative": bool(monthly.loc[monthly.direction.eq("export"), "signed_GWh"].le(0).all()),
        "verified_tn_exports_have_ba": bool(
            monthly.loc[
                monthly.direction.eq("export")
                & monthly.plant_location_state.eq("Tamil Nadu")
                & monthly.confidence.eq("verified"),
                "tn_BA",
            ].fillna("").ne("").all()
        ),
    }
    unresolved_energy = pd.to_numeric(unresolved["energy_GWh"], errors="coerce").fillna(0)
    unresolved_summary = (
        unresolved.assign(energy_numeric=unresolved_energy)
        .groupby("category", dropna=False)
        .agg(rows=("category", "size"), energy_GWh=("energy_numeric", "sum"))
        .reset_index()
        .to_dict("records")
    )
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "parquet_rows": parquet_rows,
        "approved_energy_GWh": {key: float(value) for key, value in monthly_direction.items()},
        "duplicate_report_rows": int(len(duplicates)),
        "coverage_status_counts": coverage["status"].value_counts().to_dict(),
        "validation_status_counts": validation["status"].value_counts().to_dict(),
        "unresolved_by_category": unresolved_summary,
    }
    (output / "automated_validation_summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/rea_fy2025_26"))
    args = parser.parse_args()
    result = validate(args.output_dir)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
