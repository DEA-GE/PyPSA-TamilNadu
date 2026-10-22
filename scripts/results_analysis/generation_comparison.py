"""Run-layout-independent generation comparisons by reporting period."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import CARRIER_LABELS
from .loaders import (
    load_observed_annual_generation,
    load_observed_generation,
    load_observed_monthly_generation,
)


GENERATION_CARRIERS = (
    "coal",
    "oil_gas",
    "nuclear",
    "hydro",
    "solar",
    "wind",
    "bio_power",
    "small_hydro",
    "other_res",
    "comparable_total",
)
DAILY_COMPARABLE_CARRIERS = (
    "coal",
    "oil_gas",
    "nuclear",
    "hydro",
    "solar",
    "wind",
)
@dataclass(frozen=True)
class GenerationComparison:
    """Tables and provenance for one run's generation comparison."""

    result_dir: Path
    metadata: dict
    modeled_daily: pd.DataFrame
    observed_daily: pd.DataFrame
    period: pd.DataFrame
    monthly: pd.DataFrame
    daily: pd.DataFrame
    coverage: pd.DataFrame
    full_fiscal_year: bool


def _read_daily(path: Path, *, modeled: bool) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required generation result is missing: {path}")
    frame = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    frame.index = pd.DatetimeIndex(frame.index).normalize()
    if frame.index.has_duplicates:
        raise ValueError(f"{path.name} contains duplicate dates")
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{path.name} dates must be chronological")
    frame = frame.apply(pd.to_numeric, errors="coerce")

    if "other_res" not in frame and {"bio_power", "small_hydro"}.issubset(frame):
        frame["other_res"] = frame["bio_power"] + frame["small_hydro"]
    if "comparable_total" not in frame and set(DAILY_COMPARABLE_CARRIERS).issubset(frame):
        frame["comparable_total"] = frame[list(DAILY_COMPARABLE_CARRIERS)].sum(axis=1)

    missing = set(GENERATION_CARRIERS) - set(frame.columns)
    for carrier in missing:
        frame[carrier] = np.nan
    frame = frame.loc[:, GENERATION_CARRIERS]
    if modeled:
        required = DAILY_COMPARABLE_CARRIERS + ("other_res", "comparable_total")
        if frame.loc[:, required].isna().any().any():
            unavailable = [carrier for carrier in required if frame[carrier].isna().any()]
            raise ValueError(
                f"{path.name} is missing required modeled carriers: {unavailable}"
            )
        finite = frame.loc[:, required].to_numpy()
        if not np.isfinite(finite).all():
            raise ValueError(f"{path.name} contains non-finite modeled generation")
    return frame


def _read_daily_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize official daily data to the comparison carrier schema."""
    frame = frame.copy().apply(pd.to_numeric, errors="coerce")
    if "other_res" not in frame and {"bio_power", "small_hydro"}.issubset(frame):
        frame["other_res"] = frame["bio_power"] + frame["small_hydro"]
    if "comparable_total" not in frame:
        frame["comparable_total"] = frame.reindex(
            columns=DAILY_COMPARABLE_CARRIERS + ("other_res",)
        ).sum(axis=1, min_count=1)
    for carrier in set(GENERATION_CARRIERS) - set(frame.columns):
        frame[carrier] = np.nan
    return frame.loc[:, GENERATION_CARRIERS]


def _comparison_rows(
    modeled: pd.Series,
    observed: pd.Series,
    *,
    observed_sources: pd.Series | str,
    extra: dict,
) -> list[dict]:
    rows = []
    for carrier in GENERATION_CARRIERS:
        modeled_value = float(modeled.get(carrier, np.nan))
        observed_value = observed.get(carrier, np.nan)
        observed_value = float(observed_value) if pd.notna(observed_value) else np.nan
        difference = modeled_value - observed_value if pd.notna(observed_value) else np.nan
        source = (
            observed_sources.get(carrier, "not available")
            if isinstance(observed_sources, pd.Series)
            else observed_sources if pd.notna(observed_value) else "not available"
        )
        rows.append(
            {
                **extra,
                "carrier": carrier,
                "modeled_gwh": modeled_value,
                "observed_gwh": observed_value,
                "difference_gwh": difference,
                "difference_pct": (
                    100.0 * difference / observed_value
                    if pd.notna(observed_value) and observed_value != 0.0
                    else np.nan
                ),
                "observed_available": bool(pd.notna(observed_value)),
                "observed_source": source,
            }
        )
    return rows


def analyze_generation_result(result_dir: str | Path) -> GenerationComparison:
    """Aggregate one runner result at period, month, and day resolution.

    The run-level daily CSV files are the canonical input because every run
    mode writes them, including layouts that do not retain solved networks.
    Missing dates are preserved as gaps and are never filled or annualized.
    """
    result_dir = Path(result_dir).expanduser().resolve()
    if not result_dir.is_dir():
        raise FileNotFoundError(f"Result directory does not exist: {result_dir}")
    modeled = _read_daily(result_dir / "modeled_daily_generation.csv", modeled=True)
    # Never use the runner's cached comparison output as a benchmark. Load the
    # official NITI Dashboard daily source directly for every notebook/run.
    observed = load_observed_generation().reindex(modeled.index)
    observed = _read_daily_frame(observed)
    if modeled.empty:
        raise ValueError("The result contains no populated dates")

    metadata_file = result_dir / "validation.json"
    metadata = (
        json.loads(metadata_file.read_text(encoding="utf-8"))
        if metadata_file.exists()
        else {}
    )
    annual_observed = load_observed_annual_generation().copy()
    monthly_observed = load_observed_monthly_generation()
    annual_start = pd.Timestamp(annual_observed["period_start"].iloc[0]).normalize()
    annual_end = pd.Timestamp(annual_observed["period_end"].iloc[0]).normalize()
    fiscal_dates = pd.date_range(annual_start, annual_end, freq="D")
    full_fiscal_year = modeled.index.equals(fiscal_dates)

    modeled_period = modeled.sum(min_count=1)
    if full_fiscal_year:
        modeled_period.loc["comparable_total"] = modeled_period.loc[
            list(DAILY_COMPARABLE_CARRIERS)
        ].sum() + modeled_period.loc["other_res"]
        observed_period = annual_observed["generation_gwh"].reindex(GENERATION_CARRIERS)
        sources = annual_observed["source"].reindex(GENERATION_CARRIERS)
        benchmark_basis = "full-year NITI Dashboard benchmark"
    else:
        observed_period = observed.sum(min_count=1).reindex(GENERATION_CARRIERS)
        sources = pd.Series("NITI Dashboard daily generation", index=GENERATION_CARRIERS)
        sources.loc[observed_period.isna()] = "not available"
        benchmark_basis = "sum of populated daily historical observations"

    populated_days = int(modeled.index.nunique())
    expected_period_days = len(fiscal_dates)
    period_rows = _comparison_rows(
        modeled_period,
        observed_period,
        observed_sources=sources,
        extra={
            "period_start": modeled.index.min(),
            "period_end": modeled.index.max(),
            "populated_days": populated_days,
            "fiscal_year_days": expected_period_days,
            "coverage_pct": 100.0 * populated_days / expected_period_days,
            "full_fiscal_year": full_fiscal_year,
            "benchmark_basis": benchmark_basis,
        },
    )
    period = pd.DataFrame(period_rows).set_index("carrier")

    monthly_rows: list[dict] = []
    month_keys = modeled.index.to_period("M")
    for month in month_keys.unique().sort_values():
        dates = modeled.index[month_keys == month]
        modeled_month = modeled.loc[dates].sum(min_count=1)
        full_month_dates = pd.date_range(month.start_time, month.end_time, freq="D")
        if dates.equals(full_month_dates):
            observed_month = monthly_observed.reindex(columns=GENERATION_CARRIERS).loc[
                month.start_time
            ]
            monthly_source = (
                "NITI Dashboard monthly generation + Tamil Nadu Outlook monthly RES"
            )
        else:
            observed_month = observed.loc[dates].sum(min_count=1)
            monthly_source = "NITI Dashboard daily generation"
        days_in_month = int(month.days_in_month)
        monthly_rows.extend(
            _comparison_rows(
                modeled_month,
                observed_month,
                observed_sources=monthly_source,
                extra={
                    "month": month.to_timestamp(),
                    "populated_days": len(dates),
                    "calendar_days": days_in_month,
                    "coverage_pct": 100.0 * len(dates) / days_in_month,
                    "partial_month": len(dates) != days_in_month,
                },
            )
        )
    monthly = pd.DataFrame(monthly_rows).set_index(["month", "carrier"]).sort_index()

    daily_rows: list[dict] = []
    for date in modeled.index:
        daily_rows.extend(
            _comparison_rows(
                modeled.loc[date],
                observed.loc[date],
                observed_sources="NITI Dashboard daily generation",
                extra={"date": date},
            )
        )
    daily = pd.DataFrame(daily_rows).set_index(["date", "carrier"]).sort_index()

    differences = modeled.index.to_series().diff().dropna()
    coverage_rows = [
        {
            "scope": "analyzed_period",
            "period_start": modeled.index.min(),
            "period_end": modeled.index.max(),
            "populated_days": populated_days,
            "expected_days": expected_period_days,
            "coverage_pct": 100.0 * populated_days / expected_period_days,
            "partial_period": not full_fiscal_year,
            "gap_count": int((differences > pd.Timedelta(days=1)).sum()),
        }
    ]
    month_coverage = monthly.reset_index().drop_duplicates("month")
    for row in month_coverage.itertuples(index=False):
        coverage_rows.append(
            {
                "scope": f"month:{row.month:%Y-%m}",
                "period_start": row.month,
                "period_end": row.month + pd.offsets.MonthEnd(0),
                "populated_days": int(row.populated_days),
                "expected_days": int(row.calendar_days),
                "coverage_pct": float(row.coverage_pct),
                "partial_period": bool(row.partial_month),
                "gap_count": 0,
            }
        )
    coverage = pd.DataFrame(coverage_rows).set_index("scope")

    return GenerationComparison(
        result_dir=result_dir,
        metadata=metadata,
        modeled_daily=modeled,
        observed_daily=observed,
        period=period,
        monthly=monthly,
        daily=daily,
        coverage=coverage,
        full_fiscal_year=full_fiscal_year,
    )


def _comparison_axes(title: str):
    figure, axes = plt.subplots(5, 2, figsize=(16, 20), squeeze=False)
    figure.suptitle(title, fontsize=15)
    return figure, list(axes.flat)


def generation_comparison_figures(
    comparison: GenerationComparison,
) -> dict[str, plt.Figure]:
    """Create period, monthly, and daily modeled-versus-observed figures."""
    period_figure, period_axes = plt.subplots(
        1, 2, figsize=(18, 6), gridspec_kw={"width_ratios": (2.4, 1)}
    )
    period_axis, comparable_axis = period_axes
    period_data = comparison.period.drop(index="comparable_total", errors="ignore")
    period_data[["modeled_gwh", "observed_gwh"]].rename(
        columns={"modeled_gwh": "Modeled", "observed_gwh": "Observed"}
    ).rename(index=CARRIER_LABELS).plot.bar(ax=period_axis)
    period_axis.set_title("Generation by carrier over the populated result period")
    period_axis.set_ylabel("GWh")
    period_axis.grid(axis="y", alpha=0.25)
    comparison.period.loc[["comparable_total"], ["modeled_gwh", "observed_gwh"]].rename(
        columns={"modeled_gwh": "Modeled", "observed_gwh": "Observed"}
    ).rename(index=CARRIER_LABELS).plot.bar(ax=comparable_axis)
    comparable_axis.set_title("Comparable total")
    comparable_axis.set_ylabel("GWh")
    comparable_axis.grid(axis="y", alpha=0.25)
    period_figure.tight_layout()

    monthly_figure, monthly_axes = _comparison_axes("Monthly generation comparison")
    for carrier, axis in zip(GENERATION_CARRIERS, monthly_axes):
        data = comparison.monthly.xs(carrier, level="carrier")
        data[["modeled_gwh", "observed_gwh"]].rename(
            columns={"modeled_gwh": "Modeled", "observed_gwh": "Observed"}
        ).plot(ax=axis, marker="o")
        partial = data.index[data.partial_month]
        if len(partial):
            axis.scatter(
                partial,
                data.loc[partial, "modeled_gwh"],
                marker="x",
                color="black",
                label="Partial month",
                zorder=3,
            )
        axis.set_title(CARRIER_LABELS.get(carrier, carrier))
        axis.set_ylabel("GWh")
        axis.grid(alpha=0.25)
    monthly_figure.tight_layout(rect=(0, 0, 1, 0.98))

    daily_figure, daily_axes = _comparison_axes("Daily generation comparison")
    dates = comparison.modeled_daily.index
    split_positions = np.flatnonzero(
        dates.to_series().diff().gt(pd.Timedelta(days=1)).to_numpy()
    )
    blocks = np.split(dates.to_numpy(), split_positions)
    for carrier, axis in zip(GENERATION_CARRIERS, daily_axes):
        data = comparison.daily.xs(carrier, level="carrier")
        for number, values in enumerate(blocks):
            block = pd.DatetimeIndex(values)
            axis.plot(
                block,
                data.loc[block, "modeled_gwh"],
                label="Modeled" if number == 0 else None,
            )
            observed_values = data.loc[block, "observed_gwh"]
            if observed_values.notna().any():
                axis.plot(
                    block,
                    observed_values,
                    label="Observed" if number == 0 else None,
                )
        axis.set_title(CARRIER_LABELS.get(carrier, carrier))
        axis.set_ylabel("GWh/day")
        axis.grid(alpha=0.25)
    daily_axes[0].legend()
    daily_figure.tight_layout(rect=(0, 0, 1, 0.98))

    return {"period": period_figure, "monthly": monthly_figure, "daily": daily_figure}


def export_generation_comparison(
    comparison: GenerationComparison,
    output_dir: str | Path | None = None,
    figures: dict[str, plt.Figure] | None = None,
) -> Path:
    """Export comparison tables, figures, coverage, and provenance."""
    destination = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else comparison.result_dir / "analysis" / "generation"
    )
    destination.mkdir(parents=True, exist_ok=True)
    comparison.period.to_csv(destination / "period_generation_comparison.csv")
    comparison.monthly.to_csv(destination / "monthly_generation_comparison.csv")
    comparison.daily.to_csv(destination / "daily_generation_comparison.csv")
    comparison.coverage.to_csv(destination / "period_coverage.csv")

    figures = generation_comparison_figures(comparison) if figures is None else figures
    for name, figure in figures.items():
        figure.savefig(destination / f"{name}_generation_comparison.png", dpi=170, bbox_inches="tight")

    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "result_directory": str(comparison.result_dir),
        "run_mode": comparison.metadata.get("run_mode", comparison.metadata.get("mode", "unknown")),
        "unit_commitment": comparison.metadata.get("unit_commitment", "unknown"),
        "period_start": comparison.modeled_daily.index.min().isoformat(),
        "period_end": comparison.modeled_daily.index.max().isoformat(),
        "populated_days": int(len(comparison.modeled_daily)),
        "full_fiscal_year": comparison.full_fiscal_year,
        "source_files": [
            "modeled_daily_generation.csv",
            "observed_daily_generation.csv",
            "validation.json",
        ],
    }
    (destination / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return destination
