# CEA monthly station-generation reports — FY2025–26

The consolidated CEA station-month data is stored in
`data/model_inputs/cea_hydro_monthly_generation_FY2025_26.csv`, with one row
per station and report month.  It contains the monthly actual generation,
April-to-report cumulative actual generation, standardised model key, and
source report name for all twelve reports from April 2025 through March 2026.

The original PDF files and intermediate text extracts have been removed after
consolidation.  The consolidated CSV and reconciliation checks are the
retained data artifacts.

`manifest.csv` retains the official URL, retrieval timestamp, byte size, and
SHA-256 digest recorded for each original PDF.  The reports contain actual GWh
in the current month and cumulative actual GWh from April through the report
month, by station.  `cea_hydro_monthly_generation_checks_FY2025_26.csv`
contains the reconciliation checks for the consolidated data.

Source pattern:
`https://npp.gov.in/public-reports/cea/monthly/generation/18_col_act/YYYY/MON/18_col_act-16_YYYY-MON.pdf`
