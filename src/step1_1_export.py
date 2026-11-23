# Step 1, part 1: write the 12.47 kV part of the feeder as two tables.
#
#   output/network_lines.csv  line, from_bus (side nearer the substation), to_bus, phases,
#                             length_km, linecode, r_ohm, x_ohm, rating_amps, rating_MVA
#   output/network_buses.csv  bus, parent_bus (next bus toward the substation), three_phase,
#                             peak_P_MW, peak_Q_MVAr (customers fed from this bus), x, y
#
# Run first (after step0_1_download.py):  python src/step1_1_export.py

import os

import numpy as np
import opendssdirect as dss
import pandas as pd

from common import load_feeder


def bus_name(terminal):
    # OpenDSS writes "busname.1.2.3" (the bus and its phases); keep only the bus
    return terminal.split(".")[0].lower()


os.makedirs("output", exist_ok=True)
load_feeder()
dss.Text.Command("calcvoltagebases")

# voltage, number of phases and map position of every bus
bus_kv = {}
bus_phases = {}
bus_x = {}
bus_y = {}
for i in range(dss.Circuit.NumBuses()):
    dss.Circuit.SetActiveBusi(i)
    name = dss.Bus.Name().lower()
    bus_kv[name] = dss.Bus.kVBase()
    bus_phases[name] = dss.Bus.NumNodes()
    bus_x[name] = dss.Bus.X()
    bus_y[name] = dss.Bus.Y()


def is_primary(bus):
    # 12.47 kV line-to-line = 7.2 kV line-to-neutral
    return abs(bus_kv.get(bus, 0) - 7.2) < 0.5


# which buses are connected to each other, by a line or a transformer
neighbours = {}


def connect(a, b, element):
    if a not in neighbours:
        neighbours[a] = []
    if b not in neighbours:
        neighbours[b] = []
    neighbours[a].append((b, element))
    neighbours[b].append((a, element))


for name in dss.Lines.AllNames():
    dss.Lines.Name(name)
    connect(bus_name(dss.Lines.Bus1()), bus_name(dss.Lines.Bus2()), name)

for name in dss.Transformers.AllNames():
    dss.Transformers.Name(name)
    buses = dss.CktElement.BusNames()
    for other in buses[1:]:
        connect(bus_name(buses[0]), bus_name(other), "transformer")

# Walk out from the substation (breadth-first) and note each bus's parent: the bus one
# step closer to the substation, and the line that joins them.
dss.Vsources.First()
substation = bus_name(dss.CktElement.BusNames()[0])
parent = {substation: ""}
line_from_parent = {substation: ""}
queue = [substation]
for bus in queue:  # the queue grows while we loop over it
    for other, element in neighbours[bus]:
        if other not in parent:
            parent[other] = bus
            line_from_parent[other] = element
            queue.append(other)

# ---- line table: lines with both ends on 12.47 kV ----
line_rows = []
for bus in parent:
    line = line_from_parent[bus]
    if line == "" or line == "transformer":
        continue
    if not is_primary(bus) or not is_primary(parent[bus]):
        continue

    dss.Lines.Name(line)
    phases = dss.Lines.Phases()
    length = dss.Lines.Length()
    amps = dss.Lines.NormAmps()

    # OpenDSS gives a matrix of ohms per km: each phase's own impedance on the diagonal,
    # the coupling between phases off the diagonal. For the model we need one number:
    #   3-phase: self - mutual,  2-phase: 1.5 x (self - mutual),  1-phase: 3 x self
    # (3 / phases because the model sends all the power through one line, while a
    # 1- or 2-phase line carries it on fewer wires).
    R = np.array(dss.Lines.RMatrix()).reshape(phases, phases)
    X = np.array(dss.Lines.XMatrix()).reshape(phases, phases)
    if phases == 1:
        r = 3 * R[0, 0]
        x = 3 * X[0, 0]
    else:
        r_self = np.trace(R) / phases
        x_self = np.trace(X) / phases
        r_mutual = (R.sum() - np.trace(R)) / (phases * phases - phases)
        x_mutual = (X.sum() - np.trace(X)) / (phases * phases - phases)
        r = 3 / phases * (r_self - r_mutual)
        x = 3 / phases * (x_self - x_mutual)

    line_rows.append(
        {
            "line": line,
            "from_bus": parent[bus],
            "to_bus": bus,
            "phases": phases,
            "length_km": length,
            "linecode": dss.Lines.LineCode(),
            "r_ohm": r * length,
            "x_ohm": x * length,
            "rating_amps": amps,
            "rating_MVA": phases * 7.2 * amps / 1000,
        }
    )

# ---- bus table: each customer's load added to the 12.47 kV bus that feeds it ----
peak_P = {}
peak_Q = {}
for bus in parent:
    if is_primary(bus):
        peak_P[bus] = 0.0
        peak_Q[bus] = 0.0

for name in dss.Loads.AllNames():
    dss.Loads.Name(name)
    bus = bus_name(dss.CktElement.BusNames()[0])
    while bus not in peak_P:  # walk up until we reach a 12.47 kV bus
        bus = parent[bus]
    peak_P[bus] += dss.Loads.kW() / 1000  # MW
    peak_Q[bus] += dss.Loads.kvar() / 1000  # MVAr

bus_rows = []
for bus in peak_P:
    bus_rows.append(
        {
            "bus": bus,
            "parent_bus": parent[bus],
            "three_phase": bus_phases[bus] >= 3,
            "peak_P_MW": peak_P[bus],
            "peak_Q_MVAr": peak_Q[bus],
            "x": bus_x[bus],
            "y": bus_y[bus],
        }
    )

lines = pd.DataFrame(line_rows)
buses = pd.DataFrame(bus_rows)
lines.to_csv("output/network_lines.csv", index=False)
buses.to_csv("output/network_buses.csv", index=False)

print("substation bus:", substation)
print(len(lines), "lines and", len(buses), "buses on the 12.47 kV system")
print("three-phase buses:", buses["three_phase"].sum())
print("sum of customer peaks (MW):", round(buses["peak_P_MW"].sum(), 2))
