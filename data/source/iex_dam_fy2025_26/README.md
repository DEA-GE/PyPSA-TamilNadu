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
