# NITI Dashboard monthly Tamil Nadu generation by plant

India Climate & Energy Dashboard (https://iced.niti.gov.in) exports of monthly
Tamil Nadu generation in MU (1 MU = 1 GWh), supplied by the project team on
2026-11-20. The dashboard's copyright and disclaimer footer has been removed;
the data rows are unchanged. Every series ends in September 2026, so FY2026-27
covers April to September only.

| File | Original export | Coverage | Detail |
|---|---|---|---|
| `coal.csv` | `Generation_Metatable_Data_1795165252692.csv` | FY2020-21 onward | 20 plants (some start or stop within the period) |
| `oil-gas.csv` | `Generation_Metatable_Data_1795164934024.csv` | FY2022-23 onward | 7 plants |
| `nuclear.csv` | `Generation_Metatable_Data_1795164963489.csv` | FY2022-23 onward | Kudankulam, MAPS |
| `hydro.csv` | `Generation_Metatable_Data_1795166253273.csv` | FY2016-17 onward | 19 plants |
| `small-hydro.csv` | `Generation_Metatable_Data_1795165159977.csv` | FY2020-21 onward | state total |
| `bio-power.csv` | `Generation_Metatable_Data_1795166168912.csv` | FY2018-19 onward | state total |

A second nuclear export (`Generation_Metatable_Data_1795165128135.csv`) was
identical to the first and is not kept.

`scripts/data_preparation/prepare_niti_iced_monthly_generation.py` validates
these files and writes the long table
`data/model_inputs/niti_iced_tamil_nadu_monthly_generation.csv`; run it with
`--import-from <folder>` to take in new exports.

Checks against the model's FY2025-26 inputs:

- Hydro equals the CEA monthly station data
  (`data/model_inputs/cea_hydro_monthly_generation_FY2025_26.csv`) in every
  month (5,590 MU).
- Oil/gas (1,244.7 MU) and small hydro (307.6 MU) match the observed daily
  workbook and the Tamil Nadu Outlook monthly values within rounding.
- Bio-power equals the Tamil Nadu Outlook monthly values (194.0 MU).
- Nuclear plant totals are 101 MU higher in April 2025 and 417 MU higher in
  May 2025 than the daily workbook (`model/nuclear_daily_energy_budget.csv`);
  the other ten months agree.
