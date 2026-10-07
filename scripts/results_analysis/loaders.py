"""Load single-network and rolling-checkpoint results through one interface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa


@dataclass(frozen=True)
class AnalysisRun:
    """Normalized access to one solved run, irrespective of its storage layout."""

    result_dir: Path
    run_type: str
    networks: tuple[pypsa.Network, ...]
    source_files: tuple[Path, ...]
    metadata: dict

    @property
    def base(self) -> pypsa.Network:
        return self.networks[0]

    @property
    def snapshots(self) -> pd.DatetimeIndex:
        values = np.concatenate([network.snapshots.values for network in self.networks])
        return pd.DatetimeIndex(values).drop_duplicates().sort_values()

    @property
    def weights(self) -> pd.Series:
        parts: list[pd.Series] = []
        for network in self.networks:
            weightings = network.snapshot_weightings
            if "generators" in weightings:
                part = weightings["generators"].copy()
            else:
                part = pd.Series(1.0, index=network.snapshots)
            part.index = pd.DatetimeIndex(part.index)
            parts.append(part.astype(float))
        combined = pd.concat(parts)
        return combined.loc[~combined.index.duplicated(keep="first")].sort_index()

    def frame(self, component: str, attribute: str) -> pd.DataFrame:
        """Combine a PyPSA time-series attribute over all retained checkpoints."""
        parts: list[pd.DataFrame] = []
        namespace_name = f"{component}_t"
        for network in self.networks:
            namespace = getattr(network, namespace_name)
            frame = getattr(namespace, attribute)
            if frame is None or frame.empty:
                continue
            part = frame.copy()
            part.index = pd.DatetimeIndex(part.index)
            parts.append(part)
        if not parts:
            return pd.DataFrame(index=self.snapshots)
        combined = pd.concat(parts).sort_index()
        combined = combined.loc[~combined.index.duplicated(keep="first")]
        return combined.reindex(self.snapshots)


def _infer_run_type(path: Path, network_count: int) -> str:
    label = path.name.casefold()
    if network_count > 1 or "rolling" in label:
        return "rolling"
    if "peak" in label or "peak" in path.parent.name.casefold():
        return "peak_week"
    if "selected" in label or "week_" in label:
        return "selected_week"
    if "monthly" in label:
        return "monthly"
    if "daily" in label:
        return "daily"
    return "single_network"


def _read_metadata(result_dir: Path, source_files: list[Path]) -> dict:
    validation = result_dir / "validation.json"
    if validation.exists():
        return json.loads(validation.read_text(encoding="utf-8"))

    checkpoint_metadata = []
    for network_file in source_files:
        metadata_file = network_file.with_suffix(".json")
        if metadata_file.exists():
            checkpoint_metadata.append(json.loads(metadata_file.read_text(encoding="utf-8")))
    if not checkpoint_metadata:
        return {}

    metadata = dict(checkpoint_metadata[0])
    metadata["checkpoint_count"] = len(checkpoint_metadata)
    formulations = {
        item.get("unit_commitment", "full") for item in checkpoint_metadata
    }
    if len(formulations) != 1:
        raise ValueError("Checkpoint directory mixes unit-commitment formulations")
    metadata["unit_commitment"] = formulations.pop()
    return metadata


def load_analysis_run(path: str | Path) -> AnalysisRun:
    """Load a solved ``.nc`` file or a run directory containing solved results."""
    requested = Path(path).expanduser().resolve()
    if not requested.exists():
        raise FileNotFoundError(f"Result path does not exist: {requested}")

    if requested.is_file():
        if requested.suffix.casefold() != ".nc":
            raise ValueError("A result file must be a PyPSA .nc network")
        result_dir = requested.parent
        source_files = [requested]
    else:
        result_dir = requested
        solved_network = result_dir / "solved_network.nc"
        checkpoint_dir = result_dir / "weekly_networks"
        if solved_network.exists():
            source_files = [solved_network]
        elif checkpoint_dir.exists():
            source_files = sorted(checkpoint_dir.glob("window_*.nc"))
        else:
            direct_networks = sorted(result_dir.glob("*.nc"))
            if len(direct_networks) == 1:
                source_files = direct_networks
            else:
                raise FileNotFoundError(
                    f"No unique solved network or weekly_networks directory found in {result_dir}"
                )

    if not source_files:
        raise FileNotFoundError(f"No solved network files found below {result_dir}")

    networks = tuple(pypsa.Network(source) for source in source_files)
    metadata = _read_metadata(result_dir, source_files)
    run_type = str(metadata.get("mode") or _infer_run_type(result_dir, len(networks)))
    return AnalysisRun(
        result_dir=result_dir,
        run_type=run_type,
        networks=networks,
        source_files=tuple(source_files),
        metadata=metadata,
    )


def load_observed_generation() -> pd.DataFrame:
    """Load the model's canonical FY2025-26 observed daily generation table."""
    from run_9ba_weekly_days_2025_26 import read_observed_generation

    observed = read_observed_generation().copy()
    observed.index = pd.DatetimeIndex(observed.index)
    return observed.sort_index()
