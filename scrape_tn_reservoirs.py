#!/usr/bin/env python3
"""Validated Tamil Nadu Agriculture reservoir scraper for FY2025-26.

Source pattern:
    https://tnagriculture.in/ARS/home/reservoir/YYYY-MM-DD

Key safeguards:
- Verifies the DATE printed on the returned page equals the requested date.
- Rejects silent fallback pages that return a current/repeated snapshot with HTTP 200.
- Runs a secondary repeated-snapshot QA check across multiple reservoirs.
- Keeps rejected dates out of model-ready outputs and writes them to QA CSVs.
- Adds CWC live-capacity calibration fields for overlapping reservoirs.

Dependencies:
    pip install pandas numpy requests beautifulsoup4 lxml
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import time
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://tnagriculture.in/ARS/home/reservoir/{date}"
DEFAULT_START = "2025-04-01"
DEFAULT_END = "2026-03-31"

CUSEC_TO_MCF_PER_DAY = 0.0864
MCFT_TO_M3 = 28_316.846592
MCFT_TO_BCM = MCFT_TO_M3 / 1e9

# Static CWC live capacities (physical reservoir parameters, not weekly values).
CWC_LIVE_CAPACITY_BCM = {
    "METTUR": 2.647,
    "BHAVANISAGAR": 0.792,   # CWC name: Lower Bhavani
    "Vaigai": 0.172,
    "Parambikulam": 0.380,
    "Aliyar": 0.095,
    "Sholayar": 0.143,
    "Sathanur": 0.207,
    "Manimuthar": 0.156,
}

# Calibration found from Agriculture-vs-CWC checkpoints.
# Other mapped reservoirs use Agriculture storage directly, clipped to CWC live capacity.
CWC_NONLIVE_OFFSET_BCM = {
    "Aliyar": 0.014416,
}

SNAPSHOT_ANCHOR_RESERVOIRS = (
    "METTUR",
    "BHAVANISAGAR",
    "Vaigai",
    "Sholayar",
    "Parambikulam",
    "Aliyar",
)
SNAPSHOT_FIELDS = (
    "current_level_ft",
    "current_storage_mcft",
    "current_inflow_cusec",
    "current_outflow_cusec",
)


# Known bad fallback snapshot observed when historical requests silently return
# the same current-page operating values. Only this exact multi-reservoir
# combination is rejected by the secondary QA check.
#
# Format for each reservoir:
#   current_level_ft, current_storage_mcft,
#   current_inflow_cusec, current_outflow_cusec
KNOWN_FALLBACK_SNAPSHOT = {
    "METTUR": (88.21, 50630.0, 8618.0, 12403.0),
    "BHAVANISAGAR": (53.02, 5198.0, 276.0, 155.0),
    "Vaigai": (40.22, 966.0, 260.0, 86.0),
    "Sholayar": (79.57, 1575.0, 391.0, 926.0),
    "Parambikulam": (52.20, 9133.0, 988.0, 20.0),
    "Aliyar": (66.55, 812.0, 275.0, 450.0),
}


class HistoricalPageFallbackError(RuntimeError):
    pass


def build_session(verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; TamilNaduReservoirResearch/2.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    session.verify = verify_ssl
    return session


def flatten_column(col) -> str:
    if isinstance(col, tuple):
        parts = [
            str(x).strip() for x in col
            if str(x).strip() and not str(x).startswith("Unnamed")
        ]
        text = " ".join(dict.fromkeys(parts))
    else:
        text = str(col).strip()
    return re.sub(r"\s+", " ", text)


def canonical_column_name(name: str) -> str:
    s = re.sub(r"\s+", " ", name.lower()).strip()
    mappings = [
        (r"reservoir", "reservoir"),
        (r"full depth", "full_depth_ft"),
        (r"full capacity", "full_capacity_mcft"),
        (r"current year level", "current_level_ft"),
        (r"current year storage", "current_storage_mcft"),
        (r"current year inflow", "current_inflow_cusec"),
        (r"current year outflow", "current_outflow_cusec"),
        (r"last year level", "last_year_level_ft"),
        (r"last year storage", "last_year_storage_mcft"),
    ]
    for pattern, target in mappings:
        if re.search(pattern, s):
            return target
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def clean_reservoir_name(value: str) -> str:
    value = re.sub(r"\*+", "", str(value).strip())
    return re.sub(r"\s+", " ", value)


def extract_reported_date(html: str) -> pd.Timestamp | None:
    """Read the website's visible `DATE :- dd-mm-yyyy` field."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    patterns = [
        r"\bDATE\s*:-\s*(\d{1,2}-\d{1,2}-\d{4})",
        r"\bDATE\s*:\s*(\d{1,2}-\d{1,2}-\d{4})",
        r"\bDATE\s*:-\s*(\d{4}-\d{1,2}-\d{1,2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        value = m.group(1)
        try:
            if re.match(r"^\d{4}-", value):
                return pd.Timestamp(value).normalize()
            return pd.to_datetime(value, dayfirst=True).normalize()
        except Exception:
            pass
    return None


def extract_reservoir_table(html: str) -> pd.DataFrame:
    tables = pd.read_html(StringIO(html))
    candidate = None
    for table in tables:
        cols = [flatten_column(c) for c in table.columns]
        joined = " | ".join(cols).lower()
        if (
            "reservoir" in joined
            and "current year storage" in joined
            and "current year inflow" in joined
        ):
            candidate = table.copy()
            candidate.columns = cols
            break
    if candidate is None:
        raise ValueError("Reservoir table not found in page HTML.")

    candidate = candidate.rename(
        columns={c: canonical_column_name(c) for c in candidate.columns}
    )
    required = {
        "reservoir", "full_capacity_mcft", "current_storage_mcft",
        "current_inflow_cusec", "current_outflow_cusec",
    }
    missing = required - set(candidate.columns)
    if missing:
        raise ValueError(f"Required columns missing: {sorted(missing)}")

    candidate["reservoir"] = candidate["reservoir"].map(clean_reservoir_name)
    candidate = candidate[
        candidate["reservoir"].notna()
        & ~candidate["reservoir"].astype(str).str.lower().isin(
            {"", "nan", "reservoir", "reservoirs"}
        )
    ].copy()

    numeric_cols = [
        "full_depth_ft", "full_capacity_mcft", "current_level_ft",
        "current_storage_mcft", "current_inflow_cusec", "current_outflow_cusec",
        "last_year_level_ft", "last_year_storage_mcft",
    ]
    for col in numeric_cols:
        if col in candidate.columns:
            values = (
                candidate[col].astype(str)
                .str.replace(",", "", regex=False)
                .str.strip()
                .replace({"-": np.nan, "": np.nan, "nan": np.nan, "None": np.nan})
            )
            candidate[col] = pd.to_numeric(values, errors="coerce")
    return candidate


def scrape_day(
    session: requests.Session,
    date: pd.Timestamp,
    timeout: int = 30,
    content_retries: int = 2,
) -> pd.DataFrame:
    requested = pd.Timestamp(date).normalize()
    date_str = requested.strftime("%Y-%m-%d")
    url = BASE_URL.format(date=date_str)
    last_error = None

    for attempt in range(content_retries + 1):
        # Cache-buster helps avoid intermediary cache fallback.
        response = session.get(
            url,
            params={"_ts": int(time.time() * 1000)},
            timeout=timeout,
        )
        response.raise_for_status()
        reported = extract_reported_date(response.text)

        if reported is None:
            last_error = HistoricalPageFallbackError(
                f"Could not verify reporting date for requested {date_str}."
            )
        elif reported != requested:
            last_error = HistoricalPageFallbackError(
                f"Requested {date_str}, but returned page reports "
                f"{reported.date().isoformat()}."
            )
        else:
            day_df = extract_reservoir_table(response.text)
            day_df.insert(0, "date", requested.date().isoformat())
            day_df.insert(1, "reported_page_date", reported.date().isoformat())
            day_df["source_url"] = url
            day_df["date_validation"] = "verified"
            return day_df

        if attempt < content_retries:
            time.sleep(1.0 * (attempt + 1))

    raise last_error


def add_derived_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["storage_fraction_gross"] = (
        out["current_storage_mcft"] / out["full_capacity_mcft"]
    )
    # Keep backward-compatible column name.
    out["storage_fraction"] = out["storage_fraction_gross"]

    out["inflow_mcft_per_day"] = (
        out["current_inflow_cusec"] * CUSEC_TO_MCF_PER_DAY
    )
    out["outflow_mcft_per_day"] = (
        out["current_outflow_cusec"] * CUSEC_TO_MCF_PER_DAY
    )
    out["current_storage_m3"] = out["current_storage_mcft"] * MCFT_TO_M3
    out["full_capacity_m3"] = out["full_capacity_mcft"] * MCFT_TO_M3
    out["inflow_m3_per_day"] = out["inflow_mcft_per_day"] * MCFT_TO_M3
    out["outflow_m3_per_day"] = out["outflow_mcft_per_day"] * MCFT_TO_M3
    out["agriculture_storage_bcm"] = out["current_storage_mcft"] * MCFT_TO_BCM

    out["cwc_live_capacity_bcm"] = out["reservoir"].map(CWC_LIVE_CAPACITY_BCM)
    out["cwc_nonlive_offset_bcm"] = (
        out["reservoir"].map(CWC_NONLIVE_OFFSET_BCM).fillna(0.0)
    )

    raw_live = out["agriculture_storage_bcm"] - out["cwc_nonlive_offset_bcm"]
    has_cwc = out["cwc_live_capacity_bcm"].notna()
    out["calibrated_live_storage_bcm"] = np.nan
    out.loc[has_cwc, "calibrated_live_storage_bcm"] = np.maximum(
        0.0,
        np.minimum(
            raw_live.loc[has_cwc],
            out.loc[has_cwc, "cwc_live_capacity_bcm"],
        ),
    )
    out["live_soc_fraction"] = (
        out["calibrated_live_storage_bcm"] / out["cwc_live_capacity_bcm"]
    )
    out["live_capacity_source"] = np.where(
        has_cwc, "CWC static live capacity", ""
    )
    out["soc_calibration_rule"] = np.where(
        has_cwc,
        "Agriculture storage clipped to CWC live capacity",
        "",
    )
    out.loc[out["reservoir"].eq("Aliyar") & has_cwc, "soc_calibration_rule"] = (
        "Agriculture storage - 0.014416 BCM, clipped to CWC live capacity"
    )
    return out


def date_snapshot_signature(day_df: pd.DataFrame) -> str | None:
    sub = day_df[day_df["reservoir"].isin(SNAPSHOT_ANCHOR_RESERVOIRS)].copy()
    if sub["reservoir"].nunique() < 5:
        return None
    sub = sub.set_index("reservoir")
    tokens = []
    for reservoir in SNAPSHOT_ANCHOR_RESERVOIRS:
        if reservoir not in sub.index:
            tokens.append(f"{reservoir}:MISSING")
            continue
        row = sub.loc[reservoir]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        vals = []
        for field in SNAPSHOT_FIELDS:
            val = row.get(field, np.nan)
            vals.append("NA" if pd.isna(val) else f"{float(val):.6f}")
        tokens.append(f"{reservoir}:" + "|".join(vals))
    return hashlib.sha256("||".join(tokens).encode()).hexdigest()


def matches_known_fallback_snapshot(
    day_df: pd.DataFrame,
    atol: float = 1e-9,
) -> bool:
    """
    Return True only when all six anchor reservoirs match the known bad
    fallback snapshot across level, storage, inflow and outflow.

    This deliberately does NOT reject other repeated states. A reservoir, or
    even several reservoirs, may legitimately revisit the same level/storage.
    """
    sub = day_df[
        day_df["reservoir"].isin(KNOWN_FALLBACK_SNAPSHOT)
    ].copy()

    if sub["reservoir"].nunique() != len(KNOWN_FALLBACK_SNAPSHOT):
        return False

    sub = sub.set_index("reservoir")

    for reservoir, expected_values in KNOWN_FALLBACK_SNAPSHOT.items():
        row = sub.loc[reservoir]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]

        for field, expected in zip(SNAPSHOT_FIELDS, expected_values):
            actual = row.get(field, np.nan)

            if pd.isna(actual):
                return False

            if not np.isclose(
                float(actual),
                float(expected),
                rtol=0.0,
                atol=atol,
            ):
                return False

    return True


def detect_known_fallback_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Flag only dates containing the known erroneous fallback snapshot."""
    suspicious = []

    for date, day_df in df.groupby("date", sort=True):
        if matches_known_fallback_snapshot(day_df):
            suspicious.append(
                {
                    "date": pd.Timestamp(date).date().isoformat(),
                    "reason": (
                        "Exact match to known Tamil Nadu Agriculture fallback "
                        "snapshot across six reservoirs and four operational "
                        "fields per reservoir."
                    ),
                }
            )

    return pd.DataFrame(suspicious)


def make_wide(df: pd.DataFrame) -> pd.DataFrame:
    variables = [
        "current_storage_mcft",
        "storage_fraction_gross",
        "current_inflow_cusec",
        "inflow_mcft_per_day",
        "current_outflow_cusec",
        "outflow_mcft_per_day",
        "calibrated_live_storage_bcm",
        "live_soc_fraction",
    ]
    parts = []
    for variable in variables:
        if variable not in df.columns:
            continue
        p = df.pivot_table(
            index="date", columns="reservoir", values=variable, aggfunc="first"
        )
        p.columns = [f"{r}__{variable}" for r in p.columns]
        parts.append(p)
    return pd.concat(parts, axis=1).sort_index().reset_index() if parts else pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="Validated Tamil Nadu reservoir scraper")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--output-dir", default="tn_reservoir_data")
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--content-retries", type=int, default=2)
    parser.add_argument("--insecure", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    start = pd.Timestamp(args.start).normalize()
    end = pd.Timestamp(args.end).normalize()
    if end < start:
        raise ValueError("End date must be on or after start date.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.insecure:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = build_session(verify_ssl=not args.insecure)
    all_days, failures = [], []
    dates = pd.date_range(start, end, freq="D")

    for i, date in enumerate(dates, 1):
        try:
            day_df = scrape_day(
                session,
                date,
                timeout=args.timeout,
                content_retries=args.content_retries,
            )
            all_days.append(day_df)
            logging.info("[%d/%d] %s OK", i, len(dates), date.date())
        except Exception as exc:
            logging.warning("[%d/%d] %s REJECTED: %s", i, len(dates), date.date(), exc)
            failures.append({
                "date": date.date().isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "url": BASE_URL.format(date=date.strftime("%Y-%m-%d")),
            })
        if i < len(dates):
            time.sleep(args.sleep)

    if not all_days:
        raise RuntimeError(
            "No historical pages passed date validation; the site may be "
            "returning only a current snapshot."
        )

    accepted = add_derived_fields(pd.concat(all_days, ignore_index=True))
    accepted.to_csv(
        output_dir / "tn_reservoir_all_accepted_before_snapshot_QA_FY2025_26.csv",
        index=False,
    )

    suspicious = detect_known_fallback_dates(accepted)
    suspicious_dates = set(suspicious["date"]) if not suspicious.empty else set()

    clean = accepted[~accepted["date"].astype(str).isin(suspicious_dates)].copy()
    clean["quality_flag"] = "verified"
    clean["model_ready"] = True

    clean.to_csv(output_dir / "tn_reservoir_daily_FY2025_26.csv", index=False)
    make_wide(clean).to_csv(
        output_dir / "tn_reservoir_daily_FY2025_26_wide.csv", index=False
    )

    if failures:
        pd.DataFrame(failures).to_csv(
            output_dir / "tn_reservoir_scrape_failures_FY2025_26.csv", index=False
        )
    if not suspicious.empty:
        suspicious.to_csv(
            output_dir / "tn_reservoir_suspicious_snapshots_FY2025_26.csv", index=False
        )

    logging.info(
        "Finished: %d requested dates, %d passed date validation, %d model-ready dates.",
        len(dates), accepted["date"].nunique(), clean["date"].nunique()
    )


if __name__ == "__main__":
    main()
