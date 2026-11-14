# collect.py
# Collects live bus arrival + MRT/LRT crowd data and saves it into one CSV file per day:
#   data/bus/2026-11-14.csv
#   data/mrt/2026-11-14.csv
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
SINGAPORE_TIME = timezone(timedelta(hours=8))   # GitHub's servers use UTC, so always record Singapore time

# ---- WHAT TO COLLECT ----
# My route:
#   To school : bus 991/451 from 44089, then NSL Bukit Gombak (NS3) -> Jurong East, EWL -> Dover (EW22)
#   Going home: EWL Dover -> Jurong East, NSL -> Choa Chu Kang (NS4), then bus 172 from Choa Chu Kang Int (44009)
MY_STOPS = {
    "44089": "My home stop",
    "44009": "Choa Chu Kang Int",
}

# Major bus interchanges across Singapore (add or remove codes any time)
INTERCHANGES = {
    "28009": "Jurong East Int",
    "22009": "Boon Lay Int",
    "24009": "Joo Koon Int",
    "17009": "Clementi Int",
    "43009": "Bukit Batok Int",
    "45009": "Bukit Panjang Int",
    "46009": "Woodlands Int",
    "58009": "Sembawang Int",
    "59009": "Yishun Int",
    "54009": "Ang Mo Kio Int",
    "53009": "Bishan Int",
    "52009": "Toa Payoh Int",
    "66009": "Serangoon Int",
    "64009": "Hougang Central Int",
    "67009": "Sengkang Int",
    "65009": "Punggol Int",
    "75009": "Tampines Int",
    "84009": "Bedok Int",
    "14009": "HarbourFront Int",
    "10009": "Bukit Merah Int",
}

BUS_STOPS = {**MY_STOPS, **INTERCHANGES}

# Every MRT/LRT line that LTA reports platform crowd levels for
TRAIN_LINES = ["NSL", "EWL", "CGL", "NEL", "CCL", "CEL", "DTL", "TEL", "BPL", "SLRT", "PLRT"]

INTERVAL_MINUTES = 3           # laptop mode only
# --------------------------

BUS_HEADER = ["time", "bus_stop", "service", "wait_minutes", "load", "bus_type", "monitored"]
MRT_HEADER = ["time", "line", "station", "crowd_level"]


def save_rows(folder, now, header, rows):
    """Append rows to today's CSV file, e.g. data/bus/2026-11-14.csv."""
    os.makedirs(folder, exist_ok=True)
    filename = os.path.join(folder, f"{now:%Y-%m-%d}.csv")
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
    rows, failed = [], []
    for stop in BUS_STOPS:
        try:
            r = requests.get(f"{BASE}/v3/BusArrival", headers=HEADERS,
                             params={"BusStopCode": stop}, timeout=20)
            r.raise_for_status()
        except Exception:
            failed.append(stop)          # skip this stop this round, keep going with the rest
            continue
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
    save_rows("data/bus", now, BUS_HEADER, rows)
    return len(rows), failed


def collect_mrt(now):
    rows, failed = [], []
    for line in TRAIN_LINES:
        try:
            r = requests.get(f"{BASE}/PCDRealTime", headers=HEADERS,
                             params={"TrainLine": line}, timeout=20)
            r.raise_for_status()
        except Exception:
            failed.append(line)
            continue
        for s in r.json()["value"]:      # CrowdLevel: l = low, m = moderate, h = high
            rows.append([now.isoformat(timespec="seconds"), line, s["Station"], s["CrowdLevel"]])
    save_rows("data/mrt", now, MRT_HEADER, rows)
    return len(rows), failed


def collect_once():
    now = datetime.now(SINGAPORE_TIME)
    b, bus_failed = collect_buses(now)
    m, mrt_failed = collect_mrt(now)
    print(f"{now:%Y-%m-%d %H:%M:%S}  saved {b} bus rows, {m} MRT rows")
    if bus_failed or mrt_failed:
        print("  could not reach:", bus_failed + mrt_failed)
    if b == 0 and m == 0:
        raise RuntimeError("Nothing collected - check the API key")   # makes the GitHub run show red


if __name__ == "__main__":
    if "--once" in sys.argv:
        collect_once()   # cloud mode: one round, then exit
    else:
        print("Collector started. Press Ctrl + C to stop.")
        while True:
            try:
                collect_once()
            except Exception as e:
                print(f"error: {e} (will retry)")
            time.sleep(INTERVAL_MINUTES * 60)
