# Tamil Nadu nine-balancing-area model

This repository contains the hourly, nine-balancing-area PyPSA
unit-commitment model for Tamil Nadu. It is the repository's only supported
model. The ready-to-load PyPSA CSV folder is `model/`. See [How to run](#how-to-run)
for environment setup, rebuilding, and notebook settings. To load the network
programmatically:

```python
import pypsa

network = pypsa.Network("model")
```

For the location, table structure, units, provenance, and limitations of each
input, see [Input data guide](docs/input-data.md).

## Repository layout

| Path | Purpose |
|---|---|
| `model/` | Ready-to-load 8,760-hour PyPSA CSV network |
| `data/model_inputs/` | Spatial mappings, grid capacities, hydro registries, demand allocation, and renewable-profile inputs |
| `data/build_template/` | Build-stage statewide time series and component tables; not a supported standalone model |
| `data/source/` | Source workbooks and supporting datasets |
| `data/reservoirs/` | Validated reservoir observations used by the hydro model |
| `scripts/` | Build, run, validation, analysis, and data-collection utilities |
| `notebooks/` | Interactive run and analysis workflows |
| `results/` | Local generated outputs; ignored by Git except for its README |

## How to run

Run these commands from the repository root. The example uses PowerShell; on
macOS or Linux, activate the virtual environment with `source .venv/bin/activate`.
If `python` is not on your path, use the Python executable from your environment.

1. Create an environment and install the packages used to build, solve, and
   analyze the model:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   python -m pip install pypsa==1.2.4 highspy pandas openpyxl matplotlib jupyterlab xarray
   ```

   If you also regenerate upstream data, install the packages for those
   scripts:

   ```powershell
   python -m pip install requests beautifulsoup4  # scrape_tn_reservoirs.py
   python -m pip install geopandas requests       # build_enhanced_workbook.py
   ```

2. Use the checked-in `model/` network, or rebuild it after changing inputs:

   ```powershell
   python scripts/build_9ba_model.py
   ```

3. Start Jupyter from the repository root and open
   `notebooks/9BA_run_model.ipynb`:

   ```powershell
   python -m jupyter lab
   ```

4. For a first, one-day solve, change these five values in the notebook's
   **USER SETTINGS** cell. The checked-in notebook otherwise starts a full-year
   rolling run, which takes substantially longer.

   ```python
   RUN_MODE = "daily"
   REFERENCE_DATE = "2025-07-11"
   UNIT_COMMITMENT = "relaxed"
   RUN_NAME = "daily_demo"
   SOLVER_THREADS = 1
   ```

5. Choose **Run All Cells** in Jupyter. The final cell prints the solved
   period, validation results, comparison plot, and exact output directory.
   With the settings above, the directory is
   `results/run_results/daily_2025-07-11_relaxed_uc/daily_demo/`. Inspect
   `validation.json` and `daily_generation_comparison.png` there. The folder
   also contains CSV comparisons and `solved_network.nc`.

The loaded `model/` network has annual generation equality constraints. Use
the notebook runner for short periods: calling
`network.optimize(snapshots=network.snapshots[:24])` directly retains the
annual targets and is not a valid one-day run. The runner applies the daily gas
and nuclear targets and handles the biomass constraint for the selected mode.

### Notebook parameters

The checked-in **USER SETTINGS** cell contains these values:

```python
RUN_MODE = "rolling"
REFERENCE_DATE = "2025-07-11" # ignored for peak, selected_weeks, and rolling
WEEK_STARTS = ["2025-04-07", "2025-07-07", "2025-10-13", "2026-02-16"]
WARMUP_DAYS = 1
LOOKAHEAD_DAYS = 1
RESERVOIR_INITIAL_SOC_FRACTION = 0.5
SOLVER_NAME = "highs"
SOLVER_THREADS = 8
PARALLEL_WORKERS = 1
MIP_REL_GAP = 0.01
UNIT_COMMITMENT = "relaxed"
RUN_NAME = "relaxed_lp"
ROLLING_TIME_LIMIT = None
ROLLING_MAX_TIME_LIMIT_MIP_GAP = 0.10
RESUME = True
```

| Parameter | How to set it |
| --- | --- |
| `RUN_MODE` | Choose `daily`, `monthly`, `peak`, `selected_weeks`, or `rolling`. The modes and their output behavior are described below. |
| `REFERENCE_DATE` | Use a date in `YYYY-MM-DD` format within 1 April 2025–31 March 2026. `daily` runs that date; `monthly` runs every day in its month. It is ignored for the other modes. |
| `WEEK_STARTS` | For `selected_weeks` only, supply one or more first days of seven-day test periods as `YYYY-MM-DD` strings. Periods must not overlap; the seven days plus warm-up and look-ahead must fit inside the model year. The dates need not be Mondays. |
| `WARMUP_DAYS`, `LOOKAHEAD_DAYS` | For `selected_weeks` only, set non-negative whole numbers of extra days before and after each retained week. They affect the solve but are excluded from reported comparisons. The rolling mode always uses its own seven-day accepted window plus one look-ahead day. |
| `RESERVOIR_INITIAL_SOC_FRACTION` | For `selected_weeks` only, choose a value from 0 to 1 for initial reservoir state of charge where no observed or documented proxy level is available. The checked-in value is `0.5`; test sensitivity when the fallback matters. |
| `UNIT_COMMITMENT` | `full` uses binary on/off decisions; `relaxed` uses continuous on/off fractions and is faster but approximate; `none` removes commitment decisions and minimum stable output from formerly committable generators. `MIP_REL_GAP` applies only to `full`. |
| `RUN_NAME` | Give each study a distinct output folder name. It must start with a letter or number, have at most 80 characters, and contain only letters, numbers, `.`, `_`, or `-`. Reusing a name can reuse checkpoints or overwrite outputs in that folder. |
| `SOLVER_NAME` | Use `highs`, the solver used by the repository's run workflows. The thread and MIP settings below configure HiGHS. |
| `SOLVER_THREADS` | Set an integer from 1 to 8 for threads per optimization. `full` uses HiGHS MIP parallelism; `relaxed` and `none` use parallel simplex when more than one thread is selected. |
| `PARALLEL_WORKERS` | Set a positive integer for independent daily, monthly, or selected-week solves. Use `1` for peak and rolling runs. Active workers × `SOLVER_THREADS` cannot exceed the logical CPUs visible to Python. |
| `MIP_REL_GAP` | Target relative optimality gap for `full` mixed-integer solves; `0.01` means 1%. It is ignored by `relaxed` and `none`. |
| `ROLLING_TIME_LIMIT` | For `rolling` only, set a positive number of seconds per window or `None` for no time limit. It applies to both MIP and LP solves; LP windows must still reach optimality. |
| `ROLLING_MAX_TIME_LIMIT_MIP_GAP` | For a time-limited `rolling` run with `UNIT_COMMITMENT = "full"`, set the largest acceptable reported MIP gap from 0 to 1. `0.10` allows a window stopped at its time limit to be accepted only if its gap is at most 10%. This is separate from `MIP_REL_GAP`. |
| `RESUME` | `True` reuses compatible completed selected-week solves or rolling checkpoints; `False` solves them again. It has no effect on daily, monthly, or peak runs. Use a new `RUN_NAME` when changing study assumptions. |

### Run modes and results

| Mode | Period solved | State between periods |
| --- | --- | --- |
| `daily` | The 24 hours in `REFERENCE_DATE` | One independent day. |
| `monthly` | Each day in the month of `REFERENCE_DATE`, solved separately | Commitment and storage state do not carry between days. |
| `peak` | The continuous Monday–Sunday week containing the annual maximum hourly demand | One 168-hour solve. |
| `selected_weeks` | Each seven-day period in `WEEK_STARTS`, with configured warm-up and look-ahead | Each period is independent; observed reservoir levels constrain its boundaries. |
| `rolling` | All 8,760 hours in consecutive windows; seven days accepted and one day used for look-ahead | Commitment, dispatch history, and storage state carry between windows. |

Every mode enforces exact daily oil-and-gas and nuclear generation targets and
writes comparison tables, validation, and a plot. Daily and peak runs also save
a solved network in their run folder; selected weeks save one network per week,
and rolling runs save retained checkpoints. Output goes under
`results/run_results/<mode_label>/<run_name>/`. The mode label may include a
date or period and has a `_relaxed_uc` or `_none_uc` suffix when applicable.
The notebook prints the exact path after each run. No mode introduces equipment
faults or outages.

### Selected-week seasonal tests

`RUN_MODE = "selected_weeks"` solves every date in `WEEK_STARTS` as the first
day of an independent seven-day test period. Each optimization also includes
`WARMUP_DAYS` before the retained week and `LOOKAHEAD_DAYS` after it. Only the
seven test days enter the reported comparison. The complete solved network,
daily tables, validation, and plot for each week are written under
`results/run_results/selected_weeks/<run_name>/week_YYYY-MM-DD/` for full unit
commitment. Combined outputs and `selected_week_log.csv` are written in the
`<run_name>/` folder. For relaxed or no-commitment runs, the mode folder has a
`_relaxed_uc` or `_none_uc` suffix.
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

### Peak-week run and analysis

The peak-week notebooks are kept in `notebooks/`:

- `notebooks/9BA_run_peakday_2025_26.ipynb` selects the Monday-Sunday week containing the
  annual statewide demand peak, runs HiGHS unit commitment, validates nodal
  balances and corridor limits, and writes
  `results/tamil_nadu_9ba_2025_26.nc`. The exploratory mixed-integer solve uses a 1%
  relative MIP-gap tolerance; this keeps runtime proportionate after adding
  exact daily oil-and-gas energy targets, while remaining proportionate to the
  input-data uncertainty and retaining a near-optimal commitment schedule.
- `notebooks/analyze_peak_week.ipynb` reads that solved network through the
  shared result-analysis package and analyzes
  system dispatch, storage, commitment, balancing-area supply and demand,
  net imports, corridor flow and congestion, installed district capacity in
  absolute MW and as a statewide share, area renewable generation, and hourly
  ramp adequacy.

Run the peak-week notebook before the analysis notebook. Start Jupyter from
the repository root so both notebooks resolve the same paths. Neither
notebook creates equipment faults, forced outages, or contingency scenarios.

For a wider seasonal check, run:

```powershell
python scripts/run_9ba_weekly_days_2025_26.py
```

This selects the highest-energy-demand day from each Monday-Sunday week,
including the partial weeks at the FY boundaries, and solves all 53 days as
independent 24-hour unit-commitment problems. It compares modeled daily coal,
oil and gas, nuclear, hydro, solar, wind, and like-for-like total generation
with `data/source/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx`.
Each independent solve replaces the full-year oil-and-gas target with that
date's observed daily energy target.
The workbook's Other RES series is blank, so modeled bio-power and small hydro
are reported without an observed-error statistic. Tables and figures are
written to `results/weekly_days/`. The runner does not introduce equipment
faults or outages.

For a chronological full-year approximation, open
`notebooks/9BA_run_model.ipynb` and
set `RUN_MODE = "rolling"`. Each optimization covers eight days: the first
seven are accepted and exported, while the eighth supplies look-ahead and is
re-optimized in the next window. Generator dispatch and consecutive on/off
history, plus storage state of charge, are passed across weekly boundaries.
Set `UNIT_COMMITMENT = "relaxed"` for the continuous linearized formulation.
The relaxed runner carries fractional status, dispatch, and recent fractional
start/shut-down history across weekly boundaries. Equal start-up and shut-down
costs activate PyPSA's additional tightening constraints. HiGHS uses explicit
parallel solver settings appropriate to the selected LP or MIP formulation.
Completed windows are checkpointed under
`results/run_results/rolling_2025-04-01_2026-03-31_relaxed_uc/relaxed_lp/weekly_networks/`
with the checked-in settings; keep
`RESUME = True` to continue an interrupted run. The final one-day window has
no look-ahead because it reaches the end of the available input horizon.

After the rolling run is complete, open
`notebooks/analyze_rolling_year.ipynb` and run all cells. It checks the
8,760-hour retained chronology and state handoffs, compares monthly and annual
modeled generation with the observed FY2025-26 workbook, reports full-load
hours, and analyses resource seasonality, unit commitment, storage, ramps,
imports, unserved energy, and corridor loading. The peak-week and rolling-year
notebooks use the same calculations, carrier taxonomy, plot styling, and output
schema from `scripts/results_analysis/`.

Tables and figures are saved under each run's `analysis/tables/` and
`analysis/figures/` directories. `analysis_manifest.json` records the source
networks, horizon, unit-commitment formulation, schema version, and Git commit.
The same analysis can be run without Jupyter:

```powershell
python scripts/analyze_results.py results/tamil_nadu_9ba_2025_26.nc
```

If peak mode was run from `9BA_run_model.ipynb` instead, analyze its named run
folder, for example `results/run_results/peak_2025-07-07/baseline/` when
`UNIT_COMMITMENT = "full"` and `RUN_NAME = "baseline"`.

## Model contents

July 2025 can be evaluated with `python scripts/run_9ba_month.py`. The script
solves all 31 complete days as independent 24-hour unit-commitment problems,
covering all 744 July hours at a 1% MIP tolerance. It enforces each day's gas
and nuclear equality targets and writes validation results, daily and monthly
comparison tables, and a comparison plot under `results/monthly/2025_07/`.

A continuous 744-hour formulation was also attempted, but its approximately
370,000 binary variables produced no feasible integer schedule after about
112 minutes. Solver logs and all other run products are local generated
artifacts and are not tracked. The daily
formulation does not carry commitment or storage state between dates; each
storage unit is cyclic within its day. It is suitable for monthly generation
and renewable-profile comparison, rather than month-long chronological
commitment or storage analysis.

- Nine electrical buses use the balancing-area names in
  `data/model_inputs/Balancing_areas.txt`; additional water-energy buses model
  reservoir and cascade hydraulics.
- The 18 grid corridors in `data/model_inputs/Grid_capacity.txt` are modeled as lossless,
  bidirectional PyPSA `Link` components. Each stated MW capacity has a fixed
  50% availability factor, so the modeled bidirectional rating is 50% of the
  stated value in every snapshot. Nominal and effective capacities are retained
  in `model/transmission_capacity_metadata.csv`.
- The model retains all 8,760 hourly snapshots from 1 April 2025 through
  31 March 2026.
- Installed nameplate capacity is updated to the 31 July 2026 comparison
  vintage through `data/model_inputs/additional_capacity_2026_07.csv`. Demand
  and renewable profiles remain FY2025-26, so this is a capacity-vintage
  overlay rather than a historical FY2025-26 fleet snapshot.
- Coal, oil and gas, and bio-power generators retain the statewide build
  template's commitment flags, start/shut-down costs, minimum up/down times,
  and ramp limits. Some geographically unspecified aggregate bio-power records are
  split across areas, so each allocated part is independently committable.
- The 211.70 MW diesel overlay is represented as fourteen committable units:
  seven at Samayanallur in the Madurai area and seven at Samalpatti in the
  Vellore area. In the absence of plant-specific operating data, these units
  use the model's oil-and-gas commitment and cost assumptions while retaining
  a separate `diesel` carrier.
- Conventional hydro is controlled through
  `data/model_inputs/hydro_asset_registry.csv` and the explicit fleet
  reconciliation in `data/model_inputs/hydro_fleet_reconciliation.csv`. Simple
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
`data/source/TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx`.
`PlantInfo` supplies the record-level capacities and the workbook's `District
Allocation` sheet supplies the complete district allocation, provenance,
method, and confidence fields.

Every operational `PlantInfo` row reconciles to one or more allocation rows
before districts are mapped through `data/model_inputs/Balancing_areas.txt`. Spelling variants
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
statewide hourly demand shape. This construction simultaneously:

1. matches the workbook's relative annual-energy share;
2. matches its relative peak share; and
3. preserves total Tamil Nadu demand at every hour.

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

`data/model_inputs/hydro_asset_registry.csv` classifies capacity-workbook units while
`data/model_inputs/hydro_fleet_reconciliation.csv` records capacity discrepancies and source-only
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

## Renewable profiles

`data/model_inputs/renewable_profiles.csv` contains 8,760 hourly rows and
separate wind and solar capacity factors for each of the nine balancing areas.
The current area profiles correspond to the `Wind` and `Solar` sheets in
`data/source/wind_solar_profiles_TN-9BAs-2025-26-1h.xlsx`. See the
[Input data guide](docs/input-data.md#wind-and-solar-availability) for the
column layout and the source workbook's five-hour boundary wrap. Regenerate or
check the prepared CSV with `scripts/prepare_renewable_profiles.py` before
rebuilding the model. The builder uses statewide template shapes as a fallback
only if the prepared CSV is absent.

## Generated files and Git

Generated files under `results/` are reproducible output and ignored by Git:
solved NetCDF networks, rolling checkpoints, CSV summaries, validation
JSON, plots, solver logs, and infeasibility files. Notebook cell outputs are
also cleared before commit. Keep durable, shareable study outputs in a release
or an external data archive rather than force-adding them to the repository.
`results/README.md` is the tracked exception.

Solar generators carry a small 1 currency-unit/MWh marginal-cost tie-breaker;
wind remains at zero marginal cost. When otherwise equivalent renewable
output must be curtailed, this makes the optimizer prefer curtailing solar.
Other system constraints can still determine dispatch when the resources are
not interchangeable.

## Important limitations

The grid is a transport model because only corridor capacities were supplied;
it does not enforce AC Kirchhoff voltage laws or calculate reactive power.
Capacity ranges are approximate and losses are zero. Geographically
unspecified plant allocation and synthetic area demand should be replaced
when stronger spatial data are available.
The provisional July-2026 capacity overlay must likewise be replaced when a
district-level commissioning register becomes available.
The full-year mixed-integer problem is large; use a shorter-horizon runner for
exploratory unit-commitment runs unless a full-year solve is intentional.
