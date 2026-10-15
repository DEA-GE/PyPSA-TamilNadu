"""Monthly observed bio-power generation allocated to solve-period targets."""

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
TARGET_FILE = (
    ROOT
    / "data"
    / "model_inputs"
    / "tamil_nadu_bio_power_monthly_generation_FY2025_26.csv"
)
FY_START = pd.Timestamp("2025-04-01")
FY_END = pd.Timestamp("2026-03-31")


def read_bio_power_monthly_targets(path: Path = TARGET_FILE) -> pd.Series:
    """Return the twelve reported monthly totals in MWh, indexed by month."""
    frame = pd.read_csv(path, parse_dates=["month"])
    required = {"month", "generation_gwh", "source"}
    if required - set(frame):
        raise ValueError(f"Bio-power target file is missing columns: {sorted(required - set(frame))}")
    frame["month"] = frame["month"].dt.to_period("M")
    expected = pd.period_range(FY_START, FY_END, freq="M")
    if frame["month"].duplicated().any() or not frame["month"].sort_values().reset_index(drop=True).equals(
        pd.Series(expected)
    ):
        raise ValueError("Bio-power targets must contain each FY2025-26 month exactly once")
    if frame["generation_gwh"].isna().any() or frame["generation_gwh"].lt(0).any():
        raise ValueError("Bio-power monthly generation must be finite and non-negative")
    return frame.set_index("month")["generation_gwh"].sort_index() * 1_000.0


def bio_power_energy_target_mwh(
    snapshots: pd.DatetimeIndex,
    snapshot_weights: pd.Series,
    monthly_targets_mwh: pd.Series | None = None,
) -> float:
    """Prorate monthly energy into a period, preserving every monthly total.

    Allocation is uniform per weighted hour within each calendar month. Thus a
    week crossing a month boundary receives the appropriate share of both
    months, while dispatch within the week remains unconstrained.
    """
    snapshots = pd.DatetimeIndex(snapshots)
    if snapshots.empty:
        raise ValueError("Cannot calculate a bio-power target for no snapshots")
    weights = snapshot_weights.reindex(snapshots).astype(float)
    if weights.isna().any() or not np.isfinite(weights).all() or weights.lt(0).any():
        raise ValueError("Bio-power target snapshot weights must be finite and non-negative")
    targets = read_bio_power_monthly_targets() if monthly_targets_mwh is None else monthly_targets_mwh
    months = snapshots.to_period("M")
    missing = pd.Index(months.unique()).difference(targets.index)
    if len(missing):
        raise ValueError(f"Bio-power targets are missing months: {missing.tolist()}")

    target_mwh = 0.0
    for month in months.unique():
        in_month = months == month
        month_hours = float(month.days_in_month * 24)
        target_mwh += float(targets.at[month]) * float(weights.loc[in_month].sum()) / month_hours
    return target_mwh
