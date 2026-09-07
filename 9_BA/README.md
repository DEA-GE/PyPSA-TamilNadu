# Tamil Nadu nine-balancing-area model

This folder contains an independent hourly, nine-node extension of the
single-node FY2025-26 model. The model itself is the PyPSA CSV folder in
`model/`; rebuild it from the repository root with:

```powershell
python scripts/build_9ba_model.py
```

Load it with:

```python
import pypsa

network = pypsa.Network("9_BA/model")
```

## Peak-week run and analysis

The 9BA equivalents of the single-node hourly notebooks are kept in `9_BA/`:

- `9BA_run_peakday_2025_26.ipynb` selects the Monday-Sunday week containing the
  annual statewide demand peak, runs HiGHS unit commitment, validates nodal
  balances and corridor limits, and writes
  `tamil_nadu_9ba_2025_26.nc`. The exploratory mixed-integer solve uses a 0.1%
  relative MIP-gap tolerance; this keeps runtime proportionate to the input
  data uncertainty while retaining a near-optimal commitment schedule.
- `9B_results_analysis_hourly.ipynb` reads that solved network and analyzes
  system dispatch, storage, commitment, balancing-area supply and demand,
  net imports, corridor flow and congestion, installed district capacity in
  absolute MW and as a statewide share, area renewable generation, and hourly
  ramp adequacy.

Run the peak-week notebook before the analysis notebook. Both are written to
work whether Jupyter starts in the repository root or in `9_BA/`. Neither
notebook creates equipment faults, forced outages, or contingency scenarios.

For a wider seasonal check, run:

```powershell
python scripts/run_9ba_weekly_days_2025_26.py
```

This selects the highest-energy-demand day from each Monday-Sunday week,
including the partial weeks at the FY boundaries, and solves all 53 days as
independent 24-hour unit-commitment problems. It compares modeled daily coal,
oil and gas, nuclear, hydro, solar, wind, and like-for-like total generation
with `additional_data/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx`.
Each independent solve replaces the full-year oil-and-gas target with that
date's observed daily energy target.
The workbook's Other RES series is blank, so modeled bio-power and small hydro
are reported without an observed-error statistic. Tables and figures are
written to `weekly_day_results/`. The runner does not introduce equipment
faults or outages.

A short unit-commitment run can be started, for example, with:

```python
status, condition = network.optimize(
    snapshots=network.snapshots[:24],
    solver_name="highs",
)
```

## Model contents

- Nine buses use the balancing-area names in `Balancing_areas.txt`.
- The 22 grid corridors in `Grid_capacity.txt` are modeled as lossless,
  bidirectional PyPSA `Link` components. A stated capacity range uses its
  midpoint; the original text and conversion method are retained in
  `model/transmission_capacity_metadata.csv`.
- The model retains all 8,760 hourly snapshots from 1 April 2025 through
  31 March 2026.
- Installed nameplate capacity is updated to the 31 July 2026 comparison
  vintage through `additional_capacity_2026_07.csv`. Demand and renewable
  profiles remain FY2025-26, so this is a capacity-vintage overlay rather than
  a historical FY2025-26 fleet snapshot.
- Coal, oil and gas, and bio-power generators retain the single-node model's
  commitment flags, start/shut-down costs, minimum up/down times, and ramp
  limits. Some geographically unspecified aggregate bio-power records are
  split across areas, so each allocated part is independently committable.
- The 211.70 MW diesel overlay is represented as fourteen committable units:
  seven at Samayanallur in the Madurai area and seven at Samalpatti in the
  Vellore area. In the absence of plant-specific operating data, these units
  use the model's oil-and-gas commitment and cost assumptions while retaining
  a separate `diesel` carrier.
- No fault, contingency, forced-outage, or short-circuit representation is
  included.

## Capacity allocation

Operational capacities originate in
`additional_data/TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx`.
`PlantInfo` supplies the record-level capacities and the workbook's `District
Allocation` sheet supplies the complete district allocation, provenance,
method, and confidence fields.

Every operational `PlantInfo` row reconciles to one or more allocation rows
before districts are mapped through `Balancing_areas.txt`. Spelling variants
are normalized (for example, `Kanchipuram` to `Kancheepuram`, `Kanyakumari` to
`Kanniyakumari`, and `Nilgiris` to `The Nilgiris`). District allocations that
belong to the same balancing area are recombined before a PyPSA component is
created.

The workbook resolves every allocation to a district. Only 207.13 MW remains
marked as low-confidence allocation; 11,841.51 MW is medium-confidence,
primarily reflecting OSM-backed solar and wind allocation. Inspect
`model/district_capacity_allocation.csv` for the full
district-level source table and `model/component_metadata.csv` for each
PyPSA component's assigned bus, method, confidence, and allocation share.
`model/capacity_by_balancing_area.csv` provides the resulting capacity summary.
Statewide market imports and unserved-energy mechanisms are split using demand
energy shares; they are not reported as installed plant.

The July-2026 overlay adds 563.9895 MW to the modeled fleet: 211.70 MW diesel,
212.33 MW solar, and 139.9595 MW wind. Project evidence locates the diesel
stations and 22.60 MW of solar; the remaining 189.73 MW solar and 139.9595 MW
wind are explicitly marked as provisional, low-confidence district proxies.
The source comparison contains a further 49.31 MW CEA-MNRE reporting-boundary
difference. It is retained as `unassigned` in
`model/additional_capacity_2026_07.csv` but is deliberately excluded from
PyPSA because neither a Tamil Nadu district nor a technology is substantiated.
Consequently, modeled installed plant totals 48,331.10 MW; adding the excluded
49.31 MW reporting difference reconciles to the 48,380.41 MW comparison total.
The arithmetic is exported to `model/capacity_reconciliation_2026_07.csv` on
every rebuild.

## Demand

The balancing-area workbook provides annual-energy and peak shares, but not
hourly area profiles. Each area therefore uses an affine transformation of the
single-node hourly demand shape. This construction simultaneously:

1. matches the workbook's relative annual-energy share;
2. matches its relative peak share; and
3. preserves the single-node Tamil Nadu demand at every hour.

The model consequently retains the original 131.3834 TWh realized demand and
19,987.33 MW state peak rather than rescaling to the workbook's 149.063 TWh and
21,481 MW official projection. The complete calculation and coefficients are
in `model/demand_allocation.csv`. These are synthetic area series, not metered
balancing-area load profiles.

## Oil-and-gas energy budget

The observed FY2025-26 daily generation workbook reports 1,241.45 MU
(1,241,450 MWh) of Tamil Nadu oil-and-gas generation. The model represents
this as one aggregate PyPSA `operational_limit` equality constraint applying
to all `oil_gas` generators. Full-year optimization must therefore produce
exactly the observed annual oil-and-gas energy total.

`model/oil_gas_daily_energy_budget.csv` records all 365 observed daily values,
each day's share of the annual total, and its exact MWh target. The weekly-day
runner applies the selected date's equality target. The peak-week notebook
applies all seven daily equality targets as well as their aggregate weekly
target. The annual constraint and its 1,241,450 MWh constant are exported in
`model/global_constraints.csv`; the source reconciliation is in
`model/oil_gas_energy_budget_summary.csv`.

## Renewable profile placeholders

`renewable_profiles.csv` has one `solar` and one `wind` capacity-factor column
for every balancing area, indexed by the 8,760 model timestamps. Until spatial
profiles are supplied, all areas use the same statewide shapes:

- wind copies the existing `wind_fleet` profile;
- solar uses the capacity-weighted blend of the existing utility-scale and
  rooftop/off-grid solar profiles.

This choice preserves the single-node model's aggregate renewable availability
while exposing the required 18-column replacement interface. To install the
future hourly profiles, replace the values in `renewable_profiles.csv` without
changing `snapshot` or the column names, keep all capacity factors within
0-1, and rerun the build script. The builder validates timestamps, columns,
missing values, and bounds before writing `model/generators-p_max_pu.csv`.
Delete `renewable_profiles.csv` before rebuilding if the placeholders need to
be regenerated from updated single-node profiles.

## Important limitations

The grid is a transport model because only corridor capacities were supplied;
it does not enforce AC Kirchhoff voltage laws or calculate reactive power.
Capacity ranges are approximate and losses are zero. Geographically
unspecified plant allocation, synthetic area demand, and duplicated statewide
renewable shapes should be replaced when stronger spatial data are available.
The provisional July-2026 capacity overlay must likewise be replaced when a
district-level commissioning register becomes available.
The full-year mixed-integer problem is large; select a shorter snapshot window
for exploratory unit-commitment runs unless a full-year solve is intentional.
