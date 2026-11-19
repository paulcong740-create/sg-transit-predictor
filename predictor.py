# predictor.py
# Loads the trained crowd model (made by train.py) and answers:
#   "How crowded will station X usually be at time T?"

import os
from datetime import datetime, timedelta, timezone

import pandas as pd

SG = timezone(timedelta(hours=8))
MODEL_FILE = "models/crowd_model.joblib"
LEVEL_SCORE = {"l": 0, "m": 1, "h": 2}
LEVEL_TEXT = {"l": "🟢 low", "m": "🟡 moderate", "h": "🔴 high"}

_saved = None


def _load():
    """Load the model once, the first time it's needed. Returns None if train.py hasn't been run."""
    global _saved
    if _saved is None:
        if not os.path.exists(MODEL_FILE):
            print("No crowd model found - run python train.py first. Predictions are switched off.")
            _saved = False
        else:
            import joblib
            _saved = joblib.load(MODEL_FILE)
    return _saved or None


def _features(station, when, stations):
    """Must match make_features() in train.py exactly."""
    return pd.DataFrame({
        "station": pd.Categorical([station], categories=stations),
        "hour": [when.hour],
        "minute_of_day": [when.hour * 60 + when.minute],
        "day_of_week": [when.weekday()],
        "is_weekend": [int(when.weekday() >= 5)],
    })


def predict(station, when=None):
    """Returns (level, confidence), e.g. ("m", 0.84), or None if we can't predict this station."""
    saved = _load()
    if not saved or station not in saved["stations"]:
        return None
    when = when or datetime.now(SG)
    model = saved["model"]
    probs = model.predict_proba(_features(station, when, saved["stations"]))[0]
    best = int(probs.argmax())
    return model.classes_[best], float(probs[best])


def describe(station, when=None):
    """e.g. '🟡 moderate (84% sure)', or None."""
    result = predict(station, when)
    if not result:
        return None
    level, confidence = result
    return f"{LEVEL_TEXT[level]} ({confidence:.0%} sure)"


def best_departure(stops, max_wait=60, step=15):
    """
    stops: list of (station_code, minutes_after_leaving) for every MRT station on the trip.
    If the trip would be crowded leaving now, look for a later departure (in 15-min steps,
    up to 1 hour) when every station is predicted low. Returns minutes to wait, or None.
    """
    if not _load() or not stops:
        return None
    now = datetime.now(SG)

    def worst(delay):
        levels = [predict(code, now + timedelta(minutes=delay + offset)) for code, offset in stops]
        return max((LEVEL_SCORE[r[0]] for r in levels if r), default=0)

    if worst(0) == 0:
        return None                          # already quiet - no need to wait
    for delay in range(step, max_wait + 1, step):
        if worst(delay) == 0:
            return delay
    return None
