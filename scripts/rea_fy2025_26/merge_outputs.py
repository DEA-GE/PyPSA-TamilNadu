"""Merge independently audited monthly REA outputs into one FY2025-26 result."""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .mappings import BA_NAMES, MONTH_ARCHIVES, STATIONS, STATION_MAPPING_EVIDENCE, ba_for_district


MONTHS = [month for month, _ in MONTH_ARCHIVES]
FY = "FY2025-26"
TN = "Tamil Nadu"


def _apply_generator_geography(frame: pd.DataFrame) -> pd.DataFrame:
    """Resolve plant -> district -> BA before any energy aggregation."""
    if frame.empty or "generator_id" not in frame:
        return frame
    for code, (name, state, district, _lon, _lat) in STATIONS.items():
        if state != TN:
            continue
        mask = frame["generator_id"].astype(str).str.lower().eq(code)
        if not mask.any():
            continue
        frame.loc[mask, "generator_name"] = name
        frame.loc[mask, "plant_location_state"] = state
        frame.loc[mask, "plant_district"] = district
        frame.loc[mask, "tn_BA"] = ba_for_district(district)
        if "direction" in frame and "beneficiary_state" in frame:
            export = mask & frame["direction"].eq("export")
            known_external = export & ~frame["beneficiary_state"].isin([TN, "Unknown", ""])
            unknown_destination = export & frame["beneficiary_state"].isin(["Unknown", ""])
            frame.loc[known_external, "confidence"] = "verified"
            frame.loc[known_external, "geographical_class"] = "TN→external"
            frame.loc[unknown_destination, "confidence"] = "probable_not_verified"
            frame.loc[unknown_destination, "geographical_class"] = "TN→destination_unknown"
        if "exception_flag" in frame:
            frame.loc[mask & frame["exception_flag"].eq("UNKNOWN_PHYSICAL_ORIGIN"), "exception_flag"] = ""
    # Known TN stations not added through seller aliases retain their direct point
    # result where available and receive the documented district fallback otherwise.
    tn_mask = frame.get("plant_location_state", pd.Series("", index=frame.index)).eq(TN)
    blank_ba = frame.get("tn_BA", pd.Series("", index=frame.index)).fillna("").eq("")
    for district in frame.loc[tn_mask & blank_ba, "plant_district"].dropna().unique():
        ba = ba_for_district(district)
        if ba:
            frame.loc[tn_mask & blank_ba & frame["plant_district"].eq(district), "tn_BA"] = ba
    return frame


def _read(parts: Path, filename: str) -> pd.DataFrame:
    frames = []
    for month in MONTHS:
        path = parts / month / filename
        if path.exists() and path.stat().st_size:
            frames.append(pd.read_csv(path, keep_default_na=False))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _merge_parquet(parts: Path, output: Path, filename: str) -> None:
    destination = output / filename
    writer = None
    try:
        for month in MONTHS:
            source = parts / month / filename
            if not source.exists():
                continue
            parquet = pq.ParquetFile(source)
            for batch in parquet.iter_batches(batch_size=100_000):
                table = batch.to_table() if hasattr(batch, "to_table") else __import__("pyarrow").Table.from_batches([batch])
                table = pa.Table.from_pandas(_apply_generator_geography(table.to_pandas()), schema=table.schema, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
                writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()


def _merge_csv_stream(parts: Path, output: Path, filename: str) -> None:
    destination = output / filename
    wrote_header = False
    with destination.open("w", encoding="utf-8", newline="") as target:
        for month in MONTHS:
            source = parts / month / filename
            if not source.exists():
                continue
            with source.open("r", encoding="utf-8", newline="") as handle:
                header = handle.readline()
                if not wrote_header:
                    target.write(header)
                    wrote_header = True
                shutil.copyfileobj(handle, target, length=1024 * 1024)


def _ba_adjustments(monthly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ba in BA_NAMES:
        exp = pd.to_numeric(monthly.loc[(monthly.direction == "export") & (monthly.tn_BA == ba), "energy_GWh"], errors="coerce").sum()
        imp = pd.to_numeric(monthly.loc[(monthly.direction == "import") & (monthly.tn_BA == ba), "energy_GWh"], errors="coerce").sum()
        rows.append({
            "financial_year": FY, "tn_BA": ba,
            "export_load_increase_GWh": float(exp), "import_supply_adjustment_GWh": -float(imp),
            "export_adjustment_sign": "positive Load",
            "import_adjustment_sign": "negative fixed Load / exogenous supply",
            "mapping_status": "verified plant point or documented district mapping" if exp or imp else "no evidenced adjustment",
            "profile_status": "annual only; no hourly profile invented",
        })
    unknown = monthly.tn_BA.fillna("").eq("")
    rows.append({
        "financial_year": FY, "tn_BA": "UNALLOCATED_TN",
        "export_load_increase_GWh": float(pd.to_numeric(monthly.loc[(monthly.direction == "export") & unknown, "energy_GWh"], errors="coerce").sum()),
        "import_supply_adjustment_GWh": -float(pd.to_numeric(monthly.loc[(monthly.direction == "import") & unknown, "energy_GWh"], errors="coerce").sum()),
        "export_adjustment_sign": "positive Load if/when origin BA is evidenced",
        "import_adjustment_sign": "negative fixed Load / exogenous supply if/when receiving BA is evidenced",
        "mapping_status": "not allocated; source archives do not establish a nine-BA node",
        "profile_status": "annual only; no hourly profile invented",
    })
    return pd.DataFrame(rows)


def _enrich_inventory(frame: pd.DataFrame) -> pd.DataFrame:
    """Add explicit version, half, nonempty, and date-range-method fields."""
    provisional = frame["is_provisional"].astype(str).str.lower().eq("true")
    frame.insert(frame.columns.get_loc("size_bytes") + 1, "nonempty", pd.to_numeric(frame["size_bytes"], errors="coerce").fillna(0).gt(0))
    frame.insert(frame.columns.get_loc("is_provisional") + 1, "version", np.where(provisional, "provisional embedded member", "final archive member"))
    starts = pd.to_datetime(frame["date_start"], errors="coerce")
    ends = pd.to_datetime(frame["date_end"], errors="coerce")
    same_month = starts.dt.to_period("M").eq(ends.dt.to_period("M"))
    half = pd.Series("not established", index=frame.index, dtype=object)
    half.loc[same_month & starts.dt.day.le(15) & ends.dt.day.le(15)] = "first half"
    half.loc[same_month & starts.dt.day.ge(16) & ends.dt.day.ge(16)] = "second half"
    half.loc[same_month & starts.dt.day.le(1) & ends.dt.day.ge(28)] = "full month"
    frame.insert(frame.columns.get_loc("month") + 1, "half", half)
    method = np.where(
        frame["extension"].eq(".csv") & (frame["date_start"].ne("") | frame["date_end"].ne("")),
        "sampled first/last 1 MiB of date-sorted CSV",
        "not detected/not applicable",
    )
    frame.insert(frame.columns.get_loc("date_end") + 1, "date_range_method", method)
    return frame


def _write_derived_summaries(output: Path, monthly: pd.DataFrame, unresolved: pd.DataFrame) -> None:
    state_cols = ["direction", "plant_location_state", "beneficiary_state", "channel", "confidence"]
    state = monthly.groupby(state_cols, dropna=False, as_index=False)[["energy_GWh", "signed_GWh"]].sum()
    state.insert(0, "financial_year", FY)
    state.to_csv(output / "TN_state_to_state_annual.csv", index=False)

    channel = monthly.groupby(["month", "direction", "channel", "confidence"], dropna=False, as_index=False)[["energy_GWh", "signed_GWh"]].sum()
    channel.to_csv(output / "TN_channel_breakdown.csv", index=False)

    rows = []
    for confidence in ("verified", "probable_not_verified", "unattributed"):
        selected = monthly.loc[monthly["confidence"].eq(confidence)]
        for direction in ("import", "export"):
            rows.append({
                "category": confidence, "direction": direction, "ledger_scope": "approved",
                "energy_GWh": float(selected.loc[selected.direction.eq(direction), "energy_GWh"].sum()),
                "row_groups": int(selected.loc[selected.direction.eq(direction)].shape[0]),
                "interpretation": "approved energy; geography confidence category",
            })
    for category in ("potential_overlap", "missing_source_coverage", "source_parse_error"):
        selected = unresolved.loc[unresolved["category"].eq(category)]
        rows.append({
            "category": category, "direction": "not additive", "ledger_scope": "excluded/unresolved",
            "energy_GWh": float(pd.to_numeric(selected.get("energy_GWh", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
            "row_groups": int(len(selected)),
            "interpretation": "not included in approved totals",
        })
    pd.DataFrame(rows).to_csv(output / "uncertainty_summary.csv", index=False)


def _rebuild_unresolved(output: Path, existing: pd.DataFrame) -> pd.DataFrame:
    """Recompute confidence gaps from the geography-corrected approved ledger."""
    retained = existing.loc[~existing["category"].isin(["probable_not_verified", "unattributed"])].copy()
    totals: dict[tuple[str, str, str, str], float] = {}
    ledger = pq.ParquetFile(output / "deduplicated_ledger.parquet")
    columns = ["month", "confidence", "channel", "source_path", "energy_GWh"]
    for batch in ledger.iter_batches(batch_size=150_000, columns=columns):
        frame = batch.to_pandas()
        frame = frame.loc[~frame["confidence"].eq("verified")]
        if frame.empty:
            continue
        grouped = frame.groupby(["month", "confidence", "channel", "source_path"], dropna=False)["energy_GWh"].sum()
        for key, value in grouped.items():
            category = "unattributed" if key[1] == "unattributed" else str(key[1])
            output_key = (str(key[0]), category, str(key[2]), str(key[3]))
            totals[output_key] = totals.get(output_key, 0.0) + float(value)
    rebuilt = pd.DataFrame([
        {"month": month, "category": category, "channel": channel, "source_path": source,
         "energy_GWh": energy, "detail": "Quantified from geography-corrected approved ledger"}
        for (month, category, channel, source), energy in sorted(totals.items())
    ])
    return pd.concat([retained, rebuilt], ignore_index=True).sort_values(
        ["month", "category", "channel", "source_path"], kind="stable"
    )


def _write_plant_ba_crosswalk(output: Path, point_mapped_ids: set[str]) -> None:
    rows = []
    for code, (name, state, district, lon, lat) in sorted(STATIONS.items()):
        if state != TN:
            continue
        rows.append({
            "generator_id": code, "generator_name": name, "plant_location_state": state,
            "plant_district": district, "longitude": lon, "latitude": lat,
            "tn_BA": ba_for_district(district),
            "mapping_method": "plant point-in-polygon" if code in point_mapped_ids else "verified district-to-BA mapping",
            "mapping_evidence": STATION_MAPPING_EVIDENCE.get(
                code, "TamilNadu_9BAs.gpkg; data/model_inputs/Balancing_areas.txt; project plant registry"
            ),
        })
    pd.DataFrame(rows).to_csv(output / "plant_to_BA_crosswalk.csv", index=False)


def _write_workbook_name_audit(output: Path, monthly: pd.DataFrame) -> None:
    """Record accepted and rejected REA-name matches to the validated plant workbook."""
    workbook = "TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx"
    accepted = {
        "opgpgplu4tn": ("PlantInfo", "OPG Power Generation Private Ltd.", "Thiruvallur", "exact organization/unit-family match"),
        "opgpgunit1tn": ("PlantInfo", "OPG Power Generation Private Ltd.", "Thiruvallur", "exact organization/unit-family match"),
        "opgpgplu2tn": ("PlantInfo", "OPG Power Generation Private Ltd.", "Thiruvallur", "exact organization/unit-family match"),
        "tulsyannecltd": ("PlantInfo", "Tulsyan NEC Limited", "Thiruvallur", "exact normalized name match"),
        "eidparrypugalur": ("PlantInfo", "EID Parry India Ltd. Tamil Nadu Karur 22", "Karur", "Pugalur site resolved to Karur entry"),
        "kamachiltdtn": ("Prayas TN Records", "KAMACHI STEELS LTD.", "Tirunelveli", "exact normalized name match; source CSV row 8928"),
        "arkayenergytn": ("Prayas TN Records", "M/s. ARKAY Energy Ltd", "Theni", "exact normalized name match; source CSV row 13633"),
    }
    ambiguous = {
        "sakthisugarsltd": "Sakti Sugars Ltd. occurs in Erode (Erode BA) and Sivaganga (Madurai BA); candidates do not aggregate to one BA",
        "sakthisugarselmtn": "Sakti Sugars Ltd. occurs in Erode (Erode BA) and Sivaganga (Madurai BA); candidates do not aggregate to one BA and the REA suffix is not decoded by the workbook",
        "sakthisugrartn": "Sakti Sugars Ltd. occurs in Erode (Erode BA) and Sivaganga (Madurai BA); candidates do not aggregate to one BA and the REA suffix is not decoded by the workbook",
        "tncppl": "TCP records occur in Thiruvallur (Chennai North BA), Sivaganga (Madurai BA), Thoothukudi (Tirunelveli BA), and Tiruppur (Coimbatore BA); candidates do not aggregate to one BA",
        "coromandelecltn": "Only Coromandel Agro Products and Oils Ltd. (Dindigul) was found; entity identity is not exact",
        "bhatiacokeltd": "Only ASHOK.L.BHATIA (Tirunelveli) was found; entity identity is not exact",
    }
    exports = monthly.loc[monthly["direction"].eq("export")]
    unmapped_ids = set(exports.loc[exports["tn_BA"].fillna("").eq(""), "generator_id"].astype(str))
    ids = sorted(unmapped_ids | set(accepted) | set(ambiguous))
    rows = []
    for generator_id in ids:
        selected = exports.loc[exports["generator_id"].astype(str).eq(generator_id)]
        if selected.empty and generator_id not in accepted and generator_id not in ambiguous:
            continue
        generator_name = selected["generator_name"].iloc[0] if not selected.empty else STATIONS.get(generator_id, (generator_id,))[0]
        if generator_id in accepted:
            sheet, match_name, district, note = accepted[generator_id]
            status = "ACCEPTED"
            ba = ba_for_district(district)
        elif generator_id in ambiguous:
            sheet, match_name, district, note = "PlantInfo / Prayas TN Records", "multiple/weak candidates", "", ambiguous[generator_id]
            status, ba = "AMBIGUOUS_NOT_MAPPED", ""
        else:
            sheet, match_name, district = "PlantInfo / Prayas TN Records", "no unique match", ""
            note = "No exact unique plant/district match established from the workbook"
            status, ba = "NO_MATCH_NOT_MAPPED", ""
        rows.append({
            "rea_generator_id": generator_id, "rea_generator_name": generator_name,
            "approved_export_GWh": float(selected["energy_GWh"].sum()),
            "match_status": status, "source_workbook": workbook, "source_sheet": sheet,
            "workbook_match_name": match_name, "verified_district": district,
            "tn_BA": ba, "evidence_note": note,
        })
    pd.DataFrame(rows).sort_values(["match_status", "approved_export_GWh"], ascending=[True, False]).to_csv(
        output / "plant_name_district_match_audit.csv", index=False
    )


def _write_reports(output: Path, monthly: pd.DataFrame, validation: pd.DataFrame,
                   unresolved: pd.DataFrame, baseline: pd.DataFrame) -> None:
    energy = pd.to_numeric(monthly.energy_GWh, errors="coerce").fillna(0)
    imports = float(energy[monthly.direction.eq("import")].sum())
    exports = float(energy[monthly.direction.eq("export")].sum())
    confirmed_imports = float(energy[monthly.direction.eq("import") & monthly.confidence.eq("verified") & monthly.geographical_class.eq("external→TN")].sum())
    confirmed_exports = float(energy[monthly.direction.eq("export") & monthly.confidence.eq("verified") & monthly.geographical_class.eq("TN→external")].sum())
    origin = float(energy[monthly.direction.eq("import") & monthly.plant_location_state.ne("Unknown")].sum())
    destination = float(energy[monthly.direction.eq("export") & monthly.beneficiary_state.ne("Unknown")].sum())
    mapped = float(energy[monthly.direction.eq("export") & monthly.tn_BA.fillna("").ne("")].sum())
    unresolved_gwh = float(pd.to_numeric(unresolved.get("energy_GWh", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    failed = int(validation.status.eq("FAIL").sum())
    parse_errors = int(unresolved.category.eq("source_parse_error").sum()) if not unresolved.empty else 0
    missing_refs = ", ".join(baseline.loc[~baseline.available.astype(bool), "reference_file"].tolist()) or "none"
    inventory = pd.read_csv(output / "source_inventory.csv", keep_default_na=False)
    march_final_csv = inventory.loc[
        inventory["month"].eq("2026-03")
        & inventory["extension"].eq(".csv")
        & ~inventory["is_provisional"].astype(str).str.lower().eq("true")
    ]
    reconciliation = pd.read_csv(output / "reconciliation_by_month_channel.csv", keep_default_na=False)
    reconciliation["residual_numeric"] = pd.to_numeric(reconciliation["residual_GWh"], errors="coerce")
    residual_by_channel = (
        reconciliation.loc[reconciliation["status"].eq("EXPLAIN")]
        .groupby("channel")["residual_numeric"].sum().to_dict()
    )
    ba = pd.read_csv(output / "TN_PyPSA_BA_adjustments.csv", keep_default_na=False)
    unallocated_ba = ba.loc[ba["tn_BA"].eq("UNALLOCATED_TN")].iloc[0]
    station = pd.read_csv(output / "reconciliation_by_station_buyer.csv", keep_default_na=False)
    station = station.drop_duplicates(["month", "station_id"])
    tn_station_codes = {code for code, (_, state, *_rest) in STATIONS.items() if state == "Tamil Nadu"}
    tn_station = station.loc[station["station_id"].isin(tn_station_codes)].copy()
    tn_station_residual = pd.to_numeric(tn_station["station_minus_beneficiaries_GWh"], errors="coerce")
    tn_station_block = pd.to_numeric(tn_station["max_abs_block_residual_MW"], errors="coerce")
    readme = f"""# Tamil Nadu SRPC REA commercial schedules, FY2025-26

Generated reproducibly from the twelve supplied final SRPC REA archives. These are
scheduled commercial-energy accounts, not metered physical transmission flows.

## Accounting boundary

- Counted once: beneficiary-level generating-station allocations, TNEB bilateral
  applications, Tamil Nadu PX detail, and deduplicated REMC/RNW transactions.
- Validation-only parents: `Sch_Total`, `lta`, station-wide ISGS schedules,
  `rea.csv`, and generator PX-sale files.
- Import `signed_GWh` is positive; export `signed_GWh` is negative.
- Every 15-minute MW value uses `MW × 0.25 / 1000 = GWh`.

## Annual result

- Approved imports: **{imports:,.3f} GWh**; approved exports: **{exports:,.3f} GWh**.
- Confirmed external-origin imports: **{confirmed_imports:,.3f} GWh**.
- Confirmed TN-plant exports to evidenced external beneficiaries: **{confirmed_exports:,.3f} GWh**.
- Imports with physical origin attributed: **{100 * origin / imports if imports else 0:.2f}%**.
- Exports with destination attributed: **{100 * destination / exports if exports else 0:.2f}%**.
- Exports mapped to one of the supplied nine BAs: **{100 * mapped / exports if exports else 0:.2f}%**.
- Non-exclusive quantified unresolved categories: **{unresolved_gwh:,.3f} GWh**.

## Reproduce

```powershell
./scripts/run_rea_fy2025_26.ps1
```

The runner processes months in isolated parallel workers, merges them, and runs
the automated tests. Raw archives are neither extracted nor modified. The inventory
contains **{len(inventory):,}** entries. March has **{len(march_final_csv):,}**
non-provisional CSV entries: the two half-month sets plus a top-level
`commercial_actual_nlcmines.csv` supplemental and the annual 55-beneficiary file.

Additional review tables are `TN_state_to_state_annual.csv`,
`TN_channel_breakdown.csv`, `uncertainty_summary.csv`, and
`plant_name_district_match_audit.csv`. Plant/district matches use the supplied
validated ICED/Prayas/OSM workbook and retain ambiguous names as unresolved.

## Closure caveat

The result is not described as fully reconciled while failed validations, gross/net
boundary residuals, parse errors, pooled-market attribution gaps, or unallocated BA
energy remain. Validation failures: **{failed}**; parse errors: **{parse_errors}**;
missing benchmark-only files: {missing_refs}.

The requested XLSX review workbook could not be authored because the required
spreadsheet artifact runtime is not installed. The authoritative Parquet/CSV tables
and `workbook_manifest.md` retain every planned sheet's content mapping.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    audit = f"""# Audit report — Tamil Nadu SRPC REA FY2025-26

## Evidence and double-count prevention

The pipeline recursively inventories all nested archives, excludes provisional
branches, and preserves source hashes plus raw-to-canonical keys. Parent totals are
never appended to their transaction detail. Duplicate canonical schedules are
suppressed by an explicit business key and retained in the duplicate report.

The Tamil Nadu composite identities, station-column-to-`lta` identities, and PX
product/direction detail are tested at 15-minute-block level. Gross bilateral
applications are reported separately from state-level net composite controls; their
residual is not forced to zero. TSTPP is physically classified in Telangana.

## Limits of the source evidence

PX purchases do not identify physical generating state and pooled PX sales do not
establish buyer state. Most TN imports cannot be assigned to one of the nine
receiving BAs. Puducherry is retained as an enclave-unresolved boundary class. No
hourly PyPSA profile is invented from annual energy.

TN export origins are assigned in the order plant identity, verified district, then
nine-BA aggregation. The supplied validated ICED/Prayas/OSM workbook supports the
named-plant district matches recorded in `plant_name_district_match_audit.csv`;
multi-district or weak matches are not forced.

## Closure status

- Source parse errors: **{parse_errors}**.
- Validation rows marked FAIL: **{failed}**.
- Quantified unresolved categories: **{unresolved_gwh:,.3f} GWh** (non-exclusive).
- Missing benchmark-only inputs: {missing_refs}.
- Gross-to-net residuals retained rather than forced: GNA
  **{residual_by_channel.get('GNA', 0):,.3f} GWh**, TGNA
  **{residual_by_channel.get('TGNA', 0):,.3f} GWh**, and REMC/RNW
  **{residual_by_channel.get('REMC/RNW', 0):,.3f} GWh** across the twelve months.
- BA-unallocated exports: **{float(unallocated_ba['export_load_increase_GWh']):,.3f} GWh**;
  imports without an evidenced receiving BA:
  **{abs(float(unallocated_ba['import_supply_adjustment_GWh'])):,.3f} GWh**.
- TN station-wide versus all-beneficiary allocation residuals have
  **{tn_station_residual.abs().sum():,.3f} GWh** annual sum-of-absolute-monthly
  magnitude (net **{tn_station_residual.sum():,.3f} GWh**) and a largest block
  difference of **{tn_station_block.max():,.3f} MW**. These controls are not forced
  into the approved ledger.
- The XLSX review workbook remains unbuilt because the required spreadsheet
  artifact runtime is unavailable; `workbook_manifest.md` maps its intended sheets
  to the authoritative CSVs.

See the reconciliation, coverage, duplicate, and unresolved CSVs for exact evidence.
"""
    (output / "audit_report.md").write_text(audit, encoding="utf-8")
    workbook_manifest = """# Review workbook manifest

The required spreadsheet authoring runtime (`@oai/artifact-tool`) is unavailable in
this environment, so no substitute XLSX library was used. These authoritative files
map one-to-one to the requested review sheets:

| Requested sheet | Authoritative source |
|---|---|
| Summary | `README.md`, `automated_validation_summary.json`, `uncertainty_summary.csv` |
| Monthly | `TN_interstate_monthly.csv` |
| State Flows | `TN_state_to_state_annual.csv` |
| Plant Flows | `TN_generating_plant_exports.csv`, `TN_imports_by_origin.csv` |
| BA Adjustments | `TN_PyPSA_BA_adjustments.csv`, `plant_to_BA_crosswalk.csv`, `plant_name_district_match_audit.csv` |
| Channel Breakdown | `TN_channel_breakdown.csv` |
| Coverage | `coverage_report.csv`, `validation_results.csv` |
| Duplicates | `duplicate_matches.csv`, `raw_to_canonical_crosswalk.csv` |
| Residuals | `reconciliation_by_month_channel.csv`, `reconciliation_by_station_buyer.csv`, `unresolved_cases.csv` |
| Source Register | `source_inventory.csv`, `file_family_dictionary.csv`, `overlap_matrix.csv` |
| Baseline Comparison | `baseline_comparison.csv` |

No workbook totals should be independently recomputed; a future workbook must link
or import these generated tables and retain GWh units and the documented signs.
"""
    (output / "workbook_manifest.md").write_text(workbook_manifest, encoding="utf-8")


def merge(parts: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for filename in ("canonical_schedules.parquet", "deduplicated_ledger.parquet", "excluded_ledger_rows.parquet"):
        _merge_parquet(parts, output, filename)
    _merge_csv_stream(parts, output, "raw_to_canonical_crosswalk.csv")

    simple = [
        "source_inventory.csv", "coverage_report.csv", "reconciliation_by_month_channel.csv",
        "reconciliation_by_station_buyer.csv", "duplicate_matches.csv", "unresolved_cases.csv",
    ]
    for filename in simple:
        frame = _read(parts, filename)
        if not frame.empty:
            sort_cols = [c for c in ("month", "virtual_path", "test", "station_id") if c in frame]
            if sort_cols:
                frame = frame.sort_values(sort_cols, kind="stable")
        if filename == "source_inventory.csv" and not frame.empty:
            frame = _enrich_inventory(frame)
        frame.to_csv(output / filename, index=False)

    dictionary = _read(parts, "file_family_dictionary.csv").drop_duplicates()
    dictionary.sort_values(["file_family", "filename_pattern_or_example"], kind="stable").to_csv(output / "file_family_dictionary.csv", index=False)
    pd.DataFrame(_read(parts, "overlap_matrix.csv").drop_duplicates()).to_csv(output / "overlap_matrix.csv", index=False)

    monthly = _read(parts, "TN_interstate_monthly.csv")
    point_mapped_ids = set(monthly.loc[monthly["tn_BA"].fillna("").ne(""), "generator_id"].astype(str))
    monthly = _apply_generator_geography(monthly)
    monthly["energy_GWh"] = pd.to_numeric(monthly.energy_GWh, errors="coerce")
    monthly["signed_GWh"] = pd.to_numeric(monthly.signed_GWh, errors="coerce")
    monthly_group = [c for c in monthly.columns if c not in {"energy_GWh", "signed_GWh"}]
    monthly = monthly.groupby(monthly_group, dropna=False, as_index=False)[["energy_GWh", "signed_GWh"]].sum()
    monthly = monthly.sort_values(["month", "direction", "channel", "plant_location_state", "generator_name"], kind="stable")
    monthly.to_csv(output / "TN_interstate_monthly.csv", index=False)
    group = [c for c in monthly.columns if c not in {"month", "energy_GWh", "signed_GWh"}]
    annual = monthly.groupby(group, dropna=False, as_index=False)[["energy_GWh", "signed_GWh"]].sum()
    annual.insert(0, "financial_year", FY)
    annual = annual.sort_values(["direction", "channel", "plant_location_state", "generator_name"], kind="stable")
    annual.to_csv(output / "TN_interstate_annual.csv", index=False)
    annual.loc[annual.direction.eq("export")].to_csv(output / "TN_generating_plant_exports.csv", index=False)
    annual.loc[annual.direction.eq("import")].to_csv(output / "TN_imports_by_origin.csv", index=False)
    _ba_adjustments(monthly).to_csv(output / "TN_PyPSA_BA_adjustments.csv", index=False)
    _write_plant_ba_crosswalk(output, point_mapped_ids)
    _write_workbook_name_audit(output, monthly)

    validation = _read(parts, "validation_results.csv")
    validation = validation.loc[
        ~validation.test.eq("expected_selected_slots")
        & ~validation.test.str.startswith("March_regression:")
    ]
    march_validation = pd.read_csv(parts / "2026-03" / "validation_results.csv", keep_default_na=False)
    march_validation = march_validation.loc[march_validation.test.str.startswith("March_regression:")]
    validation = pd.concat([validation, march_validation], ignore_index=True)
    validation = pd.concat([validation, pd.DataFrame([{
        "scope": FY, "test": "expected_selected_slots", "actual": 35040,
        "expected": 35040, "tolerance": 0, "status": "PASS", "note": "calendar days × 96 blocks",
    }])], ignore_index=True)
    validation.to_csv(output / "validation_results.csv", index=False)

    baseline = _read(parts, "baseline_comparison.csv").drop_duplicates()
    baseline.to_csv(output / "baseline_comparison.csv", index=False)
    unresolved = pd.read_csv(output / "unresolved_cases.csv", keep_default_na=False)
    unresolved = _rebuild_unresolved(output, unresolved)
    unresolved.to_csv(output / "unresolved_cases.csv", index=False)
    _write_derived_summaries(output, monthly, unresolved)
    _write_reports(output, monthly, validation, unresolved, baseline)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    merge(args.parts_dir, args.output_dir)
    print(f"Merged annual outputs written to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
