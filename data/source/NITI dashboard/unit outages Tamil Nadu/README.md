# NITI Dashboard unit outages, Tamil Nadu coal, 2019–2026

Two India Climate & Energy Dashboard (https://iced.niti.gov.in) exports of
Tamil Nadu thermal unit outages, supplied by the project team on 2026-11-20:

- `Electricity_Planned_Outage_1795169249625_coal.xlsx`: planned outages.
  Reasons: annual maintenance, annual maintenance (boiler overhaul), boiler
  inspection / re-certification, capital maintenance, other planned
  maintenance works, financial problem.
- `Electricity_Forced_Outage1795168629170_Coal.xlsx`: forced outages.
  Reasons: technical reason, technical grid, raw water unavailability, coal
  and other fuel shortage, natural disaster, pollution problem, no fuel supply
  agreement, no power purchase agreement, other commercial reasons, reserve
  shutdown / low schedule, no demand from distribution / reserve shutdown.

Each workbook has a wide sheet (one row per reason, one column per day, MU
lost) and a table sheet (`Plant Outage Table data`, `Generation Loss Table
data`) with one row per unit, day and reason: plant, unit, reason, date
(`Duration`), the share of the day the unit was out (`… Outage Days`),
generation lost in MU (= capacity × 24 h × share) and unit capacity in MW. The
data run from January 2019 to 1 November 2026. The coal planned-outage export
also contains three nuclear units (Kudankulam 1–2, MAPS 2); the forced-outage
export contains some hydro units in earlier years.

The data are CEA's daily outage reports (Daily Generation Report Sub-report
10, https://npp.gov.in/public-reports/cea/daily/dgr/), compiled by unit and
day. For 15 July 2025 the units, kinds and reasons match Sub-report 10 exactly,
and the share of the first day of an outage gives its start time (ITPCL unit 2
capital maintenance, 1 August 2025: share 0.93, i.e. from 01:40; CEA: 01:36).
2 June 2025 is missing for Tuticorin (P) TPP. Partial deratings of running
units are not included.

`scripts/prepare_coal_outages.py` turns the FY2025-26 rows into
`data/model_inputs/coal_unit_outages_FY2025_26.csv`, used by
`scripts/generator_outages.py`.
