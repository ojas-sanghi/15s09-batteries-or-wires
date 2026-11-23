"""
Draw a map of the feeder, with each 12.47 kV line colored by how loaded it got in Step 1.
Lines over their rating are drawn thick and red.

Run after step1.py:   python plot.py 1.3      (the number is the growth level g)
"""

import re
import sys

import matplotlib.pyplot as plt
import pandas as pd


FEEDER_FOLDER = "data/feeder"


def read_bus_coordinates():
    """
    Buscoords.dss has one line per bus:  <bus name> <x> <y>
    (x = longitude, y = latitude). Returns a dictionary: bus name -> (x, y).
    """
    coordinates = {}
    with open(f"{FEEDER_FOLDER}/Buscoords.dss") as f:
        for row in f:
            parts = row.split()
            if len(parts) != 3:
                continue
            bus, x, y = parts
            coordinates[bus.lower()] = (float(x), float(y))
    return coordinates


def read_line_ends():
    """
    Lines.dss has one "New Line..." command per line, for example:
      New Line.l(r:a-b) Units=km Length=0.01 bus1=a.1.2.3 bus2=b.1.2.3 ...
    Returns a dictionary: line name -> (from bus, to bus).
    The ".1.2.3" after a bus name lists its phases, so we cut it off.
    """
    ends = {}
    with open(f"{FEEDER_FOLDER}/Lines.dss") as f:
        for row in f:
            if not row.startswith("New Line."):
                continue
            name = row.split()[1][len("Line.") :].lower()
            bus1 = re.search(r"bus1=(\S+)", row).group(1).split(".")[0].lower()
            bus2 = re.search(r"bus2=(\S+)", row).group(1).split(".")[0].lower()
            ends[name] = (bus1, bus2)
    return ends


def main():
    # growth level to plot (default 1.3)
    if len(sys.argv) > 1:
        g = sys.argv[1]
    else:
        g = "1.3"

    coordinates = read_bus_coordinates()
    line_ends = read_line_ends()
    results = pd.read_csv(f"output/step1_g{g}.csv")  # written by step1.py, 12.47 kV lines only

    fig, ax = plt.subplots(figsize=(8, 7))

    for i in range(len(results)):
        name = results["line"][i]
        loading = results["peak_loading_pct"][i]

        bus1, bus2 = line_ends[name]
        x1, y1 = coordinates[bus1]
        x2, y2 = coordinates[bus2]

        if loading > 100:
            # overloaded: thick red line, drawn on top
            ax.plot([x1, x2], [y1, y2], color="red", linewidth=3, zorder=2)
        else:
            # within rating: grey, darker the more loaded it is
            shade = 0.3 + 0.6 * loading / 100
            ax.plot(
                [x1, x2], [y1, y2], color=plt.cm.Greys(shade), linewidth=1, zorder=1
            )

    ax.set_title(f"Peak line loading at g = {g} (red = over its rating)")
    ax.set_aspect("equal")
    ax.axis("off")

    filename = f"figures/map_g{g}.png"
    plt.savefig(filename, dpi=150, bbox_inches="tight")
    print(f"saved {filename}")


if __name__ == "__main__":
    main()
