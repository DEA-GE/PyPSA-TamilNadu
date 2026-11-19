"""Seasonal correction of ERA5/atlite solar availability for Tamil Nadu.

atlite converts ERA5 radiation to PV output including panel tilt and ambient
temperature, but not soiling, wind cooling of modules, or ERA5's own seasonal
irradiance bias.  The correction has two layers:

1. **Physical layer (literature parameters, daily, per balancing area).**
   Soiling builds up on dry days and is reset by rain or periodic cleaning.
   Wind cools modules below the temperature atlite's Huld model assumes.
   Both are driven by ERA5 daily precipitation and wind speed.
2. **Seasonal layer (fitted on historical years, one factor per calendar
   month).**  For FY2020-21 to FY2024-25, observed monthly Tamil Nadu solar
   generation (NITI Dashboard) is divided by the potential the model would
   have predicted: month-end installed solar capacity (NITI Dashboard,
   MNRE's monthly series) times an ERA5
   reconstruction of the active profile, after the physical layer.  Each year
   is normalised to its own annual mean, and each month's factor is the median
   of its ratios over the years.  The literature attributes the residual mainly to
   ERA5 underestimating monsoon irradiance under cloud and overestimating
   dry-season irradiance because of climatological aerosols.

FY2025-26 is excluded from the fit, so its monthly comparison remains an
independent test.  The annual level is calibrated separately by the builder.
Run ``python scripts/solar_seasonal_correction.py`` to refit after the active
renewable profiles change.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from solar_capacity_trajectory import read_monthly_capacity, solar_capacity_mw


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "data" / "model_inputs"
WEATHER_FILE = INPUTS / "era5_daily_weather_tn_areas_FY2020_26.csv"
PROFILE_FILE = INPUTS / "renewable_profiles.csv"
FACTOR_FILE = INPUTS / "solar_seasonal_correction_factors.csv"
OBSERVED_HISTORY_FILE = (
    ROOT
    / "data"
    / "source"
    / "NITI dashboard"
    / "monthly solar generation Tamil Nadu"
    / "Generation_Metatable_Data_1795004240611.csv"
)
MODEL_GENERATORS = ROOT / "model" / "generators.csv"

AREAS = [
    "chennai_north",
    "chennai_south",
    "coimbatore",
    "erode",
    "madurai",
    "tirunelveli",
    "trichy",
    "vellore",
    "villupuram",
]
# FY2019-20 is excluded: its monthly capacity lags commissioning (the series
# rises slowly, then jumps by 688 MW in December 2019), so its ratios are
# unreliable.
TRAINING_FISCAL_YEARS = ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
FIT_START = pd.Timestamp("2020-04-01")
TEST_FISCAL_YEAR = "2025-26"
FISCAL_MONTHS = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]

# --- Physical layer: literature parameters ---------------------------------
# Dry-period soiling rate. Chennai field measurements give 0.12-0.39 %/day
# (Herrmann et al. 2015, IEEE PVSC, doi:10.1109/PVSC.2015.7356131); IIT Bombay
# and Gandhinagar report about 0.45 %/day (IEA-PVPS T13-21:2022; Valerino et
# al. 2021, Solar Energy 227).  A mid-range value is used.
SOILING_RATE_PER_DAY = 0.0025
# Daily rainfall that washes modules and the maximum soiling loss follow the
# Kimber model defaults (pvlib.soiling.kimber).
RAIN_CLEANING_THRESHOLD_MM = 6.0
MAX_SOILING_LOSS = 0.3
# At least half of the sites in the NISE/NCPRE All-India Survey of PV Module
# Reliability (2014) cleaned modules monthly or irregularly.
MANUAL_CLEANING_INTERVAL_DAYS = 30
# Module temperature: atlite's crystalline-silicon Huld model adds
# 0.035 K per W/m2 of irradiance to ambient temperature at any wind speed
# (atlite/resources/solarpanel/CSi.yaml).  The Faiman model adds
# 1 / (U0 + U1 * wind) K per W/m2 (Faiman 2008; pvlib default coefficients).
HULD_IRRADIANCE_COEFFICIENT = 0.035
FAIMAN_U0 = 25.0
FAIMAN_U1 = 6.84
# 10 m wind to module height (~2 m) with a 0.1 m roughness length.
MODULE_HEIGHT_WIND_FACTOR = np.log(2.0 / 0.1) / np.log(10.0 / 0.1)
# Production-weighted plane-of-array irradiance and the crystalline-silicon
# power temperature coefficient (typical datasheet value).
PRODUCTION_WEIGHTED_IRRADIANCE_W_M2 = 650.0
POWER_TEMPERATURE_COEFFICIENT_PER_K = 0.004


def _hours(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray((index - pd.Timestamp("2019-01-01")) / pd.Timedelta(hours=1), dtype=float)


def fiscal_year(dates: pd.DatetimeIndex) -> pd.Index:
    start = dates.year - (dates.month < 4)
    return pd.Index([f"{year}-{str(year + 1)[2:]}" for year in start])


def file_sha256(path: Path) -> str:
    """SHA-256 of the file with line endings normalised, so Git's CRLF/LF
    conversion on different platforms does not change it."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest().upper()


# --- Inputs ----------------------------------------------------------------
def read_daily_weather(path: Path = WEATHER_FILE) -> dict[str, pd.DataFrame]:
    """ERA5 daily weather by area: GHI (MJ/m2), precipitation (mm), wind (km/h)."""
    frame = pd.read_csv(path, parse_dates=["date"])
    if sorted(frame["area"].unique()) != sorted(AREAS):
        raise ValueError("ERA5 weather must cover the nine balancing areas")
    weather = {}
    for area, group in frame.groupby("area"):
        group = group.set_index("date").sort_index()
        expected = pd.date_range(group.index.min(), group.index.max(), freq="D")
        if not group.index.equals(expected) or group.isna().any().any():
            raise ValueError(f"ERA5 weather for {area} must be complete and daily")
        weather[area] = group[["ghi_mj_m2", "precipitation_mm", "wind_speed_10m_kmh"]]
    return weather


def read_observed_history(path: Path = OBSERVED_HISTORY_FILE) -> pd.Series:
    """NITI Dashboard monthly Tamil Nadu solar generation (GWh) by month start."""
    frame = pd.read_csv(path)
    frame = frame.loc[frame["Source"].eq("solar") & frame["State"].eq("Tamil Nadu")]
    month = pd.to_datetime(frame["Month"], format="%B").dt.month
    year = frame["Year"].str[:4].astype(int) + (month < 4).astype(int)
    index = pd.to_datetime({"year": year, "month": month, "day": 1})
    series = pd.Series(frame["Monthly Generation (In MU)"].astype(float).to_numpy(), index=index)
    if series.index.duplicated().any():
        raise ValueError("Duplicate months in the observed solar history")
    return series.sort_index()


def historical_solar_capacity() -> pd.Series:
    """Month-end Tamil Nadu solar capacity (MW, incl. off-grid), 2020-03 to 2026-03.

    From the NITI Dashboard monthly installed-capacity export (MNRE's monthly
    state-wise series).  Off-grid solar is not separated: it changes only the
    level, which the fit normalises away each year.
    """
    solar = read_monthly_capacity()["solar_mw"]
    return solar.loc[FIT_START - pd.Timedelta(days=1) : "2026-03-31"]


def area_weights(path: Path = MODEL_GENERATORS) -> pd.Series:
    """Share of modeled solar nameplate in each balancing area."""
    generators = pd.read_csv(path)
    solar = generators.loc[generators["carrier"].eq("solar")]
    capacity = solar.groupby(solar["bus"].str.lower().str.replace(" ", "_"))["p_nom"].sum()
    return (capacity / capacity.sum()).reindex(AREAS).fillna(0.0)


# --- Physical layer ----------------------------------------------------------
def soiling_loss(precipitation_mm: pd.Series) -> pd.Series:
    """Daily soiling loss fraction: linear build-up, reset by rain or cleaning."""
    loss, days_since_clean, values = 0.0, 0, []
    for rain in precipitation_mm.to_numpy():
        days_since_clean += 1
        if rain >= RAIN_CLEANING_THRESHOLD_MM or days_since_clean >= MANUAL_CLEANING_INTERVAL_DAYS:
            loss, days_since_clean = 0.0, 0
        else:
            loss = min(loss + SOILING_RATE_PER_DAY, MAX_SOILING_LOSS)
        values.append(loss)
    return pd.Series(values, index=precipitation_mm.index, name="soiling_loss")


def wind_cooling_gain(wind_speed_10m_kmh: pd.Series) -> pd.Series:
    """Output gain from modules running cooler in wind than atlite's Huld model assumes."""
    wind = wind_speed_10m_kmh / 3.6 * MODULE_HEIGHT_WIND_FACTOR
    cooler_k = PRODUCTION_WEIGHTED_IRRADIANCE_W_M2 * (
        HULD_IRRADIANCE_COEFFICIENT - 1.0 / (FAIMAN_U0 + FAIMAN_U1 * wind)
    )
    return (1.0 + POWER_TEMPERATURE_COEFFICIENT_PER_K * cooler_k).rename("wind_cooling_gain")


def physical_factor(weather: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Daily (1 - soiling) x wind-cooling factor by area over the weather record."""
    weather = read_daily_weather() if weather is None else weather
    return pd.DataFrame(
        {
            area: (1.0 - soiling_loss(daily["precipitation_mm"]))
            * wind_cooling_gain(daily["wind_speed_10m_kmh"])
            for area, daily in weather.items()
        }
    )[AREAS]


# --- Seasonal layer ------------------------------------------------------------
def fit_seasonal_factors(
    weather: dict[str, pd.DataFrame] | None = None,
    profiles: pd.DataFrame | None = None,
    weights: pd.Series | None = None,
    training_years: list[str] = TRAINING_FISCAL_YEARS,
) -> pd.DataFrame:
    """Monthly ratio of observed to modeled solar, one row per calendar month."""
    weather = read_daily_weather() if weather is None else weather
    profiles = (
        pd.read_csv(PROFILE_FILE, parse_dates=["snapshot"]).set_index("snapshot")
        if profiles is None
        else profiles
    )
    weights = area_weights() if weights is None else weights
    ghi = pd.DataFrame({area: weather[area]["ghi_mj_m2"] for area in AREAS})
    physics = physical_factor(weather)
    # Energy-weighted monthly mean of the daily physical factor.
    physics_monthly = (physics * ghi).resample("MS").sum() / ghi.resample("MS").sum()
    ghi_monthly = ghi.resample("MS").mean()

    # The active profile is ERA5 GHI converted by atlite.  Its monthly ratio to
    # ERA5 GHI in the profile year (panel tilt and temperature) reconstructs it
    # in other years.
    profile_monthly = pd.DataFrame(
        {area: profiles[f"{area}_solar"] for area in AREAS}
    ).resample("MS").mean()
    conversion = profile_monthly / ghi_monthly.loc[profile_monthly.index]
    conversion.index = conversion.index.month
    if conversion.index.duplicated().any() or len(conversion) != 12:
        raise ValueError("The active profile must cover one full year")
    profile_cf = pd.DataFrame(
        {area: ghi_monthly[area] * conversion[area].reindex(ghi_monthly.index.month).to_numpy() for area in AREAS}
    )
    modeled_cf = (profile_cf * physics_monthly).mul(weights, axis=1).sum(axis=1)

    capacity_end = historical_solar_capacity()
    days = pd.date_range(FIT_START, ghi.index.max(), freq="D")
    anchors = capacity_end.index + pd.Timedelta(days=1)
    capacity_daily = pd.Series(
        np.interp(_hours(days), _hours(anchors), capacity_end.to_numpy()), index=days
    )
    capacity_monthly = capacity_daily.resample("MS").mean()
    hours = pd.Series(capacity_monthly.index.days_in_month * 24.0, index=capacity_monthly.index)
    modeled_gwh = capacity_monthly * modeled_cf.reindex(capacity_monthly.index) * hours / 1_000.0
    observed_gwh = read_observed_history().reindex(capacity_monthly.index)

    years = fiscal_year(capacity_monthly.index)
    ratio = observed_gwh / modeled_gwh
    annual = (observed_gwh.groupby(years).sum() / modeled_gwh.groupby(years).sum())
    normalised = ratio / annual.reindex(years).to_numpy()
    table = pd.DataFrame(
        {
            year: normalised[years == year].groupby(normalised.index[years == year].month).first()
            for year in sorted(set(years))
        }
    ).reindex(FISCAL_MONTHS)
    missing = [year for year in training_years if table[year].isna().any()]
    if missing:
        raise ValueError(f"Training years lack complete monthly data: {missing}")
    # The median limits the influence of one year's anomalous month.
    result = pd.DataFrame(
        {
            "month": FISCAL_MONTHS,
            "month_name": [pd.Timestamp(2001, month, 1).strftime("%B") for month in FISCAL_MONTHS],
            "seasonal_factor": table[training_years].median(axis=1).to_numpy(),
            "between_year_std": table[training_years].std(axis=1).to_numpy(),
            "training_years": len(training_years),
        }
    )
    for year in table.columns:
        result[f"ratio_FY{year}"] = table[year].to_numpy()
    result["profile_input_sha256"] = file_sha256(PROFILE_FILE)
    return result


def read_seasonal_factors(path: Path = FACTOR_FILE) -> pd.Series:
    """Fitted seasonal factor by calendar month; checks it matches the active profile."""
    frame = pd.read_csv(path)
    if sorted(frame["month"]) != list(range(1, 13)):
        raise ValueError(f"{path} must contain one factor per calendar month")
    if frame["profile_input_sha256"].nunique() != 1 or frame["profile_input_sha256"].iloc[0] != file_sha256(PROFILE_FILE):
        raise ValueError(
            "The seasonal solar correction was fitted to a different renewable profile; "
            "rerun python scripts/solar_seasonal_correction.py"
        )
    return frame.set_index("month")["seasonal_factor"]


def smooth_monthly_factor(days: pd.DatetimeIndex, factors: pd.Series) -> pd.Series:
    """Daily factor, piecewise linear between mid-month nodes, keeping each month's mean.

    Months are whole calendar months; the curve continues one node beyond each
    end so the first and last months are treated like the others.
    """
    days = pd.DatetimeIndex(days)
    months = pd.period_range(days.min(), days.max(), freq="M")
    if days.min() != months[0].start_time or days.max().normalize() != months[-1].end_time.normalize():
        raise ValueError("Smoothing needs whole calendar months")
    node_months = pd.period_range(months[0] - 1, months[-1] + 1, freq="M")
    node_times = pd.DatetimeIndex(
        [period.start_time + (period.end_time - period.start_time) / 2 for period in node_months]
    )
    calendar_month = np.array([period.month for period in node_months])
    # Hat basis of each node evaluated at every day, summed by calendar month.
    basis = np.zeros((len(days), 12))
    day_hours, node_hours = _hours(days + pd.Timedelta(hours=12)), _hours(node_times)
    for k in range(len(node_months)):
        unit = np.zeros(len(node_months))
        unit[k] = 1.0
        basis[:, calendar_month[k] - 1] += np.interp(day_hours, node_hours, unit)
    day_month = days.month.to_numpy()
    in_range = sorted(set(day_month))
    design = np.vstack([basis[day_month == month].mean(axis=0) for month in in_range])
    target = np.array([factors.loc[month] for month in in_range])
    nodes = np.linalg.lstsq(design, target, rcond=None)[0]
    return pd.Series(basis @ nodes, index=days, name="seasonal_factor")


def hourly_solar_correction(
    snapshots: pd.DatetimeIndex,
    factors: pd.Series | None = None,
    weather: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Hourly correction by area: daily physical factor x smooth seasonal factor."""
    snapshots = pd.DatetimeIndex(snapshots)
    factors = read_seasonal_factors() if factors is None else factors
    days = pd.date_range(snapshots.min().normalize(), snapshots.max().normalize(), freq="D")
    seasonal = smooth_monthly_factor(days, factors)
    physics = physical_factor(weather)
    if not days.isin(physics.index).all():
        raise ValueError("ERA5 weather does not cover the model snapshots")
    daily = physics.loc[days].mul(seasonal, axis=0)
    return daily.reindex(snapshots.normalize()).set_axis(snapshots)


def fy2025_26_check(factors: pd.DataFrame) -> pd.DataFrame:
    """Out-of-sample monthly comparison of the active model profile with observations."""
    profiles = pd.read_csv(PROFILE_FILE, parse_dates=["snapshot"]).set_index("snapshot")
    weights = area_weights()
    capacity = solar_capacity_mw(profiles.index).to_numpy()
    raw = pd.DataFrame({area: profiles[f"{area}_solar"] for area in AREAS})
    correction = hourly_solar_correction(profiles.index, factors.set_index("month")["seasonal_factor"])
    observed = read_observed_history()
    rows = {}
    for label, frame in {"uncorrected": raw, "corrected": raw * correction}.items():
        potential = frame.mul(weights, axis=1).sum(axis=1).mul(capacity).resample("MS").sum() / 1_000.0
        target = observed.reindex(potential.index)
        potential = potential * target.sum() / potential.sum()
        rows[f"{label}_error_pct"] = (potential / target - 1.0) * 100.0
        rows[f"{label}_rmse_gwh"] = np.sqrt(((potential - target) ** 2).mean())
    return pd.DataFrame(rows)


def main() -> None:
    factors = fit_seasonal_factors()
    factors.to_csv(FACTOR_FILE, index=False)
    check = fy2025_26_check(factors)
    print(factors[["month_name", "seasonal_factor", "between_year_std"]].round(3).to_string(index=False))
    print(
        f"FY{TEST_FISCAL_YEAR} monthly RMSE after annual calibration (not in the fit): "
        f"{check['uncorrected_rmse_gwh'].iloc[0]:.0f} GWh uncorrected, "
        f"{check['corrected_rmse_gwh'].iloc[0]:.0f} GWh corrected"
    )
    print(f"Wrote {FACTOR_FILE}")


if __name__ == "__main__":
    main()
