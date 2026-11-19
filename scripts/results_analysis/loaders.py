"""Load single-network and rolling-checkpoint results through one interface."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
import pypsa

from .stitching import load_saved_stitched, save_stitched, stitch_networks


def _is_dual_sidecar(path: Path) -> bool:
    """Return whether *path* stores solver duals rather than a solved network."""
    return path.name.casefold().endswith(".duals.nc")


def _primal_network_files(paths: Iterable[Path]) -> list[Path]:
    """Sort solved-network files while excluding adjacent solver-dual files."""
    return sorted(path for path in paths if not _is_dual_sidecar(path))


@dataclass(frozen=True)
class AnalysisRun:
    """One solved run as a single network, irrespective of its storage layout.

    Rolling runs are stitched from their weekly checkpoints into one network
    covering the whole run (see ``stitching``).
    """

    result_dir: Path
    run_type: str
    network: pypsa.Network
    source_files: tuple[Path, ...]
    metadata: dict

    @property
    def base(self) -> pypsa.Network:
        return self.network

    @property
    def windows(self) -> list[dict]:
        """Start time and initial storage of each stitched checkpoint, if any."""
        return list((self.network.meta or {}).get("stitched", {}).get("windows", []))

    @property
    def snapshots(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.network.snapshots)

    @property
    def weights(self) -> pd.Series:
        weightings = self.network.snapshot_weightings
        if "generators" in weightings:
            weights = weightings["generators"].astype(float)
        else:
            weights = pd.Series(1.0, index=self.network.snapshots)
        weights.index = self.snapshots
        return weights

    def frame(self, component: str, attribute: str) -> pd.DataFrame:
        """Return a PyPSA time-series attribute, such as ``("generators", "p")``."""
        frame = getattr(getattr(self.network, f"{component}_t"), attribute)
        if frame is None or frame.empty:
            return pd.DataFrame(index=self.snapshots)
        frame = frame.copy()
        frame.index = pd.DatetimeIndex(frame.index)
        return frame.reindex(self.snapshots)


def style_carriers(network: pypsa.Network) -> None:
    """Give the network's carriers the report's names and colours.

    PyPSA's statistics plots need a colour for every carrier and label groups
    by ``nice_name``. Only the in-memory network changes.
    """
    from .config import CARRIER_COLORS, CARRIER_LABELS, MODEL_CARRIER_STYLES, canonical_carrier

    used = set()
    for component in network.components:
        static = component.static
        if "carrier" in static and component.name != "Carrier":
            used.update(static.carrier.dropna().astype(str))
    missing = sorted(used - set(network.carriers.index) - {""})
    if missing:
        network.add("Carrier", missing)
    names, colors = [], []
    for carrier in network.carriers.index:
        name, color = MODEL_CARRIER_STYLES.get(carrier, (None, None))
        report = canonical_carrier(carrier)
        names.append(name or CARRIER_LABELS.get(report, str(carrier)))
        colors.append(color or CARRIER_COLORS.get(report, "#7F7F7F"))
    network.carriers["nice_name"] = names
    network.carriers["color"] = colors


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


def load_analysis_run(path: str | Path, cache: bool = True) -> AnalysisRun:
    """Load a solved ``.nc`` file or a run directory containing solved results.

    For a rolling run, the weekly checkpoints are stitched into one network.
    A saved ``stitched_network.nc`` in the run folder is used instead when it
    was built from the checkpoints as they are now; otherwise the stitched
    network is rebuilt and, with *cache*, saved there for next time.
    """
    requested = Path(path).expanduser().resolve()
    if not requested.exists():
        raise FileNotFoundError(f"Result path does not exist: {requested}")

    if requested.is_file():
        if requested.suffix.casefold() != ".nc":
            raise ValueError("A result file must be a PyPSA .nc network")
        if _is_dual_sidecar(requested):
            raise ValueError("A .duals.nc sidecar is not a solved PyPSA network")
        result_dir = requested.parent
        source_files = [requested]
    else:
        result_dir = requested
        solved_network = result_dir / "solved_network.nc"
        checkpoint_dir = result_dir / "weekly_networks"
        if solved_network.exists():
            source_files = [solved_network]
        elif checkpoint_dir.exists():
            source_files = _primal_network_files(
                checkpoint_dir.glob("window_*.nc")
            )
        else:
            direct_networks = _primal_network_files(result_dir.glob("*.nc"))
            if len(direct_networks) == 1:
                source_files = direct_networks
            else:
                raise FileNotFoundError(
                    f"No unique solved network or weekly_networks directory found in {result_dir}"
                )

    if not source_files:
        raise FileNotFoundError(f"No solved network files found below {result_dir}")

    if len(source_files) == 1:
        network = pypsa.Network(source_files[0])
    else:
        network = load_saved_stitched(result_dir, source_files)
        if network is None:
            network = stitch_networks(
                [pypsa.Network(source) for source in source_files], source_files
            )
            if cache:
                saved = save_stitched(network, result_dir)
                print(f"Saved the stitched run to {saved}")
    style_carriers(network)
    metadata = _read_metadata(result_dir, source_files)
    run_type = str(
        metadata.get("run_mode")
        or metadata.get("mode")
        or _infer_run_type(result_dir, len(source_files))
    )
    return AnalysisRun(
        result_dir=result_dir,
        run_type=run_type,
        network=network,
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
    """Load the latest supplied Tamil Nadu installed-capacity benchmark.

    The September 2026 capacity metatable contains a time series.  Select its
    latest Tamil Nadu year rather than the older Tamil Nadu Outlook snapshot.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "source"
        / "Capacity_Metatable_Data_Sept26.xlsx"
    )
    raw = pd.read_excel(source, sheet_name="Capacity_Metatable_Data_Sheet")
    required = {"Year", "State", "Source", "Capacity in MW"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"{source} is missing columns: {sorted(missing)}")
    raw = raw.loc[raw["State"].eq("Tamil Nadu")].copy()
    raw["year"] = pd.to_datetime(raw["Year"].astype(str).str[:4], errors="coerce")
    raw = raw.loc[raw["year"].notna()]
    if raw.empty:
        raise ValueError(f"{source} contains no dated Tamil Nadu capacity rows")
    latest_year = raw["year"].max()
    raw = raw.loc[raw["year"].eq(latest_year)].copy()
    raw["carrier"] = raw["Source"].astype(str).str.strip().str.lower().replace(
        {"oil-gas": "oil_gas", "bio-power": "bio_power", "small-hydro": "small_hydro"}
    )
    observed = raw.groupby("carrier", sort=True)["Capacity in MW"].sum().to_frame(
        "installed_capacity_mw"
    )
    observed["installed_capacity_mw"] = pd.to_numeric(
        observed["installed_capacity_mw"], errors="raise"
    )
    observed.loc["total", "installed_capacity_mw"] = observed[
        "installed_capacity_mw"
    ].sum()
    observed["source"] = (
        f"{source} (Tamil Nadu, {latest_year.year}-{str(latest_year.year + 1)[-2:]})"
    )
    observed.index.name = "carrier"
    return observed.sort_index()
