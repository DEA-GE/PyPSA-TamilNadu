"""Report what a scenario changes against the baseline.

    python scripts/workflow/scenario_effects.py <baseline summary.yaml> <scenario summary.yaml> <output folder>

The summaries are the files ``run_model.py --summary`` wrote for the two
operational runs. The output folder receives one CSV per table, the figures
(``figures/*.png``) and ``effects_report.html``. See
``scripts/results_analysis/scenario_effects.py`` for the measures.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from build_results_report import CSS, _esc, _figure_html, _table_html, _tile  # noqa: E402
from results_analysis.loaders import load_analysis_run  # noqa: E402
from results_analysis.scenario_effects import compare_scenario  # noqa: E402

TABLE_TITLES = {
    "offshore_summary": "Offshore wind: output and curtailment",
    "offshore_displacement": "Offshore wind: what each MWh replaces",
    "offshore_value": "Offshore wind: value, cost and emissions",
    "offshore_corridors": "Offshore wind: corridors at the connection",
    "offshore_monthly": "Offshore wind: by month (GWh)",
    "system_change": "System metrics: baseline and scenario",
    "generation_change": "Generation by carrier",
}
FIGURE_TITLES = {
    "offshore_displacement": "What offshore wind changes over the year",
    "offshore_monthly": "Offshore generation and the change it causes, by month",
    "offshore_corridors": "Loading duration of the corridors at the offshore connection",
}


def _run_dir(summary: Path) -> Path:
    return Path(yaml.safe_load(summary.read_text(encoding="utf-8"))["output_dir"])


def _headline_tiles(headline: dict[str, float]) -> str:
    labels = {
        "offshore_capacity_mw": ("Offshore capacity", "{:,.0f} MW"),
        "offshore_generation_gwh": ("Offshore generation", "{:,.0f} GWh"),
        "offshore_curtailment_pct": ("Offshore curtailment", "{:.1f} %"),
        "operating_cost_saving_million": ("Operating-cost saving", "INR {:,.0f} million"),
        "net_benefit_million": ("Net benefit after offshore cost", "INR {:,.0f} million"),
        "co2_avoided_mt": ("CO2 avoided", "{:.2f} Mt"),
    }
    tiles = [
        _tile(label, fmt.format(headline[key]), "warn" if key == "net_benefit_million" and headline[key] < 0 else "")
        for key, (label, fmt) in labels.items()
        if key in headline
    ]
    return f'<div class="tiles">{"".join(tiles)}</div>' if tiles else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("baseline", type=Path, help="run summary of the baseline operational run")
    parser.add_argument("scenario", type=Path, help="run summary of the scenario operational run")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--name", default=None, help="scenario name for the report title")
    args = parser.parse_args(argv)

    baseline_dir, scenario_dir = _run_dir(args.baseline), _run_dir(args.scenario)
    name = args.name or args.scenario.parent.name
    bundle = compare_scenario(load_analysis_run(baseline_dir), load_analysis_run(scenario_dir))

    out = args.output_dir
    (out / "figures").mkdir(parents=True, exist_ok=True)
    for table_name, table in bundle.tables.items():
        table.to_csv(out / f"{table_name}.csv")
    figure_paths = {}
    for figure_name, figure in bundle.figures.items():
        path = out / "figures" / f"{figure_name}.png"
        figure.savefig(path, dpi=150, bbox_inches="tight")
        figure_paths[figure_name] = path

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections = [
        f"<h1>Scenario effects: {_esc(name)} against the baseline</h1>",
        f'<p class="meta">Baseline: <code>{_esc(baseline_dir)}</code><br>'
        f"Scenario: <code>{_esc(scenario_dir)}</code><br>Generated {_esc(generated)}. "
        "CO2 uses the indicative factors in config/emission_factors.yaml.</p>",
        _headline_tiles(bundle.headline),
    ]
    figures = "".join(_figure_html(path, FIGURE_TITLES.get(key, key)) for key, path in figure_paths.items())
    if figures:
        sections.append(f'<div class="figures">{figures}</div>')
    for table_name in TABLE_TITLES:
        if table_name in bundle.tables:
            open_attr = " open" if table_name in {"offshore_summary", "offshore_value", "offshore_displacement"} else ""
            sections.append(
                f"<details{open_attr}><summary>{_esc(TABLE_TITLES[table_name])}</summary>"
                f"{_table_html(bundle.tables[table_name])}</details>"
            )
    html_text = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Scenario effects: {_esc(name)}</title><style>{CSS}</style></head>"
        f"<body><main>{''.join(sections)}</main></body></html>"
    )
    report = out / "effects_report.html"
    report.write_text(html_text, encoding="utf-8")
    print(f"Scenario effects report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
