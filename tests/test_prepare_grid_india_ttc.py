from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "data_preparation"))

import prepare_grid_india_ttc as ttc  # noqa: E402

# Page texts as pypdf extracts them from the Grid-India declarations.
TAMIL_NADU = """Issue Date: 28/03/2025 Revision No.1
Date Time Period Time
Blocks
Remarks
1st to 30th Apr 2025 00 -24Hrs 1-96 14050 350 13700 9177 4523 - R1
Import Transfer Capability of Tamil Nadu for April 2025
Issue Time: 18:00 Hrs
Limiting Constraints
(any one or combination
thereof)
i. N-1 contigency of 400/230 kV ICT's at Salem 400 kV SS
ii. Tripping of 400kV NCTPS-Manali S/C will cause overloading of 400kV Alamaty-Manali S/C
iii. N-1 contigency of 400/230 kV ICT's at Kalivandapattu 400 kV SS
iv. N-1 contigency of 400/230 kV ICT's at NLC TS2 and NNTPS"""

ANDHRA_PRADESH = """Issue Date: 28/03/2025 Revision No.1
Remarks
00 -06Hrs 1-24 8597 268 8329 4516 3813 - R1
06 -12Hrs 25-48 4548 268 4280 4516 - - R1
12 -18Hrs 49-72 4368 268 4100 4516 - - R1
18 -24Hrs 73-96 8015 268 7747 4516 - - R1
Import Transfer Capability of Andra Pradesh for April 2025
1st to 30th Apr 2025"""

CORRIDOR = """Issue Date: 21/11/2025 Revision No.1
1st  to 31st Dec 2025 00 -24Hrs 1-96 11300 450 10850 5574 5276 +400 R1
Transfer Capability between S1- (S2&S3) for December 2025
v. N-1 violation 2x500 MVA ICTs at 400/230kV NNTPP"""

FEBRUARY_WITHOUT_TITLE_MONTH = """Feb-26
1st to 28th Feb 2026 00 -24Hrs 1-96 14050 350 13700 9177 4523 - -
Import Transfer Capability of Tamil Nadu for
Issue Time: 18:00 Hrs"""


def test_single_period_state_page():
    header, rows, constraints = ttc.parse_page(TAMIL_NADU)

    assert header == {"area": "Tamil Nadu", "month": pd.Period("2025-04", "M"), "revision": 1,
                      "issue_date": pd.Timestamp("2025-03-28").date()}
    assert rows == [{"period": "00 -24", "blocks": "1-96", "ttc": "14050", "rm": "350", "atc": "13700",
                     "net_gna": "9177", "tgna_margin": "4523", "change": "-", "remark": "R1"}]
    assert len(constraints) == 4
    assert constraints[0].endswith("Salem 400 kV SS")


def test_time_periods_and_missing_margins():
    header, rows, _ = ttc.parse_page(ANDHRA_PRADESH)

    assert header["area"] == "Andhra Pradesh"
    assert [row["blocks"] for row in rows] == ["1-24", "25-48", "49-72", "73-96"]
    assert [row["tgna_margin"] for row in rows] == ["3813", "-", "-", "-"]


def test_corridor_page_with_signed_change():
    header, rows, constraints = ttc.parse_page(CORRIDOR)

    assert header["area"] == "S1-(S2&S3)"
    assert rows[0]["ttc"] == "11300" and rows[0]["change"] == "+400"
    assert constraints == ["N-1 violation 2x500 MVA ICTs at 400/230kV NNTPP"]


def test_month_comes_from_the_date_range():
    header, rows, _ = ttc.parse_page(FEBRUARY_WITHOUT_TITLE_MONTH)

    assert header["area"] == "Tamil Nadu"
    assert header["month"] == pd.Period("2026-02", "M")
    assert header["revision"] is None  # taken from the file name instead
    assert rows[0]["remark"] == "-"


def test_page_without_table_is_ignored():
    assert ttc.parse_page("Revision table\nNational Load Despatch Centre") is None
