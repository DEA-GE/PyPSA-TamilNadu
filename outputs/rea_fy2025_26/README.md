# Tamil Nadu SRPC REA commercial schedules, FY2025-26

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

- Approved imports: **48,592.931 GWh**; approved exports: **23,504.754 GWh**.
- Confirmed external-origin imports: **27,765.979 GWh**.
- Confirmed TN-plant exports to evidenced external beneficiaries: **15,504.698 GWh**.
- Imports with physical origin attributed: **57.14%**.
- Exports with destination attributed: **68.90%**.
- Exports mapped to one of the supplied nine BAs: **66.00%**.
- Non-exclusive quantified unresolved categories: **30,289.729 GWh**.

## Reproduce

```powershell
./scripts/run_rea_fy2025_26.ps1
```

The runner processes months in isolated parallel workers, merges them, and runs
the automated tests. Raw archives are neither extracted nor modified. The inventory
contains **16,268** entries. March has **724**
non-provisional CSV entries: the two half-month sets plus a top-level
`commercial_actual_nlcmines.csv` supplemental and the annual 55-beneficiary file.

Additional review tables are `TN_state_to_state_annual.csv`,
`TN_channel_breakdown.csv`, `uncertainty_summary.csv`, and
`plant_name_district_match_audit.csv`. Plant/district matches use the supplied
validated ICED/Prayas/OSM workbook and retain ambiguous names as unresolved.

## Resolved Tamil Nadu transaction extract

Run `scripts/export_tn_resolved_transactions.py` against the deduplicated and
excluded ledgers to create `TN_resolved_interstate_transactions_FY2025_26.parquet`
and its hourly and annual CSV summaries. The extract contains only verified
Tamil Nadu imports and exports. At the user's direction, verified TN generating-
station allocations to Puducherry are included as a separately labeled
`verified_puducherry_exception`. A flow-focused review also promotes GNA/TGNA
rows from final `tneb_exp.csv` files when Tamil Nadu is the source and an
external buyer is named. They are labeled `resolved_tn_export_register`; their
plant remains unattributed. Pooled DAM/RTM positions remain unresolved because
they contain no buyer or destination. The resolution audit records both decisions.

## Plant-level model reservations

`scripts/prepare_rea_interstate_flows.py` writes a compact hourly schedule and
mapping audit for verified exports whose Tamil Nadu generator is named. Fifteen
named REA plants map to the PyPSA inventory (15,496.913 GWh); Kamachi Steels and
ARKAY Energy remain unmatched (7.785 GWh). The model builder uses the mapped
schedules to reserve plant capacity and fixed generation at the physical plant
bus. These reservations supply the already-existing resolved gateway export
loads and therefore do not add to total interstate exports.

## PX destination forensics

`scripts/analyze_px_destinations.py` compares Tamil Nadu pooled exports with
available state imports in the same block, product and exchange. The results are
`estimated_not_verified`: only seven external state files are available and
pooled clearing does not pair sellers with buyers. No shared non-timestamp
transaction identifier was found. Plant `pxsold` files provide 22.420 GWh of
buyer-region evidence, but 876 blocks exceed the Tamil Nadu pooled schedule, so
that evidence remains diagnostic and is not promoted.

## Closure caveat

The result is not described as fully reconciled while failed validations, gross/net
boundary residuals, parse errors, pooled-market attribution gaps, or unallocated BA
energy remain. Validation failures: **0**; parse errors: **0**;
missing benchmark-only files: SRPC_REA_March2026_Complete_File_Audit.xlsx, TamilNadu_Interstate_Flows.xlsx, TamilNadu_SRPC_REA_FY2025-26_Expanded.xlsx, TamilNadu_SRPC_REA_Bilateral_Schedules_FY2025-26_PARTIAL(1).xlsx, REA_FY2025-26_TN_ISGS_Allocation_by_Buyer.csv.

The requested XLSX review workbook could not be authored because the required
spreadsheet artifact runtime is not installed. The authoritative Parquet/CSV tables
and `workbook_manifest.md` retain every planned sheet's content mapping.
