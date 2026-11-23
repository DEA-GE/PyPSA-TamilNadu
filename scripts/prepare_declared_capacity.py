"""Hourly declared capacity of the central coal stations in Tamil Nadu, FY2025-26.

Reads ``commercial_on_off_dc_isgs.csv`` from the twelve final SRPC Regional
Energy Account archives (``data/source/REA data``, gitignored; the members are
located with the REA pipeline's inventory,
``outputs/rea_fy2025_26/source_inventory.csv``) and writes:

``data/model_inputs/central_station_declared_capacity_FY2025_26.csv``
    Hourly mean declared capacity (MW, ex-bus) of each station: on-bar
    (units running) plus off-bar (available units shut down), averaged over
    the four 15-minute blocks of the hour.

``data/model_inputs/central_station_units.csv``
    The model coal units of each station and their rating.

Declared capacity is what a central station tells the regional load dispatch
centre it can deliver ex-bus in each block, given its units' outages, partial
deratings and fuel; it is the basis of the station's capacity charge (CERC
Tariff Regulations 2024, Reg. 3(59)). The seven stations are 5,890 MW of the
model's coal fleet: NTECL Vallur, NTPL Tuticorin, NLC NNTPS, NLC TPS-II Stage I
and Stage II, NLC TPS-I Expansion and NLC TPS-II Expansion.

    python scripts/prepare_declared_capacity.py [--source-dir "data/source/REA data"]
"""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data" / "source" / "REA data"
INVENTORY = ROOT / "outputs" / "rea_fy2025_26" / "source_inventory.csv"
DECLARED_OUTPUT = ROOT / "data" / "model_inputs" / "central_station_declared_capacity_FY2025_26.csv"
UNITS_OUTPUT = ROOT / "data" / "model_inputs" / "central_station_units.csv"
MODEL_DIR = ROOT / "model"
DC_FILE = "commercial_on_off_dc_isgs.csv"
HOURS = pd.date_range("2025-04-01", "2026-03-31 23:00", freq="h")

# REA station code -> model components (all units of a station are identical).
STATIONS = {
    "vallur": ["vallur_tpp__unit_01", "vallur_tpp__unit_02", "vallur_tpp__unit_03"],
    "ntpl": ["tuticorin_joint_venture__unit_01", "tuticorin_joint_venture__unit_02"],
    "nntpp": ["neyveli_new_tpp_neyveli_extension__unit_01", "neyveli_new_tpp_neyveli_extension__unit_02"],
    "nlc21": ["neyveli_tps_ii__unit_01", "neyveli_tps_ii__unit_02", "neyveli_tps_ii__unit_03"],
    "nlc22": [
        "neyveli_tps_ii__unit_04", "neyveli_tps_ii__unit_05",
        "neyveli_tps_ii__unit_06", "neyveli_tps_ii__unit_07",
    ],
    "nlcexp": ["neyveli_ext_tps_i__unit_01", "neyveli_ext_tps_i__unit_02"],
    "nlc2exp": ["neyveli_ext_tps_ii__unit_01", "neyveli_ext_tps_ii__unit_02"],
}
STATION_NAMES = {
    "vallur": "NTECL Vallur", "ntpl": "NTPL Tuticorin", "nntpp": "NLC New Neyveli TPS (NNTPS)",
    "nlc21": "NLC TPS-II Stage I", "nlc22": "NLC TPS-II Stage II",
    "nlcexp": "NLC TPS-I Expansion", "nlc2exp": "NLC TPS-II Expansion",
}


def _read_member(source_dir: Path, virtual_path: str, cache: dict) -> bytes:
    """Read a file inside nested zip archives (``outer.zip!inner.zip!file``)."""
    parts = virtual_path.split("!")
    if parts[0] not in cache:
        cache[parts[0]] = zipfile.ZipFile(source_dir / parts[0])
    archive = cache[parts[0]]
    for depth, part in enumerate(parts[1:-1], start=1):
        key = "!".join(parts[: depth + 1])
        if key not in cache:
            cache[key] = zipfile.ZipFile(io.BytesIO(archive.read(part)))
        archive = cache[key]
    return archive.read(parts[-1])


def read_declared_capacity(source_dir: Path = SOURCE_DIR, inventory: Path = INVENTORY) -> pd.DataFrame:
    """Hourly mean declared capacity (on-bar + off-bar, MW) per station."""
    listing = pd.read_csv(inventory)
    members = listing[(listing.filename == DC_FILE) & ~listing.is_provisional].sort_values("date_start")
    if len(members) != 24:
        raise ValueError(f"Expected 24 final half-month {DC_FILE} members, found {len(members)}")
    cache: dict = {}
    parts = []
    for member in members.itertuples():
        frame = pd.read_csv(io.BytesIO(_read_member(source_dir, member.virtual_path, cache)))
        stamp = pd.to_datetime(frame["date"]) + pd.to_timedelta(frame["time"].astype(str))
        columns = {}
        for code in STATIONS:
            missing = [f"{code}_{k}" for k in ("on", "off") if f"{code}_{k}" not in frame]
            if missing:
                raise ValueError(f"{member.virtual_path}: missing {missing}")
            on = pd.to_numeric(frame[f"{code}_on"], errors="raise")
            off = pd.to_numeric(frame[f"{code}_off"], errors="raise")
            columns[code] = (on + off).to_numpy()
        parts.append(pd.DataFrame(columns, index=stamp).resample("h").mean())
    declared = pd.concat(parts).sort_index()
    if declared.index.duplicated().any():
        raise ValueError("Overlapping half-month files")
    declared = declared.reindex(HOURS)
    if declared.isna().any().any():
        raise ValueError(f"Hours without declared capacity: {int(declared.isna().any(axis=1).sum())}")
    declared.index.name = "snapshot"
    return declared


def station_units(model_dir: Path = MODEL_DIR) -> pd.DataFrame:
    """Model units of each station with their physical rating."""
    metadata = pd.read_csv(model_dir / "component_metadata.csv").set_index("component_id")
    rows = []
    for code, components in STATIONS.items():
        missing = [c for c in components if c not in metadata.index]
        if missing:
            raise ValueError(f"Station {code}: units missing from the model: {missing}")
        for component in components:
            rows.append({
                "station": code,
                "station_name": STATION_NAMES[code],
                "component": component,
                "unit_rating_mw": float(metadata.loc[component, "capacity_mw"]),
            })
    table = pd.DataFrame(rows)
    sizes = table.groupby("station").unit_rating_mw.nunique()
    if (sizes > 1).any():
        raise ValueError(f"Stations with units of different ratings: {list(sizes.index[sizes > 1])}")
    return table


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    args = parser.parse_args(argv)
    declared = read_declared_capacity(args.source_dir)
    units = station_units()
    rating = units.groupby("station").unit_rating_mw.sum()
    above = declared.gt(rating[declared.columns]).sum()
    if above.any():
        raise ValueError(f"Declared capacity above the station rating in hours: {above[above > 0].to_dict()}")
    DECLARED_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    declared.to_csv(DECLARED_OUTPUT, float_format="%.2f")
    units.to_csv(UNITS_OUTPUT, index=False)
    summary = pd.DataFrame({"rating_mw": rating, "mean_declared_mw": declared.mean().round(0)})
    summary["mean_share"] = (summary.mean_declared_mw / summary.rating_mw).round(3)
    print(f"Wrote {DECLARED_OUTPUT.relative_to(ROOT)} ({len(declared)} hours) and {UNITS_OUTPUT.relative_to(ROOT)}")
    print(summary.to_string())


if __name__ == "__main__":
    main()
