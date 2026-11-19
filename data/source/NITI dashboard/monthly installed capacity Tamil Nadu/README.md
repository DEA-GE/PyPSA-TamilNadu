# NITI Dashboard monthly installed capacity, Tamil Nadu, 2019–2026

`Electricity_Installed_Capacity_1795004496111.xlsx` is the India Climate &
Energy Dashboard (https://iced.niti.gov.in) export of Tamil Nadu's month-end
installed capacity by source (MW), January 2019 to August 2026. It was
supplied by the project team on 2026-11-18. The sheet is wide: one row per
source (coal, oil and gas, nuclear, hydro, solar, wind, small hydro,
bio-power), one column per month. Its `Total` row repeats the solar row.

`data/model_inputs/tamil_nadu_installed_capacity_monthly_2019_2026.csv` is the
tidy version, with one row per month end. A column labelled `YYYY-MM` is
capacity as at the end of that month:

- Solar and wind equal MNRE's state-wise releases in all eight FY2025-26
  months where both are available.
- From March 2022, solar equals the fiscal-year-end values in
  `data/source/Capacity_Metatable_Data_Sept26.xlsx`. In March 2019–2021 it is
  44–52 MW lower.
- In 2019, solar rises slowly and then jumps by 688 MW in December. Observed
  generation suggests the capacity was commissioned earlier and reported
  late.

Used by `scripts/solar_capacity_trajectory.py` (FY2025-26 capacity) and
`scripts/solar_seasonal_correction.py` (historical capacity for the fit).
