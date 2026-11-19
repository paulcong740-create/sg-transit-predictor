# train.py
# Phase 5: trains a machine learning model that predicts MRT platform crowd levels
# (low / moderate / high) for any station, day and time.
#
#   python train.py
#
# Needs:  pip install pandas scikit-learn joblib
# Saves the trained model to models/crowd_model.joblib (used by the Telegram bot later).

import glob
import os

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, classification_report

LEVEL_NAMES = {"l": "low", "m": "moderate", "h": "high"}


# ---------- Step 1: load all the MRT data ----------

files = sorted(glob.glob("data/mrt/*.csv"))
df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Singapore")
print(f"Loaded {len(df):,} readings from {len(files)} days")


# ---------- Step 2: clean the data ----------
# Remove stations stuck on "high" at 2-4 am (broken sensors - trains aren't even running).

night = df[df["time"].dt.hour.between(2, 4)]
stuck_share = night.groupby("station")["crowd_level"].apply(lambda s: (s == "h").mean())
stuck = sorted(stuck_share[stuck_share > 0.8].index)
df = df[~df["station"].isin(stuck)].copy()
print(f"Removed stuck stations: {', '.join(stuck) or 'none'}")


# ---------- Step 3: turn each reading into "features" the model can learn from ----------

def make_features(table):
    """The information the model gets for each prediction."""
    t = table["time"]
    return pd.DataFrame({
        "station": table["station"].astype("category"),
        "hour": t.dt.hour,
        "minute_of_day": t.dt.hour * 60 + t.dt.minute,     # e.g. 18:30 -> 1110
        "day_of_week": t.dt.day_of_week,                      # 0 = Monday ... 6 = Sunday
        "is_weekend": (t.dt.day_of_week >= 5).astype(int),
    })


df["date"] = df["time"].dt.date
X = make_features(df)
y = df["crowd_level"]


# ---------- Step 4: split into training days and test days ----------
# The model learns from the earlier days, then we test it on the LAST day, which it has never seen.
# That's how we know it's really predicting, not just remembering.

dates = sorted(df["date"].unique())
test_days = dates[-1:]
is_test = df["date"].isin(test_days)
X_train, y_train = X[~is_test], y[~is_test]
X_test, y_test = X[is_test], y[is_test]
print(f"Training on {len(dates) - len(test_days)} days ({len(X_train):,} rows), "
      f"testing on {', '.join(map(str, test_days))} ({len(X_test):,} rows)")


# ---------- Step 5: train the model ----------
# HistGradientBoosting builds many small decision trees ("Is it a weekday? After 5 pm? Jurong East?")
# where each new tree fixes the mistakes of the ones before it.

model = HistGradientBoostingClassifier(categorical_features=["station"], max_iter=200, random_state=0)
model.fit(X_train, y_train)
predictions = model.predict(X_test)


# ---------- Step 6: is it any good? Compare against simple guesses ----------

def busy_recall(guess):
    """Of all the moments that were really moderate or high, how many did we catch?"""
    busy = y_test != "l"
    return (pd.Series(guess, index=y_test.index)[busy] == y_test[busy]).mean()


# Simple guess 1: always say "low" (the most common answer)
always_low = ["l"] * len(y_test)

# Simple guess 2: whatever was most common for that station + hour in the training days
usual = (pd.DataFrame({"station": X_train["station"].astype(str), "hour": X_train["hour"], "y": y_train})
         .groupby(["station", "hour"])["y"].agg(lambda s: s.value_counts().idxmax()))
keys = list(zip(X_test["station"].astype(str), X_test["hour"]))
usual_guess = [usual.get(k, "l") for k in keys]

print("\n                          Accuracy   Busy moments caught")
for name, guess in [("Always guess 'low'", always_low),
                    ("Usual level for that hour", usual_guess),
                    ("My model", predictions)]:
    print(f"  {name:<26}{accuracy_score(y_test, guess):>7.1%}   {busy_recall(guess):>12.1%}")

print("\nDetailed results for my model:")
print(classification_report(y_test, predictions, labels=["l", "m", "h"],
                            target_names=["low", "moderate", "high"], zero_division=0))


# ---------- Step 7: try a prediction ----------

def predict(station, when):
    when = pd.Timestamp(when, tz="Asia/Singapore")
    row = make_features(pd.DataFrame({"station": [station], "time": [when]}))
    row["station"] = pd.Categorical(row["station"], categories=X["station"].cat.categories)
    probs = dict(zip(model.classes_, model.predict_proba(row)[0]))
    best = max(probs, key=probs.get)
    return LEVEL_NAMES[best], probs[best]


print("Example predictions:")
for station, name in [("NS1", "Jurong East"), ("EW22", "Dover"), ("NS3", "Bukit Gombak")]:
    for when in ["2026-11-24 08:00", "2026-11-24 18:30", "2026-11-29 20:00"]:
        level, confidence = predict(station, when)
        print(f"  {name:<13} {pd.Timestamp(when):%a %d %b %H:%M}  ->  {level:<9} ({confidence:.0%} sure)")


# ---------- Step 8: retrain on ALL the data and save the model for the bot ----------

final_model = HistGradientBoostingClassifier(categorical_features=["station"], max_iter=200, random_state=0)
final_model.fit(X, y)
os.makedirs("models", exist_ok=True)
joblib.dump({"model": final_model, "stations": list(X["station"].cat.categories)},
            "models/crowd_model.joblib")
print("\nSaved the trained model to models/crowd_model.joblib")
