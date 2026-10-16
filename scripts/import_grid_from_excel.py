"""Replace the built model's transmission grid from the authoritative workbook."""

from __future__ import annotations

import pandas as pd

from build_9ba_model import OUTPUT_DIR, read_grid


def main() -> None:
    grid_buses, grid_lines, grid_links, grid_metadata = read_grid()

    existing_buses = pd.read_csv(OUTPUT_DIR / "buses.csv")
    existing_links = pd.read_csv(OUTPUT_DIR / "links.csv")
    existing_metadata = pd.read_csv(OUTPUT_DIR / "transmission_capacity_metadata.csv")

    old_grid_names = set(existing_metadata["name"])
    hydraulic_links = existing_links.loc[
        ~existing_links["name"].isin(old_grid_names)
    ].copy()
    old_grid_buses = set(existing_metadata["bus0"]) | set(existing_metadata["bus1"])
    non_grid_buses = existing_buses.loc[
        ~existing_buses["name"].isin(old_grid_buses)
    ].copy()

    bus_columns = list(dict.fromkeys([*grid_buses.columns, *non_grid_buses.columns]))
    pd.concat(
        [grid_buses.reindex(columns=bus_columns), non_grid_buses.reindex(columns=bus_columns)],
        ignore_index=True,
    ).to_csv(OUTPUT_DIR / "buses.csv", index=False)

    link_columns = list(dict.fromkeys([*grid_links.columns, *hydraulic_links.columns]))
    pd.concat(
        [grid_links.reindex(columns=link_columns), hydraulic_links.reindex(columns=link_columns)],
        ignore_index=True,
    ).to_csv(OUTPUT_DIR / "links.csv", index=False)

    grid_lines.to_csv(OUTPUT_DIR / "lines.csv", index=False)
    grid_metadata.to_csv(OUTPUT_DIR / "transmission_capacity_metadata.csv", index=False)

    carriers_path = OUTPUT_DIR / "carriers.csv"
    carriers = pd.read_csv(carriers_path)
    missing_carriers = sorted(
        (set(grid_lines["carrier"]) | set(grid_links["carrier"]))
        - set(carriers["name"])
    )
    if missing_carriers:
        carriers = pd.concat(
            [
                carriers,
                pd.DataFrame(
                    {
                        "name": missing_carriers,
                        "nice_name": [name.replace("_", " ") for name in missing_carriers],
                    }
                ),
            ],
            ignore_index=True,
        )
        carriers.to_csv(carriers_path, index=False)

    print(
        f"Imported {len(grid_buses)} grid buses, {len(grid_lines)} internal lines, "
        f"and {len(grid_links)} interconnector links into {OUTPUT_DIR}"
    )


if __name__ == "__main__":
    main()
