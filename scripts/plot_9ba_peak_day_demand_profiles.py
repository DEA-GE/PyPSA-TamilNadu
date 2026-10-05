"""Plot the nine balancing-area demand profiles on the statewide peak day."""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pypsa


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "9_BA" / "model"
OUTPUT_DIR = ROOT / "9_BA" / "demand_profile_analysis"


def main() -> None:
    network = pypsa.Network(MODEL_DIR)
    area_by_load = network.loads.bus.to_dict()
    demand = network.loads_t.p_set.rename(columns=area_by_load)
    statewide = demand.sum(axis=1)
    peak_hour = pd.Timestamp(statewide.idxmax())
    peak_day = peak_hour.normalize()
    day_index = network.snapshots[network.snapshots.normalize() == peak_day]
    if len(day_index) != 24:
        raise ValueError(f"Expected 24 hours on {peak_day.date()}, found {len(day_index)}")

    daily = demand.loc[day_index]
    daily_statewide = statewide.loc[day_index]
    normalized = daily.div(daily.mean(axis=0), axis=1)
    normalized_statewide = daily_statewide / daily_statewide.mean()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tidy = daily.rename_axis("timestamp").reset_index().melt(
        id_vars="timestamp", var_name="balancing_area", value_name="demand_mw"
    )
    normalized_tidy = normalized.rename_axis("timestamp").reset_index().melt(
        id_vars="timestamp", var_name="balancing_area", value_name="relative_to_daily_mean"
    )
    tidy = tidy.merge(normalized_tidy, on=["timestamp", "balancing_area"])
    tidy.to_csv(OUTPUT_DIR / "state_peak_day_demand_profiles.csv", index=False)

    colors = plt.get_cmap("tab10").colors
    fig, (absolute_axis, shape_axis) = plt.subplots(2, 1, figsize=(15, 11), sharex=True)
    for position, area in enumerate(daily.columns):
        color = colors[position % len(colors)]
        absolute_axis.plot(day_index, daily[area], label=area, color=color, linewidth=2)
        shape_axis.plot(day_index, normalized[area], label=area, color=color, linewidth=2)

    absolute_axis.set_title("Absolute balancing-area demand")
    absolute_axis.set_ylabel("Demand (MW)")
    absolute_axis.grid(alpha=0.25)
    absolute_axis.axvline(peak_hour, color="black", linestyle=":", linewidth=1.5)
    absolute_axis.text(
        peak_hour,
        absolute_axis.get_ylim()[1],
        f" State peak: {peak_hour:%H:%M}\n {statewide.loc[peak_hour]:,.0f} MW",
        ha="left",
        va="top",
    )

    shape_axis.plot(
        day_index,
        normalized_statewide,
        label="Tamil Nadu total",
        color="black",
        linewidth=3,
        linestyle="--",
    )
    shape_axis.set_title("Profile shape after normalising each series by its daily mean")
    shape_axis.set_ylabel("Multiple of daily mean")
    shape_axis.set_xlabel("Hour")
    shape_axis.grid(alpha=0.25)
    shape_axis.axvline(peak_hour, color="black", linestyle=":", linewidth=1.5)

    handles, labels = shape_axis.get_legend_handles_labels()
    fig.legend(handles, labels, loc="center left", bbox_to_anchor=(0.91, 0.5), frameon=False)
    fig.suptitle(
        f"Nine-balancing-area demand profiles on Tamil Nadu peak day ({peak_day:%d %B %Y})",
        fontsize=16,
    )
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0, 0.9, 0.96))
    output_path = OUTPUT_DIR / "state_peak_day_demand_profiles.png"
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)

    print(f"State peak: {peak_hour} ({statewide.loc[peak_hour]:,.2f} MW)")
    print(f"Plot: {output_path}")
    print(f"Data: {OUTPUT_DIR / 'state_peak_day_demand_profiles.csv'}")


if __name__ == "__main__":
    main()
