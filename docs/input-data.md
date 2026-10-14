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
| Plant capacities and technologies | `data/source/TamilNadu_ICED_all_source_1782910007272_prayas_OSM_validated.xlsx`, `PlantInfo` and `District Allocation`; `data/build_template/generators.csv` | `model/generators.csv`, `model/storage_units.csv`, `model/component_metadata.csv` |
| July 2026 capacity additions | `data/model_inputs/additional_capacity_2026_07.csv` | `model/additional_capacity_2026_07.csv`, `model/capacity_reconciliation_2026_07.csv` |
| Hydro assets and observations | `data/model_inputs/hydro_asset_registry.csv`, `data/model_inputs/hydro_fleet_reconciliation.csv`, `data/reservoirs/tn_reservoir_daily_FY2025_26.csv` | `model/hydro_reservoir_parameters.csv`, `model/hydro_reservoir_inflow.csv`, `model/hydro_observed_soc.csv` |
| Observed generation and energy targets | `data/source/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx` | `model/oil_gas_daily_energy_budget.csv`, `model/nuclear_daily_energy_budget.csv`, `model/global_constraints.csv` |
| Area boundaries and grid | `data/model_inputs/Balancing_areas.txt`, `data/model_inputs/Grid_capacity.txt` | `model/buses.csv`, `model/links.csv`, `model/transmission_capacity_metadata.csv` |

The `model/` CSVs are generated products. To change inputs, edit the relevant
file under `data/` and rebuild. `data/build_template/` is an intermediate,
single-node input set, not a second supported network.

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
special hydro representations are handled separately.

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
Papanasam/Servalar. Kadamparai has separate pump and generation links.

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
100 m stage head, so its electrical conversion is separate while its water
discharge preserves volume. Pechiparai flow remains a scenario estimate, not an
Upper Kodayar inflow or storage measurement. The Kodayar-II forebay retains its
own balance; Kodayar-II discharge reaches a terminal downstream sink rather
than filling a small artificial pond. The terminal sink has an effectively
unbounded finite model capacity, so it cannot restrict upstream generation.
Rolling runs audit the daily upper balance
in `upper_kodayar_balance.csv`.
Other proxy storage, such as Upper Aliyar's Parambikulam profile, remains a soft
boundary guide and never becomes an exact observation.
Rolling runs pass modeled state between adjacent windows. If
`model/hydro_monthly_stored_energy.csv` is present, its values are interpolated
at rolling boundaries; absent monthly data create no rolling target.

## Observed generation and network geography

`data/model_inputs/tamil_nadu_outlook_annual_generation_FY2025_26.csv`
contains the authoritative FY2025-26 annual historical generation totals from
the Tamil Nadu Outlook. Standardized full-year result analysis uses these
values for its annual `generation_comparison` table. Because the Outlook
values contain no daily chronology, monthly and partial-period comparisons
continue to use the daily generation workbook described below.

The Outlook values are reported in MU, where 1 MU = 1 GWh, so the CSV stores
the same numeric values in its `generation_gwh` column. Small hydro and bio-
power are aggregated into the `other_res` comparison category, and
`comparable_total` uses the supplied Outlook total.

`data/model_inputs/tamil_nadu_outlook_installed_capacity.json` contains the
Outlook's historical installed-capacity categories and shares, including
bio-power and small hydro. The main run notebook exports and displays an
`installed_capacity_comparison` table against the model fleet.

`data/source/TamilNadu_FY2025-2026_Electricity_Power_Generation_daily.xlsx`
has an `Electricity_Power_Generation_` sheet: `Parameter` and `State` identify
rows, and daily date columns contain generation in MU (1 MU = 1,000 MWh).
The Tamil Nadu oil-and-gas and nuclear rows supply daily and annual equality
targets. The same workbook is used for comparisons with coal, hydro, solar,
wind, and total generation; it is **observed generation**, not an hourly
availability profile. See the two `model/*_daily_energy_budget.csv` files and
`model/global_constraints.csv` for the processed targets. Exact target
matching for oil and gas or nuclear is an imposed calibration condition.

`data/model_inputs/Balancing_areas.txt` is a Markdown-style table mapping 38
districts to the nine bus names. `data/model_inputs/Grid_capacity.txt` is a
tab-separated table of 18 area-to-area corridors and stated MW values or
ranges. The builder uses a range midpoint when needed, then applies a 0.50
availability factor. It writes bidirectional, lossless `Link`s, with nominal
and effective ratings recorded in
`model/transmission_capacity_metadata.csv`. These are transport limits; they
do not represent AC power flow or reactive power.
