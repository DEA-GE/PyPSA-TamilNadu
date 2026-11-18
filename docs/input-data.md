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
a fixed-dispatch generator at the plant's physical balancing-area bus. The same
nameplate is removed from the ordinary plant units, conserving physical
capacity while preventing unused reserved capacity from serving Tamil Nadu.
The existing gateway export loads are unchanged, so the reservation does not
double count interstate energy.

Included measures are fixed loads at the external buses: confirmed imports have
negative `p_set` and exports positive `p_set`. Existing directional grid links
enforce
gateway capacity and carry the exchange, but no `links-p_min_pu.csv` or
`links-p_max_pu.csv` schedule is written. This allows the network to select the
parallel link used within a gateway. The audit output records every hourly
gateway value, the original share, available capacity, any capacity-driven
redistribution, and explicit included/excluded status for distributed flows.

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

The active profiles come from the area-specific wind and solar CSVs under
`model/TamilNadu9BAs_profiles_2025_2026/2025/` and `2026/`. The 2025 files
cover the full calendar year; the 2026 files cover January through April.
The FY2025-26 series uses 2025-04-01 through 2026-03-31 and is shifted five
hours later to match the model's hourly convention. The final five FY hours
wrap to the beginning. The constructed table is
`model/TamilNadu9BAs_profiles_2025_2026/renewable_profiles_FY2025_26.csv`.
To activate the corrected FY table and rebuild the generator profiles, run:

```powershell
Copy-Item model/TamilNadu9BAs_profiles_2025_2026/renewable_profiles_FY2025_26.csv data/model_inputs/renewable_profiles.csv
python scripts/build_9ba_model.py
python scripts/construct_and_compare_re_profiles_fy2025_26.py
```

The construction script checks that the corrected series agree with the active
input and all renewable generators. The previous model input is archived at
`data/model_inputs/renewable_profiles_before_external_FY2025_26.csv`. The
comparison notebook at `notebooks/compare_external_re_profiles_FY2025_26.ipynb`
uses that archive as its baseline. The older workbook
`data/source/wind_solar_profiles_TN-9BAs-2025-26-1h.xlsx` is retained for
comparison; `scripts/prepare_renewable_profiles.py` now checks or regenerates
the archived baseline by default, leaving the active input unchanged.

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
special hydro representations are handled separately. Coal costs are the
exception and are set from documented sources (next section).

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
  operation (units offline roughly 4–12 hours over the solar period). The
  model's 8-hour minimum down time prevents the shorter stops, and warm or
  cold starts use more oil (CEA 2025: 40 and 60 kl for a 210 MW unit,
  against 20 kl hot), so restarts after long outages are likely
  under-costed.
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

The remaining thermal marginal costs are still the original placeholders in
`scripts/build_unit_level_model.py`. A check against 2024+ Indian sources:

| Carrier | Model (INR/MWh) | 2024+ reference | Assessment |
|---|---:|---|---|
| Oil and gas | 6,025 | Valuthur 5,290, Kuttalam 5,470, Thirumakottai 6,570 (TNERC FY2023-24 actual); PPAC domestic gas price ceiling USD 6.75/MMBtu for FY2025-26 | consistent |
| Diesel | 6,025 | no station charge found; secondary-oil prices of INR 75,600–77,600/kL in CERC's 2026 orders 647/GT/2025 and 111/GT/2026 imply more than INR 6,025/MWh at any realistic engine heat rate | probably low |
| Bio-power | 4,512.5 | 5,200 (TNERC FY2023-24 actual, 2.4 MU only); 6,100 for Tamil Nadu (CERC RE tariff order FY2025-26, 6/SM/2025, normative) | low |
| Nuclear | 800 | only full tariffs available (MAPS 2,730, Kudankulam 4,690, JERC FY2025-26) | not checkable; nuclear output is fixed by energy targets |

Sources: https://ppac.gov.in/natural-gas/gas-price,
https://www.cercind.gov.in/2025/orders/6-SM-2025.pdf,
https://www.cercind.gov.in/2026/Orders/647-GT-2025.pdf,
https://www.cercind.gov.in/2026/Orders/111-GT-2026.pdf, and the TNERC and
JERC orders above.

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
dated Tamil Nadu row set (2026-27); the main run notebook and run scripts export
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
