#!/usr/bin/env python3
"""
Scrape Central Water Commission (CWC) weekly reservoir-storage bulletins
for Tamil Nadu during Indian FY2025-26 (2025-04-01 to 2026-03-31).

Why this script uses bulletins instead of the RSMS dashboard
------------------------------------------------------------
The public RSMS dashboard is not always accessible in a normal browser.
CWC also publishes weekly PDF reservoir-storage bulletins. Those PDFs contain
the same type of reservoir-state data needed for PyPSA:

- FRL / full-reservoir level (m)
- Live storage capacity at FRL (BCM)
- Current reservoir level (m)
- Current live storage (BCM)
- Current live storage as % of live capacity
- Last-year level/storage
- Normal storage
- Hydel capacity (MW)

The script:
1. Discovers weekly CWC bulletin PDFs using a known FY2025-26 anchor bulletin.
2. Downloads each valid bulletin.
3. Extracts the Tamil Nadu reservoir rows.
4. Writes long-format and wide-format CSV files suitable for model preprocessing.

Known anchor verified from CWC:
    2025-09-11 -> bulletin-11-09-2025-55.pdf

Dependencies
------------
    pip install requests pandas pdfplumber

Usage
-----
    python scrape_cwc_tamil_nadu.py

Optional:
    python scrape_cwc_tamil_nadu.py --output-dir cwc_data
    python scrape_cwc_tamil_nadu.py --insecure

Notes
-----
- FY2025-26 means 2025-04-01 through 2026-03-31.
- CWC normally publishes weekly, usually on Thursdays, but holiday/weekend
  shifts can occur. Discovery therefore checks nearby dates and bulletin IDs.
- The extraction first uses a robust text-regex method tailored to the CWC
  state-wise reservoir table. It does not require OCR.
"""

from __future__ import annotations

import argparse
import io
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
import pdfplumber
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


FY_START = pd.Timestamp("2025-04-01")
FY_END = pd.Timestamp("2026-03-31")

BASE_URL = (
    "https://rsms.cwc.gov.in/admin/storage/bulletins/"
    "bulletin-{date}-{bulletin_id}.pdf"
)

# Verified CWC bulletin anchor in FY2025-26.
ANCHOR_DATE = pd.Timestamp("2025-09-11")
ANCHOR_ID = 55

# CWC Tamil Nadu reservoirs seen in the FY2025-26 weekly bulletins.
# Keep aliases permissive because PDF spelling/wrapping can vary.
RESERVOIR_PATTERNS = {
    "ALIYAR": r"ALIYAR",
    "KARAYAR": r"KARAYAR",
    "LOWER BHAWANI": r"LOWER\s+BHAWANI",
    "MANIMUTHAR": r"MANIMUTHAR",
    "METTUR": r"METTUR",
    "PARAMBIKULAM": r"PARAMBIKULAM",
    "SATHANUR": r"SATHANUR",
    "SHOLAYAR": r"SHOLAYAR",
    "VAIGAI": r"VAIGAI",
}

# 1 BCM = 1e9 m3 = 35.3146667e9 ft3 = 35,314.6667 million ft3
BCM_TO_MCF = 35_314.666721


@dataclass
class Bulletin:
    nominal_week: pd.Timestamp
    bulletin_date: pd.Timestamp
    bulletin_id: int
    url: str
    pdf_bytes: bytes


def make_session(verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; CWC-TamilNadu-Reservoir-Scraper/1.0; "
                "energy-system-research)"
            )
        }
    )
    session.verify = verify_ssl
    return session


def thursdays(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Return all Thursdays covering the requested period."""
    first = start
    while first.dayofweek != 3:  # Monday=0, Thursday=3
        first += pd.Timedelta(days=1)
    return list(pd.date_range(first, end, freq="7D"))


def candidate_dates(nominal: pd.Timestamp) -> Iterable[pd.Timestamp]:
    """
    CWC usually publishes on Thursday. Check nearby dates for holiday shifts.
    Prefer nominal Thursday, then adjacent days.
    """
    for offset in (0, 1, -1, 2, -2, 3, -3):
        d = nominal + pd.Timedelta(days=offset)
        if FY_START - pd.Timedelta(days=3) <= d <= FY_END + pd.Timedelta(days=3):
            yield d


def build_url(date: pd.Timestamp, bulletin_id: int) -> str:
    return BASE_URL.format(
        date=date.strftime("%d-%m-%Y"),
        bulletin_id=bulletin_id,
    )


def looks_like_pdf(response: requests.Response) -> bool:
    if response.status_code != 200:
        return False
    content_type = response.headers.get("Content-Type", "").lower()
    return (
        "pdf" in content_type
        or response.content[:4] == b"%PDF"
    )


def fetch_candidate(
    session: requests.Session,
    date: pd.Timestamp,
    bulletin_id: int,
    timeout: int,
) -> tuple[str, bytes] | None:
    """
    Download candidate URL and validate it is a CWC PDF.
    We use GET rather than HEAD because some servers handle HEAD inconsistently.
    """
    url = build_url(date, bulletin_id)
    try:
        r = session.get(url, timeout=timeout)
    except requests.RequestException:
        return None

    if not looks_like_pdf(r):
        return None

    # Cheap content validation.
    if b"CENTRAL WATER COMMISSION" not in r.content.upper() and b"%PDF" not in r.content[:4]:
        return None

    return url, r.content


def id_candidates(previous_id: int, direction: int) -> list[int]:
    """
    Search a narrow monotonically increasing/decreasing range.
    Extra bulletin IDs sometimes occur between nominal weekly reports,
    so allow jumps up to 6.
    """
    return [previous_id + direction * k for k in range(1, 7)]


def discover_direction(
    session: requests.Session,
    weeks: list[pd.Timestamp],
    start_id: int,
    direction: int,
    timeout: int,
    pause: float,
) -> tuple[list[Bulletin], list[dict]]:
    """
    Walk sequentially through weeks from the anchor.
    direction=+1 for forward, -1 for backward.
    """
    found: list[Bulletin] = []
    failures: list[dict] = []
    previous_id = start_id

    for nominal_week in weeks:
        matched = None

        for bulletin_id in id_candidates(previous_id, direction):
            if bulletin_id <= 0:
                continue

            for bulletin_date in candidate_dates(nominal_week):
                result = fetch_candidate(
                    session,
                    bulletin_date,
                    bulletin_id,
                    timeout=timeout,
                )
                if result is None:
                    time.sleep(pause)
                    continue

                url, pdf_bytes = result
                matched = Bulletin(
                    nominal_week=nominal_week,
                    bulletin_date=bulletin_date,
                    bulletin_id=bulletin_id,
                    url=url,
                    pdf_bytes=pdf_bytes,
                )
                break

            if matched:
                break

        if matched:
            found.append(matched)
            previous_id = matched.bulletin_id
            logging.info(
                "Found %s | bulletin %d | %s",
                matched.bulletin_date.date(),
                matched.bulletin_id,
                matched.url,
            )
        else:
            # Important: keep previous_id unchanged. If one week is missing,
            # the next week's search can still jump several IDs.
            failures.append(
                {
                    "nominal_week": nominal_week.date().isoformat(),
                    "stage": "discovery",
                    "error": "No bulletin found within date/ID search window",
                }
            )
            logging.warning(
                "No bulletin found near nominal week %s",
                nominal_week.date(),
            )

    return found, failures


def discover_bulletins(
    session: requests.Session,
    timeout: int,
    pause: float,
) -> tuple[list[Bulletin], list[dict]]:
    """
    Discover all weekly bulletins in FY2025-26 from the verified anchor.
    """
    all_weeks = thursdays(FY_START, FY_END)

    before = [d for d in all_weeks if d < ANCHOR_DATE]
    after = [d for d in all_weeks if d > ANCHOR_DATE]

    # Fetch anchor first.
    anchor_result = fetch_candidate(
        session,
        ANCHOR_DATE,
        ANCHOR_ID,
        timeout=timeout,
    )
    if anchor_result is None:
        raise RuntimeError(
            "Verified anchor bulletin could not be downloaded. "
            "Check network/SSL access to rsms.cwc.gov.in."
        )

    anchor_url, anchor_bytes = anchor_result
    anchor = Bulletin(
        nominal_week=ANCHOR_DATE,
        bulletin_date=ANCHOR_DATE,
        bulletin_id=ANCHOR_ID,
        url=anchor_url,
        pdf_bytes=anchor_bytes,
    )

    backward_found, backward_failures = discover_direction(
        session=session,
        weeks=list(reversed(before)),
        start_id=ANCHOR_ID,
        direction=-1,
        timeout=timeout,
        pause=pause,
    )

    forward_found, forward_failures = discover_direction(
        session=session,
        weeks=after,
        start_id=ANCHOR_ID,
        direction=+1,
        timeout=timeout,
        pause=pause,
    )

    bulletins = backward_found + [anchor] + forward_found
    bulletins.sort(key=lambda x: x.bulletin_date)

    # Deduplicate by URL in case shifted dates produce overlap.
    unique = {}
    for b in bulletins:
        unique[b.url] = b

    return list(sorted(unique.values(), key=lambda x: x.bulletin_date)), (
        backward_failures + forward_failures
    )


def normalize_pdf_text(pdf_bytes: bytes) -> str:
    """
    Extract all text from the PDF and normalize whitespace.
    The CWC bulletins are text PDFs, so OCR is not required.
    """
    chunks = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(
                x_tolerance=1.5,
                y_tolerance=3,
                layout=False,
            )
            if text:
                chunks.append(text)

    text = "\n".join(chunks)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[\r\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def numeric(value: str) -> float:
    return float(value.replace(",", ""))


def extract_tamil_nadu_rows(
    bulletin: Bulletin,
) -> list[dict]:
    """
    Parse Tamil Nadu rows from CWC's state-wise reservoir table.

    Expected CWC row structure after 'Tamil Nadu':
      irrigation_cca_th_ha
      hydel_mw
      frl_level_m
      live_capacity_at_frl_bcm
      date
      current_level_m
      current_live_storage_bcm
      current_storage_pct
      last_year_level_m
      last_year_live_storage_bcm
      last_year_storage_pct
      normal_storage_bcm
      normal_storage_pct
      current_vs_last_year_pct
      current_vs_normal_pct
    """
    text = normalize_pdf_text(bulletin.pdf_bytes)

    num = r"-?\d+(?:,\d{3})*(?:\.\d+)?"
    datepat = r"\d{2}\.\d{2}\.\d{4}"

    rows = []

    for canonical_name, reservoir_pattern in RESERVOIR_PATTERNS.items():
        pattern = re.compile(
            rf"""
            (?P<serial>\d+)\s+
            (?P<reservoir>{reservoir_pattern})\s+
            Tamil\s+Nadu\s+
            (?P<irr>{num})\s+
            (?P<hydel>{num})\s+
            (?P<frl>{num})\s+
            (?P<livecap>{num})\s+
            (?P<obsdate>{datepat})\s+
            (?P<currlevel>{num})\s+
            (?P<currstorage>{num})\s+
            (?P<currpct>{num})\s+
            (?P<lastlevel>{num})\s+
            (?P<laststorage>{num})\s+
            (?P<lastpct>{num})\s+
            (?P<normalstorage>{num})\s+
            (?P<normalpct>{num})\s+
            (?P<currlast>{num})\s+
            (?P<currnormal>{num})
            """,
            flags=re.IGNORECASE | re.VERBOSE,
        )

        match = pattern.search(text)
        if not match:
            continue

        g = match.groupdict()

        row = {
            "bulletin_date": bulletin.bulletin_date.date().isoformat(),
            "bulletin_id": bulletin.bulletin_id,
            "reservoir": canonical_name,
            "state": "Tamil Nadu",
            "irrigation_cca_thousand_ha": numeric(g["irr"]),
            "hydel_capacity_mw": numeric(g["hydel"]),
            "frl_level_m": numeric(g["frl"]),
            "live_capacity_at_frl_bcm": numeric(g["livecap"]),
            "observation_date": pd.to_datetime(
                g["obsdate"],
                format="%d.%m.%Y",
            ).date().isoformat(),
            "current_level_m": numeric(g["currlevel"]),
            "current_live_storage_bcm": numeric(g["currstorage"]),
            "current_storage_pct_reported": numeric(g["currpct"]),
            "last_year_level_m": numeric(g["lastlevel"]),
            "last_year_live_storage_bcm": numeric(g["laststorage"]),
            "last_year_storage_pct": numeric(g["lastpct"]),
            "normal_storage_bcm": numeric(g["normalstorage"]),
            "normal_storage_pct": numeric(g["normalpct"]),
            "current_vs_last_year_pct": numeric(g["currlast"]),
            "current_vs_normal_pct": numeric(g["currnormal"]),
            "source_url": bulletin.url,
        }

        # Model-oriented derived fields.
        if row["live_capacity_at_frl_bcm"] > 0:
            row["soc_fraction"] = (
                row["current_live_storage_bcm"]
                / row["live_capacity_at_frl_bcm"]
            )
        else:
            row["soc_fraction"] = float("nan")

        row["live_capacity_at_frl_mcft"] = (
            row["live_capacity_at_frl_bcm"] * BCM_TO_MCF
        )
        row["current_live_storage_mcft"] = (
            row["current_live_storage_bcm"] * BCM_TO_MCF
        )

        rows.append(row)

    return rows


def make_wide(df: pd.DataFrame) -> pd.DataFrame:
    """
    Wide output useful for PyPSA preprocessing:
    one row per weekly observation and reservoir-specific storage fractions.
    """
    fields = [
        "soc_fraction",
        "current_live_storage_bcm",
        "current_level_m",
    ]

    parts = []
    for field in fields:
        p = df.pivot_table(
            index="observation_date",
            columns="reservoir",
            values=field,
            aggfunc="first",
        )
        p.columns = [f"{reservoir}__{field}" for reservoir in p.columns]
        parts.append(p)

    if not parts:
        return pd.DataFrame()

    return pd.concat(parts, axis=1).sort_index().reset_index()


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Scrape CWC FY2025-26 weekly reservoir-storage bulletins "
            "and extract Tamil Nadu reservoirs."
        )
    )
    parser.add_argument(
        "--output-dir",
        default="cwc_tamil_nadu_FY2025_26",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=40,
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.10,
        help="Pause between failed discovery requests in seconds.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL verification if the CWC server fails locally.",
    )
    parser.add_argument(
        "--save-pdfs",
        action="store_true",
        help="Save downloaded bulletin PDFs to the output directory.",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.insecure:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = make_session(verify_ssl=not args.insecure)

    logging.info("Discovering CWC FY2025-26 weekly bulletins...")
    bulletins, discovery_failures = discover_bulletins(
        session=session,
        timeout=args.timeout,
        pause=args.pause,
    )

    logging.info("Discovered %d bulletin PDFs.", len(bulletins))

    rows = []
    parse_failures = []

    pdf_dir = outdir / "pdfs"
    if args.save_pdfs:
        pdf_dir.mkdir(exist_ok=True)

    manifest = []

    for bulletin in bulletins:
        manifest.append(
            {
                "bulletin_date": bulletin.bulletin_date.date().isoformat(),
                "bulletin_id": bulletin.bulletin_id,
                "url": bulletin.url,
            }
        )

        if args.save_pdfs:
            name = (
                f"bulletin-{bulletin.bulletin_date.strftime('%d-%m-%Y')}"
                f"-{bulletin.bulletin_id}.pdf"
            )
            (pdf_dir / name).write_bytes(bulletin.pdf_bytes)

        try:
            extracted = extract_tamil_nadu_rows(bulletin)
            if not extracted:
                raise ValueError("No Tamil Nadu reservoir rows extracted.")

            rows.extend(extracted)

            logging.info(
                "%s: extracted %d Tamil Nadu reservoirs",
                bulletin.bulletin_date.date(),
                len(extracted),
            )

            # Expected number is normally 9 during this period.
            if len(extracted) < 9:
                parse_failures.append(
                    {
                        "bulletin_date": bulletin.bulletin_date.date().isoformat(),
                        "bulletin_id": bulletin.bulletin_id,
                        "stage": "parse_warning",
                        "error": (
                            f"Only {len(extracted)} reservoirs extracted; "
                            "expected approximately 9."
                        ),
                        "url": bulletin.url,
                    }
                )

        except Exception as exc:
            logging.warning(
                "%s: extraction failed: %s",
                bulletin.bulletin_date.date(),
                exc,
            )
            parse_failures.append(
                {
                    "bulletin_date": bulletin.bulletin_date.date().isoformat(),
                    "bulletin_id": bulletin.bulletin_id,
                    "stage": "parse",
                    "error": str(exc),
                    "url": bulletin.url,
                }
            )

    pd.DataFrame(manifest).to_csv(
        outdir / "cwc_bulletin_manifest_FY2025_26.csv",
        index=False,
    )

    failures = discovery_failures + parse_failures
    if failures:
        pd.DataFrame(failures).to_csv(
            outdir / "cwc_scrape_failures_FY2025_26.csv",
            index=False,
        )

    if not rows:
        raise RuntimeError(
            "No Tamil Nadu reservoir rows were extracted. "
            "Inspect the failures CSV and try --insecure if SSL is the issue."
        )

    df = pd.DataFrame(rows)

    numeric_cols = [
        c for c in df.columns
        if c not in {
            "bulletin_date",
            "observation_date",
            "reservoir",
            "state",
            "source_url",
        }
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.sort_values(["observation_date", "reservoir"])
        .drop_duplicates(["observation_date", "reservoir"], keep="last")
    )

    long_path = outdir / "cwc_tamil_nadu_reservoirs_FY2025_26.csv"
    df.to_csv(long_path, index=False)

    wide = make_wide(df)
    wide_path = outdir / "cwc_tamil_nadu_soc_FY2025_26_wide.csv"
    wide.to_csv(wide_path, index=False)

    # Static live-capacity reference, useful for model parameterisation.
    static = (
        df.groupby("reservoir", as_index=False)
        .agg(
            live_capacity_at_frl_bcm=("live_capacity_at_frl_bcm", "median"),
            frl_level_m=("frl_level_m", "median"),
            hydel_capacity_mw=("hydel_capacity_mw", "median"),
        )
    )
    static["live_capacity_at_frl_mcft"] = (
        static["live_capacity_at_frl_bcm"] * BCM_TO_MCF
    )
    static.to_csv(
        outdir / "cwc_tamil_nadu_reservoir_parameters.csv",
        index=False,
    )

    logging.info("Finished.")
    logging.info("Weekly observations: %d", df["observation_date"].nunique())
    logging.info("Reservoirs: %d", df["reservoir"].nunique())
    logging.info("Long CSV: %s", long_path)
    logging.info("Wide SOC CSV: %s", wide_path)


if __name__ == "__main__":
    main()
