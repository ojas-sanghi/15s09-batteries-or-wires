# Map of the feeder, each 12.47 kV line shaded by how loaded it got in the screen.
# Lines over their rating are thick and red.
#
# Run after step1_2_screen.py:  python src/step1_4_plot.py 1.3   (the growth level g)

import sys

import matplotlib.pyplot as plt
import pandas as pd

g = 1.3
if len(sys.argv) > 1:
    g = float(sys.argv[1])

lines = pd.read_csv("output/network_lines.csv")
buses = pd.read_csv("output/network_buses.csv")
screen = pd.read_csv("output/screen.csv")
screen = screen[screen["g"] == g]

x = dict(zip(buses["bus"], buses["x"]))
y = dict(zip(buses["bus"], buses["y"]))
loading = dict(zip(screen["line"], screen["peak_loading_pct"]))

fig, ax = plt.subplots(figsize=(8, 7))
for i in range(len(lines)):
    a = lines["from_bus"][i]
    b = lines["to_bus"][i]
    pct = loading[lines["line"][i]]
    if pct > 100:
        ax.plot([x[a], x[b]], [y[a], y[b]], color="red", linewidth=3, zorder=2)
    else:
        # grey, darker the more loaded it is
        ax.plot([x[a], x[b]], [y[a], y[b]], color=plt.cm.Greys(0.3 + 0.6 * pct / 100), linewidth=1)

ax.set_title(f"Peak line loading at g = {g} (red = over its rating)")
ax.set_aspect("equal")
ax.axis("off")
plt.savefig(f"figures/map_g{g}.png", dpi=150, bbox_inches="tight")
print(f"saved figures/map_g{g}.png")
