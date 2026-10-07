"""Common structural and rolling-boundary validation."""

from __future__ import annotations

import numpy as np
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
    base = run.base
    rows = []
    for component in ("buses", "generators", "storage_units", "stores", "links", "loads"):
        expected = getattr(base, component).index
        mismatches = sum(
            not expected.equals(getattr(network, component).index)
            for network in run.networks[1:]
        )
        rows.append(
            {
                "component": component,
                "component_count": len(expected),
                "checkpoint_topology_mismatches": mismatches,
            }
        )
    return pd.DataFrame(rows).set_index("component")


def boundary_handoff_metrics(run: AnalysisRun, tolerance: float = 1e-6) -> pd.DataFrame:
    """Audit state, transition, ramp, and storage handoffs between checkpoints."""
    if len(run.networks) < 2:
        return pd.DataFrame()

    rows = []
    for prior, current in zip(run.networks[:-1], run.networks[1:]):
        shared = prior.generators.index.intersection(current.generators.index)
        committable = shared[current.generators.loc[shared, "committable"].fillna(False)]
        transition_error = pd.Series(dtype=float)
        ramp_up_violation = pd.Series(dtype=float)
        ramp_down_violation = pd.Series(dtype=float)
        fractional_count = 0

        if len(committable) and not prior.generators_t.status.empty and not current.generators_t.status.empty:
            status_before = prior.generators_t.status.iloc[-1].reindex(committable).fillna(0.0)
            status_now = current.generators_t.status.iloc[0].reindex(committable).fillna(0.0)
            start_now = current.generators_t.start_up.iloc[0].reindex(committable).fillna(0.0)
            shut_now = current.generators_t.shut_down.iloc[0].reindex(committable).fillna(0.0)
            transition_error = (status_now - status_before - start_now + shut_now).abs()
            fractional_count = int(status_before.between(tolerance, 1.0 - tolerance).sum())

            p_before = prior.generators_t.p.iloc[-1].reindex(committable).fillna(0.0)
            p_now = current.generators_t.p.iloc[0].reindex(committable).fillna(0.0)
            nominal = current.generators.loc[committable, "p_nom"].fillna(0.0)
            ramp_up = nominal * current.generators.loc[committable, "ramp_limit_up"].fillna(1.0)
            ramp_down = nominal * current.generators.loc[committable, "ramp_limit_down"].fillna(1.0)
            ramp_start = nominal * current.generators.loc[committable, "ramp_limit_start_up"].fillna(1.0)
            ramp_shut = nominal * current.generators.loc[committable, "ramp_limit_shut_down"].fillna(1.0)
            ramp_up_violation = (
                p_now - p_before - ramp_up * status_before - ramp_start * (status_now - status_before)
            ).clip(lower=0.0)
            ramp_down_violation = (
                p_before - p_now - ramp_down * status_now - ramp_shut * (status_before - status_now)
            ).clip(lower=0.0)

        storage_error = 0.0
        shared_storage = prior.storage_units.index.intersection(current.storage_units.index)
        if len(shared_storage) and not prior.storage_units_t.state_of_charge.empty:
            previous_soc = prior.storage_units_t.state_of_charge.iloc[-1].reindex(shared_storage)
            initial_soc = current.storage_units.loc[shared_storage, "state_of_charge_initial"]
            storage_error = float((initial_soc - previous_soc).abs().max())

        rows.append(
            {
                "boundary_timestamp": pd.Timestamp(current.snapshots[0]),
                "fractional_units_before_boundary": fractional_count,
                "max_transition_identity_error": float(transition_error.max()) if len(transition_error) else 0.0,
                "max_ramp_up_violation_mw": float(ramp_up_violation.max()) if len(ramp_up_violation) else 0.0,
                "max_ramp_down_violation_mw": float(ramp_down_violation.max()) if len(ramp_down_violation) else 0.0,
                "max_storage_initial_soc_error_mwh": storage_error,
            }
        )
    return pd.DataFrame(rows)
