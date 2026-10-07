# Generated results

This directory is the common destination for solved networks, rolling-run
checkpoints, validation tables, plots, logs, and diagnostic exports.

Its generated contents are intentionally ignored by Git. Recreate them with
the notebooks in `notebooks/` or the runners in `scripts/`. Do not force-add
large results to the repository; publish important study outputs separately
with a release or external data archive.

Standardized analyses are written beside each run as:

```text
analysis/
├── analysis_manifest.json
├── tables/
└── figures/
```

Use `notebooks/analyze_peak_week.ipynb`,
`notebooks/analyze_rolling_year.ipynb`, or `scripts/analyze_results.py`.
