"""Recursive ZIP inventory for SRPC REA archives."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator


DATE_ISO = re.compile(rb"20\d{2}[-/]\d{2}[-/]\d{2}")
DATE_DMY = re.compile(rb"\b\d{2}[.-]\d{2}[.-]20\d{2}\b")


@dataclass
class ArchiveEntry:
    source_archive: str
    month: str
    virtual_path: str
    internal_zip_path: str
    filename: str
    extension: str
    zip_depth: int
    is_provisional: bool
    size_bytes: int
    compressed_size: int | None
    crc32: str | None
    sha256: str
    data: bytes


def is_provisional_zip(name: str) -> bool:
    base = PurePosixPath(name).name.lower()
    if "ppa" in base:
        return False
    return bool(
        base.startswith("reap")
        or re.search(r"(?:25|26)p(?:\s|\(|\.zip|$)", base)
        or "sept25p" in base
    )


def walk_month_archive(path: Path, month: str) -> Iterator[ArchiveEntry]:
    root_data = path.read_bytes()
    yield ArchiveEntry(
        source_archive=path.name,
        month=month,
        virtual_path=path.name,
        internal_zip_path="",
        filename=path.name,
        extension=".zip",
        zip_depth=0,
        is_provisional=False,
        size_bytes=len(root_data),
        compressed_size=None,
        crc32=None,
        sha256=hashlib.sha256(root_data).hexdigest(),
        data=b"",
    )
    yield from _walk_zip_bytes(root_data, path.name, path.name, month, 1, False)


def _walk_zip_bytes(
    data: bytes,
    source_archive: str,
    prefix: str,
    month: str,
    depth: int,
    inherited_provisional: bool,
) -> Iterator[ArchiveEntry]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            payload = archive.read(info)
            member_name = info.filename.replace("\\", "/")
            virtual_path = f"{prefix}!{member_name}"
            provisional = inherited_provisional or (
                member_name.lower().endswith(".zip") and is_provisional_zip(member_name)
            )
            entry = ArchiveEntry(
                source_archive=source_archive,
                month=month,
                virtual_path=virtual_path,
                internal_zip_path=member_name,
                filename=PurePosixPath(member_name).name,
                extension=PurePosixPath(member_name).suffix.lower(),
                zip_depth=depth,
                is_provisional=provisional,
                size_bytes=len(payload),
                compressed_size=info.compress_size,
                crc32=f"{info.CRC:08x}",
                sha256=hashlib.sha256(payload).hexdigest(),
                data=payload,
            )
            yield entry
            if entry.extension == ".zip":
                yield from _walk_zip_bytes(
                    payload, source_archive, virtual_path, month, depth + 1, provisional
                )


def csv_columns(data: bytes) -> tuple[list[str], str | None]:
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            text = data[:262144].decode(encoding)
            row = next(csv.reader(io.StringIO(text)))
            return [str(value).strip() for value in row], None
        except (UnicodeDecodeError, StopIteration, csv.Error):
            continue
    return [], "Unable to decode CSV header"


def date_range_from_bytes(data: bytes) -> tuple[str, str]:
    # REA extracts are date-sorted. Sampling both ends avoids repeatedly scanning
    # very large transaction files while still capturing each half's endpoints.
    window = 1_048_576
    sample = data if len(data) <= 2 * window else data[:window] + b"\n" + data[-window:]
    values: set[str] = set()
    for token in DATE_ISO.findall(sample):
        values.add(token.decode("ascii").replace("/", "-"))
    for token in DATE_DMY.findall(sample):
        text = token.decode("ascii").replace(".", "-")
        day, month, year = text.split("-")
        values.add(f"{year}-{month}-{day}")
    if not values:
        return "", ""
    return min(values), max(values)


def infer_units(columns: list[str], filename: str) -> str:
    lower = " ".join(columns).lower() + " " + filename.lower()
    if "gwh" in lower:
        return "GWh"
    if "mwh" in lower:
        return "MWh"
    if any(token in lower for token in ("_sch", "sch_", "schedule", "_imp", "_exp")):
        return "MW per 15-minute block (inferred; validated by March controls)"
    if "price" in lower or "rate" in lower or "charge" in lower:
        return "monetary/rate"
    return "not established"


def inventory_metadata(entry: ArchiveEntry, family: str, role: str) -> dict[str, object]:
    columns: list[str] = []
    read_error = ""
    date_start = date_end = ""
    if entry.extension == ".csv":
        columns, error = csv_columns(entry.data)
        read_error = error or ""
        date_start, date_end = date_range_from_bytes(entry.data)
    return {
        "source_archive": entry.source_archive,
        "month": entry.month,
        "virtual_path": entry.virtual_path,
        "internal_zip_path": entry.internal_zip_path,
        "filename": entry.filename,
        "extension": entry.extension,
        "zip_depth": entry.zip_depth,
        "is_provisional": entry.is_provisional,
        "size_bytes": entry.size_bytes,
        "compressed_size": entry.compressed_size,
        "crc32": entry.crc32,
        "sha256": entry.sha256,
        "date_start": date_start,
        "date_end": date_end,
        "columns": json.dumps(columns, ensure_ascii=False),
        "units": infer_units(columns, entry.filename),
        "file_family": family,
        "primary_role": role,
        "use_decision": "EXCLUDE_PROVISIONAL" if entry.is_provisional else role,
        "read_error": read_error,
    }
