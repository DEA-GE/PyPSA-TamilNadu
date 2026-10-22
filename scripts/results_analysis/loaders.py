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
    run_type = str(
        metadata.get("run_mode")
        or metadata.get("mode")
        or _infer_run_type(result_dir, len(networks))
    )
    return AnalysisRun(
        result_dir=result_dir,
        run_type=run_type,
        networks=networks,
        source_files=tuple(source_files),
        metadata=metadata,
    )


def load_observed_generation() -> pd.DataFrame:
    """Load official NITI Dashboard daily FY2025-26 generation benchmarks."""
    from run_9ba_weekly_days_2025_26 import read_niti_observed_daily_generation

    observed = read_niti_observed_daily_generation().copy()
    observed.index = pd.DatetimeIndex(observed.index)
    return observed.sort_index()


def load_observed_monthly_generation() -> pd.DataFrame:
    """Load monthly FY2025-26 benchmarks, including separate RES series.

    The NITI monthly dashboard export does not identify bio-power or small
    hydro. Those two categories are supplied by the corresponding Tamil Nadu
    Outlook monthly benchmark files and merged here so monthly comparisons can
    retain the separate categories without changing the daily benchmark.
    """
    model_inputs = Path(__file__).resolve().parents[2] / "data" / "model_inputs"
    source = model_inputs / "niti_dashboard_tamil_nadu_monthly_generation_FY2025_26.csv"
    raw = pd.read_csv(source, parse_dates=["month_start"])
    required = {"source", "state", "fiscal_year", "month_start", "generation_gwh"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"{source} is missing columns: {sorted(missing)}")
    if not raw["state"].eq("Tamil Nadu").all() or not raw["fiscal_year"].eq(
        "2025-26"
    ).all():
        raise ValueError("NITI monthly generation input must contain Tamil Nadu FY2025-26 only")
    raw["carrier"] = raw["source"].replace(
        {"oil-gas": "oil_gas", "other-res": "other_res"}
    )
    observed = raw.pivot_table(
        index="month_start", columns="carrier", values="generation_gwh", aggfunc="sum"
    ).sort_index()
    observed.index = pd.DatetimeIndex(observed.index, name="month")
    expected_months = pd.date_range("2025-04-01", "2026-03-01", freq="MS", name="month")
    if not observed.index.equals(expected_months):
        raise ValueError("NITI monthly generation does not cover every FY2025-26 month")
    for carrier, filename in (
        ("bio_power", "tamil_nadu_bio_power_monthly_generation_FY2025_26.csv"),
        ("small_hydro", "tamil_nadu_small_hydro_monthly_generation_FY2025_26.csv"),
    ):
        supplemental_source = model_inputs / filename
        supplemental = pd.read_csv(supplemental_source, parse_dates=["month"])
        required_supplemental = {"month", "generation_gwh"}
        missing_supplemental = required_supplemental - set(supplemental.columns)
        if missing_supplemental:
            raise ValueError(
                f"{supplemental_source} is missing columns: {sorted(missing_supplemental)}"
            )
        supplemental_index = pd.DatetimeIndex(supplemental["month"], name="month")
        if not supplemental_index.equals(expected_months):
            raise ValueError(
                f"{supplemental_source.name} does not cover every FY2025-26 month"
            )
        observed[carrier] = pd.to_numeric(
            supplemental.set_axis(supplemental_index)["generation_gwh"],
            errors="coerce",
        ).to_numpy()
    observed["comparable_total"] = observed.sum(axis=1, min_count=1)
    return observed


def load_observed_annual_generation() -> pd.DataFrame:
    """Load authoritative NITI Dashboard annual generation benchmarks."""
    from run_9ba_weekly_days_2025_26 import read_observed_annual_generation

    return read_observed_annual_generation()


def load_observed_capacity() -> pd.DataFrame:
    """Load Tamil Nadu Outlook historical installed-capacity benchmarks."""
    source = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "model_inputs"
        / "tamil_nadu_outlook_installed_capacity.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    observed = pd.DataFrame(payload["categories"]).set_index("carrier")
    if observed.index.duplicated().any():
        raise ValueError("Tamil Nadu Outlook capacity categories must be unique")
    observed["source"] = str(payload["source"])
    return observed.sort_index()
