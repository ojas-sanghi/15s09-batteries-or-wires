"""
Proposal Step 1, last part: the candidate lists for the optimization (Step 2).

For each load-growth level g we write down two lists:

  U    = lines that may be upgraded: every 12.47 kV line that went over its rating
         at least once in the screen (step1_1_screen.py).
  B(g) = buses where a battery may be placed: every three-phase 12.47 kV bus that has
         an overloaded line somewhere on its path back to the substation.
         (A battery only helps an overloaded line if the line carries the battery's power,
         which happens when the battery sits downstream of it, i.e. further from the substation.
         We keep only three-phase buses because batteries of this size connect on all three phases.)

Inputs  (made by the earlier steps):
  output/step1_g{g}.csv         from step1_1_screen.py
  output/network_lines.csv      from step1_2_export.py
  output/network_buses.csv      from step1_2_export.py

Outputs:
  output/upgrades_g{g}.csv      the set U, one row per line
  output/battery_sites_g{g}.csv the set B(g), one row per bus
  output/candidates_summary.csv how many of each, per g

No OpenDSS needed. Run from the repo root:  python src/step1_3_candidates.py
"""

import pandas as pd

from step1_1_screen import GROWTH_LEVELS

# ----------------------------------------------------------------------------
# Part 1: read the network
# ----------------------------------------------------------------------------


def read_network():
    lines = pd.read_csv("output/network_lines.csv")
    buses = pd.read_csv("output/network_buses.csv", keep_default_na=False)

    # parent_bus is "" for the substation, which has no parent
    parent = {}
    for i in range(len(buses)):
        parent[buses["bus"][i]] = buses["parent_bus"][i]

    # Every bus except the substation is reached by exactly one line from its parent.
    # Remember which line that is, so we can walk from a bus back to the substation line by line.
    line_into_bus = {}
    for i in range(len(lines)):
        line_into_bus[lines["to_bus"][i]] = lines["line"][i]

    return lines, buses, parent, line_into_bus


def lines_on_path_to_substation(bus, parent, line_into_bus):
    """List of the lines crossed when walking from `bus` back to the substation."""
    path = []
    while parent[bus] != "":
        path.append(line_into_bus[bus])
        bus = parent[bus]
    return path


# ----------------------------------------------------------------------------
# Part 2: the candidates for one growth level
# ----------------------------------------------------------------------------


def find_candidates(g, lines, buses, parent, line_into_bus):
    screen = pd.read_csv(f"output/step1_g{g}.csv")

    # --- U: lines over their rating at least one hour ---
    overloaded = screen[screen["hours_above_rating"] > 0]
    overloaded_names = set(overloaded["line"])

    upgrades = lines[lines["line"].isin(overloaded_names)]
    # add the screen results (how loaded, for how long) next to each line
    upgrades = upgrades.merge(
        overloaded[["line", "peak_loading_pct", "hours_above_rating"]], on="line"
    )
    upgrades = upgrades.sort_values("peak_loading_pct", ascending=False)

    # --- B(g): three-phase buses below at least one overloaded line ---
    rows = []
    for i in range(len(buses)):
        bus = buses["bus"][i]
        if not buses["three_phase"][i]:
            continue

        path = lines_on_path_to_substation(bus, parent, line_into_bus)
        relieved = []
        for line in path:
            if line in overloaded_names:
                relieved.append(line)

        if len(relieved) > 0:
            rows.append(
                {
                    "bus": bus,
                    "overloaded_lines_upstream": len(relieved),
                    "lines_it_could_relieve": " ".join(relieved),
                }
            )
    battery_sites = pd.DataFrame(
        rows, columns=["bus", "overloaded_lines_upstream", "lines_it_could_relieve"]
    )

    return upgrades, battery_sites


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main():
    lines, buses, parent, line_into_bus = read_network()

    summary = []
    for g in GROWTH_LEVELS:
        upgrades, battery_sites = find_candidates(
            g, lines, buses, parent, line_into_bus
        )
        upgrades.to_csv(f"output/upgrades_g{g}.csv", index=False)
        battery_sites.to_csv(f"output/battery_sites_g{g}.csv", index=False)

        result = {
            "g": g,
            "lines_to_upgrade_U": len(upgrades),
            "km_of_line_in_U": round(upgrades["length_km"].sum(), 2),
            "battery_sites_B": len(battery_sites),
        }
        summary.append(result)
        print(result)

    pd.DataFrame(summary).to_csv("output/candidates_summary.csv", index=False)


if __name__ == "__main__":
    main()
