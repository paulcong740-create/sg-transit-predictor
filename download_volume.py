# download_volume.py
# Downloads LTA's monthly "passenger volume" data:
#   - how many people tap in / tap out at EVERY bus stop, for every hour, weekdays vs weekends
#   - the same for EVERY MRT/LRT station
# Saved (compressed) into data/volume/, e.g. data/volume/bus_202610.csv.gz
#
#   python download_volume.py           -> downloads the latest month available
#   python download_volume.py 202609    -> downloads a specific month (LTA keeps about the last 3 months)
#
# LTA updates this data once a month, so run it about once a month.

import gzip
import io
import os
import sys
import zipfile

import requests
from dotenv import load_dotenv

load_dotenv()
HEADERS = {"AccountKey": os.getenv("LTA_API_KEY"), "accept": "application/json"}
BASE = "https://datamall2.mytransport.sg/ltaodataservice"
DATASETS = {"bus": "PV/Bus", "train": "PV/Train"}


def download(kind, month=None):
    params = {"Date": month} if month else {}
    r = requests.get(f"{BASE}/{DATASETS[kind]}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    link = r.json()["value"][0]["Link"]          # LTA gives a temporary download link (valid ~5 minutes)

    zipped = requests.get(link, timeout=300)
    zipped.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as z:
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        data = z.read(csv_name)

    # the file name inside the zip contains the month, e.g. transport_node_bus_202610.csv
    digits = "".join(ch for ch in csv_name if ch.isdigit())
    label = month or (digits[-6:] if len(digits) >= 6 else "latest")

    os.makedirs("data/volume", exist_ok=True)
    out = f"data/volume/{kind}_{label}.csv.gz"
    if os.path.exists(out):
        print(f"Already have {out} - nothing new from LTA yet")
        return
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
        f.write(data)
    rows = data.count(b"\n") - 1
    print(f"Saved {out}  ({rows:,} rows)")


if __name__ == "__main__":
    month = sys.argv[1] if len(sys.argv) > 1 else None
    for kind in DATASETS:
        try:
            download(kind, month)
        except Exception as e:
            print(f"Could not download {kind} data: {e}")
