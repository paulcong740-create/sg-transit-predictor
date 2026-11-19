# explore.py
# Phase 4: turns the collected data into charts, saved in the charts/ folder.
#
#   python explore.py
#
# Needs:  pip install pandas matplotlib

import glob
import os

import matplotlib
matplotlib.use("Agg")                      # save charts to files instead of opening windows
import matplotlib.pyplot as plt
import pandas as pd

os.makedirs("charts", exist_ok=True)

MY_STATIONS = {
    "NS3": "Bukit Gombak",
    "NS1": "Jurong East (NSL)",
    "EW24": "Jurong East (EWL)",
    "EW22": "Dover",
    "NS4": "Choa Chu Kang",
}
CROWD_SCORE = {"l": 0, "m": 1, "h": 2}     # low / moderate / high as numbers, so we can average them


# ---------- 1. Load every daily file into one table ----------

def load(kind):
    files = sorted(glob.glob(f"data/{kind}/*.csv"))
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Singapore")
    df["hour"] = df["time"].dt.hour
    df["weekday"] = df["time"].dt.day_of_week < 5          # Mon-Fri = True, Sat-Sun = False
    print(f"{kind}: {len(df):,} rows from {len(files)} days "
          f"({df['time'].min():%d %b %H:%M} to {df['time'].max():%d %b %H:%M})")
    return df


mrt = load("mrt")
bus = load("bus")
mrt["score"] = mrt["crowd_level"].map(CROWD_SCORE)


# ---------- 2. Find stations whose data looks broken ----------
# A station that says "high" even at 2-5 am (when trains don't run) has a stuck sensor.

night = mrt[mrt["hour"].between(2, 4)]
stuck = night.groupby("station")["crowd_level"].apply(lambda s: (s == "h").mean())
stuck = sorted(stuck[stuck > 0.8].index)
print("\nStations stuck on 'high' (ignored in the charts):", ", ".join(stuck) or "none")
clean = mrt[~mrt["station"].isin(stuck)]


# ---------- Chart 1: my stations, weekday crowd by hour ----------

weekday = clean[clean["weekday"]]
fig, ax = plt.subplots(figsize=(10, 5))
for code, name in MY_STATIONS.items():
    by_hour = weekday[weekday["station"] == code].groupby("hour")["score"].mean()
    ax.plot(by_hour.index, by_hour.values, marker="o", label=name)
ax.set_title("How crowded are my stations? (weekdays)")
ax.set_xlabel("Hour of day")
ax.set_ylabel("Average crowd level")
ax.set_yticks([0, 1, 2], ["Low", "Moderate", "High"])
ax.set_xticks(range(0, 24, 2))
ax.set_xlim(5, 24)
ax.grid(alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig("charts/1_my_stations_by_hour.png", dpi=120)
plt.close(fig)


# ---------- Chart 2: weekday vs weekend at Jurong East ----------

je = clean[clean["station"] == "NS1"]
fig, ax = plt.subplots(figsize=(10, 5))
for is_weekday, label in [(True, "Weekday"), (False, "Weekend")]:
    by_hour = je[je["weekday"] == is_weekday].groupby("hour")["score"].mean()
    ax.plot(by_hour.index, by_hour.values, marker="o", label=label)
ax.set_title("Jurong East (NS1): weekday vs weekend")
ax.set_xlabel("Hour of day")
ax.set_ylabel("Average crowd level")
ax.set_yticks([0, 1, 2], ["Low", "Moderate", "High"])
ax.set_xticks(range(0, 24, 2))
ax.set_xlim(5, 24)
ax.grid(alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig("charts/2_jurong_east_weekday_vs_weekend.png", dpi=120)
plt.close(fig)


# ---------- Chart 3: busiest stations in the weekday evening rush ----------

evening = weekday[weekday["hour"].between(17, 19)]
top = evening.groupby("station")["score"].mean().sort_values(ascending=False).head(15)
fig, ax = plt.subplots(figsize=(10, 6))
colors = ["tab:red" if s in MY_STATIONS else "tab:blue" for s in top.index[::-1]]
ax.barh(top.index[::-1], top.values[::-1], color=colors)
ax.set_title("Most crowded stations, weekdays 5-8 pm (red = on my route)")
ax.set_xlabel("Average crowd level (0 = low, 1 = moderate, 2 = high)")
fig.tight_layout()
fig.savefig("charts/3_busiest_evening_stations.png", dpi=120)
plt.close(fig)


# ---------- Chart 4: how long I wait for my buses ----------

bus["service"] = bus["service"].astype(str)
bus["wait"] = bus["wait_minutes"].clip(lower=0)          # negative = bus already arriving -> 0
my_buses = [("44089", "991", "991 at home stop"), ("44089", "451", "451 at home stop"),
            ("44009", "172", "172 at Choa Chu Kang Int")]
fig, ax = plt.subplots(figsize=(10, 5))
for stop, service, label in my_buses:
    rows = bus[(bus["bus_stop"].astype(str) == stop) & (bus["service"] == service) & bus["weekday"]]
    by_hour = rows.groupby("hour")["wait"].median()
    if len(by_hour):
        ax.plot(by_hour.index, by_hour.values, marker="o", label=label)
ax.set_title("Typical wait for the next bus (weekdays)")
ax.set_xlabel("Hour of day")
ax.set_ylabel("Median wait (minutes)")
ax.set_xticks(range(0, 24, 2))
ax.set_xlim(5, 24)
ax.grid(alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig("charts/4_my_bus_waits.png", dpi=120)
plt.close(fig)


# ---------- Some numbers to quote ----------

print("\nBus load readings:", (bus["load"].value_counts(normalize=True) * 100).round(1).to_dict(), "(%)")
print("Charts saved in the charts/ folder:")
for f in sorted(glob.glob("charts/*.png")):
    print("  ", f)
