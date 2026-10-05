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

## Simplified notebook runner

Use `9BA_run_model.ipynb` for routine runs. Its settings cell contains:

```python
RUN_MODE = "selected_weeks"
REFERENCE_DATE = "2025-07-11" # ignored for peak, selected_weeks, and rolling
WEEK_STARTS = ["2025-04-07", "2025-07-07", "2025-10-13", "2026-02-16"]
WARMUP_DAYS = 1
LOOKAHEAD_DAYS = 1
RESERVOIR_INITIAL_SOC_FRACTION = 0.0
SOLVER_NAME = "highs"
MIP_REL_GAP = 0.01
RESUME = True
```

Daily mode solves the selected 24 hours, monthly mode solves every day in the
selected month as independent daily unit-commitment problems, and peak mode
solves the continuous 168-hour week containing the annual demand peak. All
modes enforce exact daily gas and nuclear energy targets and save tables,
validation, a plot, and (for continuous/single-day runs) the solved network
under `run_results/`. No mode introduces equipment faults or outages.

### Selected-week seasonal tests

`RUN_MODE = "selected_weeks"` solves every date in `WEEK_STARTS` as the first
day of an independent seven-day test period. Each optimization also includes
`WARMUP_DAYS` before the retained week and `LOOKAHEAD_DAYS` after it. Only the
seven test days enter the reported comparison. The complete solved network,
daily tables, validation, and plot for each week are written under
`run_results/selected_weeks/week_YYYY-MM-DD/`; combined outputs and
`selected_week_log.csv` are written one level above.
With `RESUME = True`, a completed week is reused when its model version,
solver, MIP gap, warm-up, look-ahead, and reservoir initial-SOC settings match
the current run.

Reservoir state does not transfer across gaps between selected weeks. Each
independent solve instead uses the nearest available observed daily reservoir
storage at the retained period's start and constrains the nearest observed
storage at its end. The configured `RESERVOIR_INITIAL_SOC_FRACTION` is only a
fallback for assets without an observed or explicitly documented proxy level.
A full rolling run is still required when the study question needs endogenous
chronological carry-over rather than observed boundary conditions.

## Peak-week run and analysis

The 9BA equivalents of the single-node hourly notebooks are kept in `9_BA/`:

- `9BA_run_peakday_2025_26.ipynb` selects the Monday-Sunday week containing the
  annual statewide demand peak, runs HiGHS unit commitment, validates nodal
  balances and corridor limits, and writes
  `tamil_nadu_9ba_2025_26.nc`. The exploratory mixed-integer solve uses a 1%
  relative MIP-gap tolerance; this keeps runtime proportionate after adding
  exact daily oil-and-gas energy targets, while remaining proportionate to the
  input-data uncertainty and retaining a near-optimal commitment schedule.
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

For a chronological full-year approximation, open `9BA_run_model.ipynb` and
set `RUN_MODE = "rolling"`. Each optimization covers eight days: the first
seven are accepted and exported, while the eighth supplies look-ahead and is
re-optimized in the next window. Generator dispatch and consecutive on/off
history, plus storage state of charge, are passed across weekly boundaries.
Completed windows are checkpointed under
`run_results/rolling_2025-04-01_2026-03-31/weekly_networks/`; keep
`RESUME = True` to continue an interrupted run. The final one-day window has
no look-ahead because it reaches the end of the available input horizon.

After the rolling run is complete, open
`9BA_results_analysis_rolling_year.ipynb` and run all cells. It checks the
8,760-hour retained chronology and state handoffs, compares monthly and annual
modeled generation with the observed FY2025-26 workbook, reports modeled and
observed-implied full-load hours on a common capacity basis, tests wind-resource
and hydro-utilization seasonality (including amplitude, timing, low-month
overlap, and 30-day rolling profiles), and analyses unit commitment, storage,
ramps, imports, unserved energy, and corridor loading.
Tables and figures are saved under the rolling result folder's `analysis/`
subdirectory.

## Model contents

July 2025 can be evaluated with `python scripts/run_9ba_month.py`. The script
solves all 31 complete days as independent 24-hour unit-commitment problems,
covering all 744 July hours at a 1% MIP tolerance. It enforces each day's gas
and nuclear equality targets and writes validation results, daily and monthly
comparison tables, and a comparison plot under `monthly_results/2025_07/`.

A continuous 744-hour formulation was also attempted, but its approximately
370,000 binary variables produced no feasible integer schedule after about
112 minutes. Its log is retained as `continuous_attempt_solver.log`. The daily
formulation does not carry commitment or storage state between dates; each
storage unit is cyclic within its day. It is suitable for monthly generation
and renewable-profile comparison, rather than month-long chronological
commitment or storage analysis.

- Nine buses use the balancing-area names in `Balancing_areas.txt`.
- The 18 grid corridors in `Grid_capacity.txt` are modeled as lossless,
  bidirectional PyPSA `Link` components. Each stated MW capacity has a fixed
  50% availability factor, so the modeled bidirectional rating is 50% of the
  stated value in every snapshot. Nominal and effective capacities are retained
  in `model/transmission_capacity_metadata.csv`.
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
- Conventional hydro is controlled through `hydro_asset_registry.csv` and the
  explicit fleet reconciliation in `hydro_fleet_reconciliation.csv`. Simple
  reservoir plants use non-pumping `StorageUnit`s. PAP, Kodayar, Kundah,
  Pykara/Moyar and Papanasam/Servalar use `Store` water balances and
  turbine/transfer `Link`s so that upstream discharge is routed downstream;
  Kadamparai uses separate pumping and generation Links. Lower Mettur and
  Bhavani Kattalai receive availability from their upstream observed releases.
  The rolling runner transfers both `StorageUnit` and `Store` state between
  consecutive chronological windows.
- In the Papanasam--Servalar sub-cascade, the Agriculture SOC series applies
  only to `Papanasam_reservoir`. A lossless configurable tunnel connects it to
  the separate `Servalar_reservoir`; the official 20 MW Servalar turbine and a
  direct Papanasam release both feed `Papanasam_lower_pondage`. The official
  32 MW Papanasam powerhouse then discharges to the downstream-river sink, not
  to Servalar. Lower pondage is weekly water-neutral and has no seasonal SOC
  target.
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

## Nuclear energy targets

Nuclear also uses an annual generation equality from the observed workbook,
with exact daily targets enforced in the weekly-day runner and peak-week
notebook. Targets are exported in `model/nuclear_daily_energy_budget.csv`.
The inherited constant 61.2% nuclear output is replaced with a 0–100%
nameplate dispatch range to permit those targets. Hourly output and plant
allocation remain optimized; daily energy matching does not validate their
hourly schedules. Gas and nuclear comparison errors are calibrated by design.
Custom solve workflows must use `scripts/nuclear_energy_targets.py` to apply
daily targets and adjust the annual equality for their selected dates.

## Reservoir hydro and seasonal inflow

`hydro_asset_registry.csv` classifies capacity-workbook units while
`hydro_fleet_reconciliation.csv` records capacity discrepancies and source-only
assets that must not be silently added. `model/hydro_reservoir_parameters.csv`
contains the modelled reservoir/component, MW and MWh basis, fixed head,
efficiency, SOC source, data quality, and stated proxy/assumption for every
hydraulic component. `model/hydro_assumptions_report.csv` isolates all entries
that are not high-quality observations.

Electrical capacity and hydraulic storage are intentionally separate. Every
hydro turbine Link and run-of-river generator takes `p_nom` from the official
Tamil Nadu generation inventory; reservoir volume, head, and efficiency never
rescale that MW value. Where a Store represents a group reservoir, it affects
only water/SOC. The machine-readable proof is
`model/hydro_capacity_validation.csv`: it lists every official capacity record,
its model component and connected Store, the storage aggregation treatment, and
the capacity check. Sholayar's 109 MW, Kundah's 585 MW, Pykara's 61.2 MW, and
Moyar's 38 MW source-table values are flagged there as reference/system totals
only, not model capacities.

Natural inflow and observed storage use the validated daily Tamil Nadu
Agriculture reservoir data. Volumes use `1 cusec-day = 0.0864 M.cft` and are
converted to electricity-equivalent energy by
`0.0771634 × volume_M.cft × head_m × efficiency`, with a configurable first-pass
efficiency of 0.90 and documented fixed heads. No statewide hydro-generation
profile is allocated as artificial reservoir inflow. Where an agriculture
series does not correspond directly to a plant, the model either uses a
labelled connected-reservoir filling proxy or neutral endogenous pondage; it
does not copy absolute volume between reservoirs.

`model/storage_units-inflow.csv` contains simple-reservoir hourly inflow and
`model/hydro_reservoir_inflow.csv` provides its daily source audit. Inflows to
cascade water buses are represented as `water_inflow` generators. Observed
daily SOC targets are in `model/hydro_observed_soc.csv`. The selected-week
runner applies these as independent start/end boundary conditions; the rolling
runner carries state across contiguous windows. A full-year or long-horizon
solve remains necessary to optimise seasonal water value endogenously.

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

Solar generators carry a small 1 currency-unit/MWh marginal-cost tie-breaker;
wind remains at zero marginal cost. When otherwise equivalent renewable
output must be curtailed, this makes the optimizer prefer curtailing solar.
Other system constraints can still determine dispatch when the resources are
not interchangeable.

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
