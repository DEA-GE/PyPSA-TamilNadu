from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "data_preparation"))

import prepare_iex_tn_area_prices as area  # noqa: E402


def write_export(path: Path, days: list[str]) -> None:
    """A minimal IEX area-price CSV export (15-minute blocks)."""
    lines = ['"Delivery Date","Time Period","Cleared Buy (MW)","Cleared Sell (MW)","Price (Rs./MWh)"']
    for day in days:
        for block in range(96):
            start, end = block * 15, block * 15 + 15
            period = f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"
            lines.append(f'"{day}","{period}","10.5","20","{1000 + block}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_merge_drops_repeated_blocks_and_reports_missing_days(tmp_path, monkeypatch):
    monkeypatch.setattr(area, "FY_END", pd.Timestamp("2025-04-05"))
    write_export(tmp_path / "IEX Price.csv", ["31/03/2025", "01/04/2025", "02/04/2025"])
    write_export(tmp_path / "IEX Price (1).csv", ["01/04/2025", "02/04/2025", "03/04/2025"])

    table, missing = area.merge(tmp_path)

    assert len(table) == 3 * 96
    assert table.index[0] == pd.Timestamp("2025-04-01 00:00")
    assert table.index[-1] == pd.Timestamp("2025-04-03 23:45")
    assert table["block"].iloc[-1] == 96
    assert list(missing) == [pd.Timestamp("2025-04-04")]
