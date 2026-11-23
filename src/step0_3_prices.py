"""
Download the hourly electricity price at the substation (price_dt in the Step 2 objective).

We use CAISO day-ahead market (DAM) locational marginal prices (LMPs) for 2025 at the
PG&E default load aggregation point, node DLAP_PGAE-APND, from the public OASIS API
(no login needed).

OASIS returns at most 31 days per request, so we ask month by month and pause a few
seconds between requests. Each answer is a zip file with one CSV inside. The CSV has
several rows per hour, one per price component (LMP_TYPE = LMP total, MCE energy,
MCC congestion, MCL losses, MGHG greenhouse gas). We keep only the total, LMP_TYPE == "LMP".

The prices are put on the same 8760-hour clock as feeder_shape.csv:
    hour of the year = 24 x (day of the year - 1) + hour of the day, in California time.
Daylight saving time, handled simply:
  - in November one clock hour happens twice: we average its two prices,
  - in March one clock hour is skipped: we copy the price of the hour before.

Output: data/prices.csv   one column, 8760 rows, $/MWh, no header (like feeder_shape.csv)

Run from the repo root:  python src/step0_3_prices.py
"""

import io
import ssl
import time
import urllib.request
import zipfile

import certifi
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

YEAR = 2025
NODE = "DLAP_PGAE-APND"  # PG&E default load aggregation point
OASIS_URL = "https://oasis.caiso.com/oasisapi/SingleZip"
PAUSE_SECONDS = 5  # OASIS refuses requests that come too quickly
HOURS_PER_YEAR = 8760  # 2025 is not a leap year
OUTPUT_FILE = "data/prices.csv"


# ----------------------------------------------------------------------------
# Part 1: download one month
# ----------------------------------------------------------------------------


def download_month(month):
    """Download one month of day-ahead prices and return OASIS's CSV as a table."""
    # Each request runs from midnight California time on the 1st of the month to midnight
    # on the 1st of the next month. OASIS counts its 31-day limit in California days, so the
    # request must not spill even one hour into the next month.
    start_california = pd.Timestamp(
        year=YEAR, month=month, day=1, tz="America/Los_Angeles"
    )
    end_california = start_california + pd.DateOffset(months=1)

    # OASIS wants the times in GMT. Midnight in California is 08:00 GMT in winter (PST)
    # and 07:00 GMT in summer (PDT); tz_convert works out which.
    start = start_california.tz_convert("UTC").strftime("%Y%m%dT%H:%M-0000")
    end = end_california.tz_convert("UTC").strftime("%Y%m%dT%H:%M-0000")

    url = (
        f"{OASIS_URL}?queryname=PRC_LMP&market_run_id=DAM&version=12"
        f"&node={NODE}&startdatetime={start}&enddatetime={end}&resultformat=6"
    )
    # OASIS's certificate chain ends at a root that the macOS Python certificate file
    # doesn't include, so check it against certifi's up-to-date list instead
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, context=ssl_context) as response:
        zip_bytes = response.read()

    # The zip holds one file. If OASIS had a problem, that file is an XML error message.
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    file_name = archive.namelist()[0]
    if not file_name.endswith(".csv"):
        message = archive.read(file_name).decode()
        raise RuntimeError(
            f"OASIS sent {file_name} instead of prices for month {month}:\n{message}"
        )

    return pd.read_csv(archive.open(file_name))


# ----------------------------------------------------------------------------
# Part 2: turn the downloaded rows into 8760 hourly prices
# ----------------------------------------------------------------------------


def to_hourly_prices(lmp):
    """One price ($/MWh) for every hour of the year, in California time."""
    # keep only the total price, not its energy / congestion / loss / greenhouse gas parts
    lmp = lmp[lmp["LMP_TYPE"] == "LMP"]

    # the start of each hour, turned from GMT into California time (this knows about DST)
    start_gmt = pd.to_datetime(lmp["INTERVALSTARTTIME_GMT"], utc=True)
    start_california = start_gmt.dt.tz_convert("America/Los_Angeles")

    # add up the prices that land in each hour of the year, and count how many there are
    price_sum = np.zeros(HOURS_PER_YEAR)
    price_count = np.zeros(HOURS_PER_YEAR)
    for hour_start, price in zip(start_california, lmp["MW"]):
        if hour_start.year != YEAR:
            continue  # skip any hours that belong to the year before or after
        hour_of_year = 24 * (hour_start.day_of_year - 1) + hour_start.hour
        price_sum[hour_of_year] += price
        price_count[hour_of_year] += 1

    # average each hour; an hour with no price copies the hour before
    prices = np.zeros(HOURS_PER_YEAR)
    for h in range(HOURS_PER_YEAR):
        if price_count[h] > 0:
            prices[h] = price_sum[h] / price_count[h]
        else:
            prices[h] = prices[h - 1]
            print(f"no price for hour {h} of the year, copied the hour before")

    return prices


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main():
    # download every month, one request at a time
    tables = []
    for month in range(1, 13):
        print(f"downloading {YEAR}-{month:02d}")
        tables.append(download_month(month))
        time.sleep(PAUSE_SECONDS)
    lmp = pd.concat(tables)

    prices = to_hourly_prices(lmp)

    # save one number per line, no header, like feeder_shape.csv
    np.savetxt(OUTPUT_FILE, prices, fmt="%.5f")
    print(
        f"saved {OUTPUT_FILE}: {len(prices)} hours, "
        f"mean ${prices.mean():.2f}/MWh, min ${prices.min():.2f}, max ${prices.max():.2f}"
    )


if __name__ == "__main__":
    main()
