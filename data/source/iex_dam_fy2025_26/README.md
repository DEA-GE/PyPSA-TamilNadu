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

## Tamil Nadu area price (15-minute)

`iex_dam_tamil_nadu_15min_fy2025_26.csv` holds IEX's Day-Ahead Market
clearing price for Tamil Nadu's bid area in 15-minute blocks.

| Column | Meaning |
|---|---|
| `block_start` | Start of the 15-minute block, IST |
| `date`, `block` | Delivery date and block number (1 = 00:00–00:15) |
| `cleared_buy_mw`, `cleared_sell_mw` | Cleared buy and sell, as given in the export |
| `price_inr_per_mwh` | Area clearing price (INR/MWh) |

The source is the IEX website's area-price export for Tamil Nadu: CSV files
named `IEX Price*.csv`, one per month, downloaded 24 November 2026. The
exports do not name the bid area or market. To rebuild:

```text
python scripts/data_preparation/prepare_iex_tn_area_prices.py --import-from <folder>
```

- **Incomplete:** 352 of 365 days are present. Missing: 31 May, 31 Jul,
  31 Aug, 31 Oct, 29–31 Dec 2025, 29–31 Jan and 29–31 Mar 2026. The source
  exports stopped early. Add exports for these days and rerun the script;
  `--require-complete` makes a gap an error.
- **Check (24 November 2026):** averaged to hours, the area price equals the
  all-India MCP above to within 1 INR/MWh in 97.7% of the 8,448 overlapping
  hours. It differs in 197 hours: 148 in July 2025, the remainder in Jun, Aug,
  Sep 2025 and Mar 2026. These are the months CERC reports DAM congestion. In
  those hours the Tamil Nadu price is mostly lower, by up to 6,376 INR/MWh,
  so the southern bid area cleared below the rest of the market.
