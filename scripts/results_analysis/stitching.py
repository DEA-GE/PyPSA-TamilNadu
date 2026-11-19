"""Join rolling checkpoints into one network covering the whole run.

A rolling run stores one solved network per committed week. Each checkpoint
holds only its committed hours, so the weeks join end to end. The stitched
network takes its static data (components, capacities, costs) from the first
checkpoint, which also gives the run's initial conditions, and concatenates
every time series. It is an analysis container, not a model: the per-week
objective values and energy-target constraints are dropped.

``network.meta["stitched"]`` records where each week starts and its initial
storage levels (for the rolling-boundary audit) and a fingerprint of the
checkpoint files, so a saved copy can be checked against its sources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
import pypsa

STITCHED_FILENAME = "stitched_network.nc"


def source_signature(paths: Iterable[Path]) -> list[dict]:
    """Return name, size and modification time of each checkpoint file."""
    signature = []
    for path in paths:
        status = Path(path).stat()
        signature.append(
            {"file": Path(path).name, "size": status.st_size, "mtime_ns": status.st_mtime_ns}
        )
    return signature


def _require_same_components(networks: Sequence[pypsa.Network]) -> None:
    base = networks[0]
    for component in base.components:
        if component.name == "GlobalConstraint":  # per-week targets; dropped
            continue
        expected = component.static.index
        for number, network in enumerate(networks[1:], start=2):
            actual = network.components[component.name].static.index
            if not expected.equals(actual):
                raise ValueError(
                    f"Checkpoint {number} has different {component.list_name} than "
                    "checkpoint 1; the checkpoints cannot be stitched"
                )


def _window_record(network: pypsa.Network) -> dict:
    return {
        "start": pd.Timestamp(network.snapshots[0]).isoformat(),
        "storage_soc_initial": {
            str(name): float(value)
            for name, value in network.storage_units.state_of_charge_initial.items()
        },
    }


def _stitched_dynamic(
    networks: Sequence[pypsa.Network],
    component: str,
    attribute: str,
    snapshots: pd.DatetimeIndex,
) -> pd.DataFrame | None:
    """Concatenate one time-series attribute over the checkpoints.

    A component without a time series in one checkpoint takes that
    checkpoint's static value there, as PyPSA itself would.
    """
    frames = [network.components[component].dynamic[attribute] for network in networks]
    columns = pd.Index([])
    for frame in frames:
        columns = columns.union(frame.columns, sort=False)
    if columns.empty:
        return None
    parts = []
    for network, frame in zip(networks, frames):
        part = frame.reindex(index=network.snapshots, columns=columns)
        missing = columns.difference(frame.columns)
        static = network.components[component].static
        if len(missing) and attribute in static:
            part[missing] = np.broadcast_to(
                static.loc[missing, attribute].to_numpy(dtype=float), part[missing].shape
            )
        parts.append(part)
    combined = pd.concat(parts)
    combined = combined.loc[~combined.index.duplicated(keep="first")]
    combined.columns.name = frames[0].columns.name or "name"
    return combined.reindex(snapshots)


def stitch_networks(
    networks: Sequence[pypsa.Network], sources: Sequence[Path] = ()
) -> pypsa.Network:
    """Return one network covering all checkpoints, in time order.

    Where checkpoints overlap, the earlier one's values are kept.
    """
    if not networks:
        raise ValueError("No networks to stitch")
    networks = sorted(networks, key=lambda network: network.snapshots[0])
    _require_same_components(networks)
    stitched = networks[0].copy()

    snapshots = pd.DatetimeIndex(
        np.concatenate([network.snapshots.values for network in networks])
    )
    snapshots = snapshots[~snapshots.duplicated(keep="first")].sort_values()
    snapshots.name = "snapshot"
    weightings = pd.concat([network.snapshot_weightings for network in networks])
    weightings = weightings.loc[~weightings.index.duplicated(keep="first")]

    stitched.set_snapshots(snapshots)
    stitched.snapshot_weightings = weightings.reindex(snapshots)
    for component in stitched.components:
        for attribute in list(component.dynamic):
            frame = _stitched_dynamic(networks, component.name, attribute, snapshots)
            if frame is not None:
                component.dynamic[attribute] = frame

    if len(stitched.global_constraints):
        stitched.remove("GlobalConstraint", stitched.global_constraints.index)
    stitched._objective = None
    stitched._objective_constant = None
    stitched.meta = {
        **(networks[0].meta or {}),
        "stitched": {
            "windows": [_window_record(network) for network in networks],
            "sources": source_signature(sources),
        },
    }
    return stitched


def load_saved_stitched(result_dir: Path, sources: Sequence[Path]) -> pypsa.Network | None:
    """Return the saved stitched network if it was built from *sources* as they are now."""
    path = Path(result_dir) / STITCHED_FILENAME
    if not path.exists():
        return None
    network = pypsa.Network(path)
    recorded = (network.meta or {}).get("stitched", {}).get("sources")
    if recorded != source_signature(sources):
        return None
    return network


def save_stitched(network: pypsa.Network, result_dir: Path) -> Path:
    """Write the stitched network next to its checkpoints."""
    path = Path(result_dir) / STITCHED_FILENAME
    temporary = path.with_suffix(".nc.tmp")
    network.export_to_netcdf(temporary)
    temporary.replace(path)
    return path
