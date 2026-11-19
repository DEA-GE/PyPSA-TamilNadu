# MNRE renewable-capacity releases — FY2025–26

The Tamil Nadu values transcribed from these releases are stored in
`data/model_inputs/tamil_nadu_re_capacity_releases_FY2025_26.csv`, with one
row per available month end from 31 March 2025 to 31 March 2026. They are used
in two ways by `scripts/solar_capacity_trajectory.py`:

- to check the NITI Dashboard monthly capacity series, which must equal MNRE's
  solar total in every listed month;
- to subtract off-grid solar, which the model excludes.

The original PDFs are not retained. `manifest.csv` records the official URL,
retrieval timestamp, byte size, and SHA-256 digest of each file used, under the
file name cited in the CSV's `mnre_source` column.

## Sources

- **MNRE, "State-wise (Location based) installed capacity of Renewable Power
  as on DD.MM.YYYY"** (monthly). Tamil Nadu row: small hydro, wind, bio-power
  total, solar by type (ground-mounted, rooftop including PM-Surya Ghar,
  hybrid component, off-grid/KUSUM-B) and solar total, large hydro, and total.
  - Tamil Nadu reports no hybrid solar component (zero or blank) in every
    release used, so that column is omitted.
  - The 31.05.2025 edition reports only total solar, without the split by
    type.
  - Editions for June, August, September, November and December 2025 could not
    be located; the NITI monthly series covers those months.
- **MNRE, Renewable Energy Statistics 2024-25**, Table 8.2 "RE cumulative
  installed capacity as on 31.03.2025" (page 43). Totals by technology only.

Values were extracted with `pdftotext -raw` / `-table` and checked. In every
row, the solar components sum to the solar total, and small hydro + wind +
bio-power + solar + large hydro sum to the reported total.
