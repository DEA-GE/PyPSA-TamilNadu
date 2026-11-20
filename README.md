# Tamil Nadu nine-balancing-area model

This repository contains the hourly, nine-balancing-area PyPSA
unit-commitment model for Tamil Nadu. It is the repository's only supported
model. The ready-to-load PyPSA CSV folder is `model/`. See [How to run](#how-to-run)
for environment setup, rebuilding, and run configuration. To load the network
programmatically:

```python
import pypsa

network = pypsa.Network("model")
```

For the location, table structure, units, provenance, and limitations of each
input, see [Input data guide](docs/input-data.md). How hourly solar
availability is derived and validated is described in the
[Solar availability methodology](docs/solar-availability-methodology.md).

## Repository layout

| Path | Purpose |
|---|---|
| `model/` | Ready-to-load 8,760-hour PyPSA CSV network |
| `data/model_inputs/` | Spatial mappings, grid capacities, hydro registries, demand allocation, and renewable-profile inputs |
| `data/build_template/` | Build-stage statewide time series and component tables; not a supported standalone model |
| `data/source/` | Source workbooks and supporting datasets |
| `data/reservoirs/` | Validated reservoir observations used by the hydro model |
| `scripts/` | Build, run, validation, analysis, and data-collection utilities |
| `config/` | YAML run configurations: `defaults.yaml`, one file per run in `runs/`, one file per scenario in `scenarios/`, technology parameters in `technologies.yaml`, and the Snakemake settings in `workflow.yaml` |
| `Snakefile` | Scenario workflow: capacity expansion, operational run and comparison for every scenario |
| `notebooks/` | Interactive analysis workflows |
| `results/` | Local generated outputs; ignored by Git except for its README |

## How to run

Run these commands from the repository root. The example uses PowerShell; on
macOS or Linux, activate the virtual environment with `source .venv/bin/activate`.
If `python` is not on your path, use the Python executable from your environment.

1. With Python 3.11 or newer, create an environment and install the packages
   used to build, solve, and analyze the model:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Use the checked-in `model/` network, or rebuild it after changing inputs:

   ```powershell
   python scripts/build_9ba_model.py
   ```

3. For a first solve, run the one-day example config. It solves 11 July 2025
   with relaxed unit commitment on one thread and finishes in under a minute:

   ```powershell
   python scripts/run_model.py config/runs/daily_demo.yaml
   ```

4. When the solve finishes, the runner builds a results report and prints its
   location. With the settings above, the run folder is
   `results/run_results/daily_2025-07-11_relaxed/daily_demo/`. Open
   `results_report.html` there. It is a single self-contained file, with
   figures embedded, that you can share. The folder also holds
   `validation.json`, the CSV comparisons, `solved_network.nc`, the
   standardized `analysis/` tables and figures, and `run_config.yaml`, which
   records the exact settings used.

The loaded `model/` network carries no energy targets, so
`network.optimize(snapshots=network.snapshots[:24])` is feasible but is not
calibrated to observed generation. Use `scripts/run_model.py` for calibrated
runs: every mode applies the observed oil-and-gas and nuclear targets for each
solved day, and the bio-power and small-hydro targets over the solved period
(`scripts/period_energy_targets.py`).

### Run configuration

Runs are configured with YAML files. `config/defaults.yaml` holds every
setting with its default and a comment. A run config in `config/runs/` is
merged on top of it, so it only lists what differs. Unknown sections or keys
are rejected before the network is loaded, so a typo fails immediately.

| Config | Run |
| --- | --- |
| `config/runs/daily_demo.yaml` | One relaxed day on one thread; use it for a first run |
| `config/runs/rolling_relaxed.yaml` | Full-year rolling run, relaxed unit commitment |
| `config/runs/rolling_full_uc.yaml` | Full-year rolling run, binary unit commitment |
| `config/runs/selected_weeks_relaxed.yaml` | Four independent seasonal test weeks |
| `config/runs/annual_lp.yaml` | All 8,760 hours as one no-unit-commitment LP |

For a new study, copy one of these files, give `run.name` a new value, and
change the settings you need. A rolling relaxed run config looks like this:

```yaml
run:
  name: relaxed_lp
  mode: rolling
  resume: true

formulation:
  unit_commitment: relaxed

rolling:
  time_limit: Default
  max_time_limit_mip_gap: 0.10
```

Useful command-line options:

```powershell
# Check the merged settings without solving
python scripts/run_model.py config/runs/rolling_relaxed.yaml --dry-run

# Override single settings without editing the file
python scripts/run_model.py config/runs/rolling_relaxed.yaml --set run.name=relaxed_test --set rolling.max_windows=2

# Solve only; skip the results analysis and report
python scripts/run_model.py config/runs/rolling_relaxed.yaml --no-analysis

# Turn detailed logging and profiling off (or on) for this run only
python scripts/run_model.py config/runs/rolling_relaxed.yaml --no-profile

# Repeat an earlier run exactly from its saved settings
python scripts/run_model.py results/run_results/daily_2025-07-11_relaxed/daily_demo/run_config.yaml

# Also write a short YAML summary (run folder, report, status), as the Snakemake workflow does
python scripts/run_model.py config/runs/daily_demo.yaml --summary daily_demo_summary.yaml
```

`run_model.py` exits with 0 on success, with 1 if the config is invalid or the
solve fails, and with 2 if the solve succeeded but the results analysis failed.
In that last case the run outputs are kept, and you can rebuild the report
later. Unless `logging.console_log` is `false`, Python console output is also
written to `results/run_results/console_logs/<run name>_<UTC time>.log`. HiGHS
writes its solver output directly to the terminal, so it is not in that file;
with `logging.profile: true` the complete HiGHS log of every solve is kept in
the run's `profile/highs/` folder instead.

| Setting | How to set it |
| --- | --- |
| `run.mode` | Choose `daily`, `monthly`, `peak`, `selected_weeks`, `annual`, or `rolling`. The modes and their output behavior are described below. |
| `run.name` | Required. Give each study a distinct output folder name. It must start with a letter or number, have at most 80 characters, and contain only letters, numbers, `.`, `_`, or `-`. Reusing a name can reuse checkpoints or overwrite outputs in that folder. |
| `run.reference_date` | Use a date in `YYYY-MM-DD` format within 1 April 2025–31 March 2026. `daily` runs that date; `monthly` runs every day in its month. It is ignored for the other modes. |
| `run.resume` | `true` reuses compatible completed selected-week solves or rolling checkpoints; `false` solves them again. It has no effect on daily, monthly, peak, or annual runs. Use a new `run.name` when changing study assumptions. |
| `selected_weeks.week_starts` | For `selected_weeks` only, list one or more first days of seven-day test periods as `YYYY-MM-DD` dates. Periods must not overlap; the seven days plus warm-up and look-ahead must fit inside the model year. The dates need not be Mondays. |
| `selected_weeks.warmup_days`, `selected_weeks.lookahead_days` | For `selected_weeks` only, set non-negative whole numbers of extra days before and after each retained week. They affect the solve but are excluded from reported comparisons. The rolling mode always uses its own seven-day accepted window plus one look-ahead day. |
| `selected_weeks.reservoir_initial_soc_fraction` | For `selected_weeks` only, choose a value from 0 to 1 for initial reservoir state of charge where no observed or documented proxy level is available. The default is `0.5`; test sensitivity when the fallback matters. |
| `formulation.unit_commitment` | `full` uses binary on/off decisions; `relaxed` uses continuous on/off fractions and is faster but approximate; `none` removes commitment decisions and minimum stable output from formerly committable generators. `annual` requires `none`. Result folders are categorised by this setting: `_UC` (`full`), `_relaxed`, or `_LP` (`none`). |
| `solver.name` | Use `highs`, the solver used by the repository's run workflows. The thread and MIP settings below configure HiGHS. |
| `solver.threads` | Leave as `Default` to use the runner's defaults: 8 for `full`, `relaxed` and the `annual` LP, 3 for other `none` (LP) runs. Set an integer from 1 to 8 to override the default. `full` uses HiGHS MIP parallelism; `none` uses the parallel dual simplex when more than one thread is selected. `relaxed` uses HiGHS' interior-point solver with crossover, which is single-threaded and was about 4× faster than the parallel dual simplex on a relaxed week with the same optimum; its threads are used by the dual-simplex retry. |
| `solver.lp_method`, `solver.crossover` | For `annual` only. `lp_method: Default` solves the full-year LP with HiGHS' interior-point method (`ipm`); `simplex` selects the dual simplex, which is far slower at this size (about 2 million rows after presolve). `crossover: false` skips the final crossover to a basic solution: faster, with the same capacities and dispatch, and duals (prices) of interior-point accuracy. |
| `solver.parallel_workers` | Leave as `Default` to use 2 workers for independent daily, monthly, or selected-week solves. Set a positive integer to override it. Peak, annual, and rolling runs use one effective worker regardless of this setting. Active workers × effective solver threads cannot exceed the logical CPUs visible to Python. |
| `solver.mip_rel_gap` | Target relative optimality gap for `full` mixed-integer solves; `0.01` means 1%. It is ignored by `relaxed` and `none`. |
| `rolling.time_limit` | For `rolling` only. `Default` sets a per-attempt limit by unit commitment: 300 s for `none`, 700 s for `relaxed` (about 10× a normal coal-only relaxed week), 1,800 s for `full`. A positive number of seconds overrides it; `null` removes the limit. LP windows must still reach optimality. If a window fails, it is retried automatically: `relaxed` retries with the dual simplex; `none` and `full` retry with a different HiGHS random seed and then the serial solver. Each window's `solve_attempts` and `solve_attempt_log` record what happened. `null` disables retries, because a stalled solve never returns. It does not apply to `annual`. |
| `rolling.max_time_limit_mip_gap` | For a time-limited `rolling` run with `full` unit commitment, set the largest acceptable reported MIP gap from 0 to 1. `0.10` allows a window stopped at its time limit to be accepted only if its gap is at most 10%. This is separate from `solver.mip_rel_gap`. |
| `rolling.max_windows` | For testing only: stop a rolling run after this many windows. Leave as `null` for a full year. |
| `paths.network`, `paths.results_root` | Input network and output root. Relative paths are resolved from the repository root. |
| `analysis.enabled`, `analysis.compare_observed` | Build the results report after the run, and include comparisons with observed generation and capacity. Both default to `true`. |
| `logging.profile` | `true` (the default) turns on detailed run logging and profiling: the run writes the `profile/` folder described in [Run logging and profiling](#run-logging-and-profiling), including the complete HiGHS log of every solve. `false` runs without it. `--profile` / `--no-profile` on the command line override it. |
| `logging.level` | `INFO` (the default), `DEBUG`, `WARNING`, or `ERROR`. `DEBUG` adds the PyPSA and Linopy debug messages to `profile/run.log`. Used only when `logging.profile` is `true`. |
| `logging.sample_interval` | Seconds between resource samples in `profile/resources.csv`; default `1.0`. |
| `logging.console_log` | `true` (the default) copies Python console output to `results/run_results/console_logs/<run name>_<UTC time>.log`. |
| `technologies.*` | Batteries and offshore wind added for a scenario; empty by default, so runs use the model as it is. See [Scenarios](#scenarios-batteries-and-offshore-wind). |

### Run logging and profiling

With `logging.profile: true` (the default), every run writes a `profile/`
folder inside its output directory. It records the solver logs and where
wall-clock time, CPU time, and memory go, so run-time improvements can be
targeted and measured. The runner prints a short summary at the end of the
run, and the results report includes it. To run without it:

```powershell
python scripts/run_model.py config/runs/rolling_relaxed.yaml --no-profile
```

| File | Contents |
| --- | --- |
| `run.log` | Timestamped runner, PyPSA, and Linopy messages, and one line for every phase longer than 1 s |
| `events.jsonl` | One record per phase (for example `run_rolling/window/optimize/build_model`): wall and CPU seconds, average cores, memory at the end, sampled peak memory, and the window, week, day, or solve attempt |
| `solves.jsonl`, `solves.csv` | One row per solver attempt. The time is split into PyPSA model build, custom constraints, LP-file write, HiGHS model read, HiGHS solve, remaining Linopy transfer, and solution and dual assignment. Each row also has the model size before and after presolve, the largest constraint groups, iteration counts, and MIP statistics (nodes, gap, time to the first incumbent, LP iterations by source) |
| `highs/*.log` | The complete HiGHS log of every attempt |
| `resources.csv` | Resource timeline, by default every second: process memory, CPU cores in use, threads, system CPU, available memory, and the active phase |
| `environment.json`, `run_status.json` | Package versions, hardware, Git revision, and run settings; final status, any error, totals, and peak memory |
| `summary.txt`, `summary.json` | Phase totals and shares of the run, the solve-time breakdown, and the slowest solves |

Rolling runs also add the accepted attempt's breakdown to each window's
`profile_*` columns in `window_log.csv`. A failed or interrupted run still
writes its profile. A repeated run keeps the earlier profile in
`profile_history/`. To rebuild one summary, or to compare runs side by side:

```powershell
python scripts/summarize_run_profile.py results/run_results/<label>/<run_name>
python scripts/summarize_run_profile.py <run A folder> <run B folder>
```

### Results analysis and report

At the end of a run, `run_model.py` calls `scripts/build_results_report.py`.
That script:

1. runs the standardized `scripts/results_analysis/` pipeline on every solved
   network the run kept (`solved_network.nc`, rolling `weekly_networks/`
   checkpoints, or one network per selected week), and writes its tables and
   figures to `analysis/` next to them;
2. writes `results_report.html` in the run folder. The report contains status
   tiles (solver status, rolling progress, unserved energy, demand, solver
   time), generation and installed capacity compared with observed values, the
   daily comparison plot, rolling solve performance from `window_log.csv`, the
   detailed analysis figures and tables, the full `validation.json`, and the
   resolved run configuration.

The analysis is calculated with PyPSA's `n.statistics` at the electricity
buses, so the water-energy layer (reservoir stores, natural inflows, spill)
counts only where it reaches the grid through the hydro turbines. The report's
figures are drawn from these statistics, with daily means for long periods.

A rolling run is stored as one network per committed week. The analysis
stitches them into one network covering the whole run
(`scripts/results_analysis/stitching.py`): static data come from the first
week, which also holds the run's initial conditions, and every time series is
joined in time. Each week's start and initial storage are kept in
`network.meta["stitched"]` for the rolling-boundary audit; the per-week
objective values and energy-target constraints are dropped, so the stitched
network is for analysis only, not for solving. It is saved as
`stitched_network.nc` in the run folder and reused while the weekly files are
unchanged (checked by name, size and modification time), so later analyses
load one file instead of every week. To work with the whole year directly:

```python
from results_analysis import load_analysis_run

run = load_analysis_run("results/run_results/rolling_2025-04-01_2026-03-31_relaxed/rolling_relaxed")
n = run.network  # 8,760 snapshots
n.statistics.supply(bus_carrier="AC", nice_names=False)
n.statistics.supply.plot.bar(bus_carrier="AC")  # PyPSA's own plots work too
```

The loader gives the carriers the report's names and colours (in memory
only), which PyPSA's plots need.

| Output | Definition |
| --- | --- |
| Generation by carrier | `supply` from generators, storage discharge and the grid side of hydro turbines |
| Installed capacity | `installed_capacity`; turbines at their grid-side rating. The comparison's `total` and model shares exclude the unserved-energy slack generators |
| Balancing areas | Each bus's `energy_balance` split into generation, storage, net imports (Lines and AC/DC Links, with losses) and demand; the residual is zero |
| Corridors | `transmission` on every Line and AC/DC Link; loading is relative to the operating limit (`s_nom * s_max_pu` or `p_nom * p_max_pu`), so 100% means the limit binds |
| Storage units and stores | Discharge and charge from `supply` and `withdrawal` |
| Operating costs | `opex` by carrier and cost type (marginal, start-up, shut-down, …) in million INR; `transmission` is the interstate-link charge. Model penalties are listed last and totalled separately from operating cost: unserved energy, `water_balance_adjustment` (the slack that makes reservoirs match measured storage) and the spill tie-break |
| Marginal prices | `prices` per bus: time- and load-weighted means, percentiles, and hours at the unserved-energy price |
| Market values | Revenue from supplied energy (`revenue`, output direction) divided by supplied energy, and its ratio to the load-weighted average price; slack generators are left out |
| System supply and demand (figure) | `energy_balance` summed over buses: supply stacked by carrier, with demand as a line; the gap between them is storage charging, pumping and transmission losses. Daily means for periods over a month |

Marginal prices come from the solver's duals. Binary unit-commitment (`full`)
solves have none, so for those runs the price table, market values and price
figure are empty, and the system metrics mark the average price as not
available. Relaxed and LP runs have prices; with relaxed commitment they
reflect the continuous on/off approximation.

Monthly runs keep no solved networks, so their report covers the runner's own
outputs only. To rebuild a report for any existing run folder, for example
after an interrupted rolling run:

```powershell
python scripts/build_results_report.py results/run_results/rolling_2025-04-01_2026-03-31_relaxed/rolling_relaxed
```

Add `--runner-outputs-only` to skip loading the solved networks (fast), or
`--no-observed` to leave out observed-data comparisons.

### Run modes and results

| Mode | Period solved | State between periods |
| --- | --- | --- |
| `daily` | The 24 hours in `REFERENCE_DATE` | One independent day. |
| `monthly` | Each day in the month of `REFERENCE_DATE`, solved separately | Commitment and storage state do not carry between days. |
| `peak` | The continuous Monday–Sunday week containing the annual maximum hourly demand | One 168-hour solve. |
| `selected_weeks` | Each seven-day period in `WEEK_STARTS`, with configured warm-up and look-ahead | Each period is independent; observed reservoir levels constrain its boundaries. |
| `annual` | All 8,760 hours in one continuous no-unit-commitment LP | No rolling boundaries or look-ahead. Daily and build-defined weekly operating targets remain constraints inside the one model. Requires `formulation.unit_commitment: none`. |
| `rolling` | All 8,760 hours in consecutive windows; seven days accepted and one day used for look-ahead | Commitment, dispatch history, and storage state carry between windows. |

Every mode enforces exact daily oil-and-gas and nuclear generation targets and
writes comparison tables, validation, and a plot. Daily and peak runs also save
a solved network in their run folder; selected weeks save one network per week,
and rolling runs save retained checkpoints. Annual and short continuous runs save
one solved network. Output goes under
`results/run_results/<mode_label>/<run_name>/`. The mode label may include a
date or period and ends with the unit-commitment category: `_UC` for `full`,
`_relaxed` for `relaxed`, and `_LP` for `none`.
`run_model.py` prints the exact path after each run. No mode introduces equipment
faults or outages.

For rolling runs, `window_log.partial.csv` is updated after each completed
window and `window_log.csv` is written at the end. Each row includes wall time
for model construction and solving, process CPU seconds, average CPU cores used
(CPU seconds divided by wall seconds), and sampled peak process memory and
thread count. Peak values are sampled every 0.5 seconds, so short spikes may
be missed. The thread count covers the whole Python process and may include
library threads in addition to HiGHS threads. The same metrics are stored in
each window's checkpoint JSON. Resumed checkpoints created before these
metrics were added will have empty values for the new columns.

Resumed rolling windows and selected weeks are reused only if they were
solved with the same input network, checked with a content fingerprint of the
network including any scenario technologies. Checkpoints written before this
check existed are solved again once.

### Scenarios: batteries and offshore wind

Scenarios add technologies to the model and are run with
[Snakemake](https://snakemake.readthedocs.io/). Each scenario is one config file
in `config/scenarios/`; add a scenario by adding a file there.

| File | Scenario |
| --- | --- |
| `baseline.yaml` | The model as it is |
| `battery.yaml` | Baseline plus 2-hour lithium-ion batteries, capacity optimised in all nine balancing areas |
| `offshore_wind.yaml` | Baseline plus offshore wind (IEA 15 MW turbines) in Tirunelveli |
| `battery_offshore_wind.yaml` | Baseline plus both |

A scenario config is a run config (any section of `config/defaults.yaml`)
plus a `technologies` section. All four use the same operational settings as
`config/runs/rolling_relaxed.yaml`, so their results compare like for like.
The offshore-wind capacity settings are examples to replace with the
project data. Each site's capacity is either fixed or optimised:

```yaml
technologies:
  battery:                       # site name -> settings; bus defaults to the site name
    Chennai North:
      capacity_mw: 500           # installed capacity, MW
    Tirunelveli:
      capacity_mw: optimise      # chosen by the model
      min_mw: 0                  # optional bounds
      max_mw: 2000
  offshore_wind:
    block2:
      bus: Tirunelveli           # the only balancing area offshore wind can connect to
      profile: block2            # column of the profile file; defaults to the site name
      capacity_mw: 1020          # fixed scenario choice, whole 15 MW turbines (68 here)
  totals:                        # optional: total optimised MW per technology
    battery: {max_mw: 1500}      # min_mw = max_mw sets an exact target, placed optimally
  parameters:                    # optional: this scenario's changes to config/technologies.yaml
    battery: {max_hours: 2}
```

Technology parameters are in `config/technologies.yaml`: battery duration
(`max_hours`), charging and discharging efficiencies, standing loss, costs,
and for offshore wind the turbine, the balancing areas it may connect to
(`allowed_buses`) and the profile file (hourly availability per MW for every
model hour, in Indian Standard Time, one column per profile). Values still to
be provided are `null`; a run that adds a technology with a required value
missing stops before solving and names the missing values. `capital_cost` is
the annualised cost per MW per year in INR and is required only to optimise a
capacity. Offshore wind capacity is an exogenous scenario choice
(`optimisable: false` in `config/technologies.yaml`): each scenario sets it
in whole 15 MW turbines, and `optimise` is rejected. Battery capacity is
optimised by the model.

Batteries are standalone 2-hour lithium-ion (LFP) systems, the technology of
Tamil Nadu's battery tenders: 85 % round-trip efficiency and an annualised
cost of INR 2.3 lakh per MW per month, the benchmark for 2-hour standalone
battery tariffs in India; see
[docs/input-data.md](docs/input-data.md#battery-storage). Offshore wind
costs INR 41.8 million per MW per year including O&M and grid connection
(9.0 INR/kWh), from the Danish Energy Agency's FIMOI cost study for Tamil
Nadu; see [docs/input-data.md](docs/input-data.md#offshore-wind-cost).

Offshore wind uses the IEA 15 MW reference turbine and can be installed only
in Tirunelveli, the southernmost balancing area. Its capacity factors
(`data/model_inputs/offshore_wind_profiles.csv`, column `block2`, mean 0.56)
are built by `scripts/prepare_offshore_wind_profile.py` from the simulated
68-turbine farm in `data/source/ts_power_150m_block2.xlsx`; see
[docs/input-data.md](docs/input-data.md#offshore-wind-capacity-factors).

Batteries are PyPSA storage units with a fixed energy-to-power ratio; offshore
wind farms are generators with an hourly availability profile, connected
directly to a balancing-area bus. Both appear in the results analysis as
`battery` and `offshore_wind`.

Run the scenarios:

```powershell
snakemake --cores 8                                       # every scenario
snakemake --cores 8 --config scenarios=[baseline,battery] # a selection
snakemake --cores 8 -n                                    # list what would run
snakemake --cores 8 --config 'overrides=["rolling.max_windows=2"]'   # quick test
```

For each scenario the workflow:

1. **Capacity expansion** (only if a site is `optimise`): solves the full year
   as one LP without unit commitment (`run.mode: annual`), with the optimised
   capacities as decision variables, and saves the chosen capacities to
   `results/scenarios/<scenario>/optimised_capacities.csv`. Rolling, monthly and
   selected-week runs solve periods separately and could not choose one
   capacity for the year, so capacities are sized in this one continuous LP.
2. **Operation**: runs the scenario's own settings (here the rolling year with
   relaxed unit commitment) with those capacities fixed
   (`technologies.fixed_capacities`). Results and the report are in
   `results/scenarios/<scenario>/<mode label>/operation/`.
3. **Comparison**: writes headline tables of all scenarios side by side to
   `results/scenarios/comparison/`: system metrics, generation and capacity by
   carrier, operating costs, and the capacities each scenario added.
4. **Effects**: for every scenario other than the baseline (when the baseline
   runs too), quantifies what its added technologies change, in
   `results/scenarios/effects/<scenario>/effects_report.html` with the tables as
   CSV. For offshore wind: available, generated and curtailed energy, capacity
   factor, market value and capture ratio; what each MWh of offshore wind
   replaces (by carrier, in net imports into Tamil Nadu, and in onshore
   curtailment); the operating-cost saving against the offshore annualised
   cost, net benefit and break-even cost; avoided CO2 and abatement cost; and
   the loading of the corridors at the offshore connection. CO2 uses the
   indicative factors in `config/emission_factors.yaml` (the model's carriers
   carry none). For batteries: power and energy capacity, charged and
   discharged energy, losses and cycles; the prices at which they charge and
   discharge and the arbitrage revenue, per balancing area too; what each MWh
   discharged changes; the operating-cost saving against the annualised cost,
   net benefit and break-even cost; avoided CO2; the change in system price,
   peak prices and the daily price spread; and an average day of charging,
   discharging and prices. When a scenario adds both, a combined table sets
   the joint savings against the joint cost, and each technology section
   reports the joint change, which cannot be split between them. Run it for
   any two runs with
   `python scripts/workflow/scenario_effects.py <baseline summary> <scenario summary> <output folder>`.

Each run records where it wrote its results in
`results/scenarios/<scenario>/<capacity_expansion|operation>.yaml`. Snakemake
reruns a scenario when its config, `config/defaults.yaml`,
`config/technologies.yaml`, the profile file or the `model/` network changes.
Workflow settings, including extra run settings for every scenario, are in
`config/workflow.yaml`.

A scenario config also runs without Snakemake. A scenario with only fixed
capacities runs directly; one with optimised sites needs the capacity
expansion first:

```powershell
python scripts/run_model.py config/scenarios/battery.yaml --set run.mode=annual --set formulation.unit_commitment=none --set run.name=battery_capacity --summary capacity.yaml
python scripts/workflow/extract_capacities.py capacity.yaml battery_capacities.csv
python scripts/run_model.py config/scenarios/battery.yaml --set technologies.fixed_capacities=battery_capacities.csv
```

### Selected-week seasonal tests

`run.mode: selected_weeks` solves every date in `selected_weeks.week_starts` as
the first day of an independent seven-day test period. Each optimization also
includes `warmup_days` before the retained week and `lookahead_days` after it. Only the
seven test days enter the reported comparison. The complete solved network,
daily tables, validation, and plot for each week are written under
`results/run_results/selected_weeks_UC/<run_name>/week_YYYY-MM-DD/` for full
unit commitment. Combined outputs and `selected_week_log.csv` are written in the
`<run_name>/` folder. For relaxed or no-commitment runs, the mode folder ends in
`_relaxed` or `_LP` instead.
With `run.resume: true`, a completed week is reused when its model version,
solver, MIP gap, warm-up, look-ahead, and reservoir initial-SOC settings match
the current run.

Reservoir state does not transfer across gaps between selected weeks. Each
independent solve instead uses the nearest available observed daily reservoir
storage at the retained period's start and constrains the nearest observed
storage at its end. The configured `reservoir_initial_soc_fraction` is only a
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

For a chronological full-year approximation, run
`python scripts/run_model.py config/runs/rolling_relaxed.yaml` (or another
config with `run.mode: rolling`). Each optimization covers eight days: the first
seven are accepted and exported, while the eighth supplies look-ahead and is
re-optimized in the next window. Generator dispatch and consecutive on/off
history, plus storage state of charge, are passed across weekly boundaries.
Before the first window, committable coal units are online (Tamil Nadu coal
generation averaged about 8.2 GW on 1 April 2025).
Set `formulation.unit_commitment: relaxed` for the continuous linearized formulation.
The relaxed runner carries fractional status, dispatch, and recent fractional
start/shut-down history across weekly boundaries. Equal start-up and shut-down
costs activate PyPSA's additional tightening constraints. HiGHS uses explicit
parallel solver settings appropriate to the selected LP or MIP formulation.
Completed windows are checkpointed under
`results/run_results/rolling_2025-04-01_2026-03-31_relaxed/rolling_relaxed/weekly_networks/`
with `config/runs/rolling_relaxed.yaml`; keep
`run.resume: true` to continue an interrupted run. The final one-day window has
no look-ahead because it reaches the end of the available input horizon.

After the rolling run is complete, its `results_report.html` gives the
standard summary. For interactive exploration, open
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

If peak mode was run with `scripts/run_model.py` instead, its run folder, for
example `results/run_results/peak_2025-07-07_UC/baseline/` when
`formulation.unit_commitment: full` and `run.name: baseline`, already has a
results report and can be passed to either analysis script.

## Model contents

July 2025 can be evaluated with `python scripts/run_9ba_month.py`. The script
solves all 31 complete days as independent 24-hour unit-commitment problems,
covering all 744 July hours at a 1% MIP tolerance. It enforces each day's gas,
nuclear, bio-power and small-hydro equality targets and writes validation results, daily and monthly
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
- The authoritative grid workbook supplies 18 internal AC `Line` corridors and
  24 directional interstate `Link`s. Internal losses are approximated from line
  resistance during optimization; interstate links deliver 97% of sending-end
  power. Ratings and source metadata are retained in
  `model/transmission_capacity_metadata.csv`.
- The model retains all 8,760 hourly snapshots from 1 April 2025 through
  31 March 2026.
- Installed nameplate capacity is updated to the 31 July 2026 comparison
  vintage through `data/model_inputs/additional_capacity_2026_07.csv`. Demand
  and renewable profiles remain FY2025-26, so this is a capacity-vintage
  overlay rather than a historical FY2025-26 fleet snapshot.
- Only coal units are committable. They retain the statewide build template's
  minimum stable output, minimum up/down times and ramp limits. Coal marginal
  costs are station energy charges from 2024+ regulatory orders (TNERC
  FY2023-24 true-up and the JERC Puducherry FY2025-26 tariff order), set per
  component from `data/model_inputs/coal_operating_costs.csv`; they range from
  INR 2,720/MWh (NNTPS) to INR 6,190/MWh (Mettur TPS-II). Coal start-up and
  shut-down costs come from CEA's August 2026 two-shift study of Tuticorin TPS
  (INR 7,376 per MW per stop-start cycle for 210 MW units), scaled to larger
  units with CEA's January 2025 start-up oil norms. See `docs/input-data.md`
  for sources, proxies and limitations; `model/coal_operating_cost_audit.csv`
  lists the applied values.
- Oil and gas, diesel and bio-power units are dispatched without commitment
  decisions (`restrict_unit_commitment` in `scripts/build_9ba_model.py`):
  minimum stable output, start/shut-down costs and minimum up/down times are
  removed, so each unit can dispatch anywhere between zero and its available
  capacity. Their output is still bounded by the observed oil/gas and
  bio-power energy targets, and their marginal costs remain placeholders.
  This removes about two-thirds of the unit-commitment variables and
  constraints.
- Explicitly off-grid capacity is excluded from the modeled fleet. This removes
  the 75.91 MW off-grid solar category and the 27.57 MW off-grid waste-to-energy
  (bio-power) category; the remaining generators are grid-connected or treated
  as part of the modeled grid fleet.
- The 211.70 MW diesel overlay is represented as fourteen units: seven at
  Samayanallur in the Madurai area and seven at Samalpatti in the Vellore
  area. In the absence of plant-specific operating data, these units use the
  model's oil-and-gas cost assumptions while retaining a separate `diesel`
  carrier.
- Conventional hydro is controlled through
  `data/model_inputs/hydro_asset_registry.csv` and the explicit fleet
  reconciliation in `data/model_inputs/hydro_fleet_reconciliation.csv`. Simple
  reservoir plants use non-pumping `StorageUnit`s. PAP, Kodayar, Kundah,
  Pykara/Moyar and Papanasam/Servalar use `Store` water balances and
  turbine/transfer `Link`s so that upstream discharge is routed downstream;
  Kadamparai uses separate pumping and generation Links. Lower Mettur and
  Bhavani Kattalai receive availability from their upstream observed releases.
  The rolling runner initializes observed and labelled proxy storage from the
  first day's SOC, then transfers both `StorageUnit` and `Store` state between
  consecutive chronological windows. The five directly measured water Stores
  match their daily storage readings exactly; their reported inflow is also
  admitted. Any incompatible water balance uses a costly, explicit balancing
  inflow or outflow saved in `measured_water_balance.csv`. Proxy and monthly
  storage targets remain soft, with quality-based bands and penalties. Optional
  monthly stored-energy data are interpolated only at window boundaries; no
  monthly target is imposed when that file is absent.
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
Interstate exchange uses the audited FY2025-26 SRPC REA schedules. Hourly
resolved flows retain their mapped external gateway. Only these confirmed
imports are included in the model; unresolved imports remain in the audit with
zero modeled flow. Hourly unresolved exports are distributed across Andhra
Pradesh, Karnataka, Kerala, and Western Region in proportion to annual resolved
export flow. If a provisional export allocation exceeds remaining gateway
capacity, the excess is redistributed across the other positive-share gateways.
The model keeps `resolved` and `unresolved_distributed` as separate audit
measures. Imports are negative external demand and exports are positive external
demand. These loads fix total exchange at each
external bus, while the grid chooses the split among parallel links; individual
interconnector links are not fixed. See `model/interstate_flow_audit.csv` for
the hourly values, shares, capacity limits, and redistribution flags. The
former dispatchable import-entitlement generators are not included, avoiding
double counting. Unserved energy remains split using demand energy shares and
is not reported as installed plant.

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
(1,241,450 MWh) of Tamil Nadu oil-and-gas generation.

`model/oil_gas_daily_energy_budget.csv` records all 365 observed daily values,
each day's share of the annual total, and its exact MWh target. Every runner
mode applies one equality per solved day across all `oil_gas` generators, so a
full-year solve reproduces the annual total exactly. The targets are not stored
as PyPSA `GlobalConstraint`s: a single fixed constant cannot follow the solved
period. The source reconciliation is in
`model/oil_gas_energy_budget_summary.csv`.

## Nuclear energy targets

Nuclear uses exact daily generation targets from the observed workbook,
enforced for every solved day in all runner modes. Targets are exported in
`model/nuclear_daily_energy_budget.csv`.
The inherited constant 61.2% nuclear output is replaced with a 0–100%
nameplate dispatch range to permit those targets. Hourly output and plant
allocation remain optimized; daily energy matching does not validate their
hourly schedules. Gas and nuclear comparison errors are calibrated by design.
Custom solve workflows must use `scripts/nuclear_energy_targets.py` to apply
the daily targets for their selected dates.

## Bio-power energy targets

The Tamil Nadu Outlook FY2025-26 monthly bio-power generation values are kept
in `data/model_inputs/tamil_nadu_bio_power_monthly_generation_FY2025_26.csv`.
They total 194.01 GWh. Each monthly total is spread uniformly over the hours
of its month. Rolling and selected-week solves impose one exact energy
equality on all `bio_power` generators for each accepted week; daily, monthly
(independent-day) and peak-week solves impose one equality over the solved
period. A week wholly inside one month receives seven days of that month's
total; a week crossing a month boundary receives the corresponding daily share
from both months. Hourly and daily dispatch within the period remain
optimization decisions.

Small hydro uses the same method. Its twelve Tamil Nadu Outlook values are in
`data/model_inputs/tamil_nadu_small_hydro_monthly_generation_FY2025_26.csv`
and total 307.56 GWh. Every runner mode imposes a month-prorated equality
across all `small_hydro` generators over the same periods as bio-power. Because the inherited
placeholder availability profile cannot supply the observed energy, these
solves make small hydro dispatchable between zero and nameplate; the weekly
energy is fixed while its hourly and daily timing remains optimized.

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
efficiency of 0.90 and documented fixed heads. Where an agriculture series
does not correspond directly to a plant, the model either uses a labelled
connected-reservoir filling proxy or neutral endogenous pondage; it does not
copy absolute volume between reservoirs.

The six unobserved Kundah/Pykara/Suruliyar assets additionally have an
explicit *calibration* mode. Their CEA FY2025-26 annual actual generation is
in `data/model_inputs/hydro_cascade_annual_generation_FY2025_26.csv`; the
build distributes those annual totals using the observed Tamil Nadu daily
hydro profile and then aggregates them into the rolling model's seven-day
blocks. The resulting weekly targets are written to
`model/hydro_cascade_generation_targets.csv`. The rolling runner enforces one
energy equality per plant and committed week, leaving hourly and daily
dispatch within the week free. It introduces only the corresponding weekly
inferred source-water allowance needed to serve those totals:
Upper Bhavani for Kundah, Pykara reservoir for Pykara, Glenmorgan forebay for
the Pykara-Ultimate residual, and Suruliyar's own reservoir inflow. These are
calibration requirements, not measured catchment inflows or plant-level
weekly observations.

`model/storage_units-inflow.csv` contains simple-reservoir hourly inflow and
`model/hydro_reservoir_inflow.csv` provides its daily source audit. Inflows to
cascade water buses are represented as `water_inflow` generators. Observed
daily SOC targets are in `model/hydro_observed_soc.csv`. The selected-week
runner applies these as independent start/end boundary conditions. The rolling
runner starts from observed or labelled proxy storage, guides each committed
week's terminal storage toward quality-weighted seasonal targets, and carries
the optimized state across contiguous windows. Its terminal-band violations
are recorded in `window_log.csv`.

Upper Kodayar uses an assumed half-full starting level and a daily water
balance: Pechiparai daily inflow scaled by the published Upper Kodayar to
Pechiparai capacity ratio (98.66/150.22 = 0.6568), less Kodayar-I turbine
water use. Water use is converted with the plant-specific 16.5 cubic feet per
kWh figure.
Upper-reservoir spill is routed directly to the effectively unbounded Kodayar
downstream sink when required to remain within the published FRL capacity. A
small tie-breaker cost prevents gratuitous spill when retaining the water is
feasible; no other upper-reservoir release is represented.
The Pechiparai flow is a labelled estimate, not measured Kodayar inflow or
storage; see `model/upper_kodayar_inflow_estimate.csv` and a rolling run's
`upper_kodayar_balance.csv`.
Kodayar-II discharges to an effectively unbounded terminal sink, so downstream
storage capacity cannot restrict either turbine's generation.
The Kundah/Parsons Valley and Pykara/Moyar cascades likewise discharge into
effectively unbounded terminal river sinks. Their intermediate pondages have
explicit non-generating bypass releases, so documented downstream generation
below upstream generation does not fill an artificial bottleneck.

## Renewable profiles

`data/model_inputs/renewable_profiles.csv` contains 8,760 hourly rows and
separate wind and solar capacity factors for each of the nine balancing areas.
Both come from the LAVA/atlite area profiles (solar `9BA`, wind
`AggrPCWiderCUF`), whose timestamps are UTC. `scripts/prepare_lava_profiles.py`
converts them to the model's hour-beginning IST hours without wrapping the
year. Solar averages the two overlapping hour-ending ERA5 hours; wind takes the
instantaneous value at the middle of each IST hour, a 5-hour shift. The previous
model profiles are archived in
`data/model_inputs/renewable_profiles_before_external_FY2025_26.csv` for the
[comparison notebook](notebooks/compare_external_re_profiles_FY2025_26.ipynb).
See the [Input data guide](docs/input-data.md#wind-and-solar-availability)
for the source and validation details. The builder uses statewide template
shapes as a fallback only if the active CSV is absent.

Solar availability also follows the capacity installed through the year.
The NITI Dashboard's monthly installed capacity (MNRE's monthly series) puts
Tamil Nadu's grid-connected solar at 10,083 MW on 31 March 2025 and 13,505 MW
on 31 March 2026, against the 14,045.55 MW July 2026 fleet in
`generators.csv`. Each hour's solar availability is scaled by the installed
share of that fleet. A seasonal correction follows: soiling and wind cooling
from literature parameters, plus monthly factors fitted on FY2020-21 to
FY2024-25 observed generation. One multiplier (1.1863) then keeps annual solar
potential equal to the observed 19,690.99 GWh. See the [Solar availability methodology](docs/solar-availability-methodology.md)
for the method, validation and limitations, and the
[Input data guide](docs/input-data.md#solar-capacity-through-the-year) for the
files.

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

The grid uses a linearized AC formulation and does not calculate reactive
power. Active-power losses on passive AC lines are approximated from imported
resistance with two linear tangent segments and should be validated against
TNERC's 3.81% TANTRANSCO energy-balance assumption. Directional interstate
links use the CEA planning assumption of 3% losses and TNERC's FY2025-26
short-term transmission charge of 241.81 currency units/MWh. Internal AC-line
flows carry a 1 currency-unit/MWh numerical tie-breaker rather than repeating
the system-level access charge on every line in a multi-line path. Capacity
ranges remain approximate. Geographically
unspecified plant allocation and synthetic area demand should be replaced
when stronger spatial data are available.

The transmission assumptions use the following Indian regulatory sources:

- TNERC's FY2025-26 TANTRANSCO order sets short-term transmission charges at
  Rs 241.81/MW/hour (numerically Rs 241.81/MWh):
  <https://tnerc.tn.gov.in/Orders/files/TO-Intra-Stat010720251711.pdf>.
- TNERC's 2022 TANGEDCO energy balance carries a 3.81% TANTRANSCO transmission
  loss assumption through FY2025-26.
- CEA's *20th Electric Power Survey*, Volume I, section 1.4.6 assumes 3% losses
  on electricity imported through the interstate grid:
  <https://cea.nic.in/wp-content/uploads/ps___lf/2023/02/Volume_I_Report_of_20th_Electric_Power_Survey.pdf>.

The provisional July-2026 capacity overlay must likewise be replaced when a
district-level commissioning register becomes available.
The full-year mixed-integer problem is large; use a shorter-horizon runner for
exploratory unit-commitment runs unless a full-year solve is intentional.
