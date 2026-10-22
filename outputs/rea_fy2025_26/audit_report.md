# Audit report — Tamil Nadu SRPC REA FY2025-26

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

- Source parse errors: **0**.
- Validation rows marked FAIL: **0**.
- Quantified unresolved categories: **30,289.729 GWh** (non-exclusive).
- Missing benchmark-only inputs: SRPC_REA_March2026_Complete_File_Audit.xlsx, TamilNadu_Interstate_Flows.xlsx, TamilNadu_SRPC_REA_FY2025-26_Expanded.xlsx, TamilNadu_SRPC_REA_Bilateral_Schedules_FY2025-26_PARTIAL(1).xlsx, REA_FY2025-26_TN_ISGS_Allocation_by_Buyer.csv.
- Gross-to-net residuals retained rather than forced: GNA
  **885.787 GWh**, TGNA
  **501.282 GWh**, and REMC/RNW
  **24.810 GWh** across the twelve months.
- BA-unallocated exports: **7,991.752 GWh**;
  imports without an evidenced receiving BA:
  **48,592.931 GWh**.
- TN station-wide versus all-beneficiary allocation residuals have
  **1,828.879 GWh** annual sum-of-absolute-monthly
  magnitude (net **-1,003.477 GWh**) and a largest block
  difference of **724.340 MW**. These controls are not forced
  into the approved ledger.
- The XLSX review workbook remains unbuilt because the required spreadsheet
  artifact runtime is unavailable; `workbook_manifest.md` maps its intended sheets
  to the authoritative CSVs.

See the reconciliation, coverage, duplicate, and unresolved CSVs for exact evidence.
