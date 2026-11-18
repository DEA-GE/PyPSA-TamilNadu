# Solar availability methodology

This note describes how hourly solar availability (`p_max_pu` of the solar
generators in `model/generators-p_max_pu.csv`) is built for FY2025-26 and why.
It covers the installed-capacity trajectory, the seasonal correction, the
annual calibration, the validation against observed generation, and the known
limitations. File-level details are in the
[Input data guide](input-data.md#wind-and-solar-availability).

## 1. Overview

For a solar generator `g` in balancing area `a`, at hour `t` on day `d` in
calendar month `m`:

```
p_max_pu(g, t) = CF(a, t) × φ(a, d) × s(d) × C(t) / P × k
```

| Term | Meaning | Source |
| --- | --- | --- |
| `CF(a, t)` | ERA5 capacity factor of area `a`, converted by atlite (panel tilt and ambient temperature included) and aligned to IST model hours | `data/model_inputs/renewable_profiles.csv`, built by `scripts/prepare_lava_profiles.py` (§7) |
| `φ(a, d)` | Physical layer: `(1 − soiling) × wind-cooling gain`, daily | Literature parameters, ERA5 daily weather (§3.1) |
| `s(d)` | Seasonal layer: smooth daily curve whose monthly means equal the fitted factor `f(m)` | Fitted on FY2020-21 to FY2024-25 (§3.2) |
| `C(t)` | Grid-connected solar capacity installed in Tamil Nadu at hour `t` | NITI Dashboard monthly installed capacity, i.e. MNRE's monthly state series (§2) |
| `P` | Modeled solar fleet, the sum of solar `p_nom` (14,045.55 MW, July 2026 vintage) | `model/generators.csv` |
| `k` | Annual multiplier that makes FY2025-26 potential equal observed generation | NITI Dashboard, 19,690.99 GWh (§4) |

`p_nom` is not changed. The fleet keeps its July 2026 size and its area
distribution. `C(t) / P` is the share of that fleet already installed at hour
`t`. The builder applies the steps in `scripts/build_9ba_model.py`:
`apply_solar_seasonal_correction` (`φ`, `s`), then
`apply_solar_capacity_trajectory` (`C(t) / P`, `k`).

Before this method, `p_max_pu = CF × 0.9304`. That fixed factor matched the
annual total with a constant 14,045.55 MW fleet. Monthly potential was then
14.5% too high in April and 8% too low in July.

## 2. Installed capacity through the year

**Why.** Tamil Nadu solar grew from 10,154 MW on 31 March 2025 to 13,580 MW on
31 March 2026. A constant July 2026 fleet overstates capacity by 38% in April
and 4% in March. The 0.9304 factor hid this by mixing a capacity error with a
performance correction.

**Sources** (`scripts/solar_capacity_trajectory.py`):

- **Month-end installed capacity by source**, January 2019 to August 2026,
  from the NITI Dashboard (ICED) export in
  `data/source/NITI dashboard/monthly installed capacity Tamil Nadu/`.
  Tidied in `data/model_inputs/tamil_nadu_installed_capacity_monthly_2019_2026.csv`.
- **MNRE, *State-wise (Location based) installed capacity of Renewable
  Power*.** Eight FY2025-26 editions, in
  `data/model_inputs/tamil_nadu_re_capacity_releases_FY2025_26.csv` with
  provenance in `data/source/re_capacity_releases_fy2025_26/`. They split solar
  into ground-mounted, rooftop (including PM-Surya Ghar) and off-grid/KUSUM-B.
  The NITI series equals MNRE's solar and wind totals exactly in all eight
  months; the builder checks this.

**Derivation:**

1. Total solar at each month end is the NITI value.
2. Off-grid solar (70.4–74.55 MW) is subtracted because the model excludes it.
   It is taken from the MNRE releases and interpolated in time between them.
3. A value "as on D" is the capacity at the end of day D. Hourly capacity is
   interpolated linearly between month ends: 10,083 MW at the first snapshot,
   13,503 MW at the last, 11,182 MW on average.
4. The monthly series is statewide, so every area is scaled by the same share
   `C(t) / P`.

An earlier version derived the five months without an MNRE edition (June,
August, September, November, December 2025) from CEA's monthly RES total
instead. Those values were within 40 MW of the NITI series.

**Timing within the month.** Linear interpolation assumes additions arrive
evenly through the month. A step that counts each month's additions only from
the next month was tested. It lowers the monthly error only through March
2026, when 1,227 MW was added; without March the two give the same error. In
March 2026, observed daily output relative to the profile rose 15.4% ± 2.6%
through the month. Linear interpolation predicts about +10% and the step
predicts none, so linear interpolation is kept.

**Scope.** Grid-connected solar includes rooftop: about 1,000–1,530 MW over
the year, and 1,635.8 MW in the July 2026 fleet. See §6.

## 3. Seasonal correction

**Why.** With the correct capacity and one annual factor, modeled monthly
potential for FY2025-26 is 9–13% too low in June–July and 3–10% too high in
December–March. The constant fleet had partly hidden this, because its
capacity error ran the opposite way. atlite models panel tilt and ambient
temperature. It does not model wind cooling (its Huld module-temperature
model has no wind term), soiling, or inverter clipping, and it inherits ERA5's
irradiance biases. The correction has a physical layer with literature
parameters and a seasonal layer fitted on historical generation. The code is
`scripts/solar_seasonal_correction.py`.

### 3.1 Physical layer

Driven daily, per area, by ERA5 precipitation and 10 m wind at the area
centroid (`data/model_inputs/era5_daily_weather_tn_areas_FY2020_26.csv`).

**Soiling** (Kimber-type model):

```
loss(d) = 0                              if rain(d) ≥ 6 mm or 30 days since last cleaning
        = min(loss(d−1) + 0.25 %, 30 %)  otherwise
```

| Parameter | Value | Basis |
| --- | --- | --- |
| Dry-day soiling rate | 0.25 %/day | Mumbai and Gandhinagar 0.45 %/day [2, 3]; Chennai 2.3% annual loss [1], with dry-period rates of 0.12–0.39 %/day reported only in secondary material (not verified) |
| Rain that cleans modules | ≥ 6 mm/day | Kimber model default (pvlib) |
| Manual cleaning interval | 30 days | At least half the sites in the NISE All-India PV survey cleaned monthly or irregularly [4] |
| Maximum loss | 30% | Kimber model default |

**Wind cooling.** atlite's crystalline-silicon Huld model raises module
temperature by 0.035 K per W/m² of irradiance at any wind speed [5]. The
Faiman model makes the rise wind-dependent. The output gain is:

```
ΔT   = G × (0.035 − 1 / (25 + 6.84 × v))     [K, cooler than atlite assumes]
gain = 1 + 0.004 × ΔT
v    = 0.651 × v10                            [10 m wind to ~2 m module height, roughness 0.1 m]
```

`G` is a production-weighted plane-of-array irradiance of 650 W/m². The power
temperature coefficient is −0.4 %/K. The gain is 1 at a module-height wind of
0.52 m/s.

**Size in FY2025-26** (capacity-weighted): soiling averages 2.6%, between
1.2% (October) and 3.6% (March). Wind cooling adds 1.8% on average, between
1.0% (October) and 2.5% (July). The physical layer alone lowers the FY2025-26
monthly RMSE only from 108 to 100 GWh.

### 3.2 Seasonal layer

**Historical data:**

- Observed monthly Tamil Nadu solar generation, FY2015-16 to FY2026-27, from
  the NITI Dashboard (ICED) export in
  `data/source/NITI dashboard/monthly solar generation Tamil Nadu/`. Its
  FY2025-26 rows equal the series used for model validation.
- Month-end solar capacity, March 2020 to March 2026, from the NITI monthly
  installed-capacity series (§2), including off-grid. Off-grid affects only
  the level, which the fit removes. From March 2022 the series agrees with the
  fiscal-year-end values in `data/source/Capacity_Metatable_Data_Sept26.xlsx`;
  in March 2020–2021 it is about 50 MW lower.
- ERA5 daily GHI, precipitation and wind, April 2020 to March 2026, at the nine
  area centroids, retrieved from the Open-Meteo archive of Copernicus ERA5
  (`data/source/era5_open_meteo/`).

**FY2019-20 is excluded.** In 2019 the monthly capacity series rises slowly
and then jumps by 688 MW in December. Observed generation implies the capacity
was there earlier: relative to the year's average, August–October 2019 output
is 16–30% above what the series supports, and January 2020 is 22% below. The
capacity was evidently recorded when reported, not when commissioned, so that
year's ratios are not usable.

**Reconstructing the profile in other years.** atlite profiles exist only for
FY2025-26. The active profile tracks ERA5 GHI closely: daily correlation 0.977
in Tirunelveli, 0.945 in Madurai, 0.969 in Trichy. Its monthly ratio to GHI
varies smoothly from 0.94 in June to 1.09 in December (capacity-weighted,
normalised), as expected for fixed-tilt panels. For each area `a` and calendar
month, that ratio `ρ(a, m)` is taken from FY2025-26 and applied to ERA5 GHI of
other years.

**Fit.** For each month `m` of each fiscal year `y`:

```
modeled(y, m)  = C̄(y, m) × hours(m) × Σa w(a) × ρ(a, m) × GHI(a, y, m) × φ̄(a, y, m)
ratio(y, m)    = observed(y, m) / modeled(y, m)
r(y, m)        = ratio(y, m) / [Σm observed(y, m) / Σm modeled(y, m)]
f(m)           = median of r(y, m) over FY2020-21 … FY2024-25
```

- `C̄(y, m)` is the month's mean capacity, interpolated daily between month
  ends.
- `w(a)` is the area share of the modeled solar fleet: Tirunelveli 43%,
  Madurai 29%, Trichy 10%, others 1–7%.
- `φ̄` is the physical layer, averaged over the month with GHI weights.
- Dividing by each year's annual ratio removes level differences between years
  (fleet composition, rooftop share, reporting scope). Only the seasonal shape
  is fitted.
- The median limits the influence of a single anomalous month in one year.
  Mean and median give the same leave-one-year-out error (§5).

FY2025-26 is excluded from the fit and serves as the test year.

**Fitted factors** (`data/model_inputs/solar_seasonal_correction_factors.csv`,
which also stores each year's ratios):

| | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Jan | Feb | Mar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `f(m)` | 0.927 | 1.000 | 1.057 | 1.101 | 1.061 | 1.019 | 0.993 | 1.006 | 0.945 | 0.950 | 0.976 | 0.934 |
| Between-year s.d. | 0.040 | 0.034 | 0.061 | 0.020 | 0.035 | 0.055 | 0.032 | 0.056 | 0.019 | 0.021 | 0.038 | 0.034 |

The pattern recurs in all five training years and in FY2025-26. Real output
is above the ERA5-based estimate in the southwest monsoon (June–August) and
below it in the dry season (December–April). The literature gives two mechanisms:

- ERA5 underestimates surface irradiance under monsoon cloud, while it tracks
  well in February–May. This was found at 27 IMD stations over 1985–2020 [6].
- ERA5 runs IFS cycle 41r2 [6], which used a fixed monthly aerosol
  climatology [7]. Products with climatological aerosols overestimate Indian
  irradiance most from October to March [8, 9]. NREL found +63 W/m² at Chennai
  for January–June 2012 [9].

A plant in rain-shadow Andhra Pradesh also shows a performance ratio 5–10
points lower in January–March than in June–November, even after temperature
correction [10].

**Application.** `s(d)` is piecewise linear between mid-month nodes. The node
values are solved so that each month's mean of `s` equals `f(m)`, which avoids
artificial jumps at month boundaries. The largest day-to-day change is below
1%. The factors file stores the SHA-256 of `renewable_profiles.csv` it was
fitted to, and the builder stops if the active profile differs.

## 4. Annual calibration

After `φ`, `s` and `C(t) / P`, one multiplier `k` makes FY2025-26 potential
equal the NITI Dashboard annual solar generation of 19,690.99 GWh
(`data/model_inputs/niti_dashboard_annual_generation_FY2025_26.csv`). Now
`k = 1.1863`; before this method it was 0.9304.

A value above one is consistent with capacity being reported on the inverter
(AC) side while atlite output is per unit of panel capacity. Plants are
typically built with more panel than inverter capacity. This reading is not
verified. The highest solar `p_max_pu` is 0.787.
`model/solar_availability_calibration.csv` records the potentials and `k`.

## 5. Validation

**Monthly potential, FY2025-26 (not used in any fit)**, error against NITI
monthly generation after annual calibration:

| Version | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Jan | Feb | Mar | RMSE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Constant fleet, 0.9304 (before) | +14.5% | +4.4% | −2.5% | −8.0% | −1.2% | 0.0% | −0.9% | +0.2% | +1.9% | +1.3% | −3.1% | −4.5% | 86 GWh |
| Capacity trajectory only | +3.7% | −4.2% | −9.4% | −12.9% | −4.6% | −1.1% | +0.2% | +1.9% | +4.6% | +4.9% | +3.4% | +9.7% | 108 GWh |
| Trajectory + seasonal correction | −4.1% | −3.6% | −2.2% | −3.1% | +1.8% | +2.4% | +0.9% | +3.8% | −0.6% | +0.9% | +1.2% | +2.1% | 40 GWh |

The constant fleet fitted better than the trajectory alone only because its
capacity error offset the seasonal bias. Its April error was the largest of
any version.

**Leave-one-year-out.** The seasonal factors are refitted without year `y`,
always also without FY2025-26, and applied to `y`. Values are RMS monthly
error as a percentage of the mean month, from the ERA5 reconstruction:

| Year | 2020-21 | 2021-22 | 2022-23 | 2023-24 | 2024-25 | 2025-26 |
|---|---|---|---|---|---|---|
| Physical layer only | 6.1 | 5.9 | 4.6 | 7.4 | 7.0 | 5.8 |
| + seasonal factors (median) | 3.6 | 3.0 | 4.8 | 5.6 | 4.3 | 2.4 |

The correction improves five of the six years. FY2022-23 is slightly worse
(4.6% to 4.8%): its April, June and March ratios were close to 1, unlike
the other years'. With
the mean instead of the median, the average of these errors is the same
(3.95%). The median was kept for robustness, not chosen on the test year. On
FY2025-26 the mean would give 49 GWh instead of 40 GWh.

**Dispatch.** These are rolling FY2025-26 runs without unit commitment
(`run_selection("rolling", unit_commitment="none")`). Each compares dispatched
solar with the NITI monthly series and the daily series:

| Version | Annual solar | Monthly RMSE | Largest monthly error | Daily correlation with NITI daily |
|---|---|---|---|---|
| Constant fleet, 0.9304 (before) | 19,689 GWh | 86 GWh | 14.5% (Apr) | 0.806 |
| Capacity trajectory only | 19,674 GWh | 106 GWh | 12.8% (Jul) | 0.816 |
| Trajectory + seasonal correction, IST-aligned profile (§7) | 19,689 GWh | 39 GWh | 4.1% (Apr) | 0.841 |

All 53 weekly windows solve to optimality. Curtailment stays below 0.1% of
solar potential. Annual coal, hydro and wind change by 0.1% or less against
the constant-fleet run. Unserved energy, all of it in April 2025, rises from
73.8 to 76.3 GWh because April solar is now 16% lower than with the constant
fleet. Aligning the profile to IST hours (§7) changed none of the monthly
figures; it lowered April unserved energy from 80.4 to 76.3 GWh. Shifting wind to IST (§7) leaves monthly solar and wind
energy unchanged (solar RMSE 40 GWh) and raises the daily wind correlation
with observations from 0.915 to 0.919. With wind now peaking in the afternoon
rather than the late morning, April unserved energy falls from 76.3 to 24.4 GWh.
Hydro falls by 69 GWh and coal rises by 56 GWh. That April shortfall existed before the solar change and is a separate
issue.

## 6. Limitations and open questions

- **Observed benchmark.** The NITI Dashboard publishes two solar series for
  FY2025-26: the annual and monthly series (19,691 GWh) used here, and a daily
  series that sums to 15,264 GWh. Their monthly ratio varies from 1.18 to
  1.52. The daily series probably covers only plants metered by the state
  load-despatch centre (SLDC), excluding rooftop and behind-the-meter output;
  this is not confirmed. The calibration and seasonal factors follow the
  monthly series.
- **Rooftop and demand.** The fleet includes 1,635.8 MW of grid-connected
  rooftop modeled as supply. If the demand series ("Demand Met") is net of
  behind-the-meter rooftop output, that energy is counted twice. Rooftop also
  grew faster than ground-mounted solar (about 53% against 30% in FY2025-26),
  which uniform scaling ignores.
- **Capacity reporting dates.** Monthly capacity records when capacity was
  reported, which can lag commissioning. The lag was clear enough in 2019 to
  exclude FY2019-20 from the fit. The FY2025-26 series matches MNRE's
  releases, but within-month timing is still an assumption (§2).
- **Short training record.** Five years of factors; between-year standard
  deviations of 0.02–0.06 mean individual months are uncertain by a few
  percent.
- **Profile reconstruction.** The ERA5 reconstruction assumes the FY2025-26
  ratio of profile to GHI applies in other years. Changes in diffuse fraction
  or temperature between years are not captured. atlite profiles for the
  historical years, run with the same configuration, would replace the
  reconstruction.
- **Spatial detail.** Weather is taken at area centroids, not plant locations.
  Area weights are those of the July 2026 fleet, although the historical fleet
  was distributed differently.
- **Physical parameters** are literature values, not calibrated. Soiling
  depends strongly on each plant's cleaning practice.
- **Hourly shape.** The correction scales whole days; it does not change the
  shape within a day. Inverter clipping, which flattens midday output, is not
  modeled.

## 7. Profile time alignment

The wind and solar columns of `renewable_profiles.csv` are built by
`scripts/prepare_lava_profiles.py` from the LAVA/atlite area profiles, with
the source in `data/source/lava_profiles_fy2025_26/`:

- solar `9BA`: CSi panel, latitude-optimal fixed tilt, no tracking;
- wind `AggrPCWiderCUF`.

Both are computed on the ERA5 cutout `TamilNadu-2025-era5.nc`, whose time axis
is UTC; LAVA keeps those timestamps.

- **Conventions.** The model's hour `L` is `[L, L+1)` IST: REA 15-minute blocks
  1–4 map to hour 0. atlite keeps ERA5's labels for radiation: the value at
  `T` (UTC) is the mean over `(T−1h, T]` UTC, and only the solar-position
  calculation is shifted by −30 minutes. IST is UTC+5:30, so each model hour
  takes half of two atlite hours:

  ```
  CF(L) = 0.5 × (atlite(L − 5 h) + atlite(L − 4 h))      [L in IST, atlite labels in UTC]
  ```

  Output within each ERA5 hour is treated as constant.
- **No wrap-around.** The source window runs from 2025-03-31 18:00 to
  2026-04-01 00:00 UTC, so the first and last model hours use real data.
  Earlier profiles shifted the series 5 hours and wrapped the last five hours
  of the year to its start.
- **Effect.** Annual and monthly energy are unchanged, and so are the seasonal
  factors and `k`. On the average day, the midpoint of solar output moves from
  hour 12.26 to 11.76 in Tirunelveli and to 11.63 in Chennai North. That is
  0.5 h before local solar noon, as it should be for hour-beginning labels.
  Solar output is about 0.075 p.u. higher at 07:00–09:00 and up to 0.07 p.u.
  lower at 15:00–18:00 than before. The evening drop therefore starts about an
  hour earlier.
- **Wind.** ERA5 100 m wind is instantaneous. The value at `L − 5 h` (UTC)
  is the middle of model hour `L` (IST), so wind is shifted 5 hours:
  `CF(L) = atlite(L − 5 h)`. Earlier wind profiles used the UTC timestamps as
  IST, putting wind about 5 hours early; on the average day, Tirunelveli wind
  now peaks at 16:00 IST instead of 11:00. Annual wind energy is unchanged.
- **Possible refinement.** Splitting each ERA5 hour by clear-sky irradiance
  instead of in equal halves would sharpen the sunrise and sunset hours.

## 8. Updating

| Change | Action |
| --- | --- |
| New LAVA/atlite profiles | `python scripts/prepare_lava_profiles.py --extract-from <LAVA data folder>`, then `python scripts/solar_seasonal_correction.py`, then `python scripts/build_9ba_model.py` |
| Other change to `renewable_profiles.csv` | `python scripts/solar_seasonal_correction.py`, then `python scripts/build_9ba_model.py` |
| New monthly capacity export or MNRE release | Update `tamil_nadu_installed_capacity_monthly_2019_2026.csv` (and `tamil_nadu_re_capacity_releases_FY2025_26.csv` for off-grid), refit, rebuild |
| Another study year | Extend the ERA5 weather file, the monthly capacity, the observed series and `TRAINING_FISCAL_YEARS`; refit, keeping the study year out of the fit |
| Different literature parameters | Edit the constants at the top of `solar_seasonal_correction.py`, refit, rebuild |

Tests: `tests/test_prepare_lava_profiles.py`,
`tests/test_solar_capacity_trajectory.py` and
`tests/test_solar_seasonal_correction.py`. They check agreement with MNRE's
releases, interpolation, the soiling and wind models, mean-preserving
smoothing, that the stored factors reproduce the fit, and the builder.

## References

1. Herrmann, Schweiger, TamizhMani, Shisler, Kamalaksha (2015). *IEEE 42nd
   Photovoltaic Specialists Conference*. doi:10.1109/PVSC.2015.7356131.
   Chennai annual soiling loss 2.3% (from the abstract).
2. IEA-PVPS (2022). *Soiling Losses – Impact on the Performance of PV Power
   Plants*, Report T13-21:2022.
3. Valerino, Ratnaparkhi, Ghoroi, Bergin (2021). *Solar Energy* 227, 44–55.
   doi:10.1016/j.solener.2021.08.080.
4. Dubey et al. (2016). *All India Survey of Photovoltaic Module Reliability:
   2014*. NISE and NCPRE, IIT Bombay.
5. atlite, `atlite/resources/solarpanel/CSi.yaml` and
   `atlite/pv/solar_panel_model.py` (Huld model; constant 0.9 inverter
   efficiency; no wind, clipping or soiling term).
6. Jadhav, Belange, Gajbhiv, Kumar, Rahul, Sudeepkumar, Bhawar (2025).
   Evaluation of the reanalysis and satellite surface solar radiation
   datasets using ground-based observations over India. *Atmosphere* 16(8),
   957. doi:10.3390/atmos16080957.
7. Bozzo et al. (2020). *Geoscientific Model Development* 13, 1007–1034.
   doi:10.5194/gmd-13-1007-2020.
8. Riihelä, Kallio, Devraj, Sharma, Lindfors (2018). Validation of the SARAH-E
   satellite-based surface solar radiation estimates over India. *Remote
   Sensing* 10(3), 392. doi:10.3390/rs10030392.
9. Sengupta, Perez, Gueymard, Anderberg, Gotseff (2014). *Satellite-Based Solar
   Resource Data Sets for India 2002–2012*. NREL/TP-5D00-61121.
10. Raja, Jain, Rajan (2019). *International Journal of Applied Power
    Engineering* 8(1), 22–33. doi:10.11591/ijape.v8.i1.pp22-33.
