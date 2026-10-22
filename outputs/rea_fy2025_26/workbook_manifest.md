# Review workbook manifest

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
