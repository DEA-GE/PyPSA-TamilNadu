# APSPDCL FPPCA filing, FY2025-26: central coal station bills

`annexure_1_fppca.xlsx` is Annexure-1 of the Andhra Pradesh Southern Power
Distribution Company's (APSPDCL) *FPPCA application for FY 2025-26*: one sheet
per month (`04-25` to `03-26`) with each supplying station's energy and
fixed, variable and other costs, as in the tariff order and as actually
billed.

Source: https://apspdcl.in/files/regulatory/True-up%20of%20FPPCA%20application%20for%20FY%202025-26.rar
(file `FPPCA application for FY 2025-26/Annexure-1 (FPPCA).xlsx`, downloaded
24 November 2026).

## Use in the model

`scripts/data_preparation/prepare_central_coal_energy_charges.py` reads the
seven central coal and lignite stations that also supply Tamil Nadu (NTECL
Vallur, NTPL Tuticorin, NLC TPS-II Stages I and II, NLC TPS-I and TPS-II
Expansions, NNTPS) and writes their monthly energy charge to
`data/model_inputs/central_coal_energy_charges_FY2025_26.csv`:

| Column | Meaning |
|---|---|
| `station` | Station name as in `data/model_inputs/coal_operating_costs.csv` |
| `month` | Billing month |
| `billed_energy_mu` | Actual energy billed to APSPDCL (column b) |
| `billed_variable_cost_rs_million` | Actual variable cost billed (column h) |
| `billed_rate_inr_per_mwh` | Billed variable cost divided by billed energy |
| `billing_adjustment` | True when the billed rate differs from the station's median month by more than 25%, or no energy was billed |
| `energy_charge_inr_per_mwh` | Charge used by the model: the billed rate, or the next month's charge for billing adjustments |

A station's energy charge rate is the same for every beneficiary, so APSPDCL's
billed rate is the station's FY2025-26 energy charge. Monthly bills also carry
arrears and credits; six station-months are replaced:

| Station | Month | Billed rate (INR/MWh) | Used |
|---|---|---:|---:|
| NTPL Tuticorin | May 2025 | −4,508 (credit) | 4,077 |
| NLC TPS-II Expansion | Apr 2025 | 4,224 | 3,231 |
| NLC TPS-II Stage II | Jan 2026 | 7,478 | 3,532 |
| NNTPS | Feb 2026 | 5,650 | 3,148 |
| NLC TPS-I Expansion | Feb 2026 | 4,520 | 3,344 |
| NLC TPS-II Expansion | Feb 2026 | 4,834 | 3,494 |
