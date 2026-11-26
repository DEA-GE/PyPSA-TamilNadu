from pathlib import Path
import sys

import openpyxl
import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "data_preparation"))

import prepare_iex_dam_prices as iex  # noqa: E402


HEADER = ["Date", "Hour", "Purchase Bid (MWh)", "Sell Bid (MWh)", "MCV (MWh)",
          "Final Scheduled Volume (MWh)", "MCP (Rs/MWh) *", "Weighted MCP (Rs/MWh)"]


def write_export(path: Path, days: list[str], price_offset: float = 0.0, avg_offset: float = 0.0) -> Path:
    """A minimal Market Snapshot export in IEX's layout (text cells, daily summary block)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None])
    ws.append(["Market Snapshot"])
    ws.append([f"Date: {days[0]} to {days[-1]}"])
    ws.append([None])
    ws.append(HEADER)
    for day in days:
        for hour in range(1, 25):
            price = 1000 + 10 * hour + price_offset
            ws.append([day, str(hour), "200.00", "300.00", "100.00", "100.00", f"{price:.2f}", f"{price:.2f}"])
    ws.append(["Date", "Summary", *HEADER[2:]])
    for day in days:
        avg = 1000 + 10 * 12.5 + price_offset + avg_offset
        ws.append([day, "Avg", "200.00", "300.00", "100.00", "100.00", f"{avg:.2f}", f"{avg:.2f}"])
    wb.save(path)
    return path


def test_hour_one_maps_to_midnight_and_values_are_numeric(tmp_path):
    frame = iex.read_export(write_export(tmp_path / "a.xlsx", ["01-04-2025"]))

    assert len(frame) == 24
    assert frame.index[0] == pd.Timestamp("2025-04-01 00:00")
    assert frame.index[-1] == pd.Timestamp("2025-04-01 23:00")
    assert frame["mcp_inr_per_mwh"].iloc[0] == 1010.0


def test_daily_average_mismatch_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="daily 'Avg'"):
        iex.read_export(write_export(tmp_path / "a.xlsx", ["01-04-2025"], avg_offset=5.0))


def test_merge_skips_duplicates_ignores_other_files_and_requires_full_year(tmp_path, monkeypatch):
    monkeypatch.setattr(iex, "FY_END", pd.Timestamp("2025-04-03"))
    write_export(tmp_path / "a.xlsx", ["01-04-2025"])
    write_export(tmp_path / "a_copy.xlsx", ["01-04-2025"])
    write_export(tmp_path / "old.xlsx", ["01-03-2025"])
    other = openpyxl.Workbook()
    other.active.append(["Some other download"])
    other.save(tmp_path / "other.xlsx")

    with pytest.raises(ValueError, match="incomplete"):
        iex.merge(tmp_path)

    write_export(tmp_path / "b.xlsx", ["02-04-2025"])
    table = iex.merge(tmp_path)
    assert len(table) == 48
    assert table.index.is_monotonic_increasing


def test_conflicting_overlap_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(iex, "FY_END", pd.Timestamp("2025-04-02"))
    write_export(tmp_path / "a.xlsx", ["01-04-2025"])
    write_export(tmp_path / "b.xlsx", ["01-04-2025"], price_offset=1.0)

    with pytest.raises(ValueError, match="different data"):
        iex.merge(tmp_path)
