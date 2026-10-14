"""Monthly observed small-hydro generation allocated to solve-period targets."""

from pathlib import Path

import pandas as pd

from bio_power_targets import bio_power_energy_target_mwh


ROOT = Path(__file__).resolve().parents[1]
TARGET_FILE = (
    ROOT
    / "data"
    / "model_inputs"
    / "tamil_nadu_small_hydro_monthly_generation_FY2025_26.csv"
)
FY_START = pd.Timestamp("2025-04-01")
FY_END = pd.Timestamp("2026-03-31")


def read_small_hydro_monthly_targets(path: Path = TARGET_FILE) -> pd.Series:
    """Return the twelve reported monthly totals in MWh, indexed by month."""
    frame = pd.read_csv(path, parse_dates=["month"])
    required = {"month", "generation_gwh", "source"}
    if required - set(frame):
        raise ValueError(f"Small-hydro target file is missing columns: {sorted(required - set(frame))}")
    frame["month"] = frame["month"].dt.to_period("M")
    expected = pd.period_range(FY_START, FY_END, freq="M")
    if frame["month"].duplicated().any() or not frame["month"].sort_values().reset_index(drop=True).equals(
        pd.Series(expected)
    ):
        raise ValueError("Small-hydro targets must contain each FY2025-26 month exactly once")
    if frame["generation_gwh"].isna().any() or frame["generation_gwh"].lt(0).any():
        raise ValueError("Small-hydro monthly generation must be finite and non-negative")
    return frame.set_index("month")["generation_gwh"].sort_index() * 1_000.0


def small_hydro_energy_target_mwh(
    snapshots: pd.DatetimeIndex,
    snapshot_weights: pd.Series,
    monthly_targets_mwh: pd.Series | None = None,
) -> float:
    """Prorate monthly small-hydro energy over the supplied snapshots."""
    targets = (
        read_small_hydro_monthly_targets()
        if monthly_targets_mwh is None
        else monthly_targets_mwh
    )
    return bio_power_energy_target_mwh(snapshots, snapshot_weights, targets)
