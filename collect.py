# collect.py
# Collects bus arrival + MRT crowd data and saves it to CSV files.
#
#   python collect.py         -> runs forever on your laptop, every few minutes (Ctrl + C to stop)
#   python collect.py --once  -> collects one round and exits (used by GitHub Actions in the cloud)

import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("LTA_API_KEY")
HEADERS = {"AccountKey": API_KEY, "accept": "application/json"}
BASE = "https://datamall2.mytransport.sg/ltaodataservice"

# ---- CHANGE THESE TO MATCH YOUR OWN ROUTE ----
# My route:
#   To school : bus 991/451 from 44089 -> 43579, then NSL Bukit Gombak (NS3) -> Jurong East (NS1/EW24),
#               then EWL Jurong East -> Dover (EW22)
#   Going home: EWL Dover (EW22) -> Jurong East, NSL Jurong East -> Choa Chu Kang (NS4),
#               then bus 172 from Choa Chu Kang Int (44009) -> 44089
BUS_STOPS = ["44089", "44009"]  # 44089 = home stop (to school), 44009 = Choa Chu Kang Int (going home)
TRAIN_LINES = ["NSL", "EWL"]   # any of: NSL, EWL, NEL, CCL, DTL, TEL, CGL, CEL, BPL, SLRT, PLRT
INTERVAL_MINUTES = 3           # how often to collect (don't go lower than 2)
# ----------------------------------------------


def save_rows(filename, header, rows):
    """Append rows to a CSV file, writing the header if the file is new."""
    new_file = not os.path.exists(filename)
    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(header)
        writer.writerows(rows)


def minutes_until(eta_text, now):
    """Turn '2026-11-13T21:15:30+08:00' into minutes from now."""
    if not eta_text:
        return ""
    eta = datetime.fromisoformat(eta_text)
    return round((eta - now).total_seconds() / 60, 1)


def collect_buses(now):
    rows = []
    for stop in BUS_STOPS:
        r = requests.get(f"{BASE}/v3/BusArrival", headers=HEADERS,
                         params={"BusStopCode": stop}, timeout=20)
        r.raise_for_status()
        for service in r.json()["Services"]:
            bus = service["NextBus"]
            rows.append([
                now.isoformat(timespec="seconds"),
                stop,
                service["ServiceNo"],
                minutes_until(bus["EstimatedArrival"], now),
                bus["Load"],        # SEA = seats available, SDA = standing, LSD = limited standing
                bus["Type"],        # SD = single deck, DD = double deck, BD = bendy
                bus["Monitored"],   # 1 = live GPS estimate, 0 = based on schedule
            ])
    save_rows("bus_data.csv",
              ["time", "bus_stop", "service", "wait_minutes", "load", "bus_type", "monitored"],
              rows)
    return len(rows)


def collect_mrt(now):
    rows = []
    for line in TRAIN_LINES:
        r = requests.get(f"{BASE}/PCDRealTime", headers=HEADERS,
                         params={"TrainLine": line}, timeout=20)
        r.raise_for_status()
        for s in r.json()["value"]:      # CrowdLevel: l = low, m = moderate, h = high
            rows.append([now.isoformat(timespec="seconds"), line, s["Station"], s["CrowdLevel"]])
    save_rows("mrt_data.csv", ["time", "line", "station", "crowd_level"], rows)
    return len(rows)


SINGAPORE_TIME = timezone(timedelta(hours=8))  # GitHub's servers use UTC, so always record Singapore time


def collect_once():
    now = datetime.now(SINGAPORE_TIME)
    b = collect_buses(now)
    m = collect_mrt(now)
    print(f"{now:%Y-%m-%d %H:%M:%S}  saved {b} bus rows, {m} MRT rows")


if "--once" in sys.argv:
    collect_once()   # cloud mode: one round, then exit (an error makes the GitHub run show as failed)
else:
    print("Collector started. Press Ctrl + C to stop.")
    while True:
        try:
            collect_once()
        except Exception as e:
            # don't crash on a bad network moment; just try again next round
            print(f"error: {e} (will retry)")
        time.sleep(INTERVAL_MINUTES * 60)
