# Tamil Nadu's 400 kV inter-connecting transformers (ICTs)

The transformers from the 765 and 400 kV grid down to Tamil Nadu's 230 and
110 kV networks. They are what limit Tamil Nadu's import capability according
to Grid-India (see `../grid_india_ttc_fy2025_26`). They are the input for
modelling two voltage levels per balancing area.

| File | Content |
|---|---|
| `tn_icts_2023-05.csv` | Every 765/400, 400/230 and 400/110 kV unit in Tamil Nadu and Puducherry on 31 May 2023, as listed in the compendium |
| `tn_ict_additions_2023-06_2026-03.csv` | Units commissioned from June 2023 to March 2026, transcribed by hand, with the source table and row |
| `tn_icts_2026-03.csv` | Units on 31 March 2026, with balancing area, how the area was found, commissioning date and the basis of the MVA rating |
| `../../model_inputs/tn_ict_capacity_by_area_FY2025_26.csv` | Units and MVA per balancing area and voltage ratio, May 2023 and March 2026, with the MVA that rests on assumed ratings |

## Sources

- **Base list:** SRLDC, *Compendium of Southern Region Transmission Elements
  2023* (31 May 2023), section "Inter-Connecting Transformer Details".
  Julius Meier passed it on as `SR_elements_2023_2.pdf`. SRLDC classifies it
  as internal and issues it on request, so the PDF is not in the repository;
  only the extracted Tamil Nadu rows are.
- **Additions:** SRLDC annual compendiums 2023-24 (Table 32), 2024-25 (Table
  63) and 2025-26 (Table 56), "Commissioning of New ICTs / GTs / STs".
  Generator, station and renewable-pooling (230/33 kV) transformers are left
  out.

To rebuild (the first form re-extracts the base from the PDF):

```text
uv run --no-project --with pypdf --with pandas python scripts/data_preparation/prepare_tn_ict_inventory.py --compendium <SR_elements_2023 pdf>
python scripts/data_preparation/prepare_tn_ict_inventory.py
```

## Method

- **Replacements:** a unit whose substation and ICT number are already in the
  base replaces the earlier unit. Trichy ICT-2 went from 315 to 500 MVA, and
  Salem ICT-4 and Sriperumbudur ICT-1 were replaced. A replacement must keep
  the voltage ratio.
- **Assumed ratings:** the 2024-25 table gives no MVA. New units are set to
  the size of the other Tamil Nadu units added in 2023-26: 400/230 kV 500 MVA,
  400/110 kV 200 MVA, 765/400 kV 1,500 MVA. Replacements keep the previous
  rating. These units are marked in `mva_basis`. They total 1,500 MVA at
  400/230 kV, 600 MVA at 400/110 kV and 4,500 MVA at 765/400 kV.
- **Areas** (see `SUBSTATION_AREAS` in the script):
  - `corridors`: where the grid workbook's circuits reach the substation, its
    area is the only one shared by all the corridors those circuits belong to.
    The workbook's `ba_from`/`ba_to` columns are the corridor's two areas, not
    the area of each circuit end.
  - `OSM`: otherwise the substation's location in OpenStreetMap (Julius
    Meier's Overpass export, 13 October 2026), placed in the balancing-area
    polygons.
  - `district`: where neither applies, the district-to-area table.
  - Sholinganallur is in Chennai district but goes to Chennai South, because
    the workbook puts its 230 kV substation (KITZ) there.
- **Check:** for the five 400/230 kV groups that Grid-India names as limiting
  in December 2025 (Salem, Pugalur, Kalivandapattu, NNTPP, NLC Stage 2), the
  inventory gives the declared unit sizes. `tests/test_prepare_tn_ict_inventory.py`
  checks this.

## Result, 31 March 2026 (MVA)

| Area | 400/230 kV | 400/110 kV | 765/400 kV |
|---|---|---|---|
| Chennai North | 3,835 | 1,600 | 3,000 |
| Chennai South | 3,835 | 1,000 | – |
| Coimbatore | 6,095 | 1,000 | – |
| Erode | 1,760 | 600 | – |
| Madurai | 2,890 | 1,200 | – |
| Tirunelveli | 7,150 | 1,400 | 3,000 |
| Trichy | 3,075 | 200 | – |
| Vellore | 2,705 | 400 | 3,000 |
| Villupuram | 1,500 | – | 3,000 |
| **Tamil Nadu** | **32,845** | **7,400** | **12,000** |

Puducherry has 1,130 MVA at 400/230 kV.

Some 400/230 kV units sit at power-station switchyards: NLC Stage 2, NNTPP,
Kudankulam, NTPL and Mettur Stage 3. In Villupuram these are the area's only
400/230 kV units.
