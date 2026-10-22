"""Small developer utility for inspecting files inside nested SRPC REA archives."""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import pandas as pd


def find_nested_file(outer: Path, nested_name: str, basename: str) -> bytes:
    with zipfile.ZipFile(outer) as archive:
        nested_bytes = archive.read(nested_name)
    with zipfile.ZipFile(io.BytesIO(nested_bytes)) as nested:
        matches = [name for name in nested.namelist() if Path(name).name.lower() == basename.lower()]
        if not matches:
            raise FileNotFoundError(f"{basename!r} not found in {nested_name!r}")
        return nested.read(matches[0])


def read_csv(data: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            return pd.read_csv(io.BytesIO(data), encoding=encoding, low_memory=False)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, "No supported encoding")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("nested")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()

    for basename in args.files:
        print(f"\n### {basename}")
        try:
            frame = read_csv(find_nested_file(args.archive, args.nested, basename))
        except FileNotFoundError as exc:
            print(f"MISSING: {exc}")
            continue
        print(f"shape={frame.shape}")
        print("columns=", list(frame.columns))
        print(frame.head(3).to_string(index=False))
        if "time" in frame:
            values = frame["time"].dropna().astype(str)
            print("time_unique_sample=", sorted(values.unique())[:20], "count=", values.nunique())
        numeric = frame.select_dtypes(include="number")
        if not numeric.empty:
            print("numeric_sums_nonzero=")
            print(numeric.sum().loc[lambda s: s.abs() > 1e-9].to_string())


if __name__ == "__main__":
    main()
