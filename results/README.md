# Generated results

This directory is the common destination for solved networks, rolling-run
checkpoints, validation tables, plots, logs, and diagnostic exports.

Its generated contents are intentionally ignored by Git. Recreate them with
the runners in `scripts/` or the notebooks in `notebooks/`. Do not force-add
large results to the repository; publish important study outputs separately
with a release or external data archive.

| Path | Written by |
| --- | --- |
| `run_results/<mode_label>/<run_name>/` | `scripts/run_model.py`. The mode label may include a date or period and ends with `_UC` (full unit commitment), `_relaxed` or `_LP` (none). Selected-week runs add `week_YYYY-MM-DD/` folders and rolling runs `weekly_networks/` |
| `run_results/console_logs/` | Console output of each run |
| `scenarios/` | The Snakemake scenario workflow (`Snakefile`, `config/workflow.yaml`): one folder per scenario, plus `comparison/` and `effects/` |
| `tamil_nadu_9ba_2025_26.nc`, `analysis/` | The peak-week notebook and its default analysis output |
| `weekly_days/`, `monthly/` | `scripts/run_9ba_weekly_days_2025_26.py` and `scripts/run_9ba_month.py` |
| `re_profile_validation/` | `scripts/validate_re_profiles.py` |

Standardized analyses of named runs are written inside the run folder as:

```text
analysis/
├── analysis_manifest.json
├── tables/
└── figures/
```

`scripts/build_results_report.py` writes them, with `results_report.html`, at
the end of every run; `notebooks/analyze_peak_week.ipynb`,
`notebooks/analyze_rolling_year.ipynb` and `scripts/analyze_results.py` read
or rebuild them. The [README](../README.md#run-modes-and-results) describes
the run modes and their outputs.
