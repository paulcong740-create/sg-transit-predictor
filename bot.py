# bot.py
# Telegram bot that shows live bus arrivals, MRT crowd levels and train alerts for my commute.
#
# Run it with:  python bot.py      (stop with Ctrl + C)
# The bot only answers while this is running.

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from html import escape

import requests
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)

import onemap

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


# ---------- Journey planner (any place to any place) ----------

PCD_LINE = {"NS": "NSL", "EW": "EWL", "CG": "CGL", "NE": "NEL", "CC": "CCL", "CE": "CEL",
            "DT": "DTL", "TE": "TEL", "BP": "BPL", "SW": "SLRT", "SE": "SLRT", "PW": "PLRT", "PE": "PLRT"}
LINE_NAME = {"NSL": "North-South Line", "EWL": "East-West Line", "CGL": "East-West Line",
             "NEL": "North East Line", "CCL": "Circle Line", "CEL": "Circle Line",
             "DTL": "Downtown Line", "TEL": "Thomson-East Coast Line", "BPL": "Bukit Panjang LRT",
             "SLRT": "Sengkang LRT", "PLRT": "Punggol LRT"}


def station_code(leg_end):
    """Pull an MRT code like 'NS3' out of OneMap's stop info, if there is one."""
    for field in ("stopCode", "stopId", "name"):
        m = re.search(r"\b([A-Z]{2})(\d{1,2})\b", str(leg_end.get(field, "")))
        if m and m.group(1) in PCD_LINE:
            return m.group(1) + m.group(2)
    return None


def live_bus_times(stop, service):
    data = lta_get("v3/BusArrival", {"BusStopCode": stop, "ServiceNo": service})
    for s in data.get("Services", []):
        times = []
        for key in ("NextBus", "NextBus2", "NextBus3"):
            bus = s.get(key) or {}
            mins = minutes_until(bus.get("EstimatedArrival"))
            if mins is None:
                continue
            label = "Arr" if mins == 0 else f"{mins}m"
            if bus.get("Monitored") == 0:
                label = "~" + label
            times.append(f"{label} {BUS_LOAD.get(bus.get('Load'), '')}")
        if times:
            return "  ·  ".join(times)
    return "no live times right now"


def crowd_at(code, cache):
    line = PCD_LINE.get(code[:2])
    if line not in cache:
        cache[line] = {row["Station"]: row["CrowdLevel"]
                       for row in lta_get("PCDRealTime", {"TrainLine": line})["value"]}
    return CROWD.get(cache[line].get(code), "⚪ No data")


def short_summary(itinerary):
    parts = []
    for leg in itinerary.get("legs", []):
        if leg.get("mode") == "BUS":
            parts.append(f"Bus {leg.get('route', '')}")
        elif leg.get("mode") in ("SUBWAY", "RAIL", "TRAM"):
            code = station_code(leg.get("from", {}))
            parts.append(PCD_LINE.get(code[:2], "MRT") if code else "MRT")
    return " → ".join(parts) or "Walk"


def trip_text(start, end, itineraries, index=0):
    """Full step-by-step details (with live data) for one of the route options."""
    best = itineraries[index]
    total = round(best.get("duration", 0) / 60)
    transfers = best.get("transfers", 0)
    title = "Fastest route" if index == 0 else f"Option {index + 1}"
    lines = [f"🗺️ <b>{escape(start['name'])} → {escape(end['name'])}</b>",
             f"<b>{title}</b>",
             f"⏱️ {total} min  ·  🔁 {transfers} transfer{'s' if transfers != 1 else ''}"]
    fare = best.get("fare")
    if fare not in (None, "", "0"):
        lines[-1] += f"  ·  💲{fare}"
    lines.append("")

    crowd_cache = {}
    legs = best.get("legs", [])
    step = 1
    for i, leg in enumerate(legs):
        mode = leg.get("mode")
        frm, to = leg.get("from", {}), leg.get("to", {})
        mins = max(1, round(leg.get("duration", 0) / 60))
        stops = leg.get("numIntermediateStops")
        stops_txt = f" · {stops + 1} stops" if isinstance(stops, int) else ""

        if mode == "WALK":
            dist = round(leg.get("distance", 0))
            if dist < 50:
                continue
            target = "your destination" if i == len(legs) - 1 else escape(onemap.nice_name(to.get("name", "next stop")))
            lines.append(f"{step}. 🚶 Walk {dist}m to {target} ({mins} min)")

        elif mode == "BUS":
            service = str(leg.get("route", ""))
            stop_code = str(frm.get("stopCode", ""))
            lines.append(f"{step}. 🚌 <b>Bus {escape(service)}</b> from {escape(onemap.nice_name(frm.get('name', '')))}"
                         f" ({escape(stop_code)})")
            lines.append(f"     → {escape(onemap.nice_name(to.get('name', '')))}{stops_txt} · {mins} min")
            if stop_code.isdigit():
                try:
                    lines.append(f"     <i>Next bus: {live_bus_times(stop_code, service)}</i>")
                except Exception:
                    pass

        elif mode in ("SUBWAY", "RAIL", "TRAM"):
            code = station_code(frm)
            line = PCD_LINE.get(code[:2]) if code else None
            name = LINE_NAME.get(line, "MRT")
            lines.append(f"{step}. 🚇 <b>{name}</b> {escape(onemap.nice_name(frm.get('name', '')))}"
                         f" → {escape(onemap.nice_name(to.get('name', '')))}{stops_txt} · {mins} min")
            if code:
                try:
                    lines.append(f"     <i>Platform crowd now: {crowd_at(code, crowd_cache)}</i>")
                except Exception:
                    pass
        else:
            lines.append(f"{step}. {escape(str(mode).title())} to {escape(onemap.nice_name(to.get('name', '')))} ({mins} min)")
        step += 1

    others = [i for i in range(len(itineraries)) if i != index]
    if others:
        lines.append("\n<b>Other options</b> (tap a button below)")
        for i in others:
            it = itineraries[i]
            lines.append(f"• Option {i + 1}: {round(it.get('duration', 0) / 60)} min – {escape(short_summary(it))}")

    try:
        lines.append("\n" + alert_text())
    except Exception:
        pass
    lines.append("\n<i>🟢 seats  🟡 standing  🔴 packed  ·  Routes by OneMap</i>")
    return "\n".join(lines)


ASK_FROM, ASK_TO = range(2)
WHERE_FROM = ("📍 <b>Where are you starting from?</b>\n"
              "Send a postal code, address or place name.\n"
              "Or tap 📎 → <b>Location</b> to use where you are now.")


async def go_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WHERE_FROM, parse_mode="HTML")
    return ASK_FROM


async def find_place(update: Update):
    """Turns the user's message (text or shared location) into a place, or None."""
    if update.message.location:
        loc = update.message.location
        return {"name": "Your location", "lat": loc.latitude, "lon": loc.longitude}
    text = (update.message.text or "").strip()
    try:
        place = await asyncio.to_thread(onemap.search, text)
    except Exception as e:
        print("OneMap search error:", e)
        place = None
    if not place:
        await update.message.reply_text(f"😕 I couldn't find “{text}”. Try a 6-digit postal code or a clearer name.")
    return place


async def go_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = await find_place(update)
    if not place:
        return ASK_FROM
    context.user_data["start"] = place
    await update.message.reply_text(
        f"✅ From: <b>{escape(place['name'])}</b>\n\n🏁 <b>Where do you want to go?</b>", parse_mode="HTML")
    return ASK_TO


def option_buttons(itineraries, index):
    rows = []
    for i, it in enumerate(itineraries):
        if i == index:
            continue
        label = f"Option {i + 1}: {round(it.get('duration', 0) / 60)} min · {short_summary(it)}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"opt:{i}")])
    return InlineKeyboardMarkup(rows) if rows else None


async def go_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = await find_place(update)
    if not place:
        return ASK_TO
    start = context.user_data["start"]
    await update.message.reply_text(f"✅ To: <b>{escape(place['name'])}</b>\n🔎 Finding the best route…",
                                    parse_mode="HTML")
    try:
        itineraries = await asyncio.to_thread(onemap.plan_route, start, place)
    except Exception as e:
        print("OneMap route error:", e)
        itineraries = None
    if not itineraries:
        await update.message.reply_text("😕 Sorry, I couldn't find a public transport route between those places.")
        return ConversationHandler.END

    # remember this trip so the option buttons work
    context.user_data["trip"] = {"start": start, "end": place, "itineraries": itineraries}
    text = await asyncio.to_thread(trip_text, start, place, itineraries, 0)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=option_buttons(itineraries, 0))
    return ConversationHandler.END


async def show_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs when someone taps an 'Option N' button."""
    query = update.callback_query
    await query.answer()
    trip = context.user_data.get("trip")
    index = int(query.data.split(":")[1])
    if not trip or index >= len(trip["itineraries"]):
        await query.message.reply_text("That trip has expired. Send /go to plan it again.")
        return
    text = await asyncio.to_thread(trip_text, trip["start"], trip["end"], trip["itineraries"], index)
    await query.message.reply_text(text, parse_mode="HTML",
                                   reply_markup=option_buttons(trip["itineraries"], index))


async def go_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled. Send /go to plan another trip.")
    return ConversationHandler.END


HELP = (
    "👋 <b>SG Transit Bot</b>\n\n"
    "/go – plan a trip from anywhere to anywhere 🗺️\n"
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
    place_input = (filters.TEXT & ~filters.COMMAND) | filters.LOCATION
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("go", go_start)],
        states={
            ASK_FROM: [MessageHandler(place_input, go_from)],
            ASK_TO: [MessageHandler(place_input, go_to)],
        },
        fallbacks=[CommandHandler("cancel", go_cancel)],
    ))
    app.add_handler(CallbackQueryHandler(show_option, pattern=r"^opt:\d+$"))
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
