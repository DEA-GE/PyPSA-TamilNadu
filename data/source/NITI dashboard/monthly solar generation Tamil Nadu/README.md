# NITI Dashboard monthly Tamil Nadu solar generation, FY2015-16 to FY2026-27

`Generation_Metatable_Data_1795004240611.csv` is the India Climate & Energy
Dashboard (https://iced.niti.gov.in) export of monthly solar generation for
Tamil Nadu in MU (1 MU = 1 GWh), supplied by the project team on 2026-11-18.
The dashboard's copyright and disclaimer footer has been removed; the data rows
are unchanged. FY2026-27 covers April to September 2026 only.

The FY2025-26 rows equal those in
`data/model_inputs/niti_dashboard_tamil_nadu_monthly_generation_FY2025_26.csv`.
`scripts/solar_seasonal_correction.py` uses FY2020-21 to FY2024-25 to fit the
seasonal solar correction, keeping FY2025-26 as an independent test year.
FY2019-20 is not used because that year's monthly capacity data lag
commissioning.
