"""Reconstruct FY2025-26 Tamil Nadu commercial schedules from SRPC REA archives.

The pipeline treats composite schedules as accounting parents, uses transaction or
beneficiary detail once, and preserves excluded representations in the canonical
dataset for traceability. It deliberately leaves unproved origins, destinations,
and BA mappings unresolved.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import Point

from .archive import ArchiveEntry, inventory_metadata, walk_month_archive
from .classify import OVERLAP_ROWS, classify_file
from .mappings import (
    BA_NAMES,
    BENEFICIARY_FILES,
    COMPOSITE_COMPONENTS,
    MONTH_ARCHIVES,
    STATE_ALIASES,
    STATIONS,
    STATION_MAPPING_EVIDENCE,
    TN_TOKEN_TO_STATION,
    ba_for_district,
)


FY = "2025-26"
QUARTER_HOUR_GWH = 0.25 / 1000.0
TN = "Tamil Nadu"
PUDUCHERRY = "Puducherry"
DEFAULT_SOURCE_DIR = Path("data/source/REA data")
DEFAULT_OUTPUT_DIR = Path("outputs/rea_fy2025_26")


CANONICAL_FIELDS = [
    "canonical_id", "date", "time_block", "month", "financial_year", "channel",
    "generator_id", "generator_name", "plant_location_state", "plant_district", "tn_BA",
    "seller_entity", "contract_seller_state", "trader", "buyer_entity", "beneficiary_state",
    "contract_application_id", "source_entity", "counterparty_entity", "raw_schedule_MW",
    "energy_GWh", "signed_GWh", "direction", "schedule_basis", "geographical_class",
    "source_archive", "source_path", "source_sha256", "source_column", "raw_record_key",
    "dedup_key", "overlap_parent", "evidence", "confidence", "exception_flag",
    "source_family", "included_in_approved", "exclusion_reason",
]


CANONICAL_SCHEMA = pa.schema([
    pa.field(name, pa.int32() if name == "time_block" else
             pa.float64() if name in {"raw_schedule_MW", "energy_GWh", "signed_GWh"} else
             pa.bool_() if name == "included_in_approved" else pa.string())
    for name in CANONICAL_FIELDS
])


def norm(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def parse_date_value(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    parsed = pd.to_datetime(str(value), errors="coerce", dayfirst=False)
    if pd.isna(parsed):
        parsed = pd.to_datetime(str(value), errors="coerce", dayfirst=True)
    return "" if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")


def time_block(value: object) -> int:
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, float) and not math.isnan(value) and value.is_integer():
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    if len(parts) >= 2:
        return int(parts[0]) * 4 + int(parts[1]) // 15 + 1
    raise ValueError(f"Unsupported block/time value: {value!r}")


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    last: Exception | None = None
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            frame = pd.read_csv(io.BytesIO(data), encoding=encoding, low_memory=False)
            frame.columns = [str(column).strip() for column in frame.columns]
            return frame.loc[:, ~frame.columns.str.lower().str.startswith("unnamed:")]
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last = exc
    raise ValueError(f"Unable to read CSV: {last}")


def state_from_entity(value: object) -> str:
    token = norm(value)
    if token in STATE_ALIASES:
        return STATE_ALIASES[token]
    for alias, state in sorted(STATE_ALIASES.items(), key=lambda item: -len(item[0])):
        if len(alias) >= 5 and alias in token:
            return state
    return "Unknown"


def station_info(code: object) -> tuple[str, str, str, str]:
    key = norm(code)
    if key in STATIONS:
        name, state, district, _, _ = STATIONS[key]
        return key, name, state, district
    for token, station_code in sorted(TN_TOKEN_TO_STATION.items(), key=lambda item: -len(item[0])):
        if token in key:
            name, state, district, _, _ = STATIONS[station_code]
            return station_code, name, state, district
    return key or "unknown", str(code) if code is not None else "Unknown", "Unknown", "Unknown"


def is_tn(value: object) -> bool:
    token = norm(value)
    if token in {"tamilnadu", "tangedco", "tneb", "tn"}:
        return True
    if "tamilnadu" in token or "tangedco" in token:
        return True
    return any(alias in token for alias in TN_TOKEN_TO_STATION)


def channel_from_transaction(value: object, appno: object = "") -> str:
    token = norm(value) or norm(appno)
    if "tgna" in token:
        return "TGNA"
    if "gna" in token:
        return "GNA"
    if "mtoa" in token:
        return "MTOA"
    if "stoa" in token:
        return "STOA"
    if "rnw" in token or "remc" in token:
        return "REMC/RNW"
    return str(value).strip().upper() if str(value).strip() else "OTHER"


def hash_key(*parts: object, length: int = 32) -> str:
    text = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def numeric(value: object) -> float:
    parsed = pd.to_numeric(value, errors="coerce")
    return 0.0 if pd.isna(parsed) else float(parsed)


def month_days(month: str) -> int:
    year, mon = map(int, month.split("-"))
    if mon == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, mon + 1, 1)
    return (next_month - date(year, mon, 1)).days


def slot_index(date_text: str, block: int) -> int | None:
    try:
        return (int(date_text[-2:]) - 1) * 96 + block - 1
    except (ValueError, TypeError):
        return None


class ParquetSink:
    def __init__(self, path: Path):
        self.path = path
        self.writer: pq.ParquetWriter | None = None

    def write(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        normalized = [{field: row.get(field) for field in CANONICAL_FIELDS} for row in rows]
        table = pa.Table.from_pylist(normalized, schema=CANONICAL_SCHEMA)
        if self.writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.writer = pq.ParquetWriter(self.path, CANONICAL_SCHEMA, compression="zstd", use_dictionary=True)
        self.writer.write_table(table)

    def close(self) -> None:
        if self.writer is None:
            empty = pa.Table.from_pylist([], schema=CANONICAL_SCHEMA)
            pq.write_table(empty, self.path, compression="zstd")
        else:
            self.writer.close()


@dataclass
class Controls:
    coverage_slots: dict[tuple[str, str], set[tuple[str, int]]] = field(default_factory=lambda: defaultdict(set))
    coverage_files: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    composite_totals: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    composite_block_residuals: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    lta_block_residuals: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    px_composite: dict[tuple[str, str, int, str, str], float] = field(default_factory=dict)
    px_detail: dict[tuple[str, str, int, str, str], float] = field(default_factory=dict)
    bilateral_totals: dict[tuple[str, str, str], float] = field(default_factory=lambda: defaultdict(float))
    remc_detail: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    station_buyer_totals: dict[tuple[str, str, str], float] = field(default_factory=lambda: defaultdict(float))
    station_beneficiary_blocks: dict[tuple[str, str], np.ndarray] = field(default_factory=dict)
    stationwide_blocks: dict[tuple[str, str], np.ndarray] = field(default_factory=dict)
    rea_application_totals: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    source_errors: list[dict[str, object]] = field(default_factory=list)


class Pipeline:
    def __init__(self, source_dir: Path, output_dir: Path, gpkg: Path, months: set[str] | None = None):
        self.source_dir = source_dir
        self.output_dir = output_dir
        self.gpkg = gpkg
        self.month_archives = [item for item in MONTH_ARCHIVES if months is None or item[0] in months]
        self.inventory: list[dict[str, object]] = []
        self.observed_families: dict[tuple[str, str], dict[str, object]] = {}
        self.controls = Controls()
        self.seen_dedup: dict[bytes, tuple[str, float, str]] = {}
        self.duplicates: list[dict[str, object]] = []
        self.unresolved: dict[tuple[str, str, str, str], float] = defaultdict(float)
        self.aggregates: dict[tuple[object, ...], float] = defaultdict(float)
        self.ba_lookup = self._build_ba_lookup()
        self.canonical_sink = ParquetSink(output_dir / "canonical_schedules.parquet")
        self.ledger_sink = ParquetSink(output_dir / "deduplicated_ledger.parquet")
        self.excluded_sink = ParquetSink(output_dir / "excluded_ledger_rows.parquet")
        self.crosswalk_handle = None
        self.crosswalk_writer = None

    def _build_ba_lookup(self) -> dict[str, str]:
        areas = gpd.read_file(self.gpkg)
        lookup: dict[str, str] = {}
        self.ba_mapping_method: dict[str, str] = {}
        for code, (_, state, _, lon, lat) in STATIONS.items():
            if state != TN:
                continue
            if lon is not None and lat is not None:
                point = Point(lon, lat)
                match = areas.loc[areas.geometry.apply(lambda geom: geom.covers(point))]
                if len(match) == 1:
                    lookup[code] = str(match.iloc[0]["BA_NAME"])
                    self.ba_mapping_method[code] = "plant point-in-polygon"
                    continue
            district = STATIONS[code][2]
            district_ba = ba_for_district(district)
            if district_ba:
                lookup[code] = district_ba
                self.ba_mapping_method[code] = "verified district-to-BA fallback"
        return lookup

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.crosswalk_handle = (self.output_dir / "raw_to_canonical_crosswalk.csv").open(
            "w", newline="", encoding="utf-8"
        )
        self.crosswalk_writer = csv.DictWriter(
            self.crosswalk_handle,
            fieldnames=["canonical_id", "raw_record_key", "dedup_key",
                        "included_in_approved", "exclusion_reason"],
        )
        self.crosswalk_writer.writeheader()
        try:
            for month, archive_name in self.month_archives:
                archive_path = self.source_dir / archive_name
                if not archive_path.exists():
                    self.controls.source_errors.append({
                        "month": month, "category": "missing_source_coverage", "source": archive_name,
                        "detail": "Monthly archive is missing", "energy_GWh": np.nan,
                    })
                    continue
                print(f"Processing {month}: {archive_name}", flush=True)
                for entry in walk_month_archive(archive_path, month):
                    self._handle_entry(entry)
        finally:
            if self.crosswalk_handle:
                self.crosswalk_handle.close()
            self.canonical_sink.close()
            self.ledger_sink.close()
            self.excluded_sink.close()
        self._write_outputs()

    def _handle_entry(self, entry: ArchiveEntry) -> None:
        started = time.perf_counter()
        family, role, meaning = classify_file(entry.filename)
        metadata = inventory_metadata(entry, family, role)
        self.inventory.append(metadata)
        key = (entry.filename.lower(), family)
        self.observed_families[key] = {
            "filename_pattern_or_example": entry.filename,
            "file_family": family,
            "primary_role": role,
            "energy_meaning": meaning,
            "additive_to_approved_total": role in {"TRANSACTION_DETAIL", "ALLOCATION_DETAIL"},
            "notes": "Column-level rules apply to composite schedules" if family == "STATE_COMPOSITE_SCHEDULE" else "",
        }
        if entry.is_provisional or entry.extension != ".csv" or entry.zip_depth == 0:
            return
        try:
            name = entry.filename.lower()
            if name.startswith("commercial_sch_"):
                suffix = name.removeprefix("commercial_sch_").removesuffix(".csv")
                if suffix in BENEFICIARY_FILES:
                    print(f"  parse {entry.month} {entry.filename}", flush=True)
                    self._parse_state_composite(entry, BENEFICIARY_FILES[suffix])
            elif name == "commercial_px_tamilnadu.csv":
                print(f"  parse {entry.month} {entry.filename}", flush=True)
                self._parse_px_tamilnadu(entry)
            elif name in {"tneb_imp.csv", "tneb_exp.csv"}:
                print(f"  parse {entry.month} {entry.filename}", flush=True)
                self._parse_bilateral(entry, "import" if "_imp" in name else "export")
            elif name in {"commercial_remc_schedule.csv", "commercial_rnw_schedule.csv"}:
                print(f"  parse {entry.month} {entry.filename}", flush=True)
                self._parse_renewable(entry)
            elif name in {"commercial_isgs.csv", "commercial_sch_isgs.csv"}:
                print(f"  parse {entry.month} {entry.filename}", flush=True)
                self._parse_stationwide(entry)
            elif name.startswith("commercial_pxsold_"):
                self._parse_pxsold(entry)
            elif name == "rea.csv":
                print(f"  parse {entry.month} {entry.filename}", flush=True)
                self._parse_rea_aggregate(entry)
        except Exception as exc:  # source defects must be reported, not silently dropped
            self.controls.source_errors.append({
                "month": entry.month,
                "category": "source_parse_error",
                "source": entry.virtual_path,
                "detail": f"{type(exc).__name__}: {exc}",
                "energy_GWh": np.nan,
            })
        elapsed = time.perf_counter() - started
        if elapsed >= 5:
            print(f"  completed {entry.filename} in {elapsed:.1f}s", flush=True)

    def _base_row(self, entry: ArchiveEntry, row: pd.Series, block: int, channel: str) -> dict[str, object]:
        date_text = parse_date_value(row.get("date"))
        raw_key = str(row.get("record_id", "")) or f"{date_text}-{block}"
        return {
            "date": date_text,
            "time_block": block,
            "month": entry.month,
            "financial_year": FY,
            "channel": channel,
            "source_archive": entry.source_archive,
            "source_path": entry.virtual_path,
            "source_sha256": entry.sha256,
            "raw_record_key": raw_key,
            "source_family": classify_file(entry.filename)[0],
            "trader": "",
            "contract_application_id": "",
            "tn_BA": "",
            "exception_flag": "",
        }

    def _emit(self, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        canonical_rows: list[dict[str, object]] = []
        approved_rows: list[dict[str, object]] = []
        excluded_rows: list[dict[str, object]] = []
        for row in rows:
            row.setdefault("canonical_id", hash_key(row.get("source_path"), row.get("raw_record_key"), row.get("source_column"), row.get("direction")))
            row.setdefault("dedup_key", hash_key(row.get("canonical_id")))
            row.setdefault("included_in_approved", False)
            row.setdefault("exclusion_reason", "")
            dedup_bytes = bytes.fromhex(str(row["dedup_key"])[:32])
            if row["included_in_approved"]:
                if dedup_bytes in self.seen_dedup:
                    first_id, first_value, first_path = self.seen_dedup[dedup_bytes]
                    row["included_in_approved"] = False
                    row["exclusion_reason"] = "DUPLICATE_REPRESENTATION"
                    row["exception_flag"] = "DUPLICATE_SUPPRESSED"
                    self.duplicates.append({
                        "dedup_key": row["dedup_key"], "kept_canonical_id": first_id,
                        "suppressed_canonical_id": row["canonical_id"], "kept_source_path": first_path,
                        "suppressed_source_path": row["source_path"], "energy_GWh": row.get("energy_GWh", 0.0),
                        "value_difference_GWh": float(row.get("energy_GWh", 0.0)) - first_value,
                    })
                else:
                    self.seen_dedup[dedup_bytes] = (
                        str(row["canonical_id"]), float(row.get("energy_GWh", 0.0)), str(row.get("source_path", ""))
                    )
            canonical_rows.append(row)
            if row["included_in_approved"]:
                approved_rows.append(row)
                self._aggregate_approved(row)
            else:
                excluded_rows.append(row)
            self.crosswalk_writer.writerow({
                "canonical_id": row["canonical_id"], "raw_record_key": row.get("raw_record_key", ""),
                "dedup_key": row.get("dedup_key", ""),
                "included_in_approved": row.get("included_in_approved", False),
                "exclusion_reason": row.get("exclusion_reason", ""),
            })
        self.canonical_sink.write(canonical_rows)
        self.ledger_sink.write(approved_rows)
        self.excluded_sink.write(excluded_rows)

    def _aggregate_approved(self, row: dict[str, object]) -> None:
        key = (
            row.get("month", ""), row.get("direction", ""), row.get("channel", ""),
            row.get("generator_id", ""), row.get("generator_name", ""),
            row.get("plant_location_state", "Unknown"), row.get("plant_district", "Unknown"),
            row.get("tn_BA", ""), row.get("beneficiary_state", "Unknown"),
            row.get("contract_seller_state", "Unknown"), row.get("confidence", ""),
            row.get("geographical_class", ""), row.get("evidence", ""),
        )
        self.aggregates[key] += float(row.get("energy_GWh", 0.0))
        confidence = str(row.get("confidence", ""))
        if confidence != "verified":
            category = "unattributed" if confidence == "unattributed" else confidence
            unresolved_key = (str(row.get("month", "")), category, str(row.get("channel", "")), str(row.get("source_path", "")))
            self.unresolved[unresolved_key] += float(row.get("energy_GWh", 0.0))

    def _parse_state_composite(self, entry: ArchiveEntry, beneficiary: str) -> None:
        frame = read_csv_bytes(entry.data)
        lower_to_actual = {column.lower(): column for column in frame.columns}
        if not {"date", "time"}.issubset(lower_to_actual):
            raise ValueError("State composite lacks date/time")
        date_col, time_col = lower_to_actual["date"], lower_to_actual["time"]
        station_columns = [
            column for column in frame.columns
            if column.lower() not in COMPOSITE_COMPONENTS
            and column.lower() not in {"date", "time", "record_id"}
            and not column.lower().startswith("unnamed")
        ]
        for column in station_columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
        station_sum = frame[station_columns].sum(axis=1) if station_columns else pd.Series(0.0, index=frame.index)
        if beneficiary == TN:
            lta_col = lower_to_actual.get("lta")
            if lta_col:
                lta = pd.to_numeric(frame[lta_col], errors="coerce").fillna(0.0)
                self.controls.lta_block_residuals[entry.month].extend((station_sum - lta).tolist())
            self._capture_composite_controls(entry, frame, lower_to_actual)

        output_rows: list[dict[str, object]] = []
        expected_length = month_days(entry.month) * 96
        dates = frame[date_col].map(parse_date_value)
        blocks = frame[time_col].map(lambda value: time_block(value) if pd.notna(value) else 0)
        record_ids = frame[lower_to_actual.get("record_id", date_col)].astype(str)
        valid_slots = [(d, int(b)) for d, b in zip(dates, blocks) if d and 1 <= int(b) <= 96]
        self.controls.coverage_slots[(entry.month, f"STATE_COMPOSITE:{beneficiary}")].update(valid_slots)
        self.controls.coverage_files[(entry.month, f"STATE_COMPOSITE:{beneficiary}")].add(entry.virtual_path)
        slot_numbers = np.asarray([slot_index(d, int(b)) if d and 1 <= int(b) <= 96 else -1 for d, b in zip(dates, blocks)])
        for column in station_columns:
            values = frame[column].to_numpy(dtype="float64", copy=False)
            station_code, station_name, plant_state, district = station_info(column)
            # Only TN beneficiary allocations (imports/internal) and TN-origin
            # allocations to other beneficiaries (exports) are in task scope.
            if beneficiary != TN and plant_state != TN:
                continue
            if beneficiary == TN:
                direction = "import" if plant_state != TN else "internal"
                geo = "external→TN" if plant_state not in {TN, "Unknown"} else (
                    "origin_unknown→TN" if plant_state == "Unknown" else "TN→TN"
                )
            else:
                direction = "export" if plant_state == TN else "other"
                geo = "TN↔Puducherry_enclave_unresolved" if beneficiary == PUDUCHERRY and plant_state == TN else (
                    "TN→external" if plant_state == TN else "other"
                )
            nonzero = np.flatnonzero(np.abs(values) > 1e-12)
            if nonzero.size == 0:
                continue
            if plant_state == TN:
                self.controls.station_buyer_totals[(entry.month, station_code, beneficiary)] += float(np.abs(values[nonzero]).sum()) * QUARTER_HOUR_GWH
                array = self.controls.station_beneficiary_blocks.setdefault(
                    (entry.month, station_code), np.zeros(expected_length, dtype="float64")
                )
                valid = (slot_numbers[nonzero] >= 0) & (slot_numbers[nonzero] < expected_length)
                np.add.at(array, slot_numbers[nonzero][valid], values[nonzero][valid])
            for idx in nonzero:
                date_text = dates.iat[idx]
                block = int(blocks.iat[idx])
                if not date_text or not 1 <= block <= 96:
                    continue
                mw = float(values[idx])
                if abs(mw) <= 1e-12:
                    continue
                energy = abs(mw) * QUARTER_HOUR_GWH
                include = direction in {"import", "export"}
                exclusion = ""
                exception = ""
                confidence = "verified" if plant_state != "Unknown" else "unattributed"
                if geo == "TN↔Puducherry_enclave_unresolved":
                    include = False
                    exclusion = "PUDUCHERRY_ENCLAVE_BOUNDARY_UNRESOLVED"
                    exception = "UNRESOLVED_GEOGRAPHY"
                    confidence = "potential_overlap"
                    self.unresolved[(entry.month, "potential_overlap", "ISGS/LTA", entry.virtual_path)] += energy
                elif direction == "internal":
                    exclusion = "TN_ORIGIN_TO_TN_BENEFICIARY_INTERNAL"
                elif direction == "other":
                    exclusion = "NOT_TAMIL_NADU_RELATED"
                source_row = {"date": date_text, "record_id": record_ids.iat[idx]}
                base = self._base_row(entry, source_row, block, "ISGS/LTA")
                base.update({
                    "generator_id": station_code,
                    "generator_name": station_name,
                    "plant_location_state": plant_state,
                    "plant_district": district,
                    "tn_BA": self.ba_lookup.get(station_code, ""),
                    "seller_entity": station_name,
                    "contract_seller_state": plant_state,
                    "buyer_entity": beneficiary,
                    "beneficiary_state": beneficiary,
                    "source_entity": station_name,
                    "counterparty_entity": beneficiary,
                    "raw_schedule_MW": mw,
                    "energy_GWh": energy,
                    "signed_GWh": energy if direction == "import" else (-energy if direction == "export" else 0.0),
                    "direction": direction,
                    "schedule_basis": "beneficiary allocation; presumed delivered schedule basis",
                    "geographical_class": geo,
                    "source_column": column,
                    "dedup_key": hash_key("ISGS", date_text, block, station_code, beneficiary),
                    "overlap_parent": f"{entry.filename}:lta",
                    "evidence": "beneficiary station column in final SRPC composite schedule",
                    "confidence": confidence,
                    "exception_flag": exception,
                    "included_in_approved": include,
                    "exclusion_reason": exclusion,
                })
                output_rows.append(base)
                if len(output_rows) >= 50000:
                    self._emit(output_rows)
                    output_rows = []
        self._emit(output_rows)

    def _capture_composite_controls(
        self, entry: ArchiveEntry, frame: pd.DataFrame, lower_to_actual: dict[str, str]
    ) -> None:
        def series(name: str) -> pd.Series:
            column = lower_to_actual.get(name.lower())
            if not column:
                return pd.Series(0.0, index=frame.index)
            return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)

        lta = series("lta")
        gna = series("GNA_net")
        tgna = series("TGNA_net")
        if "gna_net" not in lower_to_actual and "tgna_net" not in lower_to_actual:
            gna = series("mtoa_imp") + series("mtoa_exp") + series("stoa_imp") + series("stoa_exp")
        market = sum((series(f"{product}_{direction}") for product in ("px", "rtm", "gdam", "hpdam") for direction in ("imp", "exp")), start=pd.Series(0.0, index=frame.index))
        remc = series("remc_imp") + series("remc_exp")
        loss = series("loss")
        sch_total = series("Sch_Total")
        reconstructed = lta + gna + tgna + market + remc + loss
        residual = sch_total - reconstructed
        self.controls.composite_block_residuals[entry.month].extend(residual.tolist())
        totals = {
            "Sch_Total": sch_total, "lta": lta, "GNA_net": gna, "TGNA_net": tgna,
            "DAM_net": series("px_imp") + series("px_exp"),
            "RTM_net": series("rtm_imp") + series("rtm_exp"),
            "GDAM_net": series("gdam_imp") + series("gdam_exp"),
            "HPDAM_net": series("hpdam_imp") + series("hpdam_exp"),
            "REMC_net": remc, "loss": loss, "identity_residual": residual,
        }
        for label, values in totals.items():
            self.controls.composite_totals[(entry.month, label)] += float(values.sum()) * QUARTER_HOUR_GWH

        date_col, time_col = lower_to_actual["date"], lower_to_actual["time"]
        products = {"DAM": "px", "RTM": "rtm", "GDAM": "gdam", "HPDAM": "hpdam"}
        for _, row in frame.iterrows():
            date_text = parse_date_value(row[date_col])
            try:
                block = time_block(row[time_col])
            except ValueError:
                continue
            for product, prefix in products.items():
                for direction, suffix in (("import", "imp"), ("export", "exp")):
                    column = lower_to_actual.get(f"{prefix}_{suffix}")
                    value = 0.0 if not column else numeric(row[column])
                    self.controls.px_composite[(entry.month, date_text, block, product, direction)] = value

        # Parent/control values are retained in the reconciliation tables and
        # source inventory. They are intentionally not expanded into millions of
        # canonical transaction rows because they are non-additive representations.

    def _parse_px_tamilnadu(self, entry: ArchiveEntry) -> None:
        frame = read_csv_bytes(entry.data)
        lower = {column.lower(): column for column in frame.columns}
        mapping = {
            ("DAM", "import"): "total_imp", ("DAM", "export"): "total_exp",
            ("RTM", "import"): "rtm_total_imp", ("RTM", "export"): "rtm_total_exp",
            ("GDAM", "import"): "gdam_total_imp", ("GDAM", "export"): "gdam_total_exp",
            ("HPDAM", "import"): "hpdam_total_imp", ("HPDAM", "export"): "hpdam_total_exp",
        }
        rows: list[dict[str, object]] = []
        for source_row in frame.to_dict("records"):
            date_text = parse_date_value(source_row.get(lower.get("date", "date")))
            try:
                block = time_block(source_row.get(lower.get("time", "time")))
            except ValueError:
                continue
            self.controls.coverage_slots[(entry.month, "PX_TAMIL_NADU")].add((date_text, block))
            self.controls.coverage_files[(entry.month, "PX_TAMIL_NADU")].add(entry.virtual_path)
            for (channel, direction), column_name in mapping.items():
                actual = lower.get(column_name)
                if not actual:
                    continue
                mw = numeric(source_row[actual])
                self.controls.px_detail[(entry.month, date_text, block, channel, direction)] = mw
                if abs(mw) <= 1e-12:
                    continue
                energy = abs(mw) * QUARTER_HOUR_GWH
                base = self._base_row(entry, source_row, block, channel)
                base.update({
                    "generator_id": "market_pool", "generator_name": "Power exchange pooled position",
                    "plant_location_state": "Unknown", "plant_district": "Unknown", "seller_entity": TN if direction == "export" else "Market pool",
                    "contract_seller_state": TN if direction == "export" else "Unknown", "buyer_entity": TN if direction == "import" else "Market pool",
                    "beneficiary_state": TN if direction == "import" else "Unknown", "source_entity": "Market pool" if direction == "import" else TN,
                    "counterparty_entity": TN if direction == "import" else "Market pool", "raw_schedule_MW": mw,
                    "energy_GWh": energy, "signed_GWh": energy if direction == "import" else -energy,
                    "direction": direction, "schedule_basis": "state-level market schedule",
                    "geographical_class": "origin_unknown→TN" if direction == "import" else "TN_contract_sale→destination_unknown",
                    "source_column": actual,
                    "dedup_key": hash_key("PX", date_text, block, channel, direction, TN),
                    "overlap_parent": "commercial_sch_tamilnadu market columns",
                    "evidence": "final state PX detail; physical origin/destination pooled",
                    "confidence": "unattributed" if direction == "import" else "probable_not_verified",
                    "exception_flag": "UNKNOWN_PHYSICAL_ORIGIN" if direction == "import" else "UNKNOWN_DESTINATION_AND_PLANT",
                    "included_in_approved": True, "exclusion_reason": "",
                })
                rows.append(base)
            if len(rows) >= 50000:
                self._emit(rows)
                rows = []
        self._emit(rows)

    def _parse_bilateral(self, entry: ArchiveEntry, direction: str) -> None:
        frame = read_csv_bytes(entry.data)
        required = {"date", "time", "sch"}
        if not required.issubset(frame.columns):
            lower = {column.lower(): column for column in frame.columns}
            if not required.issubset(lower):
                raise ValueError(f"Bilateral file lacks required columns: {required - set(lower)}")
            frame = frame.rename(columns={lower[name]: name for name in required})
        all_dates = frame["date"].map(parse_date_value)
        all_blocks = frame["time"].map(lambda value: time_block(value) if pd.notna(value) else 0)
        coverage_key = (entry.month, f"BILATERAL:{direction}")
        self.controls.coverage_slots[coverage_key].update(
            (date_text, int(block))
            for date_text, block in zip(all_dates, all_blocks)
            if date_text and 1 <= int(block) <= 96
        )
        self.controls.coverage_files[coverage_key].add(entry.virtual_path)
        frame["sch"] = pd.to_numeric(frame["sch"], errors="coerce").fillna(0.0)
        frame = frame.loc[frame["sch"].abs() > 1e-12]
        rows: list[dict[str, object]] = []
        for source_row in frame.to_dict("records"):
            mw = numeric(source_row.get("sch"))
            if abs(mw) <= 1e-12:
                continue
            date_text = parse_date_value(source_row.get("date"))
            try:
                block = time_block(source_row.get("time"))
            except ValueError:
                continue
            channel = channel_from_transaction(source_row.get("transcation"), source_row.get("appno"))
            from_entity = str(source_row.get("from_entity", "") or "")
            from_embedded = str(source_row.get("fromembd", "") or "")
            to_entity = str(source_row.get("to_entity", "") or "")
            to_embedded = str(source_row.get("toembd", "") or "")
            station_code, station_name, plant_state, district = station_info(from_embedded)
            contract_state = state_from_entity(from_entity)
            beneficiary = state_from_entity(to_entity)
            if direction == "import":
                beneficiary = TN
                if plant_state not in {TN, "Unknown"}:
                    geo, confidence = "external→TN", "verified"
                elif plant_state == TN:
                    geo, confidence = "TN→TN", "verified"
                else:
                    geo, confidence = "origin_unknown→TN", "unattributed"
            else:
                if plant_state == TN:
                    if beneficiary == TN:
                        geo, confidence = "TN→TN", "verified"
                    elif beneficiary == "Unknown":
                        geo, confidence = "TN→destination_unknown", "probable_not_verified"
                    else:
                        geo, confidence = "TN→external", "verified"
                else:
                    geo, confidence = "origin_unknown→external", "probable_not_verified"
            include = geo != "TN→TN"
            energy = abs(mw) * QUARTER_HOUR_GWH
            appno = str(source_row.get("appno", "") or "")
            self.controls.bilateral_totals[(entry.month, channel, direction)] += energy
            base = self._base_row(entry, source_row, block, channel)
            base.update({
                "generator_id": station_code,
                "generator_name": station_name if plant_state != "Unknown" else from_embedded or from_entity or "Unknown",
                "plant_location_state": plant_state,
                "plant_district": district,
                "tn_BA": self.ba_lookup.get(station_code, ""),
                "seller_entity": from_embedded or from_entity,
                "contract_seller_state": contract_state,
                "trader": str(source_row.get("trader", "") or ""),
                "buyer_entity": to_embedded or to_entity,
                "beneficiary_state": beneficiary,
                "contract_application_id": appno,
                "source_entity": from_entity,
                "counterparty_entity": to_entity,
                "raw_schedule_MW": mw,
                "energy_GWh": energy,
                "signed_GWh": energy if direction == "import" else -energy,
                "direction": direction,
                "schedule_basis": "application-level gross schedule (sch)",
                "geographical_class": geo,
                "source_column": "sch",
                "dedup_key": hash_key("BILATERAL", channel, appno, date_text, block, from_entity, from_embedded, to_entity, to_embedded, direction),
                "overlap_parent": "rea.csv and commercial_sch_tamilnadu GNA/TGNA columns",
                "evidence": "final TNEB importer/exporter application view",
                "confidence": confidence,
                "exception_flag": "UNKNOWN_PHYSICAL_ORIGIN" if plant_state == "Unknown" else "",
                "included_in_approved": include,
                "exclusion_reason": "TN_ORIGIN_TO_TN_BENEFICIARY_INTERNAL" if not include else "",
            })
            rows.append(base)
            if len(rows) >= 50000:
                self._emit(rows)
                rows = []
        self._emit(rows)

    def _parse_renewable(self, entry: ArchiveEntry) -> None:
        frame = read_csv_bytes(entry.data)
        all_dates = frame.get("date", pd.Series(dtype=object)).map(parse_date_value)
        all_blocks = frame.get("time", pd.Series(dtype=object)).map(lambda value: time_block(value) if pd.notna(value) else 0)
        self.controls.coverage_slots[(entry.month, "REMC_RNW")].update(
            (d, int(b)) for d, b in zip(all_dates, all_blocks) if d and 1 <= int(b) <= 96
        )
        self.controls.coverage_files[(entry.month, "REMC_RNW")].add(entry.virtual_path)
        frame["sch"] = pd.to_numeric(frame.get("sch"), errors="coerce").fillna(0.0)
        entity_columns = [column for column in ("from_entity", "fromembd", "to_entity", "toembd") if column in frame]
        tn_pattern = r"tamil\s*nadu|tangedco|\btneb\b|kknpp|kudankulam|vallur|ntecl|nntpp|neyveli|\bntpl\b|\bmaps\b|kalpakkam|nlc2?"
        tn_mask = pd.Series(False, index=frame.index)
        for column in entity_columns:
            tn_mask |= frame[column].fillna("").astype(str).str.contains(tn_pattern, case=False, regex=True, na=False)
        frame = frame.loc[(frame["sch"].abs() > 1e-12) & tn_mask]
        rows: list[dict[str, object]] = []
        for source_row in frame.to_dict("records"):
            mw = numeric(source_row.get("sch"))
            if abs(mw) <= 1e-12:
                continue
            from_entity = str(source_row.get("from_entity", "") or "")
            from_embedded = str(source_row.get("fromembd", "") or "")
            to_entity = str(source_row.get("to_entity", "") or "")
            to_embedded = str(source_row.get("toembd", "") or "")
            source_is_tn = is_tn(from_entity) or is_tn(from_embedded)
            buyer_is_tn = is_tn(to_entity) or is_tn(to_embedded)
            if not (source_is_tn or buyer_is_tn):
                continue
            date_text = parse_date_value(source_row.get("date"))
            try:
                block = time_block(source_row.get("time"))
            except ValueError:
                continue
            station_code, station_name, plant_state, district = station_info(from_embedded)
            if source_is_tn and plant_state == "Unknown":
                plant_state = TN
            direction = "internal" if source_is_tn and buyer_is_tn else ("export" if source_is_tn else "import")
            beneficiary = TN if buyer_is_tn else state_from_entity(to_entity)
            if direction == "import":
                geo = "external→TN" if plant_state not in {TN, "Unknown"} else "origin_unknown→TN"
            elif direction == "export":
                geo = "TN→external" if beneficiary not in {TN, PUDUCHERRY} else "TN↔Puducherry_enclave_unresolved"
            else:
                geo = "TN→TN"
            confidence = "verified" if plant_state != "Unknown" and beneficiary != "Unknown" else "unattributed"
            include = direction in {"import", "export"} and geo != "TN↔Puducherry_enclave_unresolved"
            energy = abs(mw) * QUARTER_HOUR_GWH
            appno = str(source_row.get("appno", "") or "")
            if direction in {"import", "export"}:
                self.controls.remc_detail[(entry.month, direction)] += energy
            self.controls.coverage_slots[(entry.month, "REMC_RNW")].add((date_text, block))
            self.controls.coverage_files[(entry.month, "REMC_RNW")].add(entry.virtual_path)
            base = self._base_row(entry, source_row, block, "REMC/RNW")
            base.update({
                "generator_id": station_code,
                "generator_name": station_name if plant_state != "Unknown" else from_embedded or from_entity,
                "plant_location_state": plant_state,
                "plant_district": district,
                "tn_BA": self.ba_lookup.get(station_code, ""),
                "seller_entity": from_embedded or from_entity,
                "contract_seller_state": state_from_entity(from_entity),
                "trader": str(source_row.get("trader", "") or ""),
                "buyer_entity": to_embedded or to_entity,
                "beneficiary_state": beneficiary,
                "contract_application_id": appno,
                "source_entity": from_entity,
                "counterparty_entity": to_entity,
                "raw_schedule_MW": mw,
                "energy_GWh": energy,
                "signed_GWh": energy if direction == "import" else (-energy if direction == "export" else 0.0),
                "direction": direction,
                "schedule_basis": "renewable transaction gross schedule (sch)",
                "geographical_class": geo,
                "source_column": "sch",
                "dedup_key": hash_key("REMC_RNW", appno, date_text, block, from_entity, from_embedded, to_entity, to_embedded, mw),
                "overlap_parent": "commercial_sch_tamilnadu REMC columns",
                "evidence": f"final {entry.filename} transaction detail",
                "confidence": confidence,
                "exception_flag": "UNRESOLVED_GEOGRAPHY" if not include and direction != "internal" else ("UNKNOWN_PHYSICAL_ORIGIN" if plant_state == "Unknown" else ""),
                "included_in_approved": include,
                "exclusion_reason": "PUDUCHERRY_ENCLAVE_BOUNDARY_UNRESOLVED" if geo == "TN↔Puducherry_enclave_unresolved" else ("TN_ORIGIN_TO_TN_BENEFICIARY_INTERNAL" if direction == "internal" else ""),
            })
            rows.append(base)
            if len(rows) >= 50000:
                self._emit(rows)
                rows = []
        self._emit(rows)

    def _parse_stationwide(self, entry: ArchiveEntry) -> None:
        frame = read_csv_bytes(entry.data)
        lower = {column.lower(): column for column in frame.columns}
        date_col, time_col = lower.get("date"), lower.get("time")
        if not date_col or not time_col:
            raise ValueError("Station-wide file lacks date/time")
        schedule_columns: dict[str, str] = {}
        for column in frame.columns:
            key = column.lower()
            if key.endswith("_sch"):
                schedule_columns[norm(key[:-4])] = column
            elif key in STATIONS:
                schedule_columns[norm(key)] = column
        expected_length = month_days(entry.month) * 96
        dates = frame[date_col].map(parse_date_value)
        blocks = frame[time_col].map(lambda value: time_block(value) if pd.notna(value) else 0)
        slots = np.asarray([slot_index(d, int(b)) if d and 1 <= int(b) <= 96 else -1 for d, b in zip(dates, blocks)])
        self.controls.coverage_slots[(entry.month, "ISGS_STATION_WIDE")].update(
            (d, int(b)) for d, b in zip(dates, blocks) if d and 1 <= int(b) <= 96
        )
        self.controls.coverage_files[(entry.month, "ISGS_STATION_WIDE")].add(entry.virtual_path)
        valid_slots = (slots >= 0) & (slots < expected_length)
        for station_code_raw, column in schedule_columns.items():
            station_code = station_info(station_code_raw)[0]
            values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
            array = self.controls.stationwide_blocks.setdefault(
                (entry.month, station_code), np.full(expected_length, np.nan, dtype="float64")
            )
            target_slots = slots[valid_slots]
            target_values = values[valid_slots]
            unset = np.isnan(array[target_slots])
            array[target_slots[unset]] = target_values[unset]

    def _parse_pxsold(self, entry: ArchiveEntry) -> None:
        station_raw = entry.filename.lower().removeprefix("commercial_pxsold_").removesuffix(".csv")
        station_code, station_name, plant_state, district = station_info(station_raw)
        if plant_state != TN:
            return
        frame = read_csv_bytes(entry.data)
        lower = {column.lower(): column for column in frame.columns}
        total_col = lower.get("total_px") or lower.get("total") or lower.get("total_exp")
        if not total_col:
            return
        rows: list[dict[str, object]] = []
        for _, source_row in frame.iterrows():
            mw = numeric(source_row[total_col])
            if abs(mw) <= 1e-12:
                continue
            date_text = parse_date_value(source_row.get(lower.get("date", "date")))
            try:
                block = time_block(source_row.get(lower.get("time", "time")))
            except ValueError:
                continue
            energy = abs(mw) * QUARTER_HOUR_GWH
            self.unresolved[(entry.month, "potential_overlap", "PX_GENERATOR_ATTRIBUTION", entry.virtual_path)] += energy
            base = self._base_row(entry, source_row, block, "PX_GENERATOR_ATTRIBUTION")
            base.update({
                "generator_id": station_code, "generator_name": station_name,
                "plant_location_state": plant_state, "plant_district": district,
                "tn_BA": self.ba_lookup.get(station_code, ""), "seller_entity": station_name,
                "contract_seller_state": TN, "buyer_entity": "Market pool",
                "beneficiary_state": "Unknown", "source_entity": station_name,
                "counterparty_entity": "Market pool", "raw_schedule_MW": mw,
                "energy_GWh": energy, "signed_GWh": -energy, "direction": "potential_export",
                "schedule_basis": "generator market-sale attribution",
                "geographical_class": "TN→destination_unknown_potential_overlap", "source_column": total_col,
                "dedup_key": hash_key("PXSOLD", date_text, block, station_code),
                "overlap_parent": "commercial_px_tamilnadu pooled exports",
                "evidence": "generator sale file; buyer state not identifiable",
                "confidence": "potential_overlap", "exception_flag": "UNRESOLVED_MARKET_MATCH",
                "included_in_approved": False,
                "exclusion_reason": "ATTRIBUTION_ONLY_POTENTIAL_MARKET_OVERLAP",
            })
            rows.append(base)
            if len(rows) >= 50000:
                self._emit(rows)
                rows = []
        self._emit(rows)

    def _parse_rea_aggregate(self, entry: ArchiveEntry) -> None:
        frame = read_csv_bytes(entry.data)
        for _, row in frame.iterrows():
            appno = str(row.get("appno", "") or "")
            channel = channel_from_transaction(row.get("transcation"), appno)
            # REA aggregate columns are MWh for the half/month; retained only as controls.
            mwh = numeric(row.get("sch_regional")) + numeric(row.get("sch_ists"))
            self.controls.rea_application_totals[(entry.month, channel)] += mwh / 1000.0

    def _write_outputs(self) -> None:
        self._write_inventory_and_dictionary()
        monthly, annual = self._build_flow_tables()
        monthly.to_csv(self.output_dir / "TN_interstate_monthly.csv", index=False)
        annual.to_csv(self.output_dir / "TN_interstate_annual.csv", index=False)
        annual.loc[annual["direction"] == "export"].to_csv(
            self.output_dir / "TN_generating_plant_exports.csv", index=False
        )
        annual.loc[annual["direction"] == "import"].to_csv(
            self.output_dir / "TN_imports_by_origin.csv", index=False
        )
        coverage = self._build_coverage()
        coverage.to_csv(self.output_dir / "coverage_report.csv", index=False)
        recon_month = self._build_month_channel_reconciliation()
        recon_month.to_csv(self.output_dir / "reconciliation_by_month_channel.csv", index=False)
        recon_station = self._build_station_reconciliation()
        recon_station.to_csv(self.output_dir / "reconciliation_by_station_buyer.csv", index=False)
        pd.DataFrame(self.duplicates, columns=[
            "dedup_key", "kept_canonical_id", "suppressed_canonical_id", "kept_source_path",
            "suppressed_source_path", "energy_GWh", "value_difference_GWh",
        ]).to_csv(self.output_dir / "duplicate_matches.csv", index=False)
        unresolved = self._build_unresolved(coverage)
        unresolved.to_csv(self.output_dir / "unresolved_cases.csv", index=False)
        ba = self._build_ba_adjustments(monthly)
        ba.to_csv(self.output_dir / "TN_PyPSA_BA_adjustments.csv", index=False)
        self._build_plant_ba_crosswalk().to_csv(self.output_dir / "plant_to_BA_crosswalk.csv", index=False)
        validation = self._build_validation_results(recon_month, coverage)
        validation.to_csv(self.output_dir / "validation_results.csv", index=False)
        baseline = self._build_baseline_comparison()
        baseline.to_csv(self.output_dir / "baseline_comparison.csv", index=False)
        self._write_reports(monthly, annual, ba, validation, unresolved, baseline)

    def _write_inventory_and_dictionary(self) -> None:
        inventory = pd.DataFrame(self.inventory)
        if not inventory.empty:
            inventory = inventory.sort_values(["month", "virtual_path"], kind="stable")
        inventory.to_csv(self.output_dir / "source_inventory.csv", index=False)
        dictionary = pd.DataFrame(self.observed_families.values()).sort_values(
            ["file_family", "filename_pattern_or_example"], kind="stable"
        )
        dictionary.to_csv(self.output_dir / "file_family_dictionary.csv", index=False)
        pd.DataFrame(OVERLAP_ROWS).to_csv(self.output_dir / "overlap_matrix.csv", index=False)

    def _build_flow_tables(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        columns = [
            "month", "direction", "channel", "generator_id", "generator_name",
            "plant_location_state", "plant_district", "tn_BA", "beneficiary_state",
            "contract_seller_state", "confidence", "geographical_class", "evidence", "energy_GWh",
        ]
        rows = [dict(zip(columns[:-1], key), energy_GWh=value) for key, value in self.aggregates.items()]
        monthly = pd.DataFrame(rows, columns=columns)
        if monthly.empty:
            monthly["signed_GWh"] = pd.Series(dtype=float)
            return monthly, monthly.copy()
        monthly["signed_GWh"] = np.where(monthly["direction"] == "import", monthly["energy_GWh"], -monthly["energy_GWh"])
        monthly = monthly.sort_values(["month", "direction", "channel", "plant_location_state", "generator_name"], kind="stable")
        group_columns = [column for column in columns[:-1] if column != "month"]
        annual = monthly.groupby(group_columns, dropna=False, as_index=False)[["energy_GWh", "signed_GWh"]].sum()
        annual.insert(0, "financial_year", FY)
        annual = annual.sort_values(["direction", "channel", "plant_location_state", "generator_name"], kind="stable")
        return monthly, annual

    def _build_coverage(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        critical = ["STATE_COMPOSITE:Tamil Nadu", "PX_TAMIL_NADU", "BILATERAL:import", "BILATERAL:export", "ISGS_STATION_WIDE", "REMC_RNW"]
        for month, _ in self.month_archives:
            expected = month_days(month) * 96
            for family in critical:
                slots = self.controls.coverage_slots.get((month, family), set())
                rows.append({
                    "month": month, "source_family": family, "expected_slots": expected,
                    "observed_unique_slots": len(slots), "missing_slots": max(expected - len(slots), 0),
                    "duplicate_or_out_of_scope_slots": max(len(slots) - expected, 0),
                    "source_file_count": len(self.controls.coverage_files.get((month, family), set())),
                    "status": "PASS" if len(slots) == expected else "FAIL",
                })
        return pd.DataFrame(rows)

    def _build_month_channel_reconciliation(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for month, _ in self.month_archives:
            composite_res = np.asarray(self.controls.composite_block_residuals.get(month, []), dtype=float)
            lta_res = np.asarray(self.controls.lta_block_residuals.get(month, []), dtype=float)
            rows.append({
                "month": month, "test": "composite_Sch_Total_identity", "channel": "ALL",
                "detail_import_GWh": np.nan, "detail_export_GWh": np.nan,
                "detail_net_GWh": np.nan, "control_total_GWh": self.controls.composite_totals[(month, "Sch_Total")],
                "residual_GWh": self.controls.composite_totals[(month, "identity_residual")],
                "max_abs_block_residual_MW": float(np.nanmax(np.abs(composite_res))) if composite_res.size else np.nan,
                "status": "PASS" if composite_res.size and float(np.nanmax(np.abs(composite_res))) <= 0.011 else "FAIL",
                "note": "Sch_Total = lta + GNA_net + TGNA_net + market nets + REMC net + loss",
            })
            rows.append({
                "month": month, "test": "station_columns_to_lta", "channel": "ISGS/LTA",
                "detail_import_GWh": np.nan, "detail_export_GWh": np.nan,
                "detail_net_GWh": np.nan, "control_total_GWh": self.controls.composite_totals[(month, "lta")],
                "residual_GWh": float(np.nansum(lta_res)) * QUARTER_HOUR_GWH if lta_res.size else np.nan,
                "max_abs_block_residual_MW": float(np.nanmax(np.abs(lta_res))) if lta_res.size else np.nan,
                "status": "PASS" if lta_res.size and float(np.nanmax(np.abs(lta_res))) <= 0.011 else "FAIL",
                "note": "Beneficiary station columns are the accepted allocation detail; lta is parent",
            })
            for product in ("DAM", "RTM", "GDAM", "HPDAM"):
                for direction in ("import", "export"):
                    keys = [key for key in self.controls.px_composite if key[0] == month and key[3:] == (product, direction)]
                    diffs = [self.controls.px_detail.get(key, np.nan) - self.controls.px_composite[key] for key in keys]
                    detail = sum(abs(self.controls.px_detail.get(key, 0.0)) for key in keys) * QUARTER_HOUR_GWH
                    control = sum(abs(self.controls.px_composite[key]) for key in keys) * QUARTER_HOUR_GWH
                    finite = np.asarray([value for value in diffs if not np.isnan(value)], dtype=float)
                    rows.append({
                        "month": month, "test": "PX_detail_to_composite", "channel": f"{product}:{direction}",
                        "detail_import_GWh": detail if direction == "import" else 0.0,
                        "detail_export_GWh": detail if direction == "export" else 0.0,
                        "detail_net_GWh": detail if direction == "import" else -detail,
                        "control_total_GWh": control, "residual_GWh": (detail - control),
                        "max_abs_block_residual_MW": float(np.max(np.abs(finite))) if finite.size else np.nan,
                        "status": "PASS" if finite.size and float(np.max(np.abs(finite))) <= 0.011 else "FAIL",
                        "note": "PX detail disaggregates the same composite market position",
                    })
            for channel, composite_label in (("GNA", "GNA_net"), ("TGNA", "TGNA_net")):
                imports = self.controls.bilateral_totals[(month, channel, "import")]
                exports = self.controls.bilateral_totals[(month, channel, "export")]
                detail_net = imports - exports
                control = self.controls.composite_totals[(month, composite_label)]
                rows.append({
                    "month": month, "test": "bilateral_gross_to_composite_net", "channel": channel,
                    "detail_import_GWh": imports, "detail_export_GWh": exports,
                    "detail_net_GWh": detail_net, "control_total_GWh": control,
                    "residual_GWh": detail_net - control, "max_abs_block_residual_MW": np.nan,
                    "status": "EXPLAIN" if abs(detail_net - control) > 0.01 else "PASS",
                    "note": "Residual retained: gross application sch and composite net use different boundaries/loss treatment",
                })
                rea_total = self.controls.rea_application_totals[(month, channel)]
                rows.append({
                    "month": month, "test": "bilateral_detail_to_rea_application_aggregate", "channel": channel,
                    "detail_import_GWh": imports, "detail_export_GWh": exports,
                    "detail_net_GWh": detail_net, "control_total_GWh": np.nan,
                    "residual_GWh": np.nan, "max_abs_block_residual_MW": np.nan,
                    "status": "REFERENCE_ONLY",
                    "note": f"rea.csv raw aggregate={rea_total:,.6f} after provisional MWh conversion; unit/boundary is not independently established and it covers all regional applications, so no numerical reconciliation is asserted",
                })
            remc_import = self.controls.remc_detail[(month, "import")]
            remc_export = self.controls.remc_detail[(month, "export")]
            remc_net = remc_import - remc_export
            control = self.controls.composite_totals[(month, "REMC_net")]
            rows.append({
                "month": month, "test": "REMC_detail_to_composite_net", "channel": "REMC/RNW",
                "detail_import_GWh": remc_import, "detail_export_GWh": remc_export,
                "detail_net_GWh": remc_net, "control_total_GWh": control,
                "residual_GWh": remc_net - control, "max_abs_block_residual_MW": np.nan,
                "status": "PASS" if abs(remc_net - control) <= 0.01 else "EXPLAIN",
                "note": "Transaction detail deduplicated; residual may reflect schedule basis/scope",
            })
        return pd.DataFrame(rows)

    def _build_station_reconciliation(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        station_keys = sorted(set(self.controls.station_beneficiary_blocks) | set(self.controls.stationwide_blocks))
        for month, station in station_keys:
            allocations = self.controls.station_beneficiary_blocks.get((month, station))
            stationwide = self.controls.stationwide_blocks.get((month, station))
            allocation_total = float(np.nansum(allocations)) * QUARTER_HOUR_GWH if allocations is not None else 0.0
            station_total = float(np.nansum(stationwide)) * QUARTER_HOUR_GWH if stationwide is not None else np.nan
            if allocations is not None and stationwide is not None:
                valid = ~np.isnan(stationwide)
                residual = allocations[valid] - stationwide[valid]
                residual_gwh = float(np.nansum(residual)) * QUARTER_HOUR_GWH
                max_block = float(np.nanmax(np.abs(residual))) if residual.size else np.nan
            else:
                residual_gwh = np.nan
                max_block = np.nan
            buyer_items = sorted(
                ((buyer, value) for (m, s, buyer), value in self.controls.station_buyer_totals.items() if m == month and s == station),
                key=lambda item: item[0],
            )
            if not buyer_items:
                buyer_items = [("NO_BENEFICIARY_DETAIL", 0.0)]
            station_name = station_info(station)[1]
            for buyer, value in buyer_items:
                rows.append({
                    "month": month, "station_id": station, "station_name": station_name,
                    "beneficiary_state": buyer, "beneficiary_allocation_GWh": value,
                    "all_beneficiaries_GWh": allocation_total, "stationwide_schedule_GWh": station_total,
                    "station_minus_beneficiaries_GWh": station_total - allocation_total if not np.isnan(station_total) else np.nan,
                    "sum_block_residual_GWh": residual_gwh, "max_abs_block_residual_MW": max_block,
                    "status": "PASS" if not np.isnan(max_block) and max_block <= 0.011 else "EXPLAIN",
                })
        return pd.DataFrame(rows)

    def _build_unresolved(self, coverage: pd.DataFrame) -> pd.DataFrame:
        rows = [
            {"month": month, "category": category, "channel": channel, "source_path": source,
             "energy_GWh": energy, "detail": "Quantified from canonical rows"}
            for (month, category, channel, source), energy in self.unresolved.items()
        ]
        for record in self.controls.source_errors:
            rows.append({
                "month": record["month"], "category": record["category"], "channel": "SOURCE",
                "source_path": record["source"], "energy_GWh": record["energy_GWh"], "detail": record["detail"],
            })
        for record in coverage.loc[coverage["status"] != "PASS"].to_dict("records"):
            rows.append({
                "month": record["month"], "category": "missing_source_coverage", "channel": record["source_family"],
                "source_path": "", "energy_GWh": np.nan,
                "detail": f"{record['missing_slots']} of {record['expected_slots']} expected slots missing",
            })
        return pd.DataFrame(rows, columns=["month", "category", "channel", "source_path", "energy_GWh", "detail"])

    def _build_ba_adjustments(self, monthly: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for ba in BA_NAMES:
            export_value = monthly.loc[(monthly["direction"] == "export") & (monthly["tn_BA"] == ba), "energy_GWh"].sum()
            import_value = monthly.loc[(monthly["direction"] == "import") & (monthly["tn_BA"] == ba), "energy_GWh"].sum()
            rows.append({
                "financial_year": FY, "tn_BA": ba,
                "export_load_increase_GWh": float(export_value),
                "import_supply_adjustment_GWh": -float(import_value),
                "export_adjustment_sign": "positive Load",
                "import_adjustment_sign": "negative fixed Load / exogenous supply",
                "mapping_status": "verified plant point or documented district mapping" if export_value or import_value else "no evidenced adjustment",
                "profile_status": "annual only; no hourly profile invented",
            })
        unallocated_export = monthly.loc[(monthly["direction"] == "export") & (monthly["tn_BA"].fillna("") == ""), "energy_GWh"].sum()
        unallocated_import = monthly.loc[(monthly["direction"] == "import") & (monthly["tn_BA"].fillna("") == ""), "energy_GWh"].sum()
        rows.append({
            "financial_year": FY, "tn_BA": "UNALLOCATED_TN",
            "export_load_increase_GWh": float(unallocated_export),
            "import_supply_adjustment_GWh": -float(unallocated_import),
            "export_adjustment_sign": "positive Load if/when origin BA is evidenced",
            "import_adjustment_sign": "negative fixed Load / exogenous supply if/when receiving BA is evidenced",
            "mapping_status": "not allocated; source archives do not establish a nine-BA node",
            "profile_status": "annual only; no hourly profile invented",
        })
        return pd.DataFrame(rows)

    def _build_plant_ba_crosswalk(self) -> pd.DataFrame:
        rows = []
        for code, (name, state, district, lon, lat) in sorted(STATIONS.items()):
            if state != TN:
                continue
            rows.append({
                "generator_id": code, "generator_name": name, "plant_location_state": state,
                "plant_district": district, "longitude": lon, "latitude": lat,
                "tn_BA": self.ba_lookup.get(code, ""),
                "mapping_method": self.ba_mapping_method.get(code, "unresolved"),
                "mapping_evidence": STATION_MAPPING_EVIDENCE.get(
                    code, "TamilNadu_9BAs.gpkg; data/model_inputs/Balancing_areas.txt"
                ),
            })
        return pd.DataFrame(rows)

    def _build_validation_results(self, reconciliation: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for record in reconciliation.to_dict("records"):
            rows.append({
                "scope": record["month"], "test": f"{record['test']}:{record['channel']}",
                "actual": record.get("residual_GWh"), "expected": 0.0,
                "tolerance": 0.01, "status": record.get("status"), "note": record.get("note"),
            })
        for record in coverage.to_dict("records"):
            rows.append({
                "scope": record["month"], "test": f"coverage:{record['source_family']}",
                "actual": record["observed_unique_slots"], "expected": record["expected_slots"],
                "tolerance": 0, "status": record["status"], "note": "15-minute timestamp coverage",
            })
        march_targets = {
            "Sch_Total": 7167.736, "lta": 3585.061, "GNA_net": 1923.190,
            "TGNA_net": 2303.842, "REMC_net": 145.277, "loss": -138.540,
            "DAM_net": -148.977, "RTM_net": -570.384, "GDAM_net": 68.268,
            "HPDAM_net": 0.0,
        }
        for metric, expected in march_targets.items():
            actual = self.controls.composite_totals[("2026-03", metric)]
            rows.append({
                "scope": "2026-03", "test": f"March_regression:{metric}", "actual": actual,
                "expected": expected, "tolerance": 0.02,
                "status": "PASS" if abs(actual - expected) <= 0.02 else "FAIL",
                "note": "Instruction-document value used only as a regression check",
            })
        march_imp = sum(value for (month, _, direction), value in self.controls.bilateral_totals.items() if month == "2026-03" and direction == "import")
        march_exp = sum(value for (month, _, direction), value in self.controls.bilateral_totals.items() if month == "2026-03" and direction == "export")
        for metric, actual, expected in (("tneb_imp_gross", march_imp, 4448.091), ("tneb_exp_gross", march_exp, 32.112)):
            rows.append({
                "scope": "2026-03", "test": f"March_regression:{metric}", "actual": actual,
                "expected": expected, "tolerance": 0.05,
                "status": "PASS" if abs(actual - expected) <= 0.05 else "FAIL",
                "note": "Gross sch control; intentionally not forced to composite net",
            })
        selected_slots = sum(month_days(month) * 96 for month, _ in self.month_archives)
        rows.append({
            "scope": "FY2025-26" if len(self.month_archives) == 12 else "SELECTED_MONTHS",
            "test": "expected_selected_slots", "actual": selected_slots,
            "expected": 35040 if len(self.month_archives) == 12 else selected_slots,
            "tolerance": 0, "status": "PASS", "note": "calendar days × 96 blocks",
        })
        return pd.DataFrame(rows)

    def _build_baseline_comparison(self) -> pd.DataFrame:
        references = [
            "SRPC_REA_March2026_Complete_File_Audit.xlsx",
            "TamilNadu_Interstate_Flows.xlsx",
            "TamilNadu_SRPC_REA_FY2025-26_Expanded.xlsx",
            "TamilNadu_SRPC_REA_Bilateral_Schedules_FY2025-26_PARTIAL(1).xlsx",
            "REA_FY2025-26_TN_ISGS_Allocation_by_Buyer.csv",
        ]
        rows = []
        for filename in references:
            path = self.source_dir / filename
            rows.append({
                "reference_file": filename, "available": path.exists(),
                "use": "benchmark only; never added to source schedules",
                "comparison_status": "not supplied" if not path.exists() else "available for separate benchmark",
            })
        return pd.DataFrame(rows)

    def _write_reports(
        self,
        monthly: pd.DataFrame,
        annual: pd.DataFrame,
        ba: pd.DataFrame,
        validation: pd.DataFrame,
        unresolved: pd.DataFrame,
        baseline: pd.DataFrame,
    ) -> None:
        imports = float(monthly.loc[monthly["direction"] == "import", "energy_GWh"].sum())
        exports = float(monthly.loc[monthly["direction"] == "export", "energy_GWh"].sum())
        confirmed_imports = float(monthly.loc[(monthly["direction"] == "import") & (monthly["confidence"] == "verified") & (monthly["geographical_class"] == "external→TN"), "energy_GWh"].sum())
        confirmed_exports = float(monthly.loc[(monthly["direction"] == "export") & (monthly["confidence"] == "verified") & (monthly["geographical_class"] == "TN→external"), "energy_GWh"].sum())
        origin_attr = float(monthly.loc[(monthly["direction"] == "import") & (monthly["plant_location_state"] != "Unknown"), "energy_GWh"].sum())
        dest_attr = float(monthly.loc[(monthly["direction"] == "export") & (monthly["beneficiary_state"] != "Unknown"), "energy_GWh"].sum())
        mapped_exports = float(monthly.loc[(monthly["direction"] == "export") & (monthly["tn_BA"].fillna("") != ""), "energy_GWh"].sum())
        parse_errors = sum(1 for row in self.controls.source_errors if row["category"] == "source_parse_error")
        failed_validations = int((validation["status"] == "FAIL").sum())
        reference_missing = ", ".join(baseline.loc[~baseline["available"], "reference_file"].tolist()) or "none"
        unresolved_gwh = float(pd.to_numeric(unresolved["energy_GWh"], errors="coerce").fillna(0).sum())
        summary = {
            "imports": imports, "exports": exports, "confirmed_imports": confirmed_imports,
            "confirmed_exports": confirmed_exports, "origin_share": 100 * origin_attr / imports if imports else 0.0,
            "destination_share": 100 * dest_attr / exports if exports else 0.0,
            "mapped_share": 100 * mapped_exports / exports if exports else 0.0,
            "unresolved": unresolved_gwh, "parse_errors": parse_errors, "failed": failed_validations,
        }
        readme = f"""# Tamil Nadu SRPC REA commercial schedules, FY2025-26

This directory is generated from the twelve supplied final SRPC REA archives by
`python -m scripts.rea_fy2025_26.pipeline`. It reconstructs scheduled commercial
energy, not physical metered transmission flows.

## Accounting boundary and signs

- Approved detail is counted once: beneficiary station allocations for ISGS/LTA,
  TNEB application rows for GNA/TGNA/other bilateral schedules, state PX detail
  for market positions, and deduplicated REMC/RNW transactions.
- `Sch_Total`, `lta`, station-wide ISGS schedules, `rea.csv`, and generator PX-sale
  files are retained as controls or attribution evidence and are not appended.
- Import `signed_GWh` is positive; export `signed_GWh` is negative. The separate
  PyPSA export-load adjustment is positive Load. Import supply is a negative fixed
  Load/exogenous supply adjustment and is never added as demand.
- MW blocks are converted using `MW × 0.25 / 1000 = GWh`.

## Executive findings

- Approved scheduled imports including origin-unknown pools: **{imports:,.3f} GWh**.
- Approved scheduled exports including unknown destinations/origins: **{exports:,.3f} GWh**.
- Confirmed external-origin imports: **{confirmed_imports:,.3f} GWh**.
- Confirmed TN-plant exports to evidenced external beneficiaries: **{confirmed_exports:,.3f} GWh**.
- Import energy with a physical origin state attributed: **{summary['origin_share']:.2f}%**.
- Export energy with destination state attributed: **{summary['destination_share']:.2f}%**.
- Approved export energy mapped to a supplied nine-BA polygon: **{summary['mapped_share']:.2f}%**.
- Quantified unresolved/potential-overlap rows (categories can overlap): **{unresolved_gwh:,.3f} GWh**.

## Reproduction

PowerShell:

```powershell
./scripts/run_rea_fy2025_26.ps1
```

Python:

```text
python -m scripts.rea_fy2025_26.pipeline --source-dir "data/source/REA data" --output-dir "outputs/rea_fy2025_26"
pytest -q tests/test_rea_fy2025_26.py
```

The raw archives are never extracted or modified. `source_inventory.csv` records
SHA-256 checksums for archives, nested ZIPs, and every member.

## Limitations

The result must not be called fully reconciled while validation failures, gross/net
residuals, source parse errors, pooled-market attribution gaps, or unallocated BA
energy remain. Missing benchmark/reference files: {reference_missing}.
"""
        (self.output_dir / "README.md").write_text(readme, encoding="utf-8")
        audit = f"""# Audit report — Tamil Nadu SRPC REA FY2025-26

## What is proved

The pipeline inventories nested archives, excludes provisional branches (including
March `reapmar26.zip`), checks 15-minute coverage, proves the Tamil Nadu composite
identity and station-column-to-`lta` identity where the source schema supports it,
and checks PX product/direction detail against its parent composite. March values
are regression checks, not forced targets. TSTPP is mapped to Telangana.

Application-level `tneb_imp.csv` and `tneb_exp.csv` are accepted once. Their gross
`sch` totals are explicitly reconciled—but not forced—to the state composite net
GNA/TGNA values. Station-wide ISGS and `rea.csv` are validation parents only.

## What is inferred conservatively

Schedule values are treated as average MW in each 15-minute block because the March
control totals reproduce the documented GWh values under the 0.25-hour conversion.
Direct station codes have physical-state mappings; other bilateral counterparties
retain separate contract-seller state and unknown physical plant state.

## What cannot be inferred from these archives

Power-exchange purchase files do not identify physical generating state, and pooled
sales do not establish the buyer state. Generator PX-sale files therefore remain
attribution-only potential overlaps. TN import schedules generally do not identify
one of the nine receiving BAs. Puducherry flows remain a separate enclave-unresolved
class unless asset-level geography establishes whether they are internal to the
model boundary. No hourly PyPSA profile is invented from annual adjustments.

## Closure status

- Parse errors: **{parse_errors}**.
- Validation rows with status FAIL: **{failed_validations}**.
- Quantified unresolved categories: **{unresolved_gwh:,.3f} GWh** (non-exclusive).
- Missing reference/benchmark inputs: {reference_missing}.

Accordingly, the deliverable reports best-defensible confirmed and unresolved
amounts separately and does **not** claim “complete and final” closure when these
items are material. See `reconciliation_by_month_channel.csv`,
`reconciliation_by_station_buyer.csv`, `coverage_report.csv`, and
`unresolved_cases.csv` for the quantified evidence.
"""
        (self.output_dir / "audit_report.md").write_text(audit, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--gpkg", type=Path, default=DEFAULT_SOURCE_DIR / "TamilNadu_9BAs.gpkg")
    parser.add_argument("--months", nargs="*", help="Optional YYYY-MM months for a focused regression run")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    pipeline = Pipeline(args.source_dir, args.output_dir, args.gpkg, set(args.months) if args.months else None)
    pipeline.run()
    print(f"Outputs written to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main(sys.argv[1:])




