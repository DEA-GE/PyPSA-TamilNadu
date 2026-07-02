# PyPSA Tamil Nadu

This repository contains a single-node PyPSA unit-commitment model for Tamil
Nadu in financial year (FY) 2025-26. The input dataset spans all 8,760 hours
from 1 April 2025 through 31 March 2026. The supplied solve notebook selects
the Monday-Sunday week containing the annual demand peak and optimizes those
168 hours with HiGHS.

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
| `additional_data/TamilNadu_ICED_all_source_1782910007272.xlsx.xlsx` | Unit-level source for operational technologies and capacities |
| `scripts/build_unit_level_model.py` | Rebuilds record-level PyPSA components from the operational workbook rows |
| `inputs/tamil_nadu_ra_2025_26/component_metadata.csv` | Links every modeled plant component to its source workbook row |
| `inputs/tamil_nadu_ra_2025_26/DATA_DOCUMENTATION.md` | Data provenance, field definitions, assumptions, and limitations |
| `run_peakday_2025_26.ipynb` | Selects and optimizes the annual peak-demand week |
| `tamil_nadu_2025_26.nc` | Committed, solved 168-hour network exported by the run notebook |
| `results_analysis.ipynb` | Plots dispatch, storage operation, state of charge, and coal commitment |
| `Tamilnadu_Telangana_Yearly Demand Profile_2025.xlsx` | Source demand workbook for April-December 2025 |
| `Tamilnadu_Telangana_Yearly Demand Profile_2026.xlsx` | Source demand workbook for January-March 2026 |
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

Then run the notebooks in this order:

1. `run_peakday_2025_26.ipynb` loads the full-year CSV dataset, identifies the
   annual peak at 16:00 on 11 July 2025, solves 7-13 July 2025, and overwrites
   `tamil_nadu_2025_26.nc`.
2. `results_analysis.ipynb` loads that NetCDF file and visualizes the solution.

To regenerate the plant components after changing the source workbook or
technology assumptions, run `python scripts/build_unit_level_model.py` first.

The committed NetCDF contains 168 snapshots and an optimal HiGHS solution. It
has zero unserved energy and closes the single-node power balance to numerical
precision. Re-running can produce solver warnings described in the data
documentation.

To load either representation directly:

```python
import pypsa

full_year_inputs = pypsa.Network("inputs/tamil_nadu_ra_2025_26")
solved_peak_week = pypsa.Network("tamil_nadu_2025_26.nc")
```

## Interpretation limits

The peak-week notebook is not a full-year resource-adequacy study. In
particular, annual hydro and market-import energy limits remain at their
full-year values after the snapshot subset is taken, and pumped-storage state
of charge is cyclic over the selected week. The model also omits reserves,
forced outages, internal transmission constraints, and reliability metrics.

See [the detailed data documentation](inputs/tamil_nadu_ra_2025_26/DATA_DOCUMENTATION.md)
before using the inputs or results. A standalone browser version is available
at [DATA_DOCUMENTATION.html](inputs/tamil_nadu_ra_2025_26/DATA_DOCUMENTATION.html).
