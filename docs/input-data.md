# Input data guide

This guide says where each input of the model is, what it contains and how it
is prepared. What the model assumes, and why, is in the
[model description](model-description.md), which the last column of each table
links to.

All paths are relative to the repository root. The supported network is
`model/`, built by `python scripts/build_9ba_model.py`. The build reads the
prepared files in `data/build_template/`, `data/model_inputs/` and
`data/reservoirs/`, plus two source workbooks: the plant inventory
(`data/source/TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx`)
and the observed daily generation
(`data/source/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx`).
Most other files in `data/source/` are retained as evidence or upstream
material; their presence does not mean the build reads them. Several inputs
are read only at run time, by the options of the run configuration.

## Conventions

- **Time.** The model covers 8,760 hourly snapshots from 2025-04-01 00:00
  through 2026-03-31 23:00. Model hours are hour-beginning Indian Standard
  Time (IST). CSV time series generally use an integer row index aligned to
  `model/snapshots.csv`; the renewable input and most prepared hourly files
  use an explicit `snapshot` or `timestamp` column.
- **Units.** MW, MWh or GWh; the NITI Dashboard and CEA report energy in MU,
  and 1 MU = 1 GWh = 1,000 MWh. Costs are in INR.
- **Generated files.** The `model/` CSVs are generated products: to change an
  input, edit the file under `data/` and rebuild. `data/build_template/` is an
  intermediate, single-node input set, not a second supported network;
  `scripts/build_unit_level_model.py` regenerates it from the plant inventory.

## Which file to open

| Question | Prepared or source input | Built output, or run-time use | Described in |
| --- | --- | --- | --- |
| Area boundaries and district mapping | `data/model_inputs/Balancing_areas.txt`, `data/source/TamilNadu_9BAs.gpkg` | `model/buses.csv` | [Scope](model-description.md#balancing-areas) |
| Plant capacities and technologies | Plant inventory, sheets `PlantInfo` and `District Allocation`; `data/build_template/generators.csv` | `model/generators.csv`, `model/storage_units.csv`, `model/component_metadata.csv`, `model/district_capacity_allocation.csv`, `model/capacity_by_balancing_area.csv` | [Scope](model-description.md#plant-inventory-and-allocation-to-areas) |
| July 2026 capacity additions | `data/model_inputs/additional_capacity_2026_07.csv` | `model/additional_capacity_2026_07.csv`, `model/capacity_reconciliation_2026_07.csv` | [Scope](model-description.md#installed-capacity) |
| Statewide hourly demand | `data/build_template/loads-p_set.csv` | Sum of the nine area columns of `model/loads-p_set.csv` | [Demand](model-description.md#demand) |
| Balancing-area demand | `data/model_inputs/tamil_nadu_balancing_area_demand.xlsx`, sheet `FY2025-26 Calibration` | `model/loads-p_set.csv`, `model/demand_allocation.csv` | [Demand](model-description.md#demand) |
| Coal costs and start-up costs | `data/model_inputs/coal_operating_costs.csv`, `central_coal_energy_charges_FY2025_26.csv` | `model/generators.csv`, `model/generators-marginal_cost.csv`, `model/coal_operating_cost_audit.csv` | [Coal](model-description.md#marginal-costs) |
| Coal outages, auxiliary consumption and capability | `data/source/NITI dashboard/unit outages Tamil Nadu`, `auxiliary consumption Tamil Nadu`; `data/model_inputs/coal_unit_outages_FY2025_26.csv`, `coal_auxiliary_consumption.csv`, `coal_unit_capability_FY2025_26.csv` | Run time, `outages.enabled` (`scripts/generator_outages.py`) | [Coal](model-description.md#availability-maintenance-and-outages) |
| Central coal stations: entitlement and declared capacity | `data/source/REA data/*.zip` (SRPC REA, not in Git); `data/model_inputs/central_station_tn_entitlement_FY2025_26.csv`, `central_station_declared_capacity_FY2025_26.csv`, `central_station_units.csv` | Entitlement shares size the Tamil Nadu units in the build; declared capacity at run time with `outages.central_availability: declared` | [Coal](model-description.md#central-stations-and-export-reservations) |
| Oil and gas, nuclear, bio-power and diesel costs | `data/model_inputs/plant_operating_costs.csv` | `model/generators.csv`, `model/plant_operating_cost_audit.csv` | [Oil and gas](model-description.md#oil-and-gas) |
| Observed generation and energy targets | Daily generation workbook; `data/model_inputs/niti_dashboard_annual_generation_FY2025_26.csv`, `niti_dashboard_tamil_nadu_generation_FY2025_26.csv`, `tamil_nadu_bio_power_monthly_generation_FY2025_26.csv`, `tamil_nadu_small_hydro_monthly_generation_FY2025_26.csv` | `model/oil_gas_daily_energy_budget.csv`, `model/nuclear_daily_energy_budget.csv`, `model/oil_gas_energy_budget_summary.csv`, `model/biomass_energy_target_summary.csv`, `model/small_hydro_energy_target_summary.csv` | [Calibration](model-description.md#energy-targets) |
| Plant-level monthly generation | `data/source/NITI dashboard/monthly plant generation Tamil Nadu`; `data/model_inputs/niti_iced_tamil_nadu_monthly_generation.csv`, `niti_iced_plant_mapping.csv` | Run time, released energy targets with `availability_source: plant` | [Calibration](model-description.md#releasing-the-energy-targets) |
| Installed-capacity benchmark | `data/source/Capacity_Metatable_Data_Sept26.xlsx` | `installed_capacity_comparison.csv` of each run | [Calibration](model-description.md#observed-data) |
| Hydro assets and observations | `data/model_inputs/hydro_asset_registry.csv`, `hydro_fleet_reconciliation.csv`, `hydro_technical_parameters.csv`, `hydro_cascade_annual_generation_FY2025_26.csv`, `cea_hydro_monthly_generation_FY2025_26.csv`; `data/reservoirs/tn_reservoir_daily_FY2025_26.csv` | `model/stores.csv`, `model/links.csv`, `model/storage_units-inflow.csv`, and the `model/hydro_*.csv` and `model/upper_kodayar_inflow_estimate.csv` audits | [Hydro](model-description.md#hydro) |
| Wind and solar availability | `data/model_inputs/renewable_profiles.csv` (from `data/source/lava_profiles_fy2025_26/`) | `model/generators-p_max_pu.csv` | [Solar](model-description.md#availability), [Onshore wind](model-description.md#profiles) |
| Solar capacity through the year | `data/model_inputs/tamil_nadu_installed_capacity_monthly_2019_2026.csv`, `tamil_nadu_re_capacity_releases_FY2025_26.csv` | `model/solar_capacity_trajectory.csv`, `model/solar_availability_calibration.csv` | [Solar](model-description.md#availability) |
| Seasonal solar correction | `data/model_inputs/solar_seasonal_correction_factors.csv`, `era5_daily_weather_tn_areas_FY2020_26.csv` | `model/solar_seasonal_correction.csv` | [Solar](model-description.md#availability) |
| Interstate wind and solar plants | `data/model_inputs/tn_ists_re_plant_output_FY2025_26.csv`, `tn_ists_re_plant_series_FY2025_26.csv`, `tn_ists_re_plants_FY2025_26.csv`, `wind_seasonal_factors_FY2025_26.csv` | Run time, `ists_re_plants` | [Onshore wind](model-description.md#interstate-wind-and-solar-plants-on-their-real-output-optional) |
| Offshore wind and batteries | `config/technologies.yaml`; `data/model_inputs/offshore_wind_profiles.csv` (from `data/source/ts_power_150m_block2.xlsx`) | Run time, `technologies` | [Offshore wind](model-description.md#offshore-wind-scenario), [Battery](model-description.md#battery-storage-scenario) |
| Grid | `data/model_inputs/TN_9BA_with_interconnectors_pypsa_FY2025-26.xlsx` | `model/buses.csv`, `model/lines.csv`, `model/links.csv`, `model/transmission_capacity_metadata.csv` | [Grid](model-description.md#corridors-between-areas) |
| FY2025-26 REA imports and exports | `data/model_inputs/rea_interstate_hourly_flows_FY2025_26.csv`, `rea_interstate_gateway_shares_FY2025_26.csv`, `rea_interstate_source_audit_FY2025_26.csv` | `model/loads.csv`, `model/loads-p_set.csv`, `model/interstate_flow_audit.csv`, `model/interstate_flow_reconciliation.csv` | [Grid](model-description.md#rea-transactions) |
| Metered interchange | `data/model_inputs/tn_metered_interchange_FY2025_26.csv` | Correction loads in `model/loads-p_set.csv`; the import limit at run time | [Grid](model-description.md#metered-interchange-default) |
| Plant-specific export reservations | `data/model_inputs/rea_plant_export_mapping.csv`, `rea_plant_export_hourly_FY2025_26.csv`, `rea_plant_export_mapping_audit_FY2025_26.csv` | `model/generators.csv`, `model/generators-p_min_pu.csv`, `model/generators-p_max_pu.csv`, `model/rea_plant_export_reservation_audit.csv` | [Grid](model-description.md#exports-from-named-tamil-nadu-plants) |
| Historical four-station capacity shares | `data/model_inputs/tn_export_capacity_allocations.csv` | `model/tn_export_capacity_allocations.csv` (reference only) | [Grid](model-description.md#exports-from-named-tamil-nadu-plants) |
| Tamil Nadu import limit | `data/source/grid_india_ttc_fy2025_26/transfer_capability_fy2025_26.csv` | Run time, `interstate.drawal_limit` | [Grid](model-description.md#tamil-nadu-import-limit-grid-india) |
| Transformers and plant connection levels | `data/source/srldc_transmission_elements/tn_icts_2026-03.csv`, `tn_plant_connection_levels.csv`; `data/model_inputs/tn_ict_capacity_by_area_FY2025_26.csv` | Run time, `grid.two_level` | [Grid](model-description.md#two-voltage-levels-per-balancing-area-optional) |
| Exchange trades and prices | `data/model_inputs/tn_exchange_trades_FY2025_26.csv`; `data/source/iex_dam_fy2025_26/iex_dam_tamil_nadu_hourly_fy2025_26.csv` | Run time, `exchange_trading` | [Grid](model-description.md#exchange-trading-at-iex-prices-optional) |

## File formats

**Statewide demand.** `data/build_template/loads-p_set.csv` has 8,760 rows, one
`Tamil_Nadu_demand` column in MW and a zero-based integer row index; the
matching timestamps are in `data/build_template/snapshots.csv`. Its values are
the FY portion of `data/source/Tamilnadu_Yearly Demand Profile_2025.xlsx` and
`..._2026.xlsx`. Each source workbook has a `Yearly Demand Profile` sheet with
`State`, `Date` (for example `01-Apr 12am`) and `Hourly Demand Met (in MW)`.
The template is the file the build reads; there is no demand-workbook import
in `scripts/build_9ba_model.py`.

**Area demand.** `data/model_inputs/tamil_nadu_balancing_area_demand.xlsx` has
`Inputs`, `Interpolation` and `FY2025-26 Calibration` sheets. The build reads
nine area rows from two tables on `FY2025-26 Calibration`: annual energy
shares at Excel rows 9–17 and peak shares at rows 23–31. `model/loads-p_set.csv`
has one MW column per load, named like `chennai_north_demand` for the areas and
`interstate_*` for the exchange at the neighbour buses, with row order matching
`model/snapshots.csv`.

**Wind and solar profiles.** `data/model_inputs/renewable_profiles.csv` is the
active hourly input: a `snapshot` column and 18 capacity-factor columns,
`<area>_wind` and `<area>_solar` for each of the nine areas (for example
`chennai_north_wind`). Values are fractions from 0 to 1, not MW or energy, in
8,760 complete rows aligned to the model snapshots. The build applies each
area's profile to its wind or solar generators, checks timestamps, required
columns, missing values and bounds, and writes component-level availability to
`model/generators-p_max_pu.csv`.

If `renewable_profiles.csv` is absent, the build generates a **fallback**: the
same statewide wind shape in all areas and one capacity-weighted solar shape
in all areas, taken from `data/build_template/technology-p_max-pu.csv`. That
fallback is not the current input. The template has five technology-level
capacity-factor columns (`hydro_reservoir`, `wind_fleet`, `solar_utility`,
`solar_DRE_rooftop`, `small_hydro`) with an integer hourly index.

**Plant inventory.** `PlantInfo` has one record per plant unit or aggregate,
including `Source` (technology), `Commissioning Group`, plant name, cleaned
district and `Capacity (MW)`. `District Allocation` may have several rows for
one source record and gives `Original Row`, `Allocated District`, allocation
share and MW, method, confidence and evidence URL.
`data/model_inputs/additional_capacity_2026_07.csv` has one row per added
record: `record_id`, technology and carrier, plant, district, `capacity_mw`,
`unit_count`, `include_in_model`, allocation method, confidence and source
reference.

**Observed daily generation.** The daily workbook's
`Electricity_Power_Generation_` sheet has `Parameter` and `State` columns that
identify rows, and daily date columns with generation in MU.

**Reservoirs.** `data/reservoirs/tn_reservoir_daily_FY2025_26.csv` has one
`date`/`reservoir` row with storage and full capacity in M.cft, inflow and
outflow in cusecs, converted daily volumes, calibrated live-storage fractions
where available, source URL, and validation and quality fields: 365 dates and
19 named reservoirs. The wide daily table and the suspicious-snapshot audit
are next to it.

**Hydro registry.** `data/model_inputs/hydro_asset_registry.csv` classifies
the inventory's hydro units by `asset_id`, `match_regex`, representation,
data status, `max_hours`, MW source and notes.

**Grid workbook.** `data/model_inputs/TN_9BA_with_interconnectors_pypsa_FY2025-26.xlsx`
has the sheets `buses`, `lines`, `links`, `circuits`, `network`, `crs`, `meta`
and `snapshots` in PyPSA's Excel layout, and a `README` sheet with the method
and caveats. The build reads it with `Network.import_from_excel()`.
`python scripts/import_grid_from_excel.py` replaces only the grid in an
already-built `model/`, without rebuilding generation, demand or hydro.

## Preparing the inputs

The prepared inputs are checked in. Rebuild one only when its source changes,
then rebuild the model.

| Prepared input | Script | Source |
| --- | --- | --- |
| `data/build_template/*` | `scripts/build_unit_level_model.py` | Plant inventory workbook |
| `renewable_profiles.csv`, `Onshorewind_9BAs_AggrPCWiderCUF_FY2025-26.csv` | `scripts/prepare_lava_profiles.py` (`--extract-from <LAVA data folder>` refreshes the extracts) | `data/source/lava_profiles_fy2025_26/` |
| `solar_seasonal_correction_factors.csv` | `scripts/solar_seasonal_correction.py` (rerun after any change to `renewable_profiles.csv`; the build refuses factors fitted to another profile) | NITI monthly solar generation and capacity, `data/source/era5_open_meteo/` |
| `tamil_nadu_installed_capacity_monthly_2019_2026.csv`, `tamil_nadu_re_capacity_releases_FY2025_26.csv` | Tidy copies of the sources (no script); the build checks them against each other (`scripts/solar_capacity_trajectory.py`) | `data/source/NITI dashboard/monthly installed capacity Tamil Nadu/`, `data/source/re_capacity_releases_fy2025_26/` |
| `offshore_wind_profiles.csv` | `scripts/prepare_offshore_wind_profile.py` | `data/source/ts_power_150m_block2.xlsx` |
| `rea_interstate_*`, `rea_plant_export_*` | `scripts/prepare_rea_interstate_flows.py --ledger <ledger.parquet>` | Deduplicated REA ledger (`scripts/run_rea_fy2025_26.ps1`, [`outputs/rea_fy2025_26/`](../outputs/rea_fy2025_26/README.md)) |
| `tn_metered_interchange_FY2025_26.csv` | `scripts/prepare_metered_interchange.py` | `data/source/REA data/` (not in Git) |
| `tn_exchange_trades_FY2025_26.csv` | `scripts/prepare_exchange_trades.py` | `data/source/REA data/` |
| `central_station_*` | `scripts/prepare_declared_capacity.py` | `data/source/REA data/` |
| `tn_ists_re_plant_*`, `tn_ists_re_plants_FY2025_26.csv` | `scripts/prepare_ists_re_plant_output.py` | `data/source/REA data/` |
| `wind_seasonal_factors_FY2025_26.csv` | `scripts/prepare_wind_seasonal_factors.py` | The plant series above |
| `coal_unit_outages_FY2025_26.csv`, `coal_auxiliary_consumption.csv`, `coal_unit_capability_FY2025_26.csv` | `scripts/prepare_coal_outages.py` | `data/source/NITI dashboard/` |
| `central_coal_energy_charges_FY2025_26.csv` | `scripts/data_preparation/prepare_central_coal_energy_charges.py` | `data/source/apspdcl_fppca_fy2025_26/` |
| `niti_iced_tamil_nadu_monthly_generation.csv` | `scripts/data_preparation/prepare_niti_iced_monthly_generation.py` | `data/source/NITI dashboard/monthly plant generation Tamil Nadu/` |
| `cea_hydro_monthly_generation_FY2025_26.csv` | `scripts/parse_cea_monthly_hydro_generation.py` | `data/source/cea_fy2025_26_monthly_generation/` |
| `data/reservoirs/*` | `scripts/scrape_tn_reservoirs.py` | Tamil Nadu Agriculture daily reservoir pages |
| `data/source/grid_india_ttc_fy2025_26/*.csv` | `scripts/data_preparation/prepare_grid_india_ttc.py` | Grid-India PDFs in that folder |
| `data/source/srldc_transmission_elements/*`, `tn_ict_capacity_by_area_FY2025_26.csv` | `scripts/data_preparation/prepare_tn_ict_inventory.py` | SRLDC compendiums |
| `data/source/iex_dam_fy2025_26/*.csv` | `scripts/data_preparation/prepare_iex_dam_prices.py`, `prepare_iex_tn_area_prices.py` | IEX market-data exports |

The REA hourly input uses the deduplicated transaction ledger: resolved
imports take the generating plant's mapped state and resolved exports the
beneficiary state; records that remain unresolved are kept as a separate
hourly total. Plant-level export reservations are prepared from the same
ledger.

## Source data folders

Each prepared source folder has a README with its provenance, retrieval date
and checks:

| Folder | Content |
| --- | --- |
| [`data/source/NITI dashboard/`](../data/source/NITI%20dashboard) | NITI Dashboard (ICED) exports: annual and monthly generation, monthly plant generation, monthly installed capacity, monthly solar generation, unit outages, auxiliary consumption |
| [`data/source/apspdcl_fppca_fy2025_26/`](../data/source/apspdcl_fppca_fy2025_26/README.md) | Monthly bills of the central coal stations |
| [`data/source/cea_fy2025_26_monthly_generation/`](../data/source/cea_fy2025_26_monthly_generation/README.md) | CEA monthly station-generation reports (manifest) |
| [`data/source/era5_open_meteo/`](../data/source/era5_open_meteo/README.md) | ERA5 daily weather at the area centroids |
| [`data/source/grid_india_ttc_fy2025_26/`](../data/source/grid_india_ttc_fy2025_26/README.md) | Grid-India transfer capability declarations |
| [`data/source/iex_dam_fy2025_26/`](../data/source/iex_dam_fy2025_26/README.md) | IEX day-ahead, green day-ahead and real-time prices |
| [`data/source/lava_profiles_fy2025_26/`](../data/source/lava_profiles_fy2025_26/README.md) | LAVA/atlite wind and solar profile extracts |
| [`data/source/re_capacity_releases_fy2025_26/`](../data/source/re_capacity_releases_fy2025_26/README.md) | MNRE state-wise renewable capacity releases |
| [`data/source/srldc_transmission_elements/`](../data/source/srldc_transmission_elements/README.md) | Tamil Nadu's 400 kV transformers and plant connection levels |

The SRPC Regional Energy Account archives (`data/source/REA data/`) are not
in Git; the REA pipeline's [report](../outputs/rea_fy2025_26/README.md)
describes them.

## Archived and legacy inputs

- `data/model_inputs/renewable_profiles_before_external_FY2025_26.csv` keeps
  the previous wind and solar profiles for
  `notebooks/compare_external_re_profiles_FY2025_26.ipynb`. They were built by
  `scripts/construct_and_compare_re_profiles_fy2025_26.py`, which shifted the
  series 5 hours and wrapped the last five hours of the year to its start, and
  needs source files no longer in the repository.
- `data/source/wind_solar_profiles_TN-9BAs-2025-26-1h.xlsx` is the older
  profile workbook, kept for comparison; `scripts/prepare_renewable_profiles.py`
  checks or regenerates the archived baseline and leaves the active input
  unchanged.
- `data/model_inputs/archive/onshorewind_profiles/` holds earlier wind
  profile variants.
- `data/model_inputs/interstate_import_generators.csv` is the former
  dispatchable import-entitlement set, replaced by the fixed interstate
  schedule.
- `data/source/Grid/Transmission_Lines_1788874302675.xlsx` is a CEA
  transmission-line list, compared with the model grid by
  `scripts/compare_transmission_source.py`.
- No script reads `data/source/CHETNA_Thermal_Power_Plants_inventory.csv`,
  `ERA5_tseries_150m_area2.csv`, `PLF_FY25-26.xlsx`,
  `Southern region state-wise installed capacity_31072026.xls`,
  `tamil_nadu_balancing_area_demand_interpolation.xlsx` or
  `TamilNadu_BAs.gpkg` (the same shapes as `TamilNadu_9BAs.gpkg`); they are
  reference material.
