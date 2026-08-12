# Tamil Nadu FY 2025-26 unit-commitment inputs

## Status

This directory is a PyPSA CSV-folder dataset for a single-node, hourly unit
commitment model of Tamil Nadu for FY 2025-26. It contains 8,760 hourly
snapshots from 1 April 2025 00:00 through 31 March 2026 23:00.

The demand series comes directly from the supplied NITI Aayog ICED workbooks.
Generation technologies and capacities come from operational records in the
supplied Tamil Nadu ICED plant workbook. Annual generation limits and technical
assumptions remain based on the CEA resource-adequacy report. Hourly generation
availability and several operational-cost and unit-commitment parameters remain
explicit dummy assumptions. Each operational workbook row is a separate PyPSA
component, but the term `record-level` is used because several workbook rows
are district, technology, or fleet aggregates rather than physical units.

The repository's run notebook does not optimize all 8,760 hours. It extracts
the 168-hour Monday-Sunday week containing the annual demand peak, solves that
subset, and exports the solved network to `tamil_nadu_2025_26.nc`.

Load the model with:

```python
import pypsa

n = pypsa.Network("inputs/tamil_nadu_ra_2025_26")
```

## Repository workflow and outputs

Run the notebooks from the repository root in this order:

1. `run_peakday_2025_26.ipynb` loads this CSV folder, finds the annual peak at
   16:00 on 11 July 2025, copies the 168 snapshots from 7 July 00:00 through
   13 July 23:00, solves the mixed-integer unit-commitment problem with HiGHS,
   and exports `tamil_nadu_2025_26.nc`.
2. `results_analysis.ipynb` loads the exported network and plots generation by
   carrier, pumped-storage power and state of charge, and coal commitment.

The committed NetCDF is a derived result rather than a second input dataset. It
contains an optimal 168-hour solution with no unserved energy. The CSV files in
the repository-level `results/` directory are legacy two-bus example outputs
and are not generated or consumed by this Tamil Nadu workflow.

Subsetting changes how some constraints should be interpreted:

- Hydro and STOA/MTOA `e_sum_max` values remain full-year limits; the notebook
  does not prorate them for the selected week.
- Pumped-storage cyclic state of charge closes across the selected week, not
  across FY 2025-26.
- Commitment initial conditions apply at the start of 7 July without a
  preceding rolling-horizon solve.

The solved week is therefore a dispatch and unit-commitment demonstration, not
a completed annual resource-adequacy or reliability assessment.

## Sources

### Tamil Nadu ICED plant workbook

`additional_data/TamilNadu_ICED_all_source_1782910007272.xlsx.xlsx`, worksheet
`PlantInfo`, supplies the generation technologies and nameplate capacities.
Only rows whose `Commissioning Group` is exactly `operational` are included.
This selects 280 records with 47,767.1105 MW across eight source categories;
pipeline, retired, and temporarily closed records are excluded. The extracted
totals are recorded in `operational_technology_capacities.csv`.

The workbook's 2,203.2 MW Hydro total includes the 400 MW Kadamparai pumped-
storage plant. Its four 100 MW workbook rows become four storage units; the
remaining 65 Hydro rows become generators. Solar records explicitly labelled
rooftop or off-grid retain the DRE availability shape, while the other Solar
records use the utility-solar shape.

Twelve rows are exact duplicates of preceding operational rows. They are
preserved because identical plant name, capacity, and commissioning date can
describe distinct units. Physical-unit IDs use
`plant_name__unit_NN`, with sequence numbers assigned deterministically within
each normalised plant name. Fuel is stored only in the PyPSA `carrier`, not in
the component ID. Excel row numbers remain in `component_metadata.csv` solely
for source traceability.

All Solar and Wind records, plus records explicitly labelled as others,
district-unspecified, bunched, rooftop, or off-grid, use
`plant_name__aggregate_NN`. This identifies 53 aggregate records; the remaining
223 generator records and four storage records use `__unit_NN`. The workbook
cannot support a more granular physical-unit representation for aggregates.

Operational records labelled off-grid are also included because the requested
filter is status-only. Connecting them to the single Tamil Nadu bus is a
modelling assumption and may overstate grid-connected supply.

### Resource adequacy report

Central Electricity Authority (CEA), Ministry of Power, Government of India,
*Report on Resource Adequacy Plan (Generation) for Tamil Nadu (2025-26 to
2035-36)*, April 2026.

The report PDF is not committed to this repository. The two demand workbooks
listed below are included at the repository root.

Relevant report references:

| Information | Reference |
|---|---|
| FY 2025-26 projected energy and peak demand | Table 7, printed page 11 |
| FY 2025-26 contracted capacity | Table 15, printed page 16 |
| FY 2025-26 projected generation by source | Figure 13, printed page 20 |
| Renewable CUFs | Annexure, printed page 25 |
| Technical and storage assumptions | Annexure, printed page 26 |
| Single-node network assumption | Annexure, printed page 26 |

### Demand workbooks

- [`Tamilnadu_Yearly Demand Profile_2025.xlsx`](../../additional_data/Tamilnadu_Yearly%20Demand%20Profile_2025.xlsx)
- [`Tamilnadu_Yearly Demand Profile_2026.xlsx`](../../additional_data/Tamilnadu_Yearly%20Demand%20Profile_2026.xlsx)

The worksheet is `Yearly Demand Profile`; the selected fields are `State`,
`Date`, and `Hourly Demand Met (in MW)`.

- April-December 2025: 6,600 Tamil Nadu rows from the 2025 workbook.
- January-March 2026: 2,160 Tamil Nadu rows from the 2026 workbook.
- The cleaned workbooks contain Tamil Nadu data only; workbook
  footer/disclaimer rows are excluded from the model input.
- The assembled fiscal-year series has 8,760 unique timestamps, no duplicates,
  no missing hours, and no extra hours.
- Values are used unchanged: no scaling, interpolation, or gap filling.

The workbooks identify NITI Aayog ICED as their source and include a disclaimer
that some platform data may be derived or assumed. In this documentation,
`workbook-sourced` means copied from the provided files; it does not make a
stronger claim about measurement or validation quality.

## Demand validation

| Metric | Workbook FY 2025-26 | CEA projection | Difference |
|---|---:|---:|---:|
| Annual energy | 131,383.400 GWh | 149,063 GWh | -17,679.600 GWh (-11.86%) |
| Peak demand | 19,987.33 MW | 21,481 MW | -1,493.67 MW (-6.95%) |
| Mean demand | 14,998.105 MW | 17,016.324 MW implied | -2,018.219 MW |
| Minimum demand | 7,483.57 MW | Not reported | n/a |

The workbook peak occurs at 16:00 on 11 July 2025. The model uses the workbook
series rather than scaling it to the higher CEA forecast.

## Capacity represented

| Component | Capacity | Representation | Provenance |
|---|---:|---|---|
| Coal | 15,032.5 MW | 49 record-level commitment generators | Operational ICED rows |
| Oil & Gas | 844.58 MW | 14 record-level commitment generators | Operational ICED rows |
| Nuclear | 2,440 MW | 4 record-level fixed-output generators | Operational ICED rows |
| Bio Power | 1,055.12 MW | 57 record-level commitment generators | Operational ICED rows |
| Hydro generation | 1,803.2 MW | 65 record-level energy-limited generators | Operational ICED rows excluding Kadamparai PSP |
| Hydro PSP | 400 MW | Four 100 MW storage units | Operational Kadamparai ICED rows; energy duration dummy |
| Small-Hydro | 123.05 MW | 39 record-level variable generators | Operational ICED rows |
| Wind | 12,159.5305 MW | 21 record-level variable generators | Operational ICED rows |
| Solar | 13,909.13 MW | 27 record-level variable generators | Operational ICED rows |
| STOA/MTOA | 6,727 MW | Dispatchable market import | Reported peak tie-up capacity |
| Unserved energy | 100,000 MW | Virtual feasibility generator | Dummy; not part of reported capacity |

The ICED operational generation capacity is 47,767.1105 MW, including the
400 MW PSP once. STOA/MTOA and the virtual unserved-energy generator are model
mechanisms outside that total. No BESS is included.

## Input files

### `network.csv`, `crs.json`, `meta.json`, and `carriers.csv`

PyPSA export metadata. The model is a single, fixed investment period using
EPSG:4326. `meta.json` is empty.

`carriers.csv` defines the eight ICED technology carriers plus the AC bus,
electricity demand, market-import, and unserved-energy carriers.

### `operational_technology_capacities.csv`

Records the auditable operational record counts, source capacity totals, and
their aggregate PyPSA representations. It is provenance metadata rather than a
PyPSA component table.

### `component_metadata.csv`

Maps all 280 operational records to their generated component ID and type,
record kind, within-plant sequence, Excel row, source label, plant name,
location, commissioning date, capacity, implementing agency, and exact-
duplicate group size. This is provenance metadata rather than a PyPSA
component table.

### `technology-p_max-pu.csv`

Stores the five technology-level availability templates used by the rebuild
script. PyPSA does not load this file directly. The script expands it into one
column per applicable workbook record in `generators-p_max_pu.csv`.

### `buses.csv`

The `Tamil_Nadu` bus implements the report's single-node assumption. The
230 kV nominal voltage and coordinates 78.6569, 11.1271 are dummy display
values; voltage does not affect this one-node dispatch model. Internal lines,
links, and transformers are intentionally absent because the report assumes no
internal transmission bottleneck.

### `snapshots.csv`

Contains all 8,760 FY 2025-26 hours. Objective, generator, and storage weights
are one for every hourly snapshot.

### `loads.csv` and `loads-p_set.csv`

`Tamil_Nadu_demand` is connected to the single bus. Every `p_set` value is a
workbook-sourced hourly demand in MW assembled as described above.

### `generators.csv`

#### Reported or report-derived settings

| Technology | `p_min_pu` | `p_max_pu` | Normal ramp limit | Basis |
|---|---:|---:|---:|---|
| Coal | 0.55 | 0.85 | 0.60 p.u./h | Report: 55% minimum, 85% availability, 1%/min ramp |
| Oil & Gas | 0.40 | 0.90 | 1.00 p.u./h | Retained Gas assumption: 40% minimum, 90% availability, 5%/min ramp; hourly value capped at 1 |
| Bio Power | 0.50 | 0.60 | 1.00 p.u./h | Retained Biomass assumption: 50% minimum, 60% availability, 2%/min ramp; hourly value capped at 1 |
| Nuclear | 0.61201 | 0.61201 | Not set | Derived from 7,763 GWh projected annual generation and constant operation |

Additional annual limits:

- Hydro `e_sum_max`: 5,528,000 MWh, from Figure 13, allocated among the 65
  conventional Hydro records in proportion to nameplate capacity.
- STOA/MTOA `e_sum_max`: 17,752,000 MWh, from Figure 13.

#### Dummy unit-commitment settings

| Technology | Minimum up/down | Start-up cost | Shut-down cost | Start/shut ramp |
|---|---:|---:|---:|---:|
| Coal | 8 h / 8 h | INR 3,991.35/MW | INR 798.27/MW | 0.55 p.u. |
| Oil & Gas | 2 h / 2 h | INR 1,184.49/MW | INR 236.90/MW | 0.40 p.u. |
| Bio Power | 4 h / 4 h | INR 379.10/MW | INR 94.78/MW | 0.50 p.u. |

Start-up and shutdown costs are scaled by each record's capacity. The rates are
derived from the previous aggregate-block assumptions, preserving the total
cost if all capacity of a technology starts once. Every thermal record starts
offline (`p_init=0`) and is assumed to have been down for at least its minimum
down time. Actual initial conditions and unit-specific UC parameters were not
supplied.

#### Dummy operating costs

| Technology | Marginal cost (INR/MWh) |
|---|---:|
| Coal records | 3,087.5 |
| Oil & Gas records | 6,025 |
| Bio Power records | 4,512.5 |
| Nuclear | 800 |
| Hydro | 300 |
| Wind, solar, and DRE | 0 |
| STOA/MTOA import | 6,500 |
| Unserved energy | 100,000 |

The CEA report does not provide sufficient unit fuel prices, variable O&M,
no-load costs, or start costs to calculate these operational costs.

### `generators-p_max_pu.csv`

Hourly generation availability shapes are **dummy** shapes retained from the
previous model. The ICED capacity update changes their available annual energy,
so they are no longer normalised to the report's FY 2025-26 generation values:

| Record source | Output columns | Capacity | Template |
|---|---:|---:|---|
| Wind | 21 | 12,159.5305 MW | `wind_fleet` |
| Solar | 27 | 13,909.13 MW | `solar_utility` or `solar_DRE_rooftop` according to the record name |
| Small-Hydro | 39 | 123.05 MW | `small_hydro` |
| Conventional Hydro | 65 | 1,803.2 MW | `hydro_reservoir` |

The resulting file has 152 component-specific columns. Records assigned to the
same template have identical per-unit availability, so this conversion adds
component identity but not unit-specific renewable or hydro behavior.

The solar shape follows daylight and a dummy seasonal modifier; wind and hydro
use deterministic dummy seasonal and intraday shapes. These profiles are not
observations and must be replaced when actual generation data are supplied.

### `storage_units.csv`

The ICED workbook includes four operational 100 MW Kadamparai pumped-storage
records but does not specify their energy capacity. Each becomes a separate
storage unit. The model assumes:

- 6 hours / 2,400 MWh energy capacity: dummy.
- 80% round-trip efficiency: reported; split symmetrically into 89.443% charge
  and discharge efficiencies.
- 95% availability applied as +/-0.95 charge/discharge limits: report-derived.
- Cyclic annual state of charge: modelling assumption.
- INR 10/MWh marginal discharge cost: dummy.

## Material data still missing

1. Actual hourly wind, utility solar, rooftop solar, hydro, and small-hydro
   generation or availability profiles.
2. Confirmation of which aggregate workbook records correspond to multiple
   physical units, particularly the regional Solar, Wind, and Bio Power rows.
3. Unit heat rates/efficiencies, fuel prices, variable O&M, no-load costs,
   start/shut costs, minimum up/down times, and initial commitment states.
4. Auxiliary consumption; report technology-level values are not yet applied.
5. Hydro inflows, reservoir energy and level constraints, and seasonal water
   budgets.
6. PSP energy capacity, initial state of charge, reservoir constraints,
   self-discharge, and cycling/degradation cost.
7. Hourly STOA/MTOA availability, prices, and contract restrictions.
8. Forced outages and the report's stochastic variations for demand and
   renewable generation.
9. Operating reserve constraints, the 4% planning reserve margin, RCO
   constraints, LOLP, and EENS/NENS calculations.
10. Generator efficiency defaults to 1 and standby/capital/fixed costs default
    to zero where absent; these are not validated assumptions.

## Solver and consistency notes

The supplied notebook solves only the peak-demand week. The record-level model
has 120 committable thermal components, so a monolithic 8,760-hour mixed-integer
solve is substantially larger. Use a full-year or rolling-horizon formulation
only after defining suitable boundary conditions, horizon length, overlap, and
treatment of annual energy budgets.

With PyPSA 1.2.4, the peak-week solve completes optimally with HiGHS 1.15.0 but
emits non-blocking consistency warnings:

- `p_init=0` is ignored for committable generators whose
  `down_time_before` already specifies their pre-horizon state; and
- PyPSA warns that the default for `include_objective_constant` will change in
  a future release.

These warnings do not invalidate the committed solution, but the input schema
and initial-condition convention should be cleaned up before treating the
model as a production study.
