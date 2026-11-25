from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "data_preparation"))

import prepare_iex_tn_area_prices as area  # noqa: E402


def write_export(path: Path, days: list[str], skip_hours: tuple[int, ...] = (), base_price: int = 1000) -> None:
    """A minimal IEX area-price CSV export (15-minute blocks); ``skip_hours`` drop from the last day."""
    lines = ['"Delivery Date","Time Period","Cleared Buy (MW)","Cleared Sell (MW)","Price (Rs./MWh)"']
    for day in days:
        for block in range(96):
            if day == days[-1] and block // 4 in skip_hours:
                continue
            start, end = block * 15, block * 15 + 15
            period = f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"
            lines.append(f'"{day}","{period}","10.5","20","{base_price + block}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_merge_gives_one_value_per_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(area, "FY_END", pd.Timestamp("2025-04-04"))
    write_export(tmp_path / "IEX Price.csv", ["31/03/2025", "01/04/2025", "02/04/2025"])
    write_export(tmp_path / "IEX Price (1).csv", ["01/04/2025", "02/04/2025", "03/04/2025"])

    table, missing = area.merge(tmp_path)

    assert len(table) == 3 * 24
    assert table.index.is_unique
    assert table.index[0] == pd.Timestamp("2025-04-01 00:00")
    assert table["price_inr_per_mwh"].iloc[0] == 1001.5  # mean of blocks 1000..1003
    assert missing.empty


def test_gaps_are_reported_per_month_and_per_partial_day(tmp_path, monkeypatch):
    monkeypatch.setattr(area, "FY_START", pd.Timestamp("2025-04-29"))
    monkeypatch.setattr(area, "FY_END", pd.Timestamp("2025-05-04"))
    write_export(tmp_path / "IEX Price.csv", ["29/04/2025", "01/05/2025"], skip_hours=(22, 23))

    _, missing = area.merge(tmp_path)

    assert area.describe_gaps(missing) == [
        "Apr 2025: 1 days missing (30)",
        "May 2025: 2 days missing (02, 03)",
        "01 May 2025: 2 of 24 hours missing",
    ]


def test_conflicting_exports_stop_the_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(area, "FY_END", pd.Timestamp("2025-04-03"))
    write_export(tmp_path / "IEX Price.csv", ["01/04/2025", "02/04/2025"])
    write_export(tmp_path / "IEX Price (1).csv", ["02/04/2025"], base_price=1500)  # another market's export

    with pytest.raises(ValueError, match="1 days have blocks with different values"):
        area.merge(tmp_path)
