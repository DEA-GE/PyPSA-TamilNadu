# Grid-India transfer capability declarations — Southern Region, FY2025–26

Grid-India (NLDC/SRLDC) declares the total transfer capability (TTC) of each
state's import and of the intra-regional corridors every month. The reliability
margin (RM) is subtracted to give the available transfer capability (ATC).
ATC is split into the contracted long-term flows (net GNA) and the margin left
for short-term trades (T-GNA). The declarations are used to judge the model's
interstate and inter-area transmission.

| File | Content |
|---|---|
| `pdf/sr_state_import_<YYYY-MM>_rev<n>.pdf` | Import capability of Andhra Pradesh, Telangana, Karnataka, Tamil Nadu and Puducherry for one month (one page each) |
| `pdf/s1_s2s3_corridor_<YYYY-MM>_rev<n>.pdf` | S1→(S2&S3) corridor and S3 (Kerala) import |
| `transfer_capability_fy2025_26.csv` | One row per area, month, revision and time period: `ttc_mw`, `rm_mw`, `atc_mw`, `net_gna_mw`, `tgna_margin_mw` (empty where the declaration shows "-"), `change` vs the previous revision, `latest` = highest revision present |
| `limiting_constraints_fy2025_26.csv` | The limiting constraints listed with each declaration |

Bid areas (note on the corridor declaration): S1 = Andhra Pradesh, Telangana,
Karnataka and Goa (SR); S2 = Tamil Nadu and Puducherry; S3 = Kerala.

## Source

The user downloaded the PDFs from the Grid-India website on 25 November 2026.
The site is only reachable from an Indian location. The original file names
are kept in the CSV (`original_file`). To rebuild (pypdf is not in
`requirements.txt`):

```text
uv run --no-project --with pypdf --with pandas python scripts/data_preparation/prepare_grid_india_ttc.py --import-from <folder>
```

The script skips exact duplicates, files without a declaration table, and
months outside FY2025-26 (a June 2026 file was downloaded too). It reports
missing months per area.

## Coverage

- **State import:** 11 of 12 months; May 2025 is missing. June 2025, October
  2025 and February 2026 are present only as revision 0. August 2025 and March
  2026 are present as revisions 0 and 1.
- **S1→(S2&S3) and S3 import:** December 2025 only.
- **Export capability:** none (the state files cover import only).

## Tamil Nadu import capability

| Revision | Issued | TTC | RM | ATC | Net GNA | T-GNA margin |
|---|---|---|---|---|---|---|
| Rev 0 (Jun, Aug, Oct 2025; Feb, Mar 2026), Rev 1 April 2025 | 4–13 months ahead, or before April 2025 | 14,050 | 350 | 13,700 | 9,177 | 4,523 |
| Rev 1 (Jul 2025 – Mar 2026) | about one month ahead | 14,600 | 420 | 14,180 | 9,177 | 5,003 |

- **Time of day:** the value is the same in all 96 blocks of every month. Net
  GNA is 9,177 MW all year.
- **Rev 0 vs Rev 1:** every Rev 1 issued from July 2025 raises TTC by 550 MW.
  These revisions follow the commissioning of a 500 MVA 400/230 kV
  transformer at Salem in April 2025. The months held only as Rev 0 most
  likely had 14,600 MW too.
- **Limiting constraints** (any one or a combination):
  - N-1 of the 400/230 kV transformers at Salem;
  - N-1 of the 400/230 kV transformers at Kalivandapattu;
  - N-1 of the 400/230 kV transformers at NLC TS-II and NNTPS;
  - N-1 of the 400/230 kV transformers at Pugalur (listed from Rev 1 of July 2025);
  - the 400 kV Alamathy–Manali line, which overloads when the NCTPS–Manali
    line trips.

## S1→(S2&S3) corridor and S3 import, December 2025 (Rev 1)

| Corridor | TTC | RM | ATC | Net GNA | T-GNA margin |
|---|---|---|---|---|---|
| S1→(S2&S3) | 11,300 | 450 | 10,850 | 5,574 | 5,276 |
| S3 import | 4,575 | 120 | 4,455 | 2,679 | 1,776 |

The corridor's limiting constraints are N-1 at these 400/230 kV transformer
groups:

| Substation | Transformers |
|---|---|
| Salem | 2×315 + 1×500 MVA |
| Pugalur | 2×315 + 1×500 MVA |
| Kalivandapattu | 1×500 + 2×315 MVA |
| NNTPP | 2×500 MVA |
| Neyveli Stage 2 | 2×250 MVA |

The Alamathy–Manali line is limiting too. In the model's balancing areas,
these substations lie in:
- Salem: Erode
- Pugalur: Trichy
- Kalivandapattu: Chennai South
- NNTPP and Neyveli Stage 2: Villupuram
- Alamathy–Manali: Chennai North
