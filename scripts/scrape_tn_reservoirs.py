#!/usr/bin/env python3
"""Validated Tamil Nadu Agriculture reservoir scraper for FY2025-26.

Source pattern:
    https://tnagriculture.in/ARS/home/reservoir/YYYY-MM-DD

Key safeguards:
- Verifies the DATE printed on the returned page equals the requested date.
- Detects complete multi-reservoir payloads repeated across implausibly distant dates.
- Interpolates every time-varying measurement in repeated payloads and records provenance.
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

# These are observations that may change from one report date to the next.
# Full depth and capacity are physical parameters and intentionally remain
# untouched when a bad daily payload is repaired.
TIME_VARYING_FIELDS = (
    "current_level_ft",
    "current_storage_mcft",
    "current_inflow_cusec",
    "current_outflow_cusec",
    "last_year_level_ft",
    "last_year_storage_mcft",
)
MIN_REPEATED_SNAPSHOT_DATES = 5
MIN_REPEATED_SNAPSHOT_SPAN_DAYS = 14

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


def repair_known_storage_outliers(df: pd.DataFrame) -> pd.DataFrame:
    """Repair isolated, documented source errors before deriving storage fields.

    The Parambikulam observation for 2026-02-19 reports 1,250 MCft, while the
    immediately adjacent daily observations are 12,603 and 12,429 MCft.  The
    source value is a misplaced-comma error; correct it by multiplying by ten
    and retain explicit provenance in the output.
    """
    out = df.copy()
    mask = out["reservoir"].eq("Parambikulam") & out["date"].eq("2026-02-19")
    if not mask.any():
        return out

    out.loc[mask, "current_storage_mcft"] = 12500.0
    out.loc[mask, "quality_flag"] = "corrected_storage_decimal"
    out.loc[mask, "date_validation"] = "verified_date_corrected_storage"
    out.loc[mask, "interpolation_method"] = "multiply_by_10"
    out.loc[mask, "interpolation_previous_date"] = ""
    out.loc[mask, "interpolation_next_date"] = ""
    out.loc[mask, "interpolated_fields"] = "current_storage_mcft"
    return out


def date_snapshot_signature(day_df: pd.DataFrame) -> str | None:
    """Return an exact fingerprint of one complete daily source payload."""
    required = {"reservoir", *TIME_VARYING_FIELDS}
    if not required.issubset(day_df.columns):
        return None
    if day_df.empty or day_df["reservoir"].duplicated().any():
        return None

    rows = day_df.sort_values("reservoir")
    tokens = []
    for row in rows[["reservoir", *TIME_VARYING_FIELDS]].itertuples(
        index=False, name=None
    ):
        values = [str(row[0])]
        for value in row[1:]:
            values.append("NA" if pd.isna(value) else float(value).hex())
        tokens.append("|".join(values))
    return hashlib.sha256("||".join(tokens).encode()).hexdigest()


def detect_repeated_snapshot_dates(
    df: pd.DataFrame,
    min_occurrences: int = MIN_REPEATED_SNAPSHOT_DATES,
    min_span_days: int = MIN_REPEATED_SNAPSHOT_SPAN_DAYS,
) -> pd.DataFrame:
    """Find implausibly repeated complete multi-reservoir source payloads.

    A single reservoir can legitimately report the same value on many dates.
    This check therefore compares the complete set of time-varying fields for
    every reservoir on a date, and only flags signatures repeated many times
    across a sufficiently long period.
    """
    if min_occurrences < 2:
        raise ValueError("min_occurrences must be at least 2.")
    if min_span_days < 1:
        raise ValueError("min_span_days must be positive.")

    working = df.copy()
    working["_date"] = pd.to_datetime(working["date"], errors="raise").dt.normalize()
    date_counts = working.groupby("_date")["reservoir"].nunique()
    if date_counts.empty:
        return pd.DataFrame(
            columns=[
                "date", "snapshot_signature", "repeat_count", "span_days",
                "reservoir_count", "reason",
            ]
        )

    # The modal count represents a complete report even if a few scraped pages
    # were incomplete. In the FY2025-26 source this is 19 reservoirs.
    complete_count = int(date_counts.mode().iloc[0])
    signatures = []
    for date, day_df in working.groupby("_date", sort=True):
        signature = (
            date_snapshot_signature(day_df)
            if day_df["reservoir"].nunique() == complete_count
            else None
        )
        signatures.append({"_date": date, "snapshot_signature": signature})

    signature_table = pd.DataFrame(signatures).dropna(subset=["snapshot_signature"])
    suspicious = []
    for signature, group in signature_table.groupby("snapshot_signature", sort=False):
        repeat_count = len(group)
        span_days = int((group["_date"].max() - group["_date"].min()).days)
        if repeat_count < min_occurrences or span_days < min_span_days:
            continue
        reason = (
            "Identical complete multi-reservoir payload across all time-varying "
            f"fields repeated {repeat_count} times over {span_days} days."
        )
        for date in group["_date"].sort_values():
            suspicious.append(
                {
                    "date": date.date().isoformat(),
                    "snapshot_signature": signature,
                    "repeat_count": repeat_count,
                    "span_days": span_days,
                    "reservoir_count": complete_count,
                    "reason": reason,
                }
            )
    return pd.DataFrame(
        suspicious,
        columns=[
            "date", "snapshot_signature", "repeat_count", "span_days",
            "reservoir_count", "reason",
        ],
    )


def _trusted_neighbour_dates(
    dates: pd.Series,
    suspicious: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """Return nearest trustworthy date before and after every row."""
    trusted_dates = dates.where(~suspicious)
    previous = trusted_dates.ffill().where(suspicious)
    following = trusted_dates.bfill().where(suspicious)
    return previous, following


def interpolate_repeated_snapshots(
    df: pd.DataFrame,
    suspicious_dates,
) -> pd.DataFrame:
    """Repair suspect payloads for every time-varying field by reservoir.

    Interior gaps use calendar-time linear interpolation. At a dataset edge,
    where interpolation cannot be bracketed, the closest trustworthy value is
    used. Values are constrained to basic physical bounds after interpolation.
    """
    missing = {"date", "reservoir", *TIME_VARYING_FIELDS} - set(df.columns)
    if missing:
        raise ValueError(f"Cannot interpolate; required columns missing: {sorted(missing)}")
    if df.duplicated(["date", "reservoir"]).any():
        raise ValueError("Cannot interpolate duplicate date/reservoir rows.")

    out = df.copy()
    out["_date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    suspect_date_index = pd.DatetimeIndex(pd.to_datetime(list(suspicious_dates))).normalize()
    out["_suspect"] = out["_date"].isin(suspect_date_index)

    out["quality_flag"] = "verified"
    out["model_ready"] = True
    out["interpolation_method"] = ""
    out["interpolation_previous_date"] = ""
    out["interpolation_next_date"] = ""
    out["interpolated_fields"] = ""

    if not out["_suspect"].any():
        return out.drop(columns=["_date", "_suspect"])

    for _, indices in out.groupby("reservoir", sort=False).groups.items():
        group = out.loc[indices].sort_values("_date")
        group_index = group.index
        suspect = group["_suspect"]
        previous, following = _trusted_neighbour_dates(group["_date"], suspect)

        for field in TIME_VARYING_FIELDS:
            values = pd.Series(
                pd.to_numeric(group[field], errors="coerce").to_numpy(),
                index=pd.DatetimeIndex(group["_date"]),
                dtype=float,
            )
            values.loc[suspect.to_numpy()] = np.nan
            repaired = values.interpolate(method="time", limit_area="inside")
            repaired = repaired.ffill().bfill()
            out.loc[group_index[suspect], field] = repaired.loc[
                suspect.to_numpy()
            ].to_numpy()

        interior = suspect & previous.notna() & following.notna()
        boundary = suspect & ~interior
        out.loc[group_index[interior], "interpolation_method"] = "linear_time"
        out.loc[group_index[boundary], "interpolation_method"] = "nearest_boundary"
        out.loc[group_index[suspect], "interpolation_previous_date"] = (
            previous.loc[suspect].dt.strftime("%Y-%m-%d").fillna("").to_numpy()
        )
        out.loc[group_index[suspect], "interpolation_next_date"] = (
            following.loc[suspect].dt.strftime("%Y-%m-%d").fillna("").to_numpy()
        )

    suspect_rows = out["_suspect"]
    lower_bounded = [
        "current_level_ft", "current_storage_mcft", "current_inflow_cusec",
        "current_outflow_cusec", "last_year_level_ft", "last_year_storage_mcft",
    ]
    for field in lower_bounded:
        out.loc[suspect_rows, field] = out.loc[suspect_rows, field].clip(lower=0.0)
    if "full_depth_ft" in out:
        for field in ("current_level_ft", "last_year_level_ft"):
            values = out.loc[suspect_rows, field]
            upper = out.loc[suspect_rows, "full_depth_ft"]
            out.loc[suspect_rows, field] = values.where(
                upper.isna(), np.minimum(values, upper)
            )
    if "full_capacity_mcft" in out:
        for field in ("current_storage_mcft", "last_year_storage_mcft"):
            values = out.loc[suspect_rows, field]
            upper = out.loc[suspect_rows, "full_capacity_mcft"]
            out.loc[suspect_rows, field] = values.where(
                upper.isna(), np.minimum(values, upper)
            )

    resolved = out.loc[suspect_rows, list(TIME_VARYING_FIELDS)].notna().all(axis=1)
    resolved_index = resolved.index[resolved]
    unresolved_index = resolved.index[~resolved]
    out.loc[resolved_index, "quality_flag"] = "interpolated_repeated_snapshot"
    out.loc[resolved_index, "date_validation"] = "verified_date_interpolated_payload"
    out.loc[resolved_index, "interpolated_fields"] = ",".join(TIME_VARYING_FIELDS)
    out.loc[unresolved_index, "quality_flag"] = "unresolved_repeated_snapshot"
    out.loc[unresolved_index, "model_ready"] = False
    out.loc[unresolved_index, "date_validation"] = "verified_date_unresolved_payload"
    return out.drop(columns=["_date", "_suspect"])


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
    parser.add_argument("--output-dir", default="data/reservoirs")
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--content-retries", type=int, default=2)
    parser.add_argument(
        "--min-repeated-snapshot-dates",
        type=int,
        default=MIN_REPEATED_SNAPSHOT_DATES,
        help="Minimum exact complete-payload repeats to treat as fallback data.",
    )
    parser.add_argument(
        "--min-repeated-snapshot-span-days",
        type=int,
        default=MIN_REPEATED_SNAPSHOT_SPAN_DAYS,
        help="Minimum first-to-last repeat span used by fallback detection.",
    )
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
    all_days = []
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
        if i < len(dates):
            time.sleep(args.sleep)

    session.close()

    if not all_days:
        raise RuntimeError(
            "No historical pages passed date validation; the site may be "
            "returning only a current snapshot."
        )

    raw = pd.concat(all_days, ignore_index=True)
    suspicious = detect_repeated_snapshot_dates(
        raw,
        min_occurrences=args.min_repeated_snapshot_dates,
        min_span_days=args.min_repeated_snapshot_span_days,
    )
    suspicious_dates = suspicious["date"] if not suspicious.empty else []
    clean = interpolate_repeated_snapshots(raw, suspicious_dates)
    clean = repair_known_storage_outliers(clean)
    # Derived values must be calculated after repairing the source fields so
    # storage fractions, unit conversions and CWC calibration stay consistent.
    clean = add_derived_fields(clean)

    clean.to_csv(output_dir / "tn_reservoir_daily_FY2025_26.csv", index=False)
    make_wide(clean).to_csv(
        output_dir / "tn_reservoir_daily_FY2025_26_wide.csv", index=False
    )
    suspicious.to_csv(
        output_dir / "tn_reservoir_suspicious_snapshots_FY2025_26.csv",
        index=False,
    )

    logging.info(
        "Finished: %d requested dates, %d passed date validation, "
        "%d repeated-snapshot dates interpolated, %d model-ready dates.",
        len(dates), raw["date"].nunique(), len(suspicious),
        clean.loc[clean["model_ready"], "date"].nunique(),
    )


if __name__ == "__main__":
    main()
