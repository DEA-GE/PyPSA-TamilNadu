# PyPSA Tamil Nadu

This repository contains a single-node PyPSA unit-commitment model for Tamil
Nadu in financial year (FY) 2025-26. The primary input dataset spans all 8,760
hours from 1 April 2025 through 31 March 2026. A reproducible 35,040-snapshot,
15-minute alternative is also provided. Separate notebooks select the
Monday-Sunday week containing the annual demand peak and optimize either 168
hourly or 672 quarter-hour snapshots with HiGHS.

The model is an exploratory resource-adequacy and dispatch dataset. Demand is
workbook-sourced. Generation technologies and capacities use only operational
records from the supplied Tamil Nadu ICED plant workbook; annual energy limits
and technical assumptions remain based on the CEA Tamil Nadu resource-adequacy
report. Every operational workbook row is represented separately, although
some source rows are themselves district or fleet aggregates. Renewable
availability, operating costs, and several commitment parameters are
documented assumptions rather than validated operational data.

## Repository contents

| Path | Purpose |
|---|---|
| `inputs/tamil_nadu_ra_2025_26/` | Full-year PyPSA CSV-folder input dataset |
| `inputs/tamil_nadu_ra_2025_26_15min/` | Full-year 15-minute alternative generated from the hourly model and quarter-hour demand |
| `additional_data/TamilNadu_ICED_all_source_1782910007272.xlsx.xlsx` | Unit-level source for operational technologies and capacities |
| `additional_data/Tamilnadu_Yearly Demand Profile_2025__Hourly_Demand_Met_in_MW__equal__hourly-to-15min.csv` | Tamil Nadu-only equal-allocation quarter-hour demand source for April-December 2025 |
| `additional_data/Tamilnadu_Yearly Demand Profile_2026__Hourly_Demand_Met_in_MW__equal__hourly-to-15min.csv` | Tamil Nadu-only equal-allocation quarter-hour demand source for January-March 2026 |
| `scripts/build_unit_level_model.py` | Rebuilds record-level PyPSA components from the operational workbook rows |
| `scripts/build_15min_model.py` | Rebuilds the 15-minute alternative and merges April-December 2025 with January-March 2026 demand |
| `scripts/merge_fiscal_year_timeseries.py` | Merges two timestamped calendar-year CSV/Excel files into an automatically detected April-March fiscal year |
| `inputs/tamil_nadu_ra_2025_26/component_metadata.csv` | Links every modeled plant component to its source workbook row |
| `inputs/tamil_nadu_ra_2025_26/DATA_DOCUMENTATION.md` | Data provenance, field definitions, assumptions, and limitations |
| `run_peakday_2025_26.ipynb` | Selects and optimizes the annual peak-demand week |
| `run_peakweek_2025_26_15min.ipynb` | Selects and optimizes the same peak week at 15-minute resolution |
| `tamil_nadu_2025_26.nc` | Committed, solved 168-hour network exported by the run notebook |
| `tamil_nadu_2025_26_15min.nc` | Solved 672-snapshot 15-minute peak-week network |
| `results_analysis.ipynb` | Analyzes 15-minute dispatch, storage, coal commitment, evening ramps, and unit ramp-limit utilization |
| `additional_data/Tamilnadu_Yearly Demand Profile_2025.xlsx` | Tamil Nadu-only source demand workbook for April-December 2025 |
| `additional_data/Tamilnadu_Yearly Demand Profile_2026.xlsx` | Tamil Nadu-only source demand workbook for January-March 2026 |
| `results/` | Legacy two-bus example CSV outputs; not produced by the current Tamil Nadu notebooks |

## Environment

There is currently no checked-in dependency lock file. The CSV export records
PyPSA 1.2.4; the remaining Python dependencies are not pinned by the
repository. A compatible environment can be created with:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install pypsa==1.2.4 highspy pandas openpyxl matplotlib jupyterlab
```

## Run the model

Start Jupyter from the repository root:

```powershell
jupyter lab
```

### Hourly peak week

Run `run_peakday_2025_26.ipynb` to load the full-year hourly CSV dataset,
identify the annual peak at 16:00 on 11 July 2025, solve 7-13 July 2025, and
overwrite `tamil_nadu_2025_26.nc`.

### 15-minute peak week

1. Run `python scripts/build_15min_model.py` to merge April-December 2025 with
   January-March 2026 demand and rebuild the full-year 15-minute CSV folder.
2. Run `run_peakweek_2025_26_15min.ipynb` to optimize the 672-snapshot peak
   week and export `tamil_nadu_2025_26_15min.nc`.
3. Run `results_analysis.ipynb` to inspect dispatch, storage, commitment, the
   evening net-load ramp, dispatchable response, and binding unit ramp limits.

The 15-minute mixed-integer problem has four times as many snapshots as the
hourly peak-week solve and can take substantially longer.

To regenerate the plant components after changing the source workbook or
technology assumptions, run `python scripts/build_unit_level_model.py` first.
Then regenerate the 15-minute alternative with:

```powershell
python scripts/build_15min_model.py
```

To merge any two consecutive calendar-year time-series files without manually
specifying their years or fiscal-year boundaries, run:

```powershell
python scripts/merge_fiscal_year_timeseries.py path\to\series_2025.csv path\to\series_2026.csv
```

Each input must contain a `timestamp` column and represent exactly one calendar
year. Input order does not matter: the utility reads the years from the
timestamps, keeps rows from 1 April of the earlier year through 31 March of the
later year, and writes `FY2025-2026_timeseries.csv` beside the first input by
default. Use `--output` only when a different destination is needed.

The hourly NetCDF contains 168 snapshots and an optimal HiGHS solution. The
15-minute NetCDF contains 672 snapshots. Both solved peak weeks have zero
unserved energy and close the single-node power balance to numerical precision.
Re-running can produce solver warnings described in the data documentation.

To load the full-year inputs or either solved peak week directly:

```python
import pypsa

full_year_inputs = pypsa.Network("inputs/tamil_nadu_ra_2025_26")
full_year_15min_inputs = pypsa.Network("inputs/tamil_nadu_ra_2025_26_15min")
solved_peak_week = pypsa.Network("tamil_nadu_2025_26.nc")
solved_peak_week_15min = pypsa.Network("tamil_nadu_2025_26_15min.nc")
```

## Interpretation limits

The peak-week notebook is not a full-year resource-adequacy study. In
particular, annual hydro and market-import energy limits remain at their
full-year values after the snapshot subset is taken, and pumped-storage state
of charge is cyclic over the selected week. The model also omits reserves,
forced outages, internal transmission constraints, and reliability metrics.

The current 15-minute demand and renewable profiles are not measured
quarter-hour series. They are constant within each hour and change only at the
hour boundary. The demand builder converts the equal quarter-hour energy shares
back to average MW, while 0.25-hour snapshot weights preserve annual energy.
Hourly renewable availability is repeated across four snapshots. Consequently,
an entire hourly change can appear in one 15-minute model step. The flexibility
results are therefore a conservative step-change stress test, not an empirical
assessment of intra-hour variability.

In the solved peak week, the largest modeled evening net-load increase is
4,252 MW in one 15-minute step. The system serves it without unserved energy,
but 47 thermal units start and 84 thermal units operate at or above 95% of their
applicable upward-ramp allowance in that interval. See `results_analysis.ipynb`
for the carrier response, ramp plots, and binding-unit tables.

See [the detailed data documentation](inputs/tamil_nadu_ra_2025_26/DATA_DOCUMENTATION.md)
before using the inputs or results. A standalone browser version is available
at [DATA_DOCUMENTATION.html](inputs/tamil_nadu_ra_2025_26/DATA_DOCUMENTATION.html).
