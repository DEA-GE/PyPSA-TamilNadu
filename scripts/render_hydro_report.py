"""Render the hydro audit's current calculated outputs into a portable report.

Run after analyze_hydro_operation.py.  This renderer intentionally contains no
conclusions or numerical observations tied to a particular historic run.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import zipfile
from pathlib import Path

import markdown
import pandas as pd


def table(frame, names=None, digits=2):
    result = frame.copy()
    if names:
        result = result.rename(columns=names)
    return result.to_markdown(index=False, floatfmt=f",.{digits}f", missingval="—")


def image(name, caption):
    return f"![{caption}](figures/{name})"


def render(out):
    out = out.resolve()
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))

    def read(name):
        return pd.read_csv(out / f"{name}.csv")

    assets = read("asset_annual")
    months = read("fleet_monthly")
    stores = read("reservoir_annual")
    cea_monthly = read("cea_monthly_generation_comparison")
    cea_annual = read("cea_annual_generation_comparison")
    asset_label = lambda value: value.replace("_", " ").title()
    assets["asset"] = assets.asset_id.map(asset_label)
    cea_annual["asset"] = cea_annual.asset_id.map(asset_label)

    sections = [f"""# Hydro operation analysis — current completed run

Run: `{metrics['run']}`. This report is generated from the selected completed run and its current input files. It contains no fixed observations or conclusions from earlier runs; rerunning the analysis refreshes every table, chart and summary below.

## Scope and outputs

The audit reconstructs operation from the retained weekly checkpoints, retains general fleet, storage, utilization and inflow diagnostics, and records their source hashes in `source_manifest.json`. CEA monthly generation is now a station/system-level validation dataset, separate from the statewide daily generation comparison.

| Output | Use |
|---|---|
| [fleet_monthly.csv](fleet_monthly.csv) | Model versus statewide observed hydro generation by month |
| [cea_monthly_generation_comparison.csv](cea_monthly_generation_comparison.csv) | Model plant-group output versus mapped CEA monthly system generation |
| [cea_annual_generation_comparison.csv](cea_annual_generation_comparison.csv) | Totals of the monthly CEA comparison; not an independent annual source |
| [asset_annual.csv](asset_annual.csv) / [asset_weekly_benchmarks.csv](asset_weekly_benchmarks.csv) | Modelled plant-group generation, capacity and water/storage diagnostics |
| [reservoir_daily.csv](reservoir_daily.csv) / [reservoir_annual.csv](reservoir_annual.csv) | Water-balance and reservoir-storage diagnostics |
| [source_manifest.json](source_manifest.json) | Hashes of checkpoints and inputs used for this report |

## Fleet generation

{image('fleet_weekly.png', 'Weekly model and statewide observed hydro generation')}

The statewide comparison is a fleet-level diagnostic. It is useful for timing and aggregate energy, but it cannot identify which hydro system caused a mismatch. Use the CEA comparison below for systems with an explicit station mapping.

""" + table(months[["month", "modeled_gwh", "observed_gwh", "difference_gwh", "difference_pct"]], {
        "month": "Month", "modeled_gwh": "Model GWh", "observed_gwh": "Statewide observed GWh",
        "difference_gwh": "Model − observed GWh", "difference_pct": "Difference %"}), f"""## CEA monthly generation comparison

{image('cea_monthly_generation_comparison.png', 'Monthly model versus CEA generation by mapped hydro system')}

The CEA values are monthly generation observations from the saved FY2025–26 CEA reports. A model group is compared only where its aggregation is explicitly defined in the analysis script. For example, a grouped cascade can be compared to the sum of its listed CEA stations. Systems without a reliable mapping are omitted rather than assigned by inference. Generation differences are model minus CEA.

""" + table(cea_annual[["asset", "cea_systems", "months_compared", "model_generation_gwh", "cea_generation_gwh", "difference_gwh", "difference_pct"]], {
        "asset": "Model group", "cea_systems": "CEA system(s)", "months_compared": "Months",
        "model_generation_gwh": "Model GWh", "cea_generation_gwh": "CEA GWh",
        "difference_gwh": "Model − CEA GWh", "difference_pct": "Difference %"}) + """

Monthly rows, report filenames and the station mapping are retained in `cea_monthly_generation_comparison.csv`, so a discrepancy can be traced back to the underlying CEA report.

## Plant operation and water diagnostics

""" + image("asset_weekly_utilization.png", "Weekly plant-group utilization") + "\n\n" + table(assets[["asset", "capacity_mw", "generation_gwh", "capacity_factor_pct", "weeks_operating"]], {
        "asset": "Model group", "capacity_mw": "MW", "generation_gwh": "Model GWh",
        "capacity_factor_pct": "Capacity factor %", "weeks_operating": "Active weeks"}) + "\n\n" + image("reservoir_storage.png", "Reservoir storage diagnostic") + "\n\n" + image("inflow_accounting.png", "Inflow accounting diagnostic") + """

Interpret these charts together. A model-versus-CEA generation difference may reflect water availability, initial or terminal storage, routing and downstream outlets, head/efficiency assumptions, capacity or availability, outage representation, or an aggregation mismatch. Generation data alone do not establish which cause applies.

""" + table(stores[["store", "benchmark_kind", "inflow_available_mwh", "inflow_used_mwh", "inflow_discarded_mwh", "storage_mae_pp"]], {
        "store": "Store", "benchmark_kind": "Benchmark", "inflow_available_mwh": "Available inflow MWh-eq",
        "inflow_used_mwh": "Admitted inflow MWh-eq", "inflow_discarded_mwh": "Unadmitted inflow MWh-eq",
        "storage_mae_pp": "Storage MAE pp"}) + """

## Reading and updating the analysis

- Re-run `analyze_hydro_operation.py --run <completed-run-directory>` to refresh diagnostics, CEA comparisons and figures.
- Then run `render_hydro_report.py <analysis-directory>` to rebuild this Markdown, HTML and ZIP report.
- CEA is observed electrical generation, not measured water volume. Use it to locate and quantify generation mismatch; test water-model explanations against the reservoir and mass-balance outputs.
- Do not sum shared reservoir-flow fields across linked plant rows. Water may be routed through several stations, whereas electrical generation can be summed by station.

## Data notes

`difference_gwh` is model minus observation. CEA comparison percentages are blank where an observed monthly value is zero. Reservoir MWh values are the model's water-energy equivalent, not independently measured recoverable electricity. The explicit model-to-CEA mapping is the controlling definition for the comparison; it is intentionally conservative.
"""]

    report = "\n\n".join(sections)
    (out / "hydro_analysis.md").write_text(report, encoding="utf-8")
    body = markdown.markdown(report, extensions=["tables", "toc"])

    def embed(match):
        path = out / match.group(1)
        return 'src="data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode() + '"'

    body = re.sub(r'src="(figures/[^\"]+\.png)"', embed, body)
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Tamil Nadu hydro analysis</title>
<style>body{font:16px/1.6 system-ui,sans-serif;color:#203342;background:#f0f4f6;margin:0}main{max-width:1180px;margin:auto;padding:48px;background:#fff}h1{color:#163e62}h2{color:#163e62;margin-top:42px;border-bottom:2px solid #d3e5ea;padding-bottom:8px}table{font-size:13px;border-collapse:collapse;display:block;overflow:auto;width:100%}th{background:#163e62;color:#fff;text-align:left}td,th{padding:8px 10px;border-bottom:1px solid #dde4e8}tr:nth-child(even){background:#f3f7f9}img{display:block;max-width:100%;height:auto;margin:24px auto}code{background:#edf2f5;padding:2px 4px;overflow-wrap:anywhere}@media(max-width:700px){main{padding:22px}}@media print{body{background:#fff}main{padding:0;max-width:none}table{display:table;font-size:9px}}</style></head><body><main>""" + body + "</main></body></html>"
    (out / "hydro_analysis.html").write_text(html, encoding="utf-8")
    dictionary = """# Data dictionary

- `cea_monthly_generation_comparison.csv`: month, model group, explicit CEA station/system mapping, model generation, CEA observed generation, difference, percentage difference and source report filename(s).
- `cea_annual_generation_comparison.csv`: sums of the monthly CEA comparison by model group; it is not an additional independent annual observation.
- `model_generation_gwh` and `cea_generation_gwh`: electrical energy; 1 GWh = 1 MU.
- `difference_gwh`: model minus observation. `difference_pct` uses observed generation as denominator and is blank for zero observations.
- `cea_systems`: all CEA systems summed to match the model group. Mappings are defined in `scripts/analyze_hydro_operation.py`.
- Reservoir `*_mwh` fields are model water-energy equivalents; do not treat them as measured electrical energy or sum repeated shared water fields over linked plants.
"""
    (out / "DATA_DICTIONARY.md").write_text(dictionary, encoding="utf-8")
    archive = out / "hydro_analysis_and_data.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for file in sorted(out.rglob("*")):
            if file.is_file() and file != archive:
                z.write(file, file.relative_to(out))
    print(f"Report: {out / 'hydro_analysis.html'}")
    print(f"Data archive: {archive}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Analysis directory emitted by analyze_hydro_operation.py")
    render(parser.parse_args().output)
