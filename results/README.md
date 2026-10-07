# Generated results

This directory is the common destination for solved networks, rolling-run
checkpoints, validation tables, plots, logs, and diagnostic exports.

Its generated contents are intentionally ignored by Git. Recreate them with
the notebooks in `notebooks/` or the runners in `scripts/`. Do not force-add
large results to the repository; publish important study outputs separately
with a release or external data archive.

The routine notebook writes to `results/run_results/<mode_label>/<run_name>/`.
Selected-week runs add `week_YYYY-MM-DD/` folders inside that named run, and
rolling runs add `weekly_networks/`. The mode label may include a date or
period and, when applicable, a `_relaxed_uc` or `_none_uc` suffix. The dedicated
peak-week notebook instead writes `results/tamil_nadu_9ba_2025_26.nc`.

Standardized analyses of named runs are written inside the run folder as:

```text
analysis/
├── analysis_manifest.json
├── tables/
└── figures/
```

Use `notebooks/analyze_peak_week.ipynb`,
`notebooks/analyze_rolling_year.ipynb`, or `scripts/analyze_results.py`.
For the dedicated peak-week `.nc` file, the default analysis output is
`results/analysis/`.
