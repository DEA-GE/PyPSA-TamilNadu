"""Write standardized analysis tables, figures, and provenance metadata."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import ANALYSIS_SCHEMA_VERSION
from .loaders import AnalysisRun
from .pipeline import AnalysisBundle


def _git_commit(repository: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def export_analysis(
    run: AnalysisRun,
    bundle: AnalysisBundle,
    output_dir: str | Path | None = None,
) -> Path:
    """Export one analysis bundle using the repository-wide directory schema."""
    destination = Path(output_dir) if output_dir is not None else run.result_dir / "analysis"
    destination = destination.resolve()
    table_dir = destination / "tables"
    figure_dir = destination / "figures"
    table_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    table_files = []
    for name, table in bundle.tables.items():
        path = table_dir / f"{name}.csv"
        table.to_csv(path)
        table_files.append(path.relative_to(destination).as_posix())

    figure_files = []
    for name, figure in bundle.figures.items():
        path = figure_dir / f"{name}.png"
        figure.savefig(path, dpi=170, bbox_inches="tight")
        figure_files.append(path.relative_to(destination).as_posix())

    repository = Path(__file__).resolve().parents[2]
    manifest = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(repository),
        "run_type": run.run_type,
        "unit_commitment": run.metadata.get("unit_commitment", "unknown"),
        "result_directory": str(run.result_dir),
        "source_networks": [str(path) for path in run.source_files],
        "period_start": run.snapshots[0].isoformat(),
        "period_end": run.snapshots[-1].isoformat(),
        "snapshot_count": len(run.snapshots),
        "tables": table_files,
        "figures": figure_files,
    }
    (destination / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return destination
