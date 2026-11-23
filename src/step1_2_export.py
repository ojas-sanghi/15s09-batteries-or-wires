"""
Proposal Step 1, part 2: export the feeder as two simple tables for the optimization (Step 2).

The optimization does not run OpenDSS. It needs the feeder written down as numbers:

  output/network_lines.csv   one row per 12.47 kV line:
        line, from_bus (side nearer the substation), to_bus, phases, length_km,
        r_ohm, x_ohm (resistance and reactance of the whole line), rating_MVA

  output/network_buses.csv   one row per 12.47 kV bus:
        bus, parent_bus (next bus toward the substation), three_phase (True/False),
        peak_P_MW, peak_Q_MVAr  (sum of the peaks of all customers fed from this bus)

Hourly load at a bus = g x peak_P_MW x (kW multiplier from feeder_shape.csv for that hour),
because every customer follows the same shape.

Run from the repo root:  python src/step1_2_export.py
"""

from collections import deque

import numpy as np
import opendssdirect as dss
import pandas as pd

from step1_1_screen import PRIMARY_VOLTAGE_KV, load_feeder

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def bus_name(terminal):
    """OpenDSS writes a connection as 'busname.1.2.3' (bus + phases). Keep only the bus."""
    return terminal.split(".")[0].lower()


# Voltage level and number of phases of every bus. Filled in once by read_bus_info(),
# because asking OpenDSS bus by bus is slow and fails on names it can't find.
BUS_KV = {}  # bus -> line-to-neutral base voltage (kV)
BUS_PHASES = {}  # bus -> number of phases (nodes) at the bus


def read_bus_info():
    """Go through every bus in the circuit once and remember its voltage and phase count."""
    for i in range(dss.Circuit.NumBuses()):
        dss.Circuit.SetActiveBusi(i)
        name = dss.Bus.Name().lower()
        BUS_KV[name] = dss.Bus.kVBase()
        BUS_PHASES[name] = dss.Bus.NumNodes()


def is_primary_bus(bus):
    """True if the bus is on the 12.47 kV system (7.2 kV line-to-neutral)."""
    return abs(BUS_KV.get(bus, 0) - PRIMARY_VOLTAGE_KV) < 0.5


def number_of_phases_at(bus):
    return BUS_PHASES.get(bus, 0)


def line_impedance_ohms(phases):
    """
    Resistance and reactance (ohms) of the active line, as one number each.

    OpenDSS stores a line as a matrix of impedances per phase (ohms per km):
    the diagonal is each phase's own impedance ("self"), the rest is the coupling
    between phases ("mutual"). For the balanced single-phase model in the proposal we use
        3-phase line:  Z = Z_self - Z_mutual        (the usual positive-sequence value)
        2-phase line:  Z = 1.5 x (Z_self - Z_mutual)
        1-phase line:  Z = 3 x Z_self
    The factor 3/phases is there because the model sends the line's total power through it,
    while a 1- or 2-phase line carries that power on fewer wires.
    """
    R = np.array(dss.Lines.RMatrix()).reshape(phases, phases)
    X = np.array(dss.Lines.XMatrix()).reshape(phases, phases)
    length = dss.Lines.Length()

    r_self = np.mean(np.diag(R))
    x_self = np.mean(np.diag(X))
    if phases == 1:
        r_per_length = 3 * r_self
        x_per_length = 3 * x_self
    else:
        off_diagonal = ~np.eye(phases, dtype=bool)
        r_mutual = np.mean(R[off_diagonal])
        x_mutual = np.mean(X[off_diagonal])
        r_per_length = (3 / phases) * (r_self - r_mutual)
        x_per_length = (3 / phases) * (x_self - x_mutual)

    return r_per_length * length, x_per_length * length


# ----------------------------------------------------------------------------
# Part 1: how is everything connected?
# ----------------------------------------------------------------------------


def find_parents():
    """
    Walk the whole circuit outward from the substation (breadth-first search) and record,
    for every bus, the bus one step closer to the substation ("parent").
    Uses both lines and transformers, so customers on the low-voltage side are reached too.
    Returns: parent (dict bus -> parent bus), and which element joins them (dict bus -> element).
    """
    neighbours = {}  # bus -> list of (other bus, element name)

    def connect(a, b, element):
        neighbours.setdefault(a, []).append((b, element))
        neighbours.setdefault(b, []).append((a, element))

    for name in dss.Lines.AllNames():
        dss.Lines.Name(name)
        connect(bus_name(dss.Lines.Bus1()), bus_name(dss.Lines.Bus2()), "line." + name)

    for name in dss.Transformers.AllNames():
        dss.Transformers.Name(name)
        buses = dss.CktElement.BusNames()
        for other in buses[1:]:
            connect(bus_name(buses[0]), bus_name(other), "transformer." + name)

    dss.Vsources.First()
    substation = bus_name(dss.CktElement.BusNames()[0])

    parent = {substation: None}
    joined_by = {substation: None}
    queue = deque([substation])
    while queue:
        bus = queue.popleft()
        for other, element in neighbours.get(bus, []):
            if other not in parent:
                parent[other] = bus
                joined_by[other] = element
                queue.append(other)
    return substation, parent, joined_by


# ----------------------------------------------------------------------------
# Part 2: the line table
# ----------------------------------------------------------------------------


def make_line_table(parent, joined_by):
    rows = []
    for bus, element in joined_by.items():
        if element is None or not element.startswith("line."):
            continue
        if not (is_primary_bus(bus) and is_primary_bus(parent[bus])):
            continue  # low-voltage customer wiring: not in the model

        name = element[len("line.") :]
        dss.Lines.Name(name)
        phases = dss.Lines.Phases()
        r, x = line_impedance_ohms(phases)
        rows.append(
            {
                "line": name,
                "from_bus": parent[bus],
                "to_bus": bus,
                "phases": phases,
                "length_km": dss.Lines.Length(),
                "linecode": dss.Lines.LineCode(),
                "r_ohm": r,
                "x_ohm": x,
                # MVA rating = phases x 7.2 kV x amps / 1000, same as in step1_1_screen.py
                "rating_MVA": phases * PRIMARY_VOLTAGE_KV * dss.Lines.NormAmps() / 1000,
            }
        )
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Part 3: the bus table, with each customer's load moved onto its 12.47 kV bus
# ----------------------------------------------------------------------------


def make_bus_table(substation, parent):
    primary_buses = [bus for bus in parent if is_primary_bus(bus)]
    peak_P = {bus: 0.0 for bus in primary_buses}
    peak_Q = {bus: 0.0 for bus in primary_buses}

    # Each customer is connected on the low-voltage side. Walk up toward the substation
    # until we reach a 12.47 kV bus (the high side of the customer's transformer).
    for name in dss.Loads.AllNames():
        dss.Loads.Name(name)
        bus = bus_name(dss.CktElement.BusNames()[0])
        while bus not in peak_P:
            bus = parent[bus]
        peak_P[bus] += dss.Loads.kW() / 1000  # MW
        peak_Q[bus] += dss.Loads.kvar() / 1000  # MVAr

    rows = []
    for bus in primary_buses:
        rows.append(
            {
                "bus": bus,
                "parent_bus": parent[bus] if bus != substation else "",
                "three_phase": number_of_phases_at(bus) >= 3,
                "peak_P_MW": peak_P[bus],
                "peak_Q_MVAr": peak_Q[bus],
            }
        )
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main():
    load_feeder()
    # bus voltages are only known after OpenDSS has worked out the circuit once
    dss.Text.Command("calcvoltagebases")
    read_bus_info()
    substation, parent, joined_by = find_parents()

    lines = make_line_table(parent, joined_by)
    buses = make_bus_table(substation, parent)

    lines.to_csv("output/network_lines.csv", index=False)
    buses.to_csv("output/network_buses.csv", index=False)

    print(f"substation bus: {substation}")
    print(f"{len(lines)} lines and {len(buses)} buses on the 12.47 kV system")
    print(f"three-phase buses: {buses['three_phase'].sum()}")
    print(
        f"sum of customer peaks: {buses['peak_P_MW'].sum():.2f} MW, {buses['peak_Q_MVAr'].sum():.2f} MVAr"
    )


if __name__ == "__main__":
    main()
