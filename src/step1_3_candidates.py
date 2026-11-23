# Step 1, part 3: the candidate lists for the optimization.
#
#   U    = lines that may be upgraded: every line that went over its rating in the screen.
#   B(g) = buses where a battery may go: every bus with an overloaded line on its path back
#          to the substation. (A battery only helps a line that carries its power, so it
#          has to sit downstream of the line.)
#
#   output/upgrades.csv            U, one row per line per g
#   output/battery_sites.csv       B(g), one row per bus per g
#   output/candidates_summary.csv  how many of each, per g
#
# Run after step1_2_screen.py:  python src/step1_3_candidates.py

import pandas as pd

from common import GROWTH_LEVELS

lines = pd.read_csv("output/network_lines.csv")
buses = pd.read_csv("output/network_buses.csv", keep_default_na=False)
screen = pd.read_csv("output/screen.csv")

parent = dict(zip(buses["bus"], buses["parent_bus"]))  # "" for the substation
line_into = dict(zip(lines["to_bus"], lines["line"]))  # the line coming into each bus

upgrades = []
sites = []
summary = []
for g in GROWTH_LEVELS:
    result = screen[screen["g"] == g]
    overloaded = result[result["hours_above_rating"] > 0]

    # U: the overloaded lines, with their screen results
    u = lines.merge(overloaded[["g", "line", "peak_loading_pct", "hours_above_rating"]])
    upgrades.append(u.sort_values("peak_loading_pct", ascending=False))

    # B(g): walk from each bus back to the substation and collect overloaded lines
    n_sites = 0
    for bus in buses["bus"]:
        relieved = []
        b = bus
        while parent[b] != "":
            if line_into[b] in list(overloaded["line"]):
                relieved.append(line_into[b])
            b = parent[b]
        if len(relieved) > 0:
            sites.append(
                {
                    "g": g,
                    "bus": bus,
                    "overloaded_lines_upstream": len(relieved),
                    "lines_it_could_relieve": " ".join(relieved),
                }
            )
            n_sites += 1

    row = {
        "g": g,
        "lines_to_upgrade_U": len(u),
        "km_of_line_in_U": round(u["length_km"].sum(), 2),
        "battery_sites_B": n_sites,
    }
    summary.append(row)
    print(row)

pd.concat(upgrades).to_csv("output/upgrades.csv", index=False)
pd.DataFrame(sites).to_csv("output/battery_sites.csv", index=False)
pd.DataFrame(summary).to_csv("output/candidates_summary.csv", index=False)
