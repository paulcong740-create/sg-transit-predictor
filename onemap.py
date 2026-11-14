# onemap.py
# Talks to OneMap (Singapore Land Authority's free map service):
#   - search(): turn a postal code / address / place name into coordinates
#   - plan_route(): get public transport routes between two points

import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
ONEMAP_EMAIL = os.getenv("ONEMAP_EMAIL")
ONEMAP_PASSWORD = os.getenv("ONEMAP_PASSWORD")

BASE = "https://www.onemap.gov.sg/api"
SG = timezone(timedelta(hours=8))

_token = {"value": None, "expires": 0}


def get_token():
    """OneMap tokens expire, so we get a new one automatically when needed."""
    if _token["value"] and time.time() < _token["expires"] - 300:
        return _token["value"]
    r = requests.post(f"{BASE}/auth/post/getToken", timeout=15,
                      json={"email": ONEMAP_EMAIL, "password": ONEMAP_PASSWORD})
    r.raise_for_status()
    data = r.json()
    _token["value"] = data["access_token"]
    try:
        _token["expires"] = float(data.get("expiry_timestamp", 0))
    except (TypeError, ValueError):
        _token["expires"] = 0
    if _token["expires"] <= time.time():
        _token["expires"] = time.time() + 2 * 3600   # if unsure, refresh every 2 hours
    return _token["value"]


def nice_name(name):
    """'DOVER MRT STATION (EW22)' -> 'Dover MRT Station (EW22)'"""
    t = str(name).title()
    t = re.sub(r"\b(Mrt|Lrt|Nil)\b", lambda m: m.group(1).upper(), t)
    t = re.sub(r"\b([A-Za-z]{2}\d{1,2})\b", lambda m: m.group(1).upper(), t)
    return t


def search(text):
    """Returns the best match for a postal code / address / place name, or None."""
    params = {"searchVal": text, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1}
    try:
        headers = {"Authorization": get_token()}
    except Exception as e:
        print("OneMap login failed (check ONEMAP_EMAIL / ONEMAP_PASSWORD in .env):", e)
        headers = {}

    r = requests.get(f"{BASE}/common/elastic/search", params=params, headers=headers, timeout=15)
    if r.status_code != 200:
        print(f"OneMap search problem -> HTTP {r.status_code} {r.text[:300]}")
        return None
    results = r.json().get("results", [])
    if not results:
        return None
    best = results[0]
    building = best.get("BUILDING", "")
    name = building if building and building != "NIL" else best.get("ADDRESS", text)
    return {
        "name": nice_name(name),
        "address": best.get("ADDRESS", ""),
        "lat": float(best["LATITUDE"]),
        "lon": float(best["LONGITUDE"]),
    }


def plan_route(start, end, how_many=3):
    """start/end are dicts with lat and lon. Returns a list of itineraries (fastest first)."""
    now = datetime.now(SG)
    params = {
        "start": f"{start['lat']},{start['lon']}",
        "end": f"{end['lat']},{end['lon']}",
        "routeType": "pt",
        "mode": "TRANSIT",
        "date": now.strftime("%m-%d-%Y"),
        "time": now.strftime("%H:%M:%S"),
        "maxWalkDistance": 1000,
        "numItineraries": how_many,
    }
    token = get_token()
    url = f"{BASE}/public/routingsvc/route"
    r = requests.get(url, params=params, headers={"Authorization": token}, timeout=30)
    if r.status_code in (401, 403):            # some setups want "Bearer <token>"
        r = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    itineraries = (r.json().get("plan") or {}).get("itineraries", [])
    return sorted(itineraries, key=lambda it: it.get("duration", 10**9))
