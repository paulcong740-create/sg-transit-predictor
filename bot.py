# bot.py
# Telegram bot that shows live bus arrivals, MRT crowd levels and train alerts for my commute.
#
# Run it with:  python bot.py      (stop with Ctrl + C)
# The bot only answers while this is running.

import asyncio
import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()
LTA_API_KEY = os.getenv("LTA_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

BASE = "https://datamall2.mytransport.sg/ltaodataservice"
SG = timezone(timedelta(hours=8))

# ---- MY ROUTE ----
HOME_STOP = "44089"            # board here going to school
HOME_STOP_BUSES = {"991", "451"}
CCK_INT = "44009"              # Choa Chu Kang Int, board here going home
CCK_INT_BUSES = {"172"}
TO_SCHOOL_STATIONS = ["NS3", "NS1", "EW24", "EW22"]
GO_HOME_STATIONS = ["EW22", "EW24", "NS1", "NS4"]
STATION_NAMES = {
    "NS3": "Bukit Gombak",
    "NS1": "Jurong East (NSL)",
    "EW24": "Jurong East (EWL)",
    "EW22": "Dover",
    "NS4": "Choa Chu Kang",
}
# ------------------

BUS_LOAD = {"SEA": "🟢", "SDA": "🟡", "LSD": "🔴"}
CROWD = {"l": "🟢 Low", "m": "🟡 Moderate", "h": "🔴 High"}


# ---------- Getting data from LTA ----------

def lta_get(path, params=None):
    r = requests.get(f"{BASE}/{path}", params=params, timeout=15,
                     headers={"AccountKey": LTA_API_KEY, "accept": "application/json"})
    r.raise_for_status()
    return r.json()


def minutes_until(eta_text):
    if not eta_text:
        return None
    eta = datetime.fromisoformat(eta_text)
    return max(0, round((eta - datetime.now(SG)).total_seconds() / 60))


def service_sort_key(service_no):
    digits = "".join(ch for ch in service_no if ch.isdigit())
    return (int(digits) if digits else 9999, service_no)


# ---------- Building the reply messages ----------

def bus_text(stop, only=None):
    services = lta_get("v3/BusArrival", {"BusStopCode": stop})["Services"]
    if only:
        services = [s for s in services if s["ServiceNo"] in only]
    if not services:
        return f"🚌 <b>Bus stop {stop}</b>\nNo buses right now."

    lines = [f"🚌 <b>Bus stop {stop}</b>"]
    for s in sorted(services, key=lambda s: service_sort_key(s["ServiceNo"])):
        times = []
        for key in ("NextBus", "NextBus2", "NextBus3"):
            bus = s.get(key) or {}
            mins = minutes_until(bus.get("EstimatedArrival"))
            if mins is None:
                continue
            label = "Arr" if mins == 0 else f"{mins}m"
            if bus.get("Monitored") == 0:
                label = "~" + label          # ~ = timetable estimate, not live GPS
            times.append(f"{label} {BUS_LOAD.get(bus.get('Load'), '')}")
        if times:
            lines.append(f"<b>{s['ServiceNo']}</b>: " + "  ·  ".join(times))
    return "\n".join(lines)


def mrt_text(stations):
    wanted_lines = {"NSL" if code.startswith("NS") else "EWL" for code in stations}
    levels = {}
    for line in wanted_lines:
        for row in lta_get("PCDRealTime", {"TrainLine": line})["value"]:
            levels[row["Station"]] = row["CrowdLevel"]

    lines = ["🚇 <b>Platform crowd</b>"]
    for code in stations:
        level = CROWD.get(levels.get(code), "⚪ No data")
        lines.append(f"{STATION_NAMES.get(code, code)}: {level}")
    return "\n".join(lines)


def alert_text():
    value = lta_get("TrainServiceAlerts").get("value", {})
    if isinstance(value, list):            # be safe if LTA wraps it in a list
        value = value[0] if value else {}
    if str(value.get("Status")) != "2":
        return "✅ No train disruptions"

    lines = ["⚠️ <b>Train disruption</b>"]
    for seg in value.get("AffectedSegments", []):
        lines.append(f"{seg.get('Line', '')} ({seg.get('Direction', '')}): {seg.get('Stations', '')}")
    for msg in value.get("Message", []):
        lines.append(msg.get("Content", ""))
    return "\n".join(lines)


LEGEND = "\n\n<i>🟢 seats  🟡 standing  🔴 packed  ·  ~ = timetable estimate</i>"


def school_text():
    return "\n\n".join([
        "🎒 <b>Going to school</b>",
        bus_text(HOME_STOP, HOME_STOP_BUSES),
        mrt_text(TO_SCHOOL_STATIONS),
        alert_text(),
    ]) + LEGEND


def home_text():
    return "\n\n".join([
        "🏠 <b>Going home</b>",
        mrt_text(GO_HOME_STATIONS),
        bus_text(CCK_INT, CCK_INT_BUSES),
        alert_text(),
    ]) + LEGEND


HELP = (
    "👋 <b>SG Transit Bot</b>\n\n"
    "/school – my bus + MRT crowd going to school\n"
    "/home – MRT crowd + my bus going home\n"
    "/mrt – crowd at my stations\n"
    "/bus 44089 – live buses at any stop\n\n"
    "Tip: you can also just send a 5-digit bus stop code."
)


# ---------- Telegram commands ----------

async def reply(update: Update, make_text):
    """Fetch data in the background (so the bot doesn't freeze), then reply."""
    try:
        text = await asyncio.to_thread(make_text)
    except Exception as e:
        print("Error:", e)
        text = "😕 Sorry, I couldn't get data from LTA right now. Try again in a moment."
    await update.message.reply_text(text, parse_mode="HTML")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode="HTML")


async def school(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, school_text)


async def home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, home_text)


async def mrt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stations = list(dict.fromkeys(TO_SCHOOL_STATIONS + GO_HOME_STATIONS))
    await reply(update, lambda: mrt_text(stations) + "\n\n" + alert_text())


async def bus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not (context.args[0].isdigit() and len(context.args[0]) == 5):
        await update.message.reply_text("Send it like this: /bus 44089")
        return
    stop = context.args[0]
    await reply(update, lambda: bus_text(stop) + LEGEND)


async def plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    if msg.isdigit() and len(msg) == 5:
        await reply(update, lambda: bus_text(msg) + LEGEND)
    else:
        await update.message.reply_text(HELP, parse_mode="HTML")


def main():
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN is missing from your .env file.")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("school", school))
    app.add_handler(CommandHandler("home", home))
    app.add_handler(CommandHandler("mrt", mrt))
    app.add_handler(CommandHandler("bus", bus))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text))
    print("Bot is running. Open Telegram and message your bot. Press Ctrl + C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
