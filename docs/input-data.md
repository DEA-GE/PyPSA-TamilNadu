# Input data guide

All paths below are relative to the repository root. The supported network is
`model/`, built by `python scripts/build_9ba_model.py`. The builder reads the
prepared files in `data/build_template/`, `data/model_inputs/`, and
`data/reservoirs/`, plus two source workbooks. Most other files in
`data/source/` are retained as evidence or upstream material; their presence
does not mean the builder reads them. The model covers 8,760 hourly snapshots
from 2025-04-01 00:00 through 2026-03-31 23:00. CSV time series generally use
an integer row index aligned to `model/snapshots.csv`; the renewable input uses
an explicit `snapshot` timestamp.

## Which file to open

| Question | Prepared or source input | Built model output |
| --- | --- | --- |
| Statewide hourly demand | `data/build_template/loads-p_set.csv` | Sum the nine columns of `model/loads-p_set.csv` |
| Balancing-area demand | `data/model_inputs/tamil_nadu_balancing_area_demand.xlsx`, `FY2025-26 Calibration` | `model/loads-p_set.csv`, `model/demand_allocation.csv` |
| Wind and solar availability | `data/model_inputs/renewable_profiles.csv` | `model/generators-p_max_pu.csv` |
| Solar capacity through the year | `data/model_inputs/tamil_nadu_installed_capacity_monthly_2019_2026.csv`, `tamil_nadu_re_capacity_releases_FY2025_26.csv` | `model/solar_capacity_trajectory.csv`, `model/solar_availability_calibration.csv` |
| Seasonal solar correction | `data/model_inputs/solar_seasonal_correction_factors.csv`, `era5_daily_weather_tn_areas_FY2020_26.csv` | `model/solar_seasonal_correction.csv` |
| Plant capacities and technologies | `data/source/TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx`, `PlantInfo` and `District Allocation`; `data/build_template/generators.csv` | `model/generators.csv`, `model/storage_units.csv`, `model/component_metadata.csv` |
| July 2026 capacity additions | `data/model_inputs/additional_capacity_2026_07.csv` | `model/additional_capacity_2026_07.csv`, `model/capacity_reconciliation_2026_07.csv` |
| Hydro assets and observations | `data/model_inputs/hydro_asset_registry.csv`, `data/model_inputs/hydro_fleet_reconciliation.csv`, `data/reservoirs/tn_reservoir_daily_FY2025_26.csv` | `model/hydro_reservoir_parameters.csv`, `model/hydro_reservoir_inflow.csv`, `model/hydro_observed_soc.csv` |
| Observed generation and energy targets | `data/source/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx` | `model/oil_gas_daily_energy_budget.csv`, `model/nuclear_daily_energy_budget.csv`, `model/oil_gas_energy_budget_summary.csv` |
| Area boundaries and grid | `data/model_inputs/Balancing_areas.txt`, `data/model_inputs/TN_9BA_with_interconnectors_pypsa_FY2025-26.xlsx` | `model/buses.csv`, `model/lines.csv`, `model/links.csv`, `model/transmission_capacity_metadata.csv` |
| FY2025-26 REA imports and exports | `data/model_inputs/rea_interstate_hourly_flows_FY2025_26.csv`, `rea_interstate_gateway_shares_FY2025_26.csv`, `rea_interstate_source_audit_FY2025_26.csv` | `model/loads.csv`, `model/loads-p_set.csv`, `model/interstate_flow_audit.csv`, `model/interstate_flow_reconciliation.csv` |
| Plant-specific REA export reservations | `data/model_inputs/rea_plant_export_mapping.csv`, `rea_plant_export_hourly_FY2025_26.csv`, `rea_plant_export_mapping_audit_FY2025_26.csv` | `model/generators.csv`, `model/generators-p_min_pu.csv`, `model/rea_plant_export_reservation_audit.csv` |
| Historical four-station capacity shares | `data/model_inputs/tn_export_capacity_allocations.csv` | `model/tn_export_capacity_allocations.csv` (reference only) |
| Coal unit outages, auxiliary consumption and capability | `data/source/NITI dashboard/unit outages Tamil Nadu`, `auxiliary consumption Tamil Nadu`; `data/model_inputs/coal_unit_outages_FY2025_26.csv`, `coal_auxiliary_consumption.csv`, `coal_unit_capability_FY2025_26.csv` | Applied at run time with `outages.enabled` (`scripts/generator_outages.py`); not in `model/` |
| Central coal station declared capacity | `data/source/REA data/*.zip` (SRPC REA, not in Git); `data/model_inputs/central_station_declared_capacity_FY2025_26.csv`, `central_station_units.csv`, `central_station_tn_entitlement_FY2025_26.csv` | Declared capacity applied at run time with `outages.enabled` and `central_availability: declared`; the entitlement shares size the Tamil Nadu units in the build |

The `model/` CSVs are generated products. To change inputs, edit the relevant
file under `data/` and rebuild. `data/build_template/` is an intermediate,
single-node input set, not a second supported network.

The REA hourly input is produced from the deduplicated transaction ledger with
`python scripts/prepare_rea_interstate_flows.py --ledger <ledger.parquet>`.
Resolved imports use the generating plant's mapped state and resolved exports
use the beneficiary state. State routes are then grouped into the four modeled
external gateways. Records that remain unresolved are retained as a separate
hourly total, not relabeled as verified. Unresolved imports are excluded from
the modeled loads, so modeled imports contain only confirmed `resolved`
records; their source energy remains visible in the audit with zero modeled
flow. Unresolved exports are allocated by resolved export-flow shares and
preserved under the `unresolved_distributed` measure. Imports and exports are
never netted.

Verified exports with a named Tamil Nadu generator are also prepared at plant
level. Fifteen REA plant labels map to modeled units and cover 15,496.913 GWh
of the 15,504.698 GWh resolved export total. The remaining 7.785 GWh from
Kamachi Steels and ARKAY Energy stays explicitly unmatched because those plants
are absent from the model inventory. For every matched plant, the builder
reserves capacity equal to its peak concurrent REA export schedule and creates
a fixed-dispatch generator at the plant's physical balancing-area bus. The
existing gateway export loads are unchanged, so the reservation does not
double count interstate energy. The plant's ordinary units, which serve Tamil
Nadu, keep:

- at the seven central coal stations (Vallur, NTPL, NNTPS, NLC TPS-II Stage I
  and II, TPS-I and TPS-II Expansion), Tamil Nadu's **entitlement share** of
  their rating: its entitlement in the SRPC accounts
  (`commercial_ent_tamilnadu.csv`) over the station's declared capacity, for
  the year (`data/model_inputs/central_station_tn_entitlement_FY2025_26.csv`,
  from `scripts/prepare_declared_capacity.py`). The shares follow the Ministry
  of Power's allocation and hardly change through the year: Vallur 69%, NTPL
  41%, NNTPS 70%, NLC TPS-II Stage I 39% and Stage II 45%, TPS-I and TPS-II
  Expansion 49%. With `outages.central_availability: declared`, the Tamil Nadu
  units are then available at the share times the station's declared capacity,
  hour by hour, which is Tamil Nadu's entitlement. Tamil Nadu's peak hourly
  withdrawal from each station is close to the share times the rating
  (Vallur 1,036 MW drawn against 1,040 MW). Other states also take Tamil
  Nadu's undrawn share (unrequisitioned surplus), so at Vallur, NTPL, NNTPS
  and TPS-I Expansion the export peak plus Tamil Nadu's share exceeds the
  rating (by 707 MW in total). At the NLC TPS-II stations it falls short (by 249
  MW). `capacity_overlap_mw` in `model/rea_plant_export_reservation_audit.csv`
  records the difference, and the reported installed capacity
  (`model/capacity_by_balancing_area.csv`) removes it. Sizing the Tamil Nadu
  units at the rating less the export peak, as before, left them 25% short of
  Tamil Nadu's actual share of these stations (NTPL: 188 MW against an average
  withdrawal of 242 MW);
- elsewhere (Kudankulam, MAPS, OPG, Tulsyan NEC, EID Parry), the rating less
  the export peak, which conserves the physical nameplate and keeps unused
  reserved capacity from serving Tamil Nadu.

Included measures are fixed loads at the external buses: confirmed imports have
negative `p_set` and exports positive `p_set`. Existing directional grid links
enforce
gateway capacity and carry the exchange, but no `links-p_min_pu.csv` or
`links-p_max_pu.csv` schedule is written. This allows the network to select the
parallel link used within a gateway. The audit output records every hourly
gateway value, the original share, available capacity, any capacity-driven
redistribution, and explicit included/excluded status for distributed flows.

**Metered interchange.** Summing REA transactions leaves Tamil Nadu short of
supply in April–May and in surplus in the wind season: 20.8 TWh of imports
cannot be attributed to a plant (all from sellers outside Tamil Nadu, but
partly trading and duplication), and 7.5 TWh of interstate-grid wind and
solar inside Tamil Nadu is sold to other states without passing through Tamil
Nadu's account. The build therefore matches the measured total instead
(`INTERCHANGE_BASIS = "metered"`, the default; `python
scripts/build_9ba_model.py --interchange transactions` restores the
transaction-only exchange). `scripts/prepare_metered_interchange.py` reads
from the twelve SRPC REA archives (`data/source/REA data`, not in Git):

- the actual metered net drawal of Tamil Nadu's control area from the
  interstate grid (`commercial_dev2022_tamilnadu.csv`, `act_total`): 67.85
  TWh, 1.1% below schedule;
- the output of the interstate-grid plants inside Tamil Nadu, which reaches
  the state as drawal: central stations including Kudankulam and MAPS
  (station-wide schedules, 39.66 TWh), Coastal Energen and ITPCL (schedules to
  Tamil Nadu, 6.98 TWh) and the wind and solar at Tuticorin, Karur and
  Pugalur (7.48 TWh).

Their difference, 13.72 TWh, is the physical net import of the Tamil Nadu
area (`data/model_inputs/tn_metered_interchange_FY2025_26.csv`, hourly). The
transaction loads deliver 4.3 TWh, so each hour a correction load
(`interstate_metered_correction_import_*` or `_export_*`, measure
`metered_correction` in the audit) is added at the gateways, split by the
same shares and within gateway capacity, so that the net import delivered
into Tamil Nadu after the 3% interconnector loss equals the metered value. The
correction adds 13.35 TWh of imports and 1.87 TWh of exports at the gateways:
2.4 TWh in April, 1.6 TWh in May, near zero in June–September. Limitations:
Coastal Energen and ITPCL sales to other states are not subtracted; the
interstate-grid wind and solar inside Tamil Nadu follow the model's wind and
solar profiles, not their scheduled output; the transaction loads keep their
gateway routing, so the correction changes totals, not routes.

The grid is imported with PyPSA's `Network.import_from_excel()` API. To replace
only the grid in an already-built model (without rebuilding generation, demand,
or hydro inputs), run `python scripts/import_grid_from_excel.py`.

## Demand: hourly total and area profiles

The authoritative **model input** for realized statewide demand is
`data/build_template/loads-p_set.csv`: 8,760 rows, one `Tamil_Nadu_demand`
column in MW, and a zero-based integer row index. The matching timestamps are
in `data/build_template/snapshots.csv`. Its values correspond to the FY portion
of `data/source/Tamilnadu_Yearly Demand Profile_2025.xlsx` and
`data/source/Tamilnadu_Yearly Demand Profile_2026.xlsx`. Each source workbook
has a `Yearly Demand Profile` sheet with `State`, `Date` (for example,
`01-Apr 12am`), and `Hourly Demand Met (in MW)`. The checked-in template is
the file the builder actually reads; there is no demand-workbook import in
`scripts/build_9ba_model.py`.

`data/model_inputs/tamil_nadu_balancing_area_demand.xlsx` contains `Inputs`,
`Interpolation`, and `FY2025-26 Calibration` sheets. The builder reads nine
area rows from two tables on `FY2025-26 Calibration`: annual energy shares at
Excel rows 9-17 and peak shares at rows 23-31. These are shares of projected
area totals, **not hourly meter readings**. For each area, the builder applies
an affine transformation (`slope × statewide hourly MW + intercept`) chosen to
match its annual-energy and peak shares. It checks that the nine loads sum to
the original state load in every hour. The statewide input sums to 131.3834
TWh and peaks at 19,987.33 MW; the workbook's 149.063 TWh and 21,481 MW
projection is not substituted for it.

`model/loads-p_set.csv` has one MW column per area load, named like
`chennai_north_demand`, with row order matching `model/snapshots.csv`.
`model/demand_allocation.csv` records source shares, modeled energy and peak,
and the slope and intercept. Area profiles are synthetic and share the shape
information of the state series; they are not independently observed profiles.

## Wind and solar availability

`data/model_inputs/renewable_profiles.csv` is the **active** hourly input. It
has a `snapshot` column and 18 capacity-factor columns: `<area>_wind` and
`<area>_solar` for each of the nine areas (for example,
`chennai_north_wind`). Values are fractions from 0 to 1, not MW or generated
energy. It contains 8,760 complete hourly rows, exactly aligned to the model
snapshots. The current checked-in columns vary by area. The builder applies
each area's technology profile to its corresponding wind or solar generators
and writes component-level availability to `model/generators-p_max_pu.csv`.
The builder checks timestamps, required columns, missing values, and bounds.

**Solar columns** come from the LAVA/atlite `9BA` area profiles (CSi panel,
latitude-optimal fixed tilt, ERA5). `scripts/prepare_lava_profiles.py` reads
the UTC source window in `data/source/lava_profiles_fy2025_26/`. atlite
labels each value at the end of its UTC hour, while model hours begin at their
IST label. Each model hour is therefore the mean of the two atlite hours it
overlaps. The window extends beyond the fiscal year, so nothing wraps around.
The method and checks are in
[Solar availability methodology §7](solar-availability-methodology.md#7-profile-time-alignment).

**Wind columns** come from the LAVA `AggrPCWiderCUF` area profiles, through
the same script and source folder. LAVA labels values with the ERA5 cutout's
UTC timestamps, and ERA5 100 m wind is instantaneous. Each IST model hour
therefore takes the value at its midpoint, 5 hours earlier in UTC. The script
also writes the fiscal-year wind table
`data/model_inputs/Onshorewind_9BAs_AggrPCWiderCUF_FY2025-26.csv`. Until
November 2026 that table and the model's wind columns used the UTC timestamps
as if they were IST, putting wind about 5 hours early.

Earlier versions built both technologies with
`scripts/construct_and_compare_re_profiles_fy2025_26.py`. That script shifted
the series 5 hours later and wrapped the last five hours of the year to its
start, and it needs source files no longer in the repository. The previous
model input is archived at
`data/model_inputs/renewable_profiles_before_external_FY2025_26.csv` for
`notebooks/compare_external_re_profiles_FY2025_26.ipynb`. The older workbook
`data/source/wind_solar_profiles_TN-9BAs-2025-26-1h.xlsx` is retained for
comparison; `scripts/prepare_renewable_profiles.py` checks or regenerates the
archived baseline, leaving the active input unchanged.

### Solar capacity through the year

The [Solar availability methodology](solar-availability-methodology.md)
describes the complete solar method, from capacity to annual calibration.

Solar `p_nom` is the July 2026 fleet (14,045.55 MW, off-grid excluded), but
Tamil Nadu's grid-connected solar grew from 10,083 MW on 31 March 2025 to
13,505 MW on 31 March 2026. The builder therefore multiplies every solar
generator's availability by the installed share of the fleet at each hour
(`scripts/solar_capacity_trajectory.py`). The inputs are:

- `data/model_inputs/tamil_nadu_installed_capacity_monthly_2019_2026.csv`:
  month-end Tamil Nadu installed capacity by source, January 2019 to August
  2026. It is tidied from the NITI Dashboard (ICED) export in
  `data/source/NITI dashboard/monthly installed capacity Tamil Nadu/`. It
  supplies total solar at each month end.
- `data/model_inputs/tamil_nadu_re_capacity_releases_FY2025_26.csv`: eight
  MNRE state-wise (location based) releases for FY2025-26, with solar split
  into ground-mounted, rooftop, and off-grid. Source URLs and checksums are in
  `data/source/re_capacity_releases_fy2025_26/`. The builder checks that the
  NITI series equals MNRE's solar total in all eight months. It subtracts
  off-grid solar (70.4 to 74.55 MW), interpolated between releases.

A value "as on D" is the capacity at the end of day D; hourly capacity is
interpolated linearly between month ends. The data are statewide, so every
area is scaled by the same share and the area distribution of the fleet is
unchanged. `model/solar_capacity_trajectory.csv` lists each month end and the
installed share of the fleet.

One availability multiplier then makes the year's solar potential equal the
NITI Dashboard's observed 19,690.99 GWh
(`data/model_inputs/niti_dashboard_annual_generation_FY2025_26.csv`). With the
capacity trajectory and the seasonal correction below, the multiplier is
1.1863 (0.9304 with the previous constant fleet and no correction).
`model/solar_availability_calibration.csv` records the potentials, which
include the seasonal correction, and the multiplier.

### Seasonal solar correction

Before the annual multiplier, `scripts/solar_seasonal_correction.py` corrects
the atlite profile for effects atlite does not model. The full method,
parameters, validation and references are in the
[Solar availability methodology](solar-availability-methodology.md#3-seasonal-correction).

- **Physical layer:** daily soiling and wind cooling of modules, with
  literature parameters. It is driven by ERA5 daily precipitation and wind at
  each area centroid (`data/model_inputs/era5_daily_weather_tn_areas_FY2020_26.csv`,
  provenance in `data/source/era5_open_meteo/`).
- **Seasonal layer:** one factor per calendar month (0.93 in March to 1.10
  in July), the median over FY2020-21 to FY2024-25. Observed monthly
  generation (`data/source/NITI dashboard/monthly solar generation Tamil Nadu/`)
  is compared with monthly installed capacity
  (`tamil_nadu_installed_capacity_monthly_2019_2026.csv`) times an ERA5
  reconstruction of the profile.

FY2025-26 is not used in the fit. On it, the monthly RMSE of solar potential
against observed generation falls from 108 GWh to 40 GWh.
`data/model_inputs/solar_seasonal_correction_factors.csv` stores the factors,
the per-year ratios, and a hash of the profile they were fitted to. The
builder refuses factors fitted to a different profile. After changing
`renewable_profiles.csv`, run `python scripts/solar_seasonal_correction.py`
before rebuilding. `model/solar_seasonal_correction.csv` reports the applied
monthly correction.

If `renewable_profiles.csv` is absent, the builder generates a **fallback**:
the same statewide wind shape in all areas and one capacity-weighted solar
shape in all areas, taken from `data/build_template/technology-p_max-pu.csv`.
That fallback is not the current checked-in input. The template has five
technology-level capacity-factor columns (`hydro_reservoir`, `wind_fleet`,
`solar_utility`, `solar_DRE_rooftop`, `small_hydro`) with an integer hourly
index. The older `data/source/profile_onwind_2020.csv` and
`data/source/profile_solar_2020.csv` are retained source files; the nine-area
builder does not read them directly.

### Offshore wind capacity factors

Offshore wind is a scenario technology (see the scenario section of the
README); the base model has none. `data/source/ts_power_150m_block2.xlsx`
(sheet `wp1_ts_power`) holds one weather year, 8 January 2020 to 6 January
2021, of hourly simulated output of an offshore wind farm ("block 2") of IEA
15 MW reference turbines at 150 m hub height:

| Column | Meaning |
| --- | --- |
| `Production_wp1 (GW)` | Farm output with wake effects, homogeneous layout (used) |
| `WS150_wp1`, `WD150_wp1` | Area-mean wind speed (m/s) and direction (°) at 150 m, with wakes |
| `WS150_wp0`, `WD150_wp0` | The same without wake effects |

The output levels off at 1.020 GW in 1,426 hours, the rated output of
68 turbines × 15 MW, so `scripts/prepare_offshore_wind_profile.py` divides it
by 1,020 MW to get the capacity factor. It writes
`data/model_inputs/offshore_wind_profiles.csv` (column `block2`, one value per
model hour; mean 0.56, from 0.13 in March to 0.87 in August).

The series comes from the NIWE / DTU Wind offshore wake study for Tamil Nadu
([*Wake interaction*, NIWE, 2025](https://coe-osw.org/wp-content/uploads/2025/12/Wake_interaction_NIWE_finalized.pdf)):
WRF 4.2.2 mesoscale simulations at 2 km resolution, driven by ERA5, for the
representative year 2020, with the wind farms represented by DTU's Explicit
Wake Parameterization. Block 2 is its Case 1 layout (68 IEA 15 MW turbines,
1.02 GW, 5 MW/km²), with a capacity factor of 56.2 % including wakes, the
same as the profile built here.

- **Time zone.** Neither the workbook nor the report states one. WRF and its
  ERA5 forcing run in UTC and WRF writes instantaneous values, so the
  timestamps are taken as UTC and the values as instantaneous, like the
  ERA5-based onshore wind profiles: model hour `L` (IST) takes the value at
  `L − 5 h` UTC, the middle of the model hour.
- **Weather year.** The 2020 weather year is mapped onto FY2025-26 by
  calendar date and hour. 1–6 January come from the 2021 rows; 7 January, not
  in the source, repeats 8 January 2020; 29 February 2020 is not used.
- **Scaling.** Every MW of a site uses this farm's per-MW profile, so a larger
  or smaller capacity is assumed to have the same wake losses.

`config/technologies.yaml` allows offshore wind only in Tirunelveli, the
southernmost balancing area.

### Offshore wind cost

`capital_cost` in `config/technologies.yaml` is the annualised cost per MW
per year, including fixed O&M and the full grid connection. Offshore wind
capacity is a fixed scenario choice, so this cost does not change any model
decision; it is kept for reporting and for comparing scenarios' total costs. It comes from the
Danish Energy Agency's *Cost estimates for the first offshore wind farms in
India* (FIMOI v2, 2022), which is based on stakeholder and investor
consultation, for Tamil Nadu zone B, 15 MW turbines, a 1 GW project and an
investment decision in 2025:

| FIMOI v2 input (2021 prices) | Value |
| --- | --- |
| Capex, including offshore and onshore substations, array and export cables | 207.5 million INR/MW |
| Fixed O&M | 4.64 million INR/MW/year |
| Technical lifetime | 27 years |
| Net capacity factor (after 10 % wake, 5 % electrical and 4 % outage losses) | 53 % |
| Financing | loans at 8 %, 10 % return on equity, 5 % inflation |
| Basic LCOE (no GST or import duties) | 7.4 INR/kWh |

The annualised cost is the LCOE times the energy one MW delivers in a year,
in 2025 prices: 7.4 INR/kWh × 1.05⁴ (FIMOI's 5 % inflation, 2021 to 2025) ×
0.53 × 8,760 h = **41.76 million INR per MW per year**, or 9.0 INR/kWh. This
keeps FIMOI's financing, construction-period and O&M assumptions. Annualising
the capex alone at an 8–10 % discount rate gives only about 5.3 INR/kWh,
because it leaves those out. The same revenue-requirement basis is used for
batteries (tender tariffs). It is consistent with later Indian evidence:

- Developers estimated 9.7–9.8 INR/kWh for the first projects (45.0–45.5
  million INR/MW/year on the same basis).
- Indian offshore capex is quoted at 180–200 million INR/MW in 2025.
- The 2025 seabed-lease tender for 4 GW off Tamil Nadu drew no bids; MNRE
  plans to retender with viability-gap funding (up to 81 million INR/MW in
  the Gujarat tranche).

The model's block 2 profile has a capacity factor of 0.56, close to FIMOI's
0.53, so the cost per MWh the model sees is 8.5 INR/kWh. GST and import
duties (5 % and 13 % in FIMOI) are not included.

Sources:

- [Danish Energy Agency, FIMOI v2: Cost estimates for the first offshore wind farms in India (2022)](https://ens.dk/media/5021/download)
- [New Avenues: Cost economics, challenges and opportunities in the offshore wind segment](https://powerline.net.in/2025/09/15/new-avenues-cost-economics-challenges-and-opportunities-in-the-offshore-wind-segment/) (Powerline, September 2025)
- [Is viability gap funding enough to power India's offshore wind dreams?](https://www.downtoearth.org.in/renewable-energy/is-viability-gap-funding-enough-to-power-indias-offshore-wind-dreams) (Down To Earth)
- [India reconsiders 1 GW offshore wind tender after earlier auctions fail to attract bidders](https://www.downtoearth.org.in/energy/india-reconsiders-1-gw-offshore-wind-tender-after-earlier-auctions-fail-to-attract-bidders-official-tells-dte) (Down To Earth)
- [India's first offshore wind tenders fall flat and are canceled](https://maritime-executive.com/article/india-s-first-offshore-wind-tenders-fall-flat-and-are-canceled) (Maritime Executive)

### Battery storage

Batteries are a scenario technology; the base model has none (apart from the
pumped-storage hydro unit). The parameters in `config/technologies.yaml`
describe the battery Tamil Nadu is procuring: a standalone, grid-connected,
2-hour lithium-ion system.

| Parameter | Value | Basis |
| --- | --- | --- |
| Technology | Lithium-ion (LFP cells), standalone, 2 hours | TNGECL's 500 MW / 1,000 MWh standalone BESS tender (six projects, 2 hours, state viability-gap funding; awarded to NLC India Renewables, Bondada Engineering and Oriana Power) and NLC India's 250 MW / 500 MWh Tamil Nadu project, which requires LFP or NMC cells |
| `max_hours` | 2 | Duration of those projects; TNGECL's later 375 MW / 1,500 MWh tender (November 2025) is 4-hour |
| `efficiency_store`, `efficiency_dispatch` | 0.922 each | 85 % AC-to-AC round-trip efficiency (monthly), the requirement of Indian standalone BESS tenders (SECI, GUVNL, SJVN), split equally: √0.85 |
| `standing_loss`, `marginal_cost` | 0 | Self-discharge and variable cost are small for lithium-ion over hours |
| `capital_cost` | INR 2,760,000 per MW per year | INR 2.3 lakh per MW per month, the IEEFA-JMK indicative viable benchmark for 2-hour standalone tariffs; 2025–26 tenders cleared at INR 1.78–2.40 lakh (RVUNL 1.775, GUVNL 1.85–1.89 and 2.10, SJVN 1.97). A capacity tariff pays the developer's capital, O&M and return, so it is the annualised cost per MW of a 2-hour system |

The capital cost is per MW of a 2-hour battery. For a 4-hour battery change
`max_hours` and `capital_cost` together.

Sources:

- [TNGECL floats tender for 1 GWh BESS with VGF](https://renewablewatch.in/2025/03/18/tngecl-floats-tender-for-1-gwh-bess-with-vgf/) (Renewable Watch, March 2025)
- [TNGECL awards 1000 MWh BESS tender to NLC, Bondada, Oriana](https://www.saurenergy.com/solar-energy-news/tngecl-awards-1000-mwh-bess-tender-to-nlc-bondada-oriana) (Saur Energy)
- [NLC India invites EPC bids for 250 MW/500 MWh BESS in Tamil Nadu](https://www.saurenergy.com/solar-energy-news/nlc-india-invites-epc-bids-for-250-mw500-mwh-bess-project-in-tamil-nadu-with-2-hour-storage-10972962) (Saur Energy)
- [TNGECL issues tender to develop 1.5 GWh standalone BESS](https://renewablewatch.in/2025/11/20/tngecl-issues-tender-to-develop-1-5-gwh-standalone-bess-in-tamil-nadu/) (Renewable Watch, November 2025)
- [India awards 10.4 GW of standalone BESS capacity in 2025, but tariff viability remains a concern: IEEFA-JMK report](https://www.pv-magazine-india.com/?p=15030) (pv magazine India)
- [Rajasthan's 1 GW/2 GWh battery storage tender sets new tariff benchmark](https://www.pv-magazine-india.com/?p=13143), [GUVNL's 2 GW/4 GWh tender](https://www.pv-magazine-india.com/?p=13553), [GUVNL's 335 MW/670 MWh tender](https://www.pv-magazine-india.com/?p=14197), [SJVN's 250 MW/500 MWh tender](https://www.pv-magazine-india.com/2026/02/16/sjvn-ltds-250-mw-500-mwh-bess-tender-yields-a-tariff-of-inr-1-97-lakh-mw-month) (pv magazine India)
- [GUVNL 450 MW/900 MWh standalone BESS auction: 85 % monthly AC-to-AC efficiency](https://www.energetica-india.net/news/nlc-india-kp-group-win-guvnl-450-mw-900-mwh-standalone-bess-auction), [SECI clarification: minimum AC-to-AC RTE 85 %](https://www.seci.co.in/Upload/Tender/SECI000196-7902882-Clarification-01ESS327012025Final.pdf)

## Technologies, capacity, and location

`data/source/TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx`
is the operational plant inventory. `PlantInfo` has one record per plant unit
or aggregate, including `Source` (technology), `Commissioning Group`, plant
name, cleaned district, and `Capacity (MW)`. Only `operational` records enter
the build template. `District Allocation` may have several rows for one source
record; it gives `Original Row`, `Allocated District`, allocation share and MW,
method, confidence, and evidence URL. The builder reconciles allocated MW to
each operational source record, maps districts to areas, and combines pieces
within an area. Other sheets document allocation checks and evidence.

`scripts/build_unit_level_model.py` can regenerate the intermediate
`data/build_template/generators.csv`, `storage_units.csv`,
`generators-p_max_pu.csv`, and `component_metadata.csv` from that workbook and
the technology profile template. The checked-in template is the immediate
input to `scripts/build_9ba_model.py`. Its generator table includes `name`,
`bus`, `p_nom` (MW), `carrier`, dispatch limits, marginal cost, commitment
flag, start/shut-down costs, minimum up/down times, and ramp limits. These
operating and cost values are model assumptions encoded in
`scripts/build_unit_level_model.py`, not fields measured in the capacity
workbook. The `Source` categories map to `coal`, `oil_gas`, `nuclear`,
`bio_power`, `hydro`, `small_hydro`, `solar`, and `wind`; pumped hydro and
special hydro representations are handled separately. Coal costs and coal
flexibility are the exception and are set from documented sources (next
sections).

`data/model_inputs/additional_capacity_2026_07.csv` adds a July 2026
capacity vintage. Each row includes `record_id`, technology/carrier, plant,
district, `capacity_mw`, `unit_count`, `include_in_model`, allocation method,
confidence, and source reference. The builder includes 211.70 MW diesel,
212.33 MW solar, and 139.9595 MW wind. A 49.31 MW reporting difference is
retained for reconciliation but excluded from the network. Thus installed
capacity uses a July 2026 comparison vintage while demand and renewable
availability use FY2025-26. Inspect `model/district_capacity_allocation.csv`,
`model/component_metadata.csv`, and `model/capacity_by_balancing_area.csv`
for the resulting allocations and provenance.

### Coal operating costs

`data/model_inputs/coal_operating_costs.csv` gives every coal component,
including the fixed REA export reservations, a station energy charge and,
for committable units, the physical unit rating. Each row matches components
by a regular expression (`component_regex`); `scripts/build_9ba_model.py`
fails if a coal component matches no row or more than one. The build writes
the applied values per component to `model/coal_operating_cost_audit.csv`.
Only sources dated 2024 or later are used.

**Marginal cost.** The coal `marginal_cost` is the station's energy charge
(fuel and secondary oil, no fixed charge), in INR/MWh:

| Station | INR/MWh | Basis | Source |
|---|---:|---|---|
| NLC New Neyveli TPS (NNTPS) | 2,720 | FY2025-26 approved | JERC |
| NLC TPS-I Expansion | 2,920 | FY2025-26 approved | JERC |
| NLC TPS-II Expansion | 3,240 | FY2025-26 approved | JERC |
| NLC TPS-II Stage II / Stage I | 3,300 / 3,320 | FY2025-26 approved | JERC |
| North Chennai TPS-I | 3,590 | FY2023-24 actual | TNERC |
| Tuticorin TPS | 3,800 | FY2023-24 actual | TNERC |
| NTECL Vallur | 4,090 | FY2025-26 approved | JERC |
| North Chennai TPS-II | 4,110 | FY2023-24 actual | TNERC |
| North Chennai TPS-III | 4,110 | proxy: Stage II | TNERC |
| SEPC Tuticorin | 4,280 | FY2023-24 actual | TNERC |
| ITPCL, Coastal Energen, Tuticorin private TPP, OPG, TCP, Tulsyan | 4,280 | proxy: SEPC | TNERC |
| NTPL Tuticorin | 4,340 | FY2025-26 approved | JERC |
| TAQA Neyveli (ST-CMS) | 4,590 | FY2023-24 actual | TNERC |
| Mettur TPS-I | 5,400 | FY2023-24 actual | TNERC |
| Mettur TPS-II | 6,190 | FY2023-24 actual | TNERC |

- TNERC: *Approval of True Up of TNPDCL (formerly TANGEDCO) for FY 2023-24*,
  order of 30 April 2025 (M.P. No. 18 of 2025). State stations from Table
  2-42 (energy charge approved after true-up); SEPC and TAQA from the
  power-purchase cost table (variable cost per unit).
  https://www.tnerc.tn.gov.in/Orders/files/TO-MP%20No%201300420251931.pdf
  (archived copy:
  http://web.archive.org/web/20250505210653/http://www.tnerc.tn.gov.in/Orders/files/TO-MP%20No%201300420251931.pdf)
- JERC (Goa and UTs): Electricity Department of Puducherry tariff order of
  24 September 2025 (Petition 139/2025), Table 5-14, "Variable rate
  (FY 2025-26)". Puducherry buys from the same central stations as Tamil
  Nadu. https://electricity.py.gov.in/sites/default/files/Tariff%20Order%202025-25-to-FY-2029-30.pdf

The capacity-weighted mean over committable units is about INR 4,146/MWh
(the previous placeholder was a flat INR 3,087.5/MWh). Limitations:

- State and private stations use FY2023-24 actuals; the only FY2025-26
  projection for them is TNERC's 2022 tariff order, which is older than the
  2024 cut-off.
- No 2024+ energy charge was found for North Chennai TPS-III, ITPCL,
  Coastal Energen, OPG, TCP, Tulsyan or the Tuticorin private TPP. These use
  the proxies in the table (25% of committable coal capacity is on the SEPC
  proxy, 7% on the Stage II proxy).
- TNERC's FY2023-24 power-purchase costs for the NLC lignite stations are
  INR 0.6–1.3/kWh higher than JERC's FY2024-25 figures, probably because they
  include arrears; the model uses JERC.

**Flexibility (minimum load, up/down times, ramps).** Committable coal units
use the values of CEA's *Indian Technology Catalogue – Generation and Storage
of Electricity* (January 2022), data sheets 01a (subcritical, 210–600 MW) and
01b (supercritical, 660–800 MW), which give the same values for both:

| Parameter | Model | Catalogue (2020) | Other evidence |
|---|---|---|---|
| Minimum stable load | 55% of rating | 55% (range 50–75%); the normative central-station technical minimum of the grid code | Central-sector stations back down to about 55% of declared capacity in solar hours; NTPC considers operation below 55% not feasible today (CEA wear-and-tear report, Aug 2026). CEA's Flexible Operation Regulations 2023 set a 40% target under a phasing plan |
| Ramp up / down | 0.6 of rating per hour | 1% of full load per minute | The 2023 regulations require 3%/min at 70–100% load and 2%/min at 55–70% |
| Minimum up time | 6 h | 6 h | — |
| Minimum down time | 4 h | 4 h | Tuticorin 5×210 MW and Mettur 4×210 MW two-shift over the solar hours: units stop between 07:20 and 11:45 and restart between 15:31 and 19:14 (CEA, Aug 2026) |
| Start-up ramp | 0.55 (minimum load in the first hour) | Hot / warm / cold start 2–3 / 3–5 / 5–10 h | The model reaches minimum load faster than a real start |

https://cdnbbsr.s3waas.gov.in/s3716e1b8c6cd17b771da77391355749f3/uploads/2023/10/202310121323054265.pdf
(pp. 38–41). Until November 2026 the model used 8-hour minimum up and down
times without a cited source. An 8-hour stop (for example 10:00–18:00) still
covers the solar trough, but it ruled out the shorter stops seen at
Tuticorin and Mettur, which last from about 4 to 12 hours.

Sensitivity (relaxed unit commitment, `selected_weeks` mode with the weeks of
7 Apr, 7 Jul, 13 Oct 2025 and 16 Feb 2026, outages on, November 2026):
moving from 8 h / 8 h to 6 h / 4 h changed the mean Tamil Nadu price by at
most 0.5% in any week (the April week was identical), weekly coal energy by
less than 0.1%, and coal start-ups by less than 1 per week. With continuous
(relaxed) commitment, a unit can be partly committed, which largely bypasses
the minimum up/down constraints. The same weeks with full (binary) unit
commitment (each week solved at the root node within the 1% gap, about
1 minute per week) changed weekly coal energy by at most 0.2% and variable
cost by at most 0.3%. Start-ups changed by −5 to +4 per week and midday
coal output by −2% to +1%, with no consistent direction. Both settings
already let units stop over the midday trough (24 unit-days in the February
week with 8 h, 21 with 4 h). The minimum up/down times therefore do not
explain the gap between the model's flat midday price and the exchange
price, which comes from the fixed interstate schedule.

**Start-up and shut-down costs.** The anchor is the Central Electricity
Authority's *Report of the Committee on Wear & Tear, O&M and Plant Lifespan
Implications due to flexible operation* (August 2026). CEA and BHEL analysed
two-shift operation of the 5×210 MW Tuticorin TPS and found shutdown and
start-up costs of 29.42 paise/kWh and increased O&M of 16.68 paise/kWh,
charged on a two-shift day of 16 generating hours that contains one
stop–start cycle. That is INR 7,376 per MW of unit capacity per cycle
(`COAL_CYCLE_COST_PER_MW` in `scripts/build_unit_level_model.py`); retrofit
capital cost is a fixed cost and is excluded.
https://cea.nic.in/whats-new/file?path=/home/cea_website/reports/general/en/18082026_Wear_and_Tear_Report_Final_ry_merged___with_Appendix_2.pdf&mode=view

Larger units need less start-up oil per MW, so the per-MW cost is scaled by
unit size with CEA's *Recommendations on Operational norms of secondary fuel
oil consumption* to CERC (13 January 2025), which give oil per hot start of
20, 30, 45 and 60 kl for the 210 MW, 500 MW, 660 MW and 800 MW series.
https://cercind.gov.in/2024/draft_reg/Recommendations-of-CEA-130125.pdf
Each unit is assigned a series from its physical rating (below 300 MW: 210;
300–650 MW: 500; 650–750 MW: 660; 750 MW and above: 800) and its cost per MW
is INR 7,376 times the series' oil per start per MW divided by the 210 MW
series' value:

| Series | Units in the model | Cycle cost (INR/MW) |
|---|---|---:|
| 210 MW | all units below 300 MW, including 250 MW and the small captive units | 7,376 |
| 500 MW | 500, 525 and 600 MW units | 4,647 |
| 800 MW | North Chennai TPS-III | 5,809 |

The physical rating is used, not the Tamil Nadu share in `p_nom`: for
stations shared with other states it is the Tamil Nadu share plus the REA
export reservation in this model (for example Vallur, 656 + 844 MW = 3×500 MW).
Each committable unit's start-up cost and shut-down cost are each half of
`p_nom` × cycle cost, because equal start-up and shut-down costs activate
PyPSA's tightening constraints for linearized unit commitment. Limitations:

- The anchor is one plant (old state-owned 210 MW units) in hot two-shift
  operation (units offline roughly 4–12 hours over the solar period). Warm or
  cold starts use more oil (CEA 2025: 40 and 60 kl for a 210 MW unit,
  against 20 kl hot), so restarts after long outages are likely
  under-costed. The catalogue's start-up costs (INR 25,000 / 30,000 /
  52,000 per MW for hot / warm / cold starts, from CEA's 2019 flexibility
  study; labelled "cr./MW" in the sheet) are three to seven times higher than
  this cycle cost.
- Lignite units (about 19% of committable coal capacity) and imported-coal
  units are scaled like domestic-coal units; CEA's 2025 analysis reports
  higher oil use per start at NLC.
- CEA does not break the Tuticorin figure into oil, coal, auxiliary power and
  maintenance.

Sensitivity (relaxed unit commitment, rolling week 1, November 2026): halving
or doubling all start-up and shut-down costs changed weekly coal energy by
less than 0.1% and the mean Tamil Nadu price by about 1%, but changed
cycling strongly. Fractional coal start-ups after the first hour were 26.7,
10.4 and 0.8 at 0.5×, 1× and 2×, almost all at Mettur TPS-I, the most
expensive coal station. The cycling cost level therefore matters for how
often the expensive stations two-shift, not for energy or prices.

**Initial commitment state.** Committable coal units start the horizon
online: `up_time_before` = 168 h, `down_time_before` = 0 and `p_init` empty,
so there is no ramp reference for the first hour and the optimizer chooses
the opening dispatch (and pays the shut-down cost for any unit it turns off).
The build template instead marks every thermal unit offline for its minimum
down time, which made the whole coal fleet cold-start at 00:00 on 1 April.
Observed Tamil Nadu coal generation on 1 April 2025 was 196.6 MU, about
8.2 GW on average (`data/source/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx`),
so the fleet was running; unit-level status is not available, so all
committable coal units start online (coal is the only committable carrier;
see `restrict_unit_commitment` in `scripts/build_9ba_model.py`). This applies to every
run mode; in rolling runs, later windows take their state from the previous
window as before. In the relaxed rolling week-1 test this removed 49 forced
start-ups and all 1,134 MWh of unserved energy (caused by start-up ramp limits
in the opening hours), and the week's peak Tamil Nadu price fell from the
INR 100,000/MWh unserved-energy cost to INR 6,908/MWh.

### Other marginal costs

`data/model_inputs/plant_operating_costs.csv` gives official per-station
energy charges for oil/gas, nuclear and bio-power (and the diesel
placeholder). Rows are matched to components of their carrier by
`component_regex` in file order, the first match wins, and every component
must match; `scripts/build_9ba_model.py` (`apply_plant_operating_costs`)
writes the applied values to `model/plant_operating_cost_audit.csv`. The
values are not fitted to observed generation; a mismatch between released runs
and observations is a finding about missing constraints (for example must-run
or contract obligations), not a reason to change the costs.

| Station(s) | INR/MWh | Basis | Source |
|---|---:|---|---|
| Valuthur GTPS | 5,290 | FY2023-24 approved after true-up | TNERC true-up, Table 2-42 |
| Kuttalam GTPS | 5,470 | FY2023-24 approved after true-up | TNERC true-up, Table 2-42 |
| Thirumakottai GTPS | 6,570 | FY2023-24 approved after true-up (normative heat rate; TNPDCL filed 14,440) | TNERC true-up, Table 2-42 |
| Basin Bridge GTPS (naphtha) | 13,570 | FY2019-20 approved (pre-2024: no generation in FY2023-24) | TNERC Order 07/2022, Table 3-47 |
| PPN (Pillaiperumalnallur) | 6,410 | FY2016-17 approved (pre-2024: no purchases in FY2023-24) | TNERC Order 07/2022, Table 3-49 |
| Kudankulam (incl. REA export reservations) | 4,690 | FY2025-26 single-part tariff | JERC 24 Sep 2025, Table 5-14 |
| MAPS (incl. REA export reservation) | 2,730 | FY2025-26 single-part tariff | JERC 24 Sep 2025, Table 5-14 |
| Bagasse co-generation (sugar mills, 36 components, 729 MW) | 4,380 | FY2025-26 normative variable cost, Tamil Nadu | CERC 06/SM/2025, p.29 |
| Other bio-power (biomass, non-bagasse co-generation, waste-to-energy, biogas; 45 components, 299 MW) | 6,100 | FY2025-26 normative variable cost, biomass Rankine (water-cooled, travelling grate), Tamil Nadu | CERC 06/SM/2025, pp.27–28 |
| Diesel (Samayanallur, Samalpatti) | 6,025 | placeholder | none found |

- Nuclear has only single-part tariffs (no separate fuel or energy
  charge). Its cost therefore matters for total system cost and comparisons
  only: in released runs nuclear is must-run (`energy_targets.nuclear:
  must_run`) and produces exactly its available energy.
- Bagasse co-generation is identified by name (`sugar`, `eid_parry`, and the
  aggregate "biomass/bagasse based co-gen" components); `synergy_sakthi_renewable_energy`
  is treated as biomass. The TNERC FY2023-24 true-up records biomass purchases
  at 5,200 (2.4 MU only) and co-generation only as all-in costs.
- Diesel: TNERC's last recorded purchase is Samayanallur at 4,100 in
  FY2016-17; TNPDCL bought nothing from either plant in FY2023-24, and the
  secondary-oil prices of INR 75,600–77,600/kL in CERC's 2026 orders
  647/GT/2025 and 111/GT/2026 imply more than INR 6,025/MWh at any realistic
  engine heat rate. The placeholder is kept.
- Gas plants that did not run in FY2025-26 (Basin Bridge, PPN, Thirumakottai)
  get no availability with plant-level availability profiles, so their older
  charges matter only in `availability_source: carrier` or `free` runs.

Sources:
- TNERC, *Approval of True Up of TNPDCL (formerly TANGEDCO) for FY 2023-24*,
  30 April 2025 (M.P. No. 18 of 2025), see the coal section above.
- TNERC, Order No. 07 of 2022 (tariff order, 9 September 2022).
  https://jmkresearch.com/wp-content/uploads/2022/09/tnerc-tariff-order-FY-2023.pdf
- JERC, Electricity Department of Puducherry tariff order of 24 September
  2025 (Petition 139/2025), see the coal section above.
- CERC, *Determination of generic levellised generation tariff for RE
  projects for FY2025-26*, 9 July 2025 (Petition 06/SM/2025).
  https://www.cercind.gov.in/2025/orders/6-SM-2025.pdf
- https://www.cercind.gov.in/2026/Orders/647-GT-2025.pdf,
  https://www.cercind.gov.in/2026/Orders/111-GT-2026.pdf

### Coal unit availability: auxiliary consumption, maintenance and outages

The `outages` section of a run config (off by default) sets the hourly
`p_max_pu` of the 49 committable coal units to

    base availability × capability × (1 − share of the hour the unit is out)

or, for the seven central stations, to their declared capacity
(`scripts/generator_outages.py`). Without it, every coal unit has a static
`p_max_pu` of 0.85: the "85% availability" of CEA's *Report on Resource
Adequacy Plan (Generation) for Tamil Nadu* (April 2026), which equals CERC's
normative annual plant availability factor. That factor is defined on
declared capacity net of auxiliary consumption and already includes planned,
forced and partial outages (CERC Tariff Regulations 2024, Reg. 3(59)), so it
must not be combined with explicit outages.

**Base availability.** `base_availability: net_of_auxiliary` (the default)
uses 1 − auxiliary consumption, so the model dispatches net (ex-bus) coal
output. Tamil Nadu demand is on that basis: CEA's *Load Generation Balance
Report 2025-26* (§4.2.4, §5.2.2) compares each state's energy requirement and
peak demand with net availability, i.e. gross generation minus normative
auxiliary consumption. The FY2025-26 energy balance agrees: demand (131.38 TWh)
plus exports (23.51) minus imports (27.77) needs 127.12 TWh from in-state
plants, 5.09 TWh less than their observed generation (132.21 TWh), about the
auxiliary consumption of the coal and nuclear fleet. The observed generation
is gross: CEA's daily generation report, the source of the NITI series, states
"Actual generation is gross generation". `gross` uses 1.0 and `model` keeps
the network's 0.85.

Auxiliary consumption (% of gross generation) per unit is in
`data/model_inputs/coal_auxiliary_consumption.csv`
(`scripts/prepare_coal_outages.py`), capacity-weighted 7.9%:

| Basis | Units | Values |
|---|---:|---|
| CEA actual 2017-18 (NITI export, `data/source/NITI dashboard/auxiliary consumption Tamil Nadu`) | 36 | Vallur 6.46, Mettur-II 6.79, Muthiara 7.07, ITPCL 7.51, NLC TPS-I Exp 7.70, NCTPS-I 7.97, Tuticorin TPS 8.28, NTPL 8.59, Mettur-I 8.64, NLC TPS-II 9.25, TAQA 9.78, Tuticorin (P) 12.36, NLC TPS-II Exp 12.61 |
| TNERC approved FY2025-26 (Order 07 of 2022, Table 4-41) | 3 | NCTPS-II and III 7.00 |
| CERC 2024 norm, Reg. 70(E) | 3 | NNTPS 6.75 (500 MW with steam-driven feed pumps 5.25 + 1.5 for lignite CFBC), SEPC 5.25 |
| Assumption | 7 | OPG, TCP, Tulsyan 10.0 (no CERC norm below 200 MW) |

The feed-pump drive of NNTPS and SEPC is assumed; with electric pumps the
norms would be 9.5% and 8.0%. CEA's 2017-18 value for NCTPS-I (7.97%) is below
TNERC's approved 8.5% and its 2016-21 actuals (9.05–10.40%).

**Capability (partial deratings).** The outage record lists only whole units
out; a running unit that cannot reach full load (mill problems, wet or poor
coal, condenser vacuum, tube leaks) leaves no trace in it. With
`partial_derating: demonstrated` (the default) each unit is capped at its best
whole day of FY2025-26 on days without any recorded outage, as a share of its
rating (daily gross energy from
`data/model_inputs/niti_dashboard_tamil_nadu_generation_FY2025_26.csv` ÷
(rating × 24 h)); `data/model_inputs/coal_unit_capability_FY2025_26.csv`
lists it with the median and 95th-percentile day. The cap applies to 33 of
the 49 units and removes 1,308 MW of their 15,032 MW rating. The lowest are
Neyveli TPS-II Expansion unit 2 (0.745), North Chennai units 6 (0.754, from
40 days after commissioning), 4 (0.760), 2 (0.810) and 5 (0.828), and Mettur
unit 5 (0.847). Units with fewer than 20 days without outage (those out all
year, Tulsyan) and units whose best day reaches the rating are not capped.
Every capped unit can still run at its 55% minimum stable output. A daily
average is a lower bound on what a unit can do in an hour, because units are
backed down at night and in solar hours, so the cap errs on the low side and
partly reflects dispatch rather than derating. SRPC's 15-minute declared
capacity would separate the two for the central stations. `none` leaves units
at their full rating.

**Outage record.** `data/model_inputs/coal_unit_outages_FY2025_26.csv` holds one
row per unit and day out in FY2025-26, with the share of the day, from the NITI
Dashboard's unit outage exports (`data/source/NITI dashboard/unit outages Tamil
Nadu`), which compile CEA's daily outage reports. Units are matched by plant name
and unit number; all 47 reported coal units match a model unit with the same
capacity (Tulsyan NEC is not reported). Kinds and FY2025-26 totals:

| Kind | Reasons | Unit-days | Imposed |
|---|---|---:|---|
| `planned` | annual, boiler, capital and other planned maintenance | 1,244 | `planned: actual` |
| `forced` | technical, raw water, grid, fuel shortage | 2,767 | `forced: actual` |
| `commercial` | no fuel supply agreement, no PPA | 728 | always |
| `commissioning` | days before the commissioning date (NCTPS-III 24 Jan 2026, Tulsyan 1 Mar 2026) | 966 | always |
| `reserve_shutdown` | reserve shutdown, low schedule, no demand | 845 | never (unit commitment decides) |

Units out on at least 99% of the days are out of service for the whole run:
Tuticorin TPS units 1–2 (cable-gallery fire on 15 March 2025), Tuticorin (P)
TPP units 1–2 (no fuel supply agreement since 2016) and TCP (planned
maintenance since 2021), 783.5 MW. A day's share is placed in hours at the
start of the day when the unit was out the day before, at the end when it is
out the day after, otherwise in the middle. Units out in the first hour of the
year start offline instead of being shut down in it.

**Planned maintenance.** `planned: actual` replays the record.
`planned: optimise` uses PyPSA's maintenance scheduling (generators with
`maintainable=True`, `maintenance_duration`, `maintenance_events`): each unit
takes its FY2025-26 maintenance duration (whole days, one event) at dates chosen
by a MILP. A maintenance event must fit inside one optimisation horizon, so the
schedule is solved once over the whole year at daily resolution without unit
commitment, with the units' commissioning, commercial and out-of-service
unavailability, and is then fixed as hourly availability in every run mode.
Among dates of similar cost, a small cost (INR 100 per MW out per hour at the
year's highest daily residual load, scaled by the day's residual load) prefers
days of low demand net of wind and solar. On `model/` it schedules 891
unit-days for 26 units in about 20 s. Average capacity on maintenance by month
(MW, Tamil Nadu share):

| Month | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Jan | Feb | Mar |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Actual FY2025-26 | 204 | 98 | 605 | 1,210 | 1,133 | 1,043 | 823 | 679 | 1,706 | 1,141 | 131 | 64 |
| Optimised | 0 | 191 | 363 | 0 | 0 | 0 | 0 | 492 | 1,980 | 3,248 | 1,182 | 772 |

The optimiser moves maintenance to December–March, the lowest-demand months,
whereas the utilities took most of it during the south-west monsoon (June to
October). `scripts/schedule_maintenance.py` writes a schedule for runs to share
(`outages.maintenance_schedule`); the Snakemake workflow does this once per
scenario on the base network without the scenario's technologies, so scenarios
with the same outage settings get the same dates.

**Forced outages.** `forced: actual` replays the record. `forced: sampled`
draws outages at random with a fixed `seed`: each unit alternates between
available spells, exponentially distributed with its size class's mean time
between outages, and outages whose durations are resampled from the class's
observed outages. Each unit has its own random stream, so scenarios with the
same seed get the same outages. Parameters from the FY2025-26 record (exposure
excludes planned, commercial and pre-commissioning time; out-of-service units
excluded):

| Unit size | Units | Forced-outage rate | Outages per unit-year | Mean duration (h) | Median duration (h) |
|---|---:|---:|---:|---:|---:|
| below 200 MW | 8 | 3.3% | 2.9 | 101 | 47 |
| 200–400 MW | 22 | 18.4% | 12.6 | 128 | 40 |
| 400 MW and above | 16 | 11.7% | 10.5 | 97 | 46 |

Outages per unit-year are per year of exposure. CEA's last station review
(*Review of Performance of Thermal Power Stations 2017-18*) counted 6,026
forced outages on 626 units, 9.6 per unit and year, including reserve
shutdowns. NERC GADS (Generating Unit Statistical Brochure 3, 2021–2025)
reports weighted forced-outage rates of 6.4–15.0% by size class for US coal
units, 8.0% overall.

**Central stations: declared capacity.** For the seven central coal
stations, `central_availability: declared` (the default) uses the capacity
they declared to the Southern Regional Load Dispatch Centre for every
15-minute block, from the SRPC Regional Energy Accounts
(`commercial_on_off_dc_isgs.csv` in each month's archive). Declared capacity
is ex-bus and covers whole-unit outages, partial deratings and fuel; CERC pays
the stations' capacity charge on it. On-bar (units running) and off-bar
(available units shut down) declarations are added, so reserve shutdowns
still count as available. `scripts/prepare_declared_capacity.py` reads the
twelve archives (`data/source/REA data`, not in Git) and writes the hourly
means to `data/model_inputs/central_station_declared_capacity_FY2025_26.csv`
and the station units to `central_station_units.csv`:

| Station (REA code) | Units | Rating (MW) | Mean declared (MW) | Share |
|---|---:|---:|---:|---:|
| NTECL Vallur (`vallur`) | 3 × 500 | 1,500 | 1,250 | 0.83 |
| NTPL Tuticorin (`ntpl`) | 2 × 500 | 1,000 | 851 | 0.85 |
| NLC NNTPS (`nntpp`) | 2 × 500 | 1,000 | 625 | 0.63 |
| NLC TPS-I Expansion (`nlcexp`) | 2 × 210 | 420 | 244 | 0.58 |
| NLC TPS-II Stage I (`nlc21`) | 3 × 210 | 630 | 269 | 0.43 |
| NLC TPS-II Stage II (`nlc22`) | 4 × 210 | 840 | 343 | 0.41 |
| NLC TPS-II Expansion (`nlc2exp`) | 2 × 250 | 500 | 201 | 0.40 |

The outage record follows declared capacity closely in time (hourly
correlation 0.85–0.97), but the record alone overstates the NLC stations and
NNTPS by 21–58%, while the capability cap understates Vallur and NTPL by
9–14%.

With `planned: actual` and `forced: actual` declared capacity replaces the
record and the capability cap of these 18 units hour by hour. Each hour a
station's capacity is spread evenly over the number of its identical units
that gives the most usable capacity, each at or below its rating and at or
above its minimum load, preferring fewer units (942 MW at Vallur is two units
at 471 MW); units the record shows available are used first. This keeps 98.7%
of the declared capacity; the rest is hours when a station declared less than
one unit's minimum load. In the other modes each station gets one factor in
place of the capability cap, its declared capacity over the capacity the
record leaves available (Vallur 1.00, NTPL 1.03, NNTPS 0.83, NLC TPS-I
Expansion 0.78, TPS-II Expansion 0.77, TPS-II Stage I 0.65, Stage II 0.63), and
maintenance and forced outages come from those modes. `central_availability:
record` treats the stations like the other units.

**Resolution.** `resolution: hourly` (the default) keeps the hourly
availability. `monthly` and `annual` replace each unit's availability by its
mean over the month or the year, for a less restricted run without outage
timing. A unit available on average a share of its best hour then acts as a
smaller unit: its minimum load (`p_min_pu`) is scaled by the same share, so it
can still run. Means are taken over the hours after commissioning, and the
commissioning dates stay exact. Means are preserved, so the available energy
is the same as hourly.

Limitations:

- Partial deratings are measured for the central stations (declared
  capacity) and approximated for the other units by each unit's best day
  without outage, constant over the year; the record itself has only
  whole-unit outages.
- Outages are known in advance within each solve (perfect foresight), as for
  all other inputs.
- Auxiliary consumption is normative or from 2017-18, and constant over the
  year; part-load running raises it in reality.
- Model coal dispatch is net, observed generation gross. With
  `net_of_auxiliary`, every coal generator carries an `auxiliary_consumption`
  share (the REA export reservations take their station's capacity-weighted
  value, from `model/coal_operating_cost_audit.csv`), and the comparisons with
  observed generation add the auxiliary consumption back: the runner's daily
  table has a `coal_own_use` column (GWh) that the runner's and the report's
  comparisons add to coal, and the report's generation tables show it as
  `own_use_gwh`. All other results (dispatch, energy balance, costs) stay net.
- The REA export reservation generators keep their observed hourly schedules.
- Daily, monthly, peak and selected-week runs start each solve with the
  units online, as without outages; a unit out in a solve's first hour is shut
  down in it.

Each run with outages writes `coal_unit_availability.csv` (per unit:
availability source, auxiliary consumption, capability or station factor, base
availability, hours out by kind, mean availability), and
`forced_outage_parameters.csv` or `maintenance_schedule.csv` when used.

## Hydro: assets, water, and storage

`data/model_inputs/hydro_asset_registry.csv` classifies inventory units by
`asset_id`, `match_regex`, representation, data status, `max_hours`, MW source,
and notes. `data/model_inputs/hydro_fleet_reconciliation.csv` records
differences between official plant MW and reference or grouped hydro figures;
it prevents double counting. Electrical turbine capacities come from the
official inventory, not from reservoir volume. The builder uses simple
non-pumping `StorageUnit`s for some plants, run-of-river generators for
others, and water `Store`s plus turbine, pump, or transfer `Link`s for
cascades such as PAP, Kodayar, Kundah, Pykara/Moyar, and
Papanasam/Servalar. Each water `Store` is measured in the electricity its own
plant would generate from the stored volume, so every turbine `Link` has
efficiency 1.0 and its `p_nom` equals the official electrical MW. Kadamparai
has separate pump and generation links. Its upper Store holds an assumed six
hours of full output (2,400 MWh). The pump takes up to 400 MW of electricity
on `bus0`, stores 0.81 MWh of later generation per MWh pumped (0.90 pump ×
0.90 turbine), and withdraws the same water volume from Upper Aliyar, so
pumping loses electricity but not water. Kadamparai's dam is on the Kadamparai
river, so it also has natural inflow, which is not observed. Its estimate
(`kadamparai_calibration_inflow`) is CEA's FY2025-26 Kadamparai generation
(258 GWh) minus the generation the model itself produces from pumped water
(`KADAMPARAI_MODELED_PUMPED_GENERATION_GWH` in the build script, calibrated on
a full-year rolling run without unit commitment), spread over the year in
proportion to CEA's monthly Kadamparai generation. Modeled annual Kadamparai
generation therefore approximates the CEA total. Recalibrate the constant if
pumping behaviour changes, for example with unit commitment enabled.

`data/reservoirs/tn_reservoir_daily_FY2025_26.csv` is the model-ready daily
observation table: one `date`/`reservoir` row with storage and full capacity
in M.cft, inflow and outflow in cusecs, converted daily volumes, calibrated
live storage fractions where available, source URL, and validation/quality
fields. It covers 365 dates and 19 named reservoirs. It is produced by
`scripts/scrape_tn_reservoirs.py` from Tamil Nadu Agriculture daily pages.
That script checks printed page dates and repeated snapshots before accepting
records. The wide daily table and suspicious-snapshot audit are also in
`data/reservoirs/`; the builder reads the long model-ready table.

The builder converts observed daily inflow to hourly electricity-equivalent
inflow with documented fixed heads and efficiency (default 0.90), using
`1 cusec-day = 0.0864 M.cft` and
`MWh = 0.0771634 × volume_M.cft × head_m × efficiency`. It does not allocate
observed statewide hydro generation as inflow. Where the observation is
indirect or absent, the model uses the proxy or endogenous pondage recorded in
`model/hydro_reservoir_parameters.csv` and
`model/hydro_assumptions_report.csv`.

For auditing, `model/hydro_reservoir_inflow.csv` records daily source-derived
inflow; `model/storage_units-inflow.csv` holds hourly simple-reservoir inflow;
cascade water inflow is represented by `water_inflow` generators.
`model/hydro_observed_soc.csv` gives dated MWh and fractional storage targets
with source and quality. `model/hydro_capacity_validation.csv` checks every
official MW record against its model component. Selected-week runs use
observed storage at independent period boundaries. Rolling runs set the five
directly observed water Stores to their dated storage readings at each daily
boundary, treating a dated reading as the start-of-day stock, and admit their
reported inflow. A separate, costly water-balance
inflow/outflow reconciles days on which the storage readings and reported
inflow cannot otherwise satisfy the water balance; it is reported in
`measured_water_balance.csv`, not counted as measured natural inflow. This
prevents an inconsistent source record from silently changing storage or
creating unlabelled water. Reported reservoir outflow is not an equality
constraint in this formulation; the optimizer routes required releases through
turbines when power has system value and uses spill paths for remaining water.
The exact constraint applies to the converted `soc_mwh` input. The builder caps
source storage fractions at 100% of reported full capacity; thus a raw M.cft
reading above that capacity is not represented exactly. The current Sholayar
source has 44 such dates, which require separate source/capacity review.
Upper Kodayar has no measured storage series. Its initial Store is assumed 50%
full, after which its water balance is estimated Pechiparai daily inflow minus
Kodayar-I turbine withdrawal and explicit non-generating spill to the Kodayar
downstream sink. Spill has a small tie-breaker cost to prevent gratuitous water
dumping and is used when needed to respect the published FRL capacity; no other
upper-reservoir release path is represented.
`model/upper_kodayar_inflow_estimate.csv` records the Pechiparai daily
inflow and its scale factor. The model scales each day's Pechiparai inflow by
the published Upper Kodayar/Pechiparai full-reservoir capacity ratio,
98.66/150.22 = 0.6568. Both capacities
are stated in the Government of India Expert Appraisal Committee's
[Kodayar project minutes](https://environmentclearance.nic.in/writereaddata/Form-1A/Minutes/2103202340054026Final_MOM_43rd_EAC_River_07_03_23.pdf).
Upper Kodayar's storage capacity uses its published 98.66 MCM volume. The
Kodayar-I conversion uses TANGEDCO's [16.5 cubic feet per kWh turbine water
consumption](https://tenders.bhel.com/sites/default/files/TCCSCT2159-2024-07-29-04%3A00%3A36.pdf),
equivalent to 60.606 MWh per M.cft in the model. Kodayar-II retains a provisional
100 m stage head (6.94 MWh per M.cft), so the Kodayar-II forebay is measured in
Kodayar-II MWh: Kodayar-I discharge enters it at 0.1146 MWh per Upper Kodayar
MWh, which preserves the water volume. The Kodayar-II turbine therefore has
`p_nom` 40 MW and efficiency 1.0, and the weekly CEA Kodayar target counts
its electrical output rather than its water use. Pechiparai flow remains a scenario estimate, not an
Upper Kodayar inflow or storage measurement. The Kodayar-II forebay retains its
own balance; Kodayar-II discharge reaches a terminal downstream sink rather
than filling a small artificial pond. The terminal sink has an effectively
unbounded finite model capacity, so it cannot restrict upstream generation.
Rolling runs audit the daily upper balance
in `upper_kodayar_balance.csv`.
Other proxy storage, such as Upper Aliyar's Parambikulam profile, remains a soft
boundary guide and never becomes an exact observation.
Upper Aliyar is a dam on the Aliyar headwaters, upstream of the Aliyar
reservoir, and the lower reservoir of the Kadamparai pumped-storage plant. The
60 MW Aliyar power house uses the fall between Upper and Lower Aliyar
([PAP project description](https://parambikulam.in/?p=214)); Parambikulam
water reaches only the lower Aliyar dam, through the Contour and Aliyar Feeder
canals ([IAMWARM Aliyar sub-basin plan](https://iamwarm.gov.in/iamwarm/old/dpr-pdf/Aliyar.pdf)).
Upper Aliyar has no flow or storage observation, so its natural inflow
(`upper_aliyar_calibration_inflow`) is estimated from Aliyar's CEA monthly
generation, 174.31 GWh in FY2025-26, delivered uniformly within each rolling
week and recorded in `model/hydro_cascade_inferred_inflow.csv`. Pumping
returns the water it takes, so this is the least inflow consistent with the
observed generation; unobserved spill is not included. The inflow is
mandatory, and a spill link (`upper_aliyar_spill`, sized to the combined peak
of the Upper Aliyar and Kadamparai inflows, since Kadamparai discharges into
Upper Aliyar) releases surplus water to Lower Aliyar. Without this inflow, Upper Aliyar held
only its assumed starting water and ran dry by July, which stopped Kadamparai
from pumping for the rest of the year.
Rolling runs pass modeled state between adjacent windows. If
`model/hydro_monthly_stored_energy.csv` is present, its values are interpolated
at rolling boundaries; absent monthly data create no rolling target.

## Observed generation and network geography

`data/model_inputs/niti_dashboard_annual_generation_FY2025_26.csv` contains
the authoritative FY2025-26 annual historical generation totals supplied from
the NITI Dashboard. Standardized full-year result analysis and full-year run
summaries use these values for annual `generation_comparison` tables. Because
the NITI Dashboard totals contain no daily chronology, monthly and
partial-period comparisons continue to use the daily generation workbook
described below.

The NITI Dashboard values are reported in MU, where 1 MU = 1 GWh, so the CSV
stores the same numeric values in `generation_mu` and `generation_gwh`. Annual
comparisons retain the separate small-hydro (307.56 GWh) and bio-power (194.01
GWh) rows; `comparable_total` is the supplied and reconciled NITI total of
132,210.74 GWh.

`data/source/Capacity_Metatable_Data_Sept26.xlsx` contains the latest supplied
Tamil Nadu installed-capacity benchmark. The analysis loader selects the latest
dated Tamil Nadu row set (2026-27); the run scripts export
and display an `installed_capacity_comparison` table against that benchmark.

`data/source/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx`
has an `Electricity_Power_Generation_` sheet: `Parameter` and `State` identify
rows, and daily date columns contain generation in MU (1 MU = 1,000 MWh).
The Tamil Nadu oil-and-gas and nuclear rows supply daily equality targets. The same workbook is used for comparisons with coal, hydro, solar,
wind, and total generation; it is **observed generation**, not an hourly
availability profile. See the two `model/*_daily_energy_budget.csv` files for
the processed targets; the run scripts apply them for each solved day. Exact target
matching for oil and gas or nuclear is an imposed calibration condition.

`data/model_inputs/tn_export_capacity_allocations.csv` records four thermal
stations physically in Tamil Nadu whose full capacity is not allocated to
Tamil Nadu. It is retained for comparison, but no longer derates the modeled
fleet. Verified plant-level REA schedules supersede that four-station proxy and
also cover nuclear, additional Neyveli stages, OPG, Tulsyan, and EID Parry.
The generated reference copy is `model/tn_export_capacity_allocations.csv`.

`data/model_inputs/Balancing_areas.txt` is a Markdown-style table mapping 38
districts to the nine bus names. The authoritative grid workbook supplies 18
internal AC `Line` corridors and 24 directional interstate `Link`s. During
optimization, internal active-power losses are approximated from the imported
line resistance with two linear tangent segments. Interstate links use 97%
efficiency, following CEA's 3% interstate-import planning loss assumption, and
the FY2025-26 TNERC short-term transmission charge of Rs 241.81/MWh. Internal
line flow has a separate Rs 1/MWh numerical routing tie-breaker; it is not a
regulated tariff. Ratings and provenance are recorded in
`model/transmission_capacity_metadata.csv`. Reactive power is not modeled.
