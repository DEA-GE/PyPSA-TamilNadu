from pathlib import Path
import sys

import matplotlib.pyplot as plt
import pandas as pd


run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "results/run_results/rolling_2025-04-01_2026-03-31_none_uc/LP_solar_availability"
)
log = pd.read_csv(run_dir / "window_log.csv")
values = log["peak_cpu_cores_sampled"].dropna()

fig, ax = plt.subplots(figsize=(8, 5))
ax.hist(values, bins=10, color="#4472C4", edgecolor="white")
ax.axvline(values.mean(), color="#C00000", linestyle="--", linewidth=1.5,
           label=f"Mean: {values.mean():.2f} cores")
ax.axvline(values.max(), color="#70AD47", linestyle=":", linewidth=1.8,
           label=f"Maximum: {values.max():.2f} cores")
ax.set_title(f"{run_dir.name}: Weekly Peak CPU Core Usage")
ax.set_xlabel("Peak sampled CPU cores")
ax.set_ylabel("Number of weekly solves")
ax.grid(axis="y", alpha=0.25)
ax.legend()
fig.tight_layout()
output_path = run_dir / "peak_cpu_cores_histogram.png"
fig.savefig(output_path, dpi=180)
plt.close(fig)

print(f"Saved {output_path}")
