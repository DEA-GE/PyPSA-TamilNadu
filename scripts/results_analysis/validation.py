"""Common structural and rolling-boundary validation."""

from __future__ import annotations

import pandas as pd

from .loaders import AnalysisRun


def chronology_validation(run: AnalysisRun) -> pd.DataFrame:
    snapshots = run.snapshots
    differences = snapshots.to_series().diff().dropna()
    expected_hours = 0 if len(snapshots) < 2 else int(
        (snapshots[-1] - snapshots[0]) / pd.Timedelta(hours=1)
    ) + 1
    rows = [
        ("snapshot_count", float(len(snapshots)), "snapshots"),
        ("expected_contiguous_hours", float(expected_hours), "hours"),
        ("duplicate_snapshot_count", float(snapshots.duplicated().sum()), "snapshots"),
        ("non_hourly_step_count", float((differences != pd.Timedelta(hours=1)).sum()), "steps"),
        ("is_monotonic", float(snapshots.is_monotonic_increasing), "boolean"),
    ]
    return pd.DataFrame(rows, columns=["metric", "value", "unit"]).set_index("metric")


def topology_validation(run: AnalysisRun) -> pd.DataFrame:
    """Count components; stitching already requires every checkpoint to match."""
    rows = [
        {
            "component": component,
            "component_count": len(getattr(run.network, component)),
            "checkpoints": max(len(run.windows), 1),
        }
        for component in ("buses", "generators", "storage_units", "stores", "links", "loads")
    ]
    return pd.DataFrame(rows).set_index("component")


def boundary_handoff_metrics(run: AnalysisRun, tolerance: float = 1e-6) -> pd.DataFrame:
    """Audit state, transition, ramp, and storage handoffs between checkpoints.

    Uses the stitched time series on both sides of each checkpoint start and
    the initial storage levels recorded for each checkpoint.
    """
    windows = run.windows
    if len(windows) < 2:
        return pd.DataFrame()

    network = run.network
    snapshots = run.snapshots
    generators = network.generators
    committable = generators.index[generators.committable.fillna(False)]
    status = run.frame("generators", "status").reindex(columns=committable)
    start_up = run.frame("generators", "start_up").reindex(columns=committable)
    shut_down = run.frame("generators", "shut_down").reindex(columns=committable)
    dispatch = run.frame("generators", "p").reindex(columns=committable)
    soc = run.frame("storage_units", "state_of_charge")

    nominal = generators.loc[committable, "p_nom"].fillna(0.0)
    ramp_up = nominal * generators.loc[committable, "ramp_limit_up"].fillna(1.0)
    ramp_down = nominal * generators.loc[committable, "ramp_limit_down"].fillna(1.0)
    ramp_start = nominal * generators.loc[committable, "ramp_limit_start_up"].fillna(1.0)
    ramp_shut = nominal * generators.loc[committable, "ramp_limit_shut_down"].fillna(1.0)
    has_status = len(committable) and status.notna().any().any()

    rows = []
    for window in windows[1:]:
        boundary = pd.Timestamp(window["start"])
        position = snapshots.get_loc(boundary)
        if position == 0:
            continue
        before = snapshots[position - 1]
        transition_error = pd.Series(dtype=float)
        ramp_up_violation = pd.Series(dtype=float)
        ramp_down_violation = pd.Series(dtype=float)
        fractional_count = 0

        if has_status:
            status_before = status.loc[before].fillna(0.0)
            status_now = status.loc[boundary].fillna(0.0)
            transition_error = (
                status_now - status_before
                - start_up.loc[boundary].fillna(0.0) + shut_down.loc[boundary].fillna(0.0)
            ).abs()
            fractional_count = int(status_before.between(tolerance, 1.0 - tolerance).sum())
            p_before = dispatch.loc[before].fillna(0.0)
            p_now = dispatch.loc[boundary].fillna(0.0)
            ramp_up_violation = (
                p_now - p_before - ramp_up * status_before - ramp_start * (status_now - status_before)
            ).clip(lower=0.0)
            ramp_down_violation = (
                p_before - p_now - ramp_down * status_now - ramp_shut * (status_before - status_now)
            ).clip(lower=0.0)

        storage_error = 0.0
        initial = pd.Series(window.get("storage_soc_initial", {}), dtype=float)
        shared = initial.index.intersection(soc.columns)
        if len(shared):
            storage_error = float((initial[shared] - soc.loc[before, shared]).abs().max())

        rows.append(
            {
                "boundary_timestamp": boundary,
                "fractional_units_before_boundary": fractional_count,
                "max_transition_identity_error": float(transition_error.max()) if len(transition_error) else 0.0,
                "max_ramp_up_violation_mw": float(ramp_up_violation.max()) if len(ramp_up_violation) else 0.0,
                "max_ramp_down_violation_mw": float(ramp_down_violation.max()) if len(ramp_down_violation) else 0.0,
                "max_storage_initial_soc_error_mwh": storage_error,
            }
        )
    return pd.DataFrame(rows)
