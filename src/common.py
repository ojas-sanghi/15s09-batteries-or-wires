# Things used by more than one script. Run every script from the repo root.

import re

import opendssdirect as dss

GROWTH_LEVELS = [1.0, 1.3]  # load-growth multipliers g we study


def load_feeder():
    """Load the SMART-DS feeder into OpenDSS, with every customer on the same load shape."""

    # Loads.dss gives each customer its own load shape ("yearly=..."). Remove those and
    # save a copy of the file.
    text = open("data/feeder/Loads.dss").read()
    text = re.sub(r"\s+yearly=\S+", "", text)
    open("data/feeder/Loads_oneshape.dss", "w").write(text)

    # Run the commands in Master.dss one by one, except the ones that solve, plot or export,
    # and the one that loads the per-customer load shapes.
    for line in open("data/feeder/Master.dss").read().splitlines():
        lower = line.lower()
        if lower.startswith(("solve", "plot", "export")) or "loadshapes.dss" in lower:
            continue
        line = line.replace("Loads.dss", "Loads_oneshape.dss")
        line = line.replace("Redirect ", "Redirect data/feeder/")
        line = line.replace("Buscoords ", "Buscoords data/feeder/")
        dss.Text.Command(line)

    # one load shape (kW in column 1, kvar in column 2) for every customer
    dss.Text.Command(
        "New Loadshape.feeder npts=8760 interval=1 "
        "mult=(file=data/feeder_shape.csv, col=1) "
        "qmult=(file=data/feeder_shape.csv, col=2)"
    )
    dss.Text.Command("batchedit load..* yearly=feeder")
