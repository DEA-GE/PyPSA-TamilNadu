"""Analyze a finished run and write a self-contained HTML results report.

``run_model.py`` calls this automatically at the end of a run. It can also be
run on any existing run folder:

    python scripts/build_results_report.py results/run_results/<mode_label>/<run_name>

Steps:
1. Where the run folder holds solved networks (``solved_network.nc``, rolling
   ``weekly_networks/`` checkpoints, or one ``week_*/solved_network.nc`` per
   selected week), run the standardized ``results_analysis`` pipeline and
   export its tables and figures to ``analysis/`` next to those networks.
2. Combine those outputs with the runner's own files (``validation.json``,
   generation and capacity comparisons, comparison plot, rolling window log,
   resolved ``run_config.yaml``) into ``results_report.html`` in the run folder.
   Figures are embedded, so the single HTML file can be shared on its own.

Monthly runs keep no solved networks, so their report covers the runner's
outputs only.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

REPORT_FILENAME = "results_report.html"

# Detailed-analysis tables shown inline; every other table is linked as CSV.
INLINE_TABLES = (
    ("system_metrics", "System metrics"),
    ("generation_by_carrier", "Generation by carrier"),
    ("generation_comparison", "Generation versus observed (detailed)"),
    ("monthly_generation", "Monthly generation"),
    ("storage_metrics", "Storage units"),
    ("balancing_area_metrics", "Balancing areas"),
    ("operating_costs", "Operating costs"),
    ("price_metrics", "Marginal prices"),
    ("market_values", "Market values"),
    ("commitment_by_carrier", "Unit commitment by carrier"),
    ("chronology_validation", "Chronology checks"),
    ("topology_validation", "Topology checks"),
)
FIGURE_TITLES = {
    "energy_balance": "System electricity balance",
    "generation_comparison": "Generation versus observed",
    "monthly_generation": "Monthly generation",
    "resource_seasonality": "Resource seasonality",
    "storage": "Storage units and reservoirs",
    "transmission": "Transmission flows",
    "ramps": "Net-load ramps",
    "prices": "Marginal prices",
    "costs": "Operating costs and market values",
}
MAX_INLINE_ROWS = 200


@dataclass
class DetailedAnalysis:
    label: str
    result_dir: Path
    export_dir: Path | None = None
    run_type: str = ""
    period: str = ""
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    figure_files: dict[str, Path] = field(default_factory=dict)
    error: str | None = None


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def analysis_targets(result_dir: Path) -> list[Path]:
    """Folders under a run directory that ``load_analysis_run`` can open."""
    if (result_dir / "solved_network.nc").exists() or (result_dir / "weekly_networks").is_dir():
        return [result_dir]
    return sorted(
        path
        for path in result_dir.glob("week_*")
        if path.is_dir() and (path / "solved_network.nc").exists()
    )


def _load_observed(compare_observed: bool) -> dict[str, pd.DataFrame | None]:
    keys = ("observed_daily", "observed_monthly", "observed_annual", "observed_capacity")
    if not compare_observed:
        return dict.fromkeys(keys)
    from results_analysis import (
        load_observed_annual_generation,
        load_observed_capacity,
        load_observed_generation,
        load_observed_monthly_generation,
    )

    loaders = (
        load_observed_generation,
        load_observed_monthly_generation,
        load_observed_annual_generation,
        load_observed_capacity,
    )
    observed: dict[str, pd.DataFrame | None] = {}
    for key, loader in zip(keys, loaders):
        try:
            observed[key] = loader()
        except Exception as error:  # observed data are optional context
            print(f"Observed data unavailable ({loader.__name__}): {error}", flush=True)
            observed[key] = None
    return observed


def run_detailed_analysis(
    target: Path, result_dir: Path, observed: dict[str, pd.DataFrame | None]
) -> DetailedAnalysis:
    from results_analysis import analyze_run, export_analysis, load_analysis_run

    label = "Full run" if target == result_dir else target.name.replace("_", " ").capitalize()
    detail = DetailedAnalysis(label=label, result_dir=target)
    try:
        print(f"Analyzing {target} ...", flush=True)
        run = load_analysis_run(target)
        bundle = analyze_run(run, **observed)
        detail.export_dir = export_analysis(run, bundle)
        detail.run_type = run.run_type
        detail.period = f"{run.snapshots[0]:%Y-%m-%d %H:%M} to {run.snapshots[-1]:%Y-%m-%d %H:%M}"
        detail.tables = bundle.tables
        detail.figure_files = {
            name: detail.export_dir / "figures" / f"{name}.png" for name in bundle.figures
        }
    except Exception:
        detail.error = traceback.format_exc()
        print(f"Detailed analysis failed for {target}:\n{detail.error}", flush=True)
    finally:
        plt.close("all")
    return detail


# --------------------------------------------------------------------------- #
# Runner outputs
# --------------------------------------------------------------------------- #
def _read_csv(path: Path, **kwargs: Any) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, **kwargs)
    except Exception as error:
        print(f"Could not read {path}: {error}", flush=True)
        return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not read {path}: {error}", flush=True)
        return {}


def rolling_window_summary(window_log: pd.DataFrame) -> pd.DataFrame:
    """Condense a rolling ``window_log.csv`` into solve-performance figures."""
    rows: list[tuple[str, Any]] = [("Windows in log", len(window_log))]
    if "termination_condition" in window_log:
        conditions = window_log["termination_condition"].astype(str)
        rows.append(("Windows not optimal", int(conditions.ne("optimal").sum())))
    if "accepted_solution" in window_log:
        accepted = window_log["accepted_solution"].astype(str).str.casefold().eq("true")
        rows.append(("Windows not accepted", int((~accepted).sum())))
    if "solve_attempts" in window_log:
        attempts = pd.to_numeric(window_log["solve_attempts"], errors="coerce")
        rows.append(("Windows needing a retry", int(attempts.gt(1).sum())))
    if "runtime_seconds" in window_log:
        runtime = pd.to_numeric(window_log["runtime_seconds"], errors="coerce")
        rows += [
            ("Solver time, total", _format_duration(runtime.sum())),
            ("Solver time per window, median", _format_duration(runtime.median())),
            ("Solver time per window, max", _format_duration(runtime.max())),
        ]
        if "window" in window_log and runtime.notna().any():
            rows.append(("Slowest window", window_log.loc[runtime.idxmax(), "window"]))
    if "mip_gap" in window_log:
        gap = pd.to_numeric(window_log["mip_gap"], errors="coerce")
        if gap.notna().any():
            rows.append(("MIP gap, max", f"{gap.max():.2%}"))
    # Pre-format so counts stay integers instead of being upcast to floats.
    return pd.DataFrame(
        [(metric, _format_number(value)) for metric, value in rows],
        columns=["Metric", "Value"],
    )


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    return html.escape(str(value))


def _format_number(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float):
        if pd.isna(value):
            return "—"
        if abs(value) < 1e-6:  # solver round-off, e.g. 1.8e-12
            return "0.00"
        if abs(value) < 0.01:
            return f"{value:.2g}"
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _format_duration(seconds: Any) -> str:
    seconds = float(seconds)
    if pd.isna(seconds):
        return "—"
    if seconds < 120:
        return f"{seconds:,.0f} s"
    if seconds < 2 * 3600:
        return f"{seconds / 60.0:,.1f} min"
    return f"{seconds / 3600.0:,.2f} h"


def _table_html(frame: pd.DataFrame | None, *, max_rows: int = MAX_INLINE_ROWS) -> str:
    if frame is None or frame.empty:
        return '<p class="muted">No rows.</p>'
    note = ""
    if len(frame) > max_rows:
        note = f'<p class="muted">First {max_rows:,} of {len(frame):,} rows; see the CSV for all.</p>'
        frame = frame.head(max_rows)
    if not isinstance(frame.index, pd.RangeIndex):
        frame = frame.reset_index()  # one header row instead of a separate index-name row
    body = frame.to_html(
        classes="data",
        border=0,
        index=False,
        na_rep="—",
        float_format=_format_number,
        escape=True,
    )
    return f'<div class="table-wrap">{body}</div>{note}'


def _mapping_table(values: dict[str, Any]) -> str:
    if not values:
        return '<p class="muted">Not available.</p>'
    rows = "".join(
        f"<tr><th>{_esc(key)}</th><td>{_esc(_format_number(value))}</td></tr>"
        for key, value in values.items()
    )
    return f'<div class="table-wrap"><table class="data kv">{rows}</table></div>'


def _figure_html(path: Path, title: str) -> str:
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        f'<figure><img alt="{_esc(title)}" src="data:image/png;base64,{encoded}">'
        f"<figcaption>{_esc(title)}</figcaption></figure>"
    )


def _tile(label: str, value: str, tone: str = "") -> str:
    return (
        f'<div class="tile {tone}"><div class="tile-label">{_esc(label)}</div>'
        f'<div class="tile-value">{_esc(value)}</div></div>'
    )


def _link(path: Path, base: Path) -> str:
    try:
        relative = path.relative_to(base).as_posix()
    except ValueError:
        relative = path.as_uri()
    return f'<a href="{_esc(relative)}">{_esc(relative)}</a>'


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _status_tiles(
    validation: dict[str, Any], details: list[DetailedAnalysis]
) -> str:
    tiles = []
    statuses_ok = validation.get("all_solver_statuses_ok")
    complete = validation.get("complete", True)
    if statuses_ok is not None:
        ok = bool(statuses_ok) and bool(complete)
        text = "OK" if ok else ("Incomplete" if not complete else "Check solver log")
        tiles.append(_tile("Solver status", text, "good" if ok else "warn"))
    if "windows_planned" in validation:
        tiles.append(
            _tile(
                "Rolling windows",
                f"{validation.get('windows_completed', '?')} / {validation['windows_planned']}",
            )
        )
    unserved = validation.get("total_unserved_gwh")
    if unserved is not None:
        tiles.append(
            _tile(
                "Unserved energy",
                f"{float(unserved):,.3f} GWh",
                "good" if float(unserved) <= 1e-6 else "warn",
            )
        )
    for detail in details:
        metrics = detail.tables.get("system_metrics")
        if metrics is None or metrics.empty:
            continue
        for metric, label, unit in (
            ("demand_energy", "Demand", "GWh"),
            ("peak_demand", "Peak demand", "MW"),
            ("renewable_generation", "Solar + wind", "GWh"),
        ):
            if metric in metrics.index:
                prefix = "" if len(details) == 1 else f"{detail.label}: "
                tiles.append(
                    _tile(f"{prefix}{label}", f"{metrics.at[metric, 'value']:,.0f} {unit}")
                )
        if len(details) > 1:
            break
    runtime = validation.get("solver_runtime_seconds_sum")
    if runtime is not None:
        tiles.append(_tile("Solver time", _format_duration(runtime)))
    wall = validation.get("wall_clock_seconds") or validation.get(
        "wall_clock_seconds_this_session"
    )
    if wall is not None:
        tiles.append(_tile("Wall clock", _format_duration(wall)))
    return f'<div class="tiles">{"".join(tiles)}</div>' if tiles else ""


def _generation_summary(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if frame is None:
        return None
    frame = frame.set_index(frame.columns[0])
    keep = [
        column
        for column in ("modeled_gwh", "observed_gwh", "difference_gwh", "difference_pct", "observed_source")
        if column in frame
    ]
    return frame[keep].rename(
        columns={
            "modeled_gwh": "Model (GWh)",
            "observed_gwh": "Observed (GWh)",
            "difference_gwh": "Difference (GWh)",
            "difference_pct": "Difference (%)",
            "observed_source": "Observed source",
        }
    )


def _capacity_summary(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if frame is None:
        return None
    frame = frame.set_index(frame.columns[0])
    keep = [
        column
        for column in ("modeled_capacity_mw", "historical_capacity_mw", "difference_mw", "difference_pct", "modeled_share_pct")
        if column in frame
    ]
    return frame[keep].rename(
        columns={
            "modeled_capacity_mw": "Model (MW)",
            "historical_capacity_mw": "Historical (MW)",
            "difference_mw": "Difference (MW)",
            "difference_pct": "Difference (%)",
            "modeled_share_pct": "Share of model (%)",
        }
    )


def _detail_section(detail: DetailedAnalysis, result_dir: Path, collapsed: bool) -> str:
    parts = [f"<h3>{_esc(detail.label)}</h3>"]
    if detail.error:
        parts.append(
            '<p class="warn-text">Detailed analysis failed; the runner outputs above are '
            "unaffected.</p>"
            f"<details><summary>Error</summary><pre>{_esc(detail.error)}</pre></details>"
        )
        return "".join(parts)
    parts.append(
        f'<p class="muted">Run type <code>{_esc(detail.run_type)}</code> · {_esc(detail.period)}</p>'
    )
    figures = "".join(
        _figure_html(path, FIGURE_TITLES.get(name, name.replace("_", " ").capitalize()))
        for name, path in detail.figure_files.items()
    )
    if figures:
        parts.append(f'<div class="figures">{figures}</div>')
    for name, title in INLINE_TABLES:
        if name in detail.tables:
            open_attr = "" if collapsed or name != "system_metrics" else " open"
            parts.append(
                f"<details{open_attr}><summary>{_esc(title)}</summary>"
                f"{_table_html(detail.tables[name])}</details>"
            )
    if detail.export_dir is not None:
        table_dir = detail.export_dir / "tables"
        links = "".join(
            f"<li>{_link(path, result_dir)}</li>" for path in sorted(table_dir.glob("*.csv"))
        )
        parts.append(
            f"<details><summary>All exported tables (CSV)</summary><ul>{links}</ul></details>"
        )
    return "".join(parts)


def _profile_section(result_dir: Path) -> str:
    """Summary and log links from the run's ``profile/`` folder, if logging was on."""
    profile_dir = result_dir / "profile"
    if not profile_dir.is_dir():
        return ""
    parts = ["<h2>Run logging and profile</h2>"]
    status = _read_json(profile_dir / "run_status.json")
    if status.get("status"):
        tone = "" if status["status"] == "completed" else ' class="warn-text"'
        parts.append(f"<p{tone}>Profiled run status: <code>{_esc(status['status'])}</code></p>")
    summary = profile_dir / "summary.txt"
    if summary.exists():
        parts.append(
            "<details open><summary>Profile summary</summary>"
            f"<pre>{_esc(summary.read_text(encoding='utf-8', errors='replace'))}</pre></details>"
        )
    links = [
        path
        for path in (
            profile_dir / "run.log",
            profile_dir / "solves.csv",
            profile_dir / "resources.csv",
            profile_dir / "summary.json",
        )
        if path.exists()
    ]
    highs_logs = sorted((profile_dir / "highs").glob("*.log"))
    items = "".join(f"<li>{_link(path, result_dir)}</li>" for path in links)
    if highs_logs:
        items += (
            f"<li>{_link(profile_dir / 'highs', result_dir)}/ "
            f"({len(highs_logs):,} HiGHS solver logs)</li>"
        )
    if items:
        parts.append(f"<ul>{items}</ul>")
    return "".join(parts)


CSS = """
:root {
  --bg: #f7f7f5; --surface: #ffffff; --text: #1d1d1b; --muted: #6b6b66;
  --border: #e2e2dc; --accent: #1f6feb; --good: #1a7f37; --warn: #b35900;
  --good-bg: #e8f5ec; --warn-bg: #fdf0e1; --code-bg: #f0f0ec;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161615; --surface: #1f1f1d; --text: #ececea; --muted: #a2a29c;
    --border: #34342f; --accent: #6ea8fe; --good: #5cc27a; --warn: #f0a050;
    --good-bg: #1c2e21; --warn-bg: #33261a; --code-bg: #2a2a27;
  }
}
:root[data-theme="dark"] {
  --bg: #161615; --surface: #1f1f1d; --text: #ececea; --muted: #a2a29c;
  --border: #34342f; --accent: #6ea8fe; --good: #5cc27a; --warn: #f0a050;
  --good-bg: #1c2e21; --warn-bg: #33261a; --code-bg: #2a2a27;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 1.7rem; margin: 0 0 4px; }
h2 { font-size: 1.2rem; margin: 40px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
h3 { font-size: 1.05rem; margin: 24px 0 8px; }
a { color: var(--accent); }
code, pre { background: var(--code-bg); border-radius: 4px; font-size: 0.85em; }
code { padding: 1px 5px; }
pre { padding: 12px; overflow-x: auto; white-space: pre; }
.muted { color: var(--muted); }
.warn-text { color: var(--warn); }
.meta { color: var(--muted); margin: 0 0 24px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.tile.good { background: var(--good-bg); border-color: transparent; }
.tile.warn { background: var(--warn-bg); border-color: transparent; }
.tile-label { color: var(--muted); font-size: 0.8rem; }
.tile-value { font-size: 1.25rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile.good .tile-value { color: var(--good); }
.tile.warn .tile-value { color: var(--warn); }
.table-wrap { overflow-x: auto; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; margin: 8px 0; }
table.data { border-collapse: collapse; width: 100%; font-size: 0.85rem; font-variant-numeric: tabular-nums; }
table.data th, table.data td { padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: right; white-space: nowrap; }
table.data th:first-child, table.data td:first-child, table.data tbody th { text-align: left; }
table.data thead th { color: var(--muted); font-weight: 600; }
table.data tr:last-child td, table.data tr:last-child th { border-bottom: 0; }
table.kv td { text-align: left; white-space: normal; word-break: break-word; }
details { margin: 8px 0; }
summary { cursor: pointer; font-weight: 600; padding: 4px 0; }
.figures { display: grid; grid-template-columns: 1fr; gap: 16px; }
figure { margin: 0; background: #ffffff; border: 1px solid var(--border); border-radius: 8px; padding: 8px; }
figure img { width: 100%; height: auto; display: block; }
figcaption { color: #555550; font-size: 0.85rem; padding: 6px 4px 0; }
ul { padding-left: 20px; }
"""


def render_report(
    result_dir: Path,
    details: list[DetailedAnalysis],
    config_text: str | None = None,
) -> str:
    validation = _read_json(result_dir / "validation.json")
    run_name = validation.get("run_name", result_dir.name)
    mode = validation.get("run_mode", "unknown")
    unit_commitment = validation.get("unit_commitment", "unknown")
    period = f"{validation.get('start_date', '?')} to {validation.get('end_date', '?')}"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    commit = _git_commit()

    sections = [
        f"<h1>9-BA model results: {_esc(run_name)}</h1>",
        '<p class="meta">'
        f"Mode <code>{_esc(mode)}</code> · unit commitment <code>{_esc(unit_commitment)}</code>"
        f" · {_esc(period)} · generated {_esc(generated)}"
        + (f" · commit <code>{_esc(commit)}</code>" if commit else "")
        + f"<br>Run folder: <code>{_esc(result_dir)}</code></p>",
        _status_tiles(validation, details),
    ]

    sections.append("<h2>Generation versus observed</h2>")
    if validation.get("complete") is False:
        sections.append(
            '<p class="warn-text">This run is incomplete, so the model totals below cover '
            "only the solved windows while the observed totals cover the full period. The "
            "differences are not like-for-like; see the detailed analysis for the solved "
            "period.</p>"
        )
    sections.append(
        _table_html(_generation_summary(_read_csv(result_dir / "generation_comparison_summary.csv")))
    )
    sections.append(
        _figure_html(
            result_dir / "daily_generation_comparison.png", "Daily generation: model versus observed"
        )
    )

    capacity = _capacity_summary(_read_csv(result_dir / "installed_capacity_comparison.csv"))
    if capacity is not None:
        sections.append("<h2>Installed capacity</h2>")
        sections.append(_table_html(capacity))

    window_log = _read_csv(result_dir / "window_log.csv")
    if window_log is None:
        window_log = _read_csv(result_dir / "window_log.partial.csv")
    if window_log is not None and not window_log.empty:
        sections.append("<h2>Rolling solve performance</h2>")
        sections.append(_table_html(rolling_window_summary(window_log)))

    sections.append("<h2>Detailed analysis</h2>")
    if details:
        collapsed = len(details) > 1
        sections.extend(_detail_section(detail, result_dir, collapsed) for detail in details)
    else:
        sections.append(
            '<p class="muted">This run kept no solved networks (for example a monthly run), '
            "so only the runner outputs above are available.</p>"
        )

    sections.append(_profile_section(result_dir))

    sections.append("<h2>Run record</h2>")
    sections.append(
        f"<details><summary>Validation ({len(validation)} values)</summary>"
        f"{_mapping_table(validation)}</details>"
    )
    if config_text:
        sections.append(
            f"<details><summary>Run configuration</summary><pre>{_esc(config_text)}</pre></details>"
        )
    files = "".join(
        f"<li>{_link(path, result_dir)}</li>"
        for path in sorted(result_dir.iterdir())
        if path.is_file() and path.name != REPORT_FILENAME
    )
    sections.append(f"<details><summary>Files in the run folder</summary><ul>{files}</ul></details>")

    title = f"{run_name} results"
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n<style>{CSS}</style>\n</head>\n"
        f"<body><main>{''.join(sections)}</main></body>\n</html>\n"
    )


def build_report(
    result_dir: str | Path,
    *,
    compare_observed: bool = True,
    detailed: bool = True,
    config_text: str | None = None,
) -> Path:
    """Analyze ``result_dir`` and write ``results_report.html`` into it."""
    result_dir = Path(result_dir).expanduser().resolve()
    if not result_dir.is_dir():
        raise FileNotFoundError(f"Run folder does not exist: {result_dir}")
    if config_text is None:
        config_file = result_dir / "run_config.yaml"
        if config_file.exists():
            config_text = config_file.read_text(encoding="utf-8")

    details: list[DetailedAnalysis] = []
    if detailed:
        targets = analysis_targets(result_dir)
        if targets:
            observed = _load_observed(compare_observed)
            details = [run_detailed_analysis(target, result_dir, observed) for target in targets]

    report_path = result_dir / REPORT_FILENAME
    report_path.write_text(render_report(result_dir, details, config_text), encoding="utf-8")
    print(f"Results report: {report_path}", flush=True)
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("result_dir", type=Path, help="Run folder under results/run_results/")
    parser.add_argument(
        "--no-observed", action="store_true", help="Skip comparisons with observed data"
    )
    parser.add_argument(
        "--runner-outputs-only",
        action="store_true",
        help="Skip loading solved networks; report the runner's files only (fast)",
    )
    args = parser.parse_args(argv)
    build_report(
        args.result_dir,
        compare_observed=not args.no_observed,
        detailed=not args.runner_outputs_only,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
