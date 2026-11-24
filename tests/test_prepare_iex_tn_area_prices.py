from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "data_preparation"))

import prepare_iex_tn_area_prices as area  # noqa: E402


def write_export(path: Path, days: list[str], price_offset: float = 0.0) -> Path:
    """A minimal IEX area-price CSV export (15-minute blocks, quoted text)."""
    lines = ['"Delivery Date","Time Period","Cleared Buy (MW)","Cleared Sell (MW)","Price (Rs./MWh)"']
    for day in days:
        for block in range(96):
            start = block * 15
            end = start + 15
            period = f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"
            lines.append(f'"{day}","{period}","10.5","20","{1000 + block + price_offset}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_blocks_map_to_ist_starts_including_the_24_00_end(tmp_path):
    frame = area.read_export(write_export(tmp_path / "a.csv", ["01/04/2025"]))

    assert len(frame) == 96
    assert frame.index[0] == pd.Timestamp("2025-04-01 00:00")
    assert frame.index[-1] == pd.Timestamp("2025-04-01 23:45")
    assert frame["block"].tolist() == list(range(1, 97))
    assert frame["price_inr_per_mwh"].iloc[-1] == 1095.0


def test_merge_skips_duplicates_ignores_other_files_and_lists_missing_days(tmp_path, monkeypatch):
    monkeypatch.setattr(area, "FY_END", pd.Timestamp("2025-04-04"))
    write_export(tmp_path / "a.csv", ["01/04/2025", "02/04/2025"])
    write_export(tmp_path / "a_copy.csv", ["01/04/2025", "02/04/2025"])
    write_export(tmp_path / "old.csv", ["31/03/2025"])
    (tmp_path / "other.csv").write_text("x,y\n1,2\n", encoding="utf-8")

    table, missing = area.merge(tmp_path)

    assert len(table) == 2 * 96
    assert missing == [pd.Timestamp("2025-04-03")]


def test_partial_export_and_its_full_reexport_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(area, "FY_END", pd.Timestamp("2025-04-04"))
    write_export(tmp_path / "a_partial.csv", ["01/04/2025", "02/04/2025"])
    write_export(tmp_path / "b_full.csv", ["01/04/2025", "02/04/2025", "03/04/2025"])

    table, missing = area.merge(tmp_path)

    assert len(table) == 3 * 96
    assert not table.index.duplicated().any()
    assert missing == []


def test_conflicting_overlap_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(area, "FY_END", pd.Timestamp("2025-04-02"))
    write_export(tmp_path / "a.csv", ["01/04/2025"])
    write_export(tmp_path / "b.csv", ["01/04/2025"], price_offset=1.0)

    with pytest.raises(ValueError, match="different data"):
        area.merge(tmp_path)
