# IEX Day-Ahead Market hourly prices — FY2025–26

`iex_dam_hourly_fy2025_26.csv` holds the Indian Energy Exchange (IEX)
Day-Ahead Market result for every hour from 1 April 2025 to 31 March 2026
(8,760 rows). It is used to compare the model's marginal prices with observed
exchange prices.

| Column | Meaning |
|---|---|
| `timestamp` | Start of the hour, IST (matches the model snapshots) |
| `date`, `hour` | IEX delivery date and hour number (1 = 00:00–01:00) |
| `purchase_bid_mwh`, `sell_bid_mwh` | Total buy and sell bids |
| `mcv_mwh` | Market clearing volume |
| `final_scheduled_volume_mwh` | Volume finally scheduled |
| `mcp_inr_per_mwh` | Market clearing price (INR/MWh) |
| `weighted_mcp_inr_per_mwh` | IEX's volume-weighted MCP over the hour's 15-minute blocks |

## Source

IEX website → Market Data → Day-Ahead Market → Market Snapshot, hourly
interval, one export per calendar month (downloaded 24 November 2026). The
raw `.xlsx` exports were not kept. To rebuild the CSV from new downloads:

```text
python scripts/data_preparation/prepare_iex_dam_prices.py --import-from <folder with the exports>
```

The script ignores other workbooks in the folder and exports outside FY2025–26.
It skips exact duplicates and refuses overlapping exports whose data differ.
It requires all 8,760 hours, and it checks each day's hourly MCP against the
export's own daily "Avg" row.

## Notes

- The price is the all-India MCP. During DAM congestion (market splitting) the
  bid areas can clear at different prices, but the CERC monthly market reports
  (Table 38) show this happened in at most 5.4% of hours in any FY2025–26
  month, and in none in most months.
- The price ceiling is 10,000 INR/MWh.
- Checks (24 November 2026): the simple mean MCP is 3,861 INR/MWh, matching
  IEX's published FY26 DAM average of 3.86 Rs/kWh. The monthly
  volume-weighted MCP matches CERC's "Short-term transactions" Table-5 IEX DAM
  weighted average to within 15 INR/MWh in every month.
- The volume-weighted price is lower than the hourly mean (3,713 vs 3,861
  INR/MWh for the year), because traded volume is largest in the cheap
  solar hours.

## Tamil Nadu area price (hourly)

`iex_dam_tamil_nadu_hourly_fy2025_26.csv` holds IEX's Day-Ahead Market
clearing price for Tamil Nadu's bid area, one row per hour (8,760 rows).

| Column | Meaning |
|---|---|
| `timestamp` | Start of the hour, IST (matches the model snapshots) |
| `cleared_buy_mw`, `cleared_sell_mw` | Cleared buy and sell, as given in the export, averaged over the hour |
| `price_inr_per_mwh` | Area clearing price (INR/MWh), mean of the hour's four 15-minute blocks |

The source is the IEX website's area-price export for Tamil Nadu: CSV files
named `IEX Price*.csv` in 15-minute blocks, one per month, downloaded 24
November 2026. The exports do not name the bid area or market. To rebuild:

```text
python scripts/data_preparation/prepare_iex_tn_area_prices.py --import-from <folder>
```

The script combines all `IEX Price*.csv` files in the folder and averages
each hour's blocks, so every hour has a single value. It keeps FY2025–26
and reports gaps: missing days grouped by month, and days with only some
hours missing. All 8,760 hours are present.

- **Check (24 November 2026):** the hourly area price equals the all-India
  MCP above to within 1 INR/MWh in 97.7% of the 8,760 hours. It differs in
  202 hours, all in months where CERC reports DAM congestion: Jun 6, Jul 148,
  Aug 16, Sep 21, Mar 11. In 146 of those hours Tamil Nadu is lower (by up to
  6,376 INR/MWh), so the southern bid area cleared below the rest of the
  market. In 56 it is higher (by up to 732). The annual mean is 3,848 INR/MWh
  vs 3,861 all-India.

## Tamil Nadu green DAM and real-time market prices (hourly)

`iex_gdam_tamil_nadu_hourly_fy2025_26.csv` (Green Day-Ahead Market, G-DAM)
and `iex_rtm_tamil_nadu_hourly_fy2025_26.csv` (Real-Time Market) hold IEX's
results for Tamil Nadu's bid area, in the same columns as the DAM area price
above. The source is the same area-price export (`IEX Price*.csv`, 15-minute
blocks, one file per month) with the market selected, downloaded 25 November
2026. The exports do not name the market, so each market is merged from its
own folder. The script stops if two exports in a folder disagree on any block.

```text
python scripts/data_preparation/prepare_iex_tn_area_prices.py --import-from <folder> --market gdam
python scripts/data_preparation/prepare_iex_tn_area_prices.py --import-from <folder> --market rtm
```

The market of each export was identified from its cleared volumes. Hourly
cleared buy and sell are compared with Tamil Nadu's trades per market in the
SRPC REA (`data/model_inputs/tn_exchange_trades_FY2025_26.csv`); the REA is
measured at the regional periphery, so IEX volumes run somewhat higher.

**Gaps.** Green DAM: 8,736 of 8,760 hours, with 31 October 2025 missing. RTM:
8,016 of 8,760 hours, with 31 May 2025 and 1–30 December 2025 missing.

**Green DAM checks (25 November 2026).**
- Cleared buy vs REA `gdam_import_mw`: r = 0.99. IEX is 4–10% above the REA
  in each month, except July at 24%.
- Tamil Nadu only buys in the green DAM: 0.85 TWh in FY2025-26 per the REA.
- The green DAM price averages 4,363 INR/MWh against 3,852 for the DAM in
  the same hours, a green premium of 510. The premium is largest at night
  (600–880) and smallest at midday (210–260).
- Weighted by Tamil Nadu's purchases, which peak at 06–14h, it is 3,212
  against 2,765 DAM (+447).
- It clears below the DAM in 18% of hours and reaches the 10,000 ceiling in
  812 hours.

**RTM checks (25 November 2026).**
- Cleared buy vs REA `rtm_import_mw`: r = 0.99, with IEX 4–15% above the REA
  in each month.
- Cleared sell vs REA `rtm_export_mw`: r = 0.99, with IEX 11–25% above.
- The RTM price averages 3,537 INR/MWh against 3,848 for the DAM (−311);
  the hourly correlation is r = 0.81.
- The RTM is above the DAM at midday (+90 to +400 at 09–16h) and well
  below it in the evening and at night (−980 to −1,290 at 18–23h).
- Weighted by Tamil Nadu's trades, its RTM sales (4.10 TWh) fetched 2,596
  against 2,870 DAM in the same hours, and its RTM purchases (1.63 TWh) cost
  4,013 against 4,477.
