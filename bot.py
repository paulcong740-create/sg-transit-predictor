# bot.py
# Telegram bot: plans trips anywhere in Singapore, with live bus times, MRT crowd levels
# and crowd predictions from my own model. Each user saves their own home and school,
# so the 🎒 / 🏠 buttons work for everyone.
#
# Run it with:  python bot.py      (stop with Ctrl + C)

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from html import escape, unescape

import requests
from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, PicklePersistence, filters)

import onemap
import predictor

load_dotenv()
LTA_API_KEY = os.getenv("LTA_API_KEY")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

BASE = "https://datamall2.mytransport.sg/ltaodataservice"
SG = timezone(timedelta(hours=8))

# Each user's saved places live in this file on the server (it is in .gitignore - never upload it)
DATA_FILE = "bot_data.pickle"

DOT = {"l": "🟢", "m": "🟡", "h": "🔴"}                 # MRT crowd: low / moderate / high
BUS_LOAD = {"SEA": "🟢", "SDA": "🟡", "LSD": "🔴"}       # bus: seats / standing / packed

# ---- the buttons under the chat box ----
BTN_SCHOOL, BTN_HOME, BTN_TRIP, BTN_MRT = "🎒 To school", "🏠 Go home", "🗺️ Plan a trip", "🚇 MRT crowds"
BTN_PLACES, BTN_CANCEL = "⚙️ My places", "✖️ Cancel"
MAIN_MENU = ReplyKeyboardMarkup([[BTN_SCHOOL, BTN_HOME], [BTN_TRIP, BTN_MRT], [BTN_PLACES]],
                                resize_keyboard=True)
ASK_MENU = ReplyKeyboardMarkup([[KeyboardButton("📍 Use my location", request_location=True)], [BTN_CANCEL]],
                               resize_keyboard=True)
MENU_BUTTONS = [BTN_SCHOOL, BTN_HOME, BTN_TRIP, BTN_MRT, BTN_PLACES, BTN_CANCEL]


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


def bus_times(service):
    """'4🟢 · 11🟡 · ~22🟢'  (~ = timetable estimate, not live GPS)"""
    times = []
    for key in ("NextBus", "NextBus2", "NextBus3"):
        bus = service.get(key) or {}
        mins = minutes_until(bus.get("EstimatedArrival"))
        if mins is None:
            continue
        label = "now" if mins == 0 else str(mins)
        if bus.get("Monitored") == 0:
            label = "~" + label
        times.append(label + BUS_LOAD.get(bus.get("Load"), ""))
    return " · ".join(times)


def crowd_levels(lines):
    """Live crowd level for every station on the given lines, e.g. {'NS1': 'm', ...}"""
    levels = {}
    for line in lines:
        for row in lta_get("PCDRealTime", {"TrainLine": line})["value"]:
            levels[row["Station"]] = row["CrowdLevel"]
    return levels


def disruption_text():
    """Empty string when trains are running normally, so it doesn't take up space."""
    try:
        value = lta_get("TrainServiceAlerts").get("value", {})
    except Exception:
        return ""
    if isinstance(value, list):
        value = value[0] if value else {}
    if str(value.get("Status")) != "2":
        return ""
    lines = ["\n⚠️ <b>Train disruption</b>"]
    for seg in value.get("AffectedSegments", []):
        lines.append(f"{seg.get('Line', '')}: {seg.get('Stations', '')}")
    for msg in value.get("Message", [])[:1]:
        lines.append(f"<i>{escape(msg.get('Content', ''))}</i>")
    return "\n".join(lines)


# ---------- Bus stop and MRT blocks ----------

def bus_block(stop, only=None):
    services = lta_get("v3/BusArrival", {"BusStopCode": stop})["Services"]
    if only:
        services = [s for s in services if s["ServiceNo"] in only]
    lines = [f"🚌 <b>Bus stop {stop}</b>  <i>(min)</i>"]
    rows = [(s["ServiceNo"], bus_times(s)) for s in sorted(services, key=lambda s: service_sort_key(s["ServiceNo"]))]
    rows = [(no, t) for no, t in rows if t]
    lines += [f"<b>{no}</b>   {t}" for no, t in rows] or ["No buses right now"]
    return "\n".join(lines)


def header(title):
    return f"{title}  <i>{datetime.now(SG):%H:%M}</i>"


def mrt_text(stations):
    """stations: list of (code, name) on this user's commute."""
    lines_needed = {PCD_LINE.get(code[:2]) for code, _ in stations} - {None}
    levels = crowd_levels(lines_needed)
    in_30 = datetime.now(SG) + timedelta(minutes=30)
    lines = [header("🚇 <b>MRT crowds</b>"), "", "<i>now → 🔮 in 30 min</i>"]
    for code, name in stations:
        forecast = predictor.predict(code, in_30)
        later = f" → {DOT[forecast[0]]}" if forecast else ""
        lines.append(f"{DOT.get(levels.get(code), '⚪')}{later}  {escape(name)}")
    return "\n".join(lines) + disruption_text()


# ---------- Journey planner (any place to any place) ----------

PCD_LINE = {"NS": "NSL", "EW": "EWL", "CG": "CGL", "NE": "NEL", "CC": "CCL", "CE": "CEL",
            "DT": "DTL", "TE": "TEL", "BP": "BPL", "SW": "SLRT", "SE": "SLRT", "PW": "PLRT", "PE": "PLRT"}
LINE_LABEL = {"NSL": "NSL", "EWL": "EWL", "CGL": "EWL", "NEL": "NEL", "CCL": "CCL", "CEL": "CCL",
              "DTL": "DTL", "TEL": "TEL", "BPL": "BP LRT", "SLRT": "Sengkang LRT", "PLRT": "Punggol LRT"}


def station_code(leg_end):
    """Pull an MRT code like 'NS3' out of OneMap's stop info, if there is one."""
    for field in ("stopCode", "stopId", "name"):
        m = re.search(r"\b([A-Z]{2})(\d{1,2})\b", str(leg_end.get(field, "")))
        if m and m.group(1) in PCD_LINE:
            return m.group(1) + m.group(2)
    return None


def short_name(name):
    """'BUKIT GOMBAK MRT STATION (NS3)' -> 'Bukit Gombak'"""
    t = onemap.nice_name(name)
    t = re.sub(r"\s*\([A-Z]{2}\d{1,2}\)", "", t)
    t = re.sub(r"\s+(MRT|LRT)(\s+Station)?\b|\s+Stn\b", "", t, flags=re.IGNORECASE)
    return escape(t.strip())


def line_of(leg):
    code = station_code(leg.get("from", {}))
    return LINE_LABEL.get(PCD_LINE.get(code[:2]), "MRT") if code else "MRT"


def route_stations(itinerary):
    """Every MRT/LRT station where this route boards or changes trains: [(code, name), ...]"""
    found = {}
    for leg in itinerary.get("legs", []):
        if leg.get("mode") in ("SUBWAY", "RAIL", "TRAM"):
            for end in (leg.get("from", {}), leg.get("to", {})):
                code = station_code(end)
                name = unescape(short_name(end.get("name", code)))
                if code and code not in found and name not in found.values():   # interchange: list once
                    found[code] = name
    return list(found.items())


def short_summary(itinerary):
    parts = []
    for leg in itinerary.get("legs", []):
        if leg.get("mode") == "BUS":
            parts.append(f"🚌{leg.get('route', '')}")
        elif leg.get("mode") in ("SUBWAY", "RAIL", "TRAM"):
            parts.append(f"🚇{line_of(leg)}")
    return " → ".join(parts) or "🚶 Walk"


def trip_text(start, end, itineraries, index=0):
    """One route option, step by step, with live data and predictions."""
    trip = itineraries[index]
    total = round(trip.get("duration", 0) / 60)
    transfers = trip.get("transfers", 0)
    fare = trip.get("fare")
    option = "" if index == 0 else f"  <i>(option {index + 1})</i>"
    lines = [f"🗺️ <b>{short_name(start['name'])} → {short_name(end['name'])}</b>{option}",
             f"⏱ <b>{total} min</b> · {transfers} transfer{'s' if transfers != 1 else ''}"
             + (f" · ${fare}" if fare not in (None, "", "0") else ""),
             ""]

    now = datetime.now(SG)
    elapsed = 0.0                 # minutes from now until this leg starts
    walk = 0.0                    # walking minutes not shown yet (we merge walks together)
    mrt_stops = []                # (station code, minutes from now), for the "leave later?" tip
    crowd_cache = {}

    def flush_walk():
        nonlocal walk
        if walk >= 1:
            lines.append(f"🚶 {round(walk)} min walk")
        walk = 0.0

    for leg in trip.get("legs", []):
        starts_at = now + timedelta(minutes=elapsed)
        minutes = leg.get("duration", 0) / 60
        elapsed += minutes
        mode = leg.get("mode")
        frm, to = leg.get("from", {}), leg.get("to", {})
        stops = leg.get("numIntermediateStops")
        stops_txt = f" · {stops + 1} stops" if isinstance(stops, int) else ""

        if mode == "WALK":
            walk += minutes
            continue
        flush_walk()

        if mode == "BUS":
            service = escape(str(leg.get("route", "")))
            stop_code = str(frm.get("stopCode", ""))
            lines.append(f"🚌 <b>{service}</b>  {short_name(frm.get('name', ''))} → "
                         f"{short_name(to.get('name', ''))}{stops_txt}")
            if stop_code.isdigit():
                try:
                    data = lta_get("v3/BusArrival", {"BusStopCode": stop_code, "ServiceNo": leg.get("route")})
                    times = next((bus_times(s) for s in data.get("Services", []) if bus_times(s)), "")
                    lines.append(f"      <i>next bus (min): {times}</i>" if times else "      <i>no live times</i>")
                except Exception:
                    pass

        elif mode in ("SUBWAY", "RAIL", "TRAM"):
            lines.append(f"🚇 <b>{line_of(leg)}</b>  {short_name(frm.get('name', ''))} → "
                         f"{short_name(to.get('name', ''))}{stops_txt}")
            code = station_code(frm)
            if code:
                parts = []
                try:
                    line = PCD_LINE.get(code[:2])
                    if line not in crowd_cache:
                        crowd_cache[line] = crowd_levels([line])
                    parts.append(f"now {DOT.get(crowd_cache[line].get(code), '⚪')}")
                except Exception:
                    pass
                forecast = predictor.predict(code, starts_at)
                if forecast:
                    parts.append(f"🔮 {starts_at:%H:%M} {DOT[forecast[0]]}")
                    mrt_stops.append((code, (starts_at - now).total_seconds() / 60))
                if parts:
                    lines.append("      <i>" + " · ".join(parts) + "</i>")
        else:
            lines.append(f"➡️ {escape(str(mode).title())} to {short_name(to.get('name', ''))}")
    flush_walk()

    try:
        wait = predictor.best_departure(mrt_stops)
    except Exception as e:
        print("Prediction error:", e)
        wait = None
    if wait:
        lines.append(f"\n💡 Quieter if you leave at <b>{now + timedelta(minutes=wait):%H:%M}</b>")

    return "\n".join(lines) + disruption_text()


# ---------- Trip planning conversation ----------

ASK_FROM, ASK_TO, SET_HOME, SET_SCHOOL = range(4)


async def go_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📍 <b>From where?</b>\nPostal code, address or place name.",
                                    parse_mode="HTML", reply_markup=ASK_MENU)
    return ASK_FROM


async def find_place(update: Update):
    """Turns the user's message (text or shared location) into a place, or None."""
    if update.message.location:
        loc = update.message.location
        return {"name": "My location", "lat": loc.latitude, "lon": loc.longitude}
    text = (update.message.text or "").strip()
    try:
        place = await asyncio.to_thread(onemap.search, text)
    except Exception as e:
        print("OneMap search error:", e)
        place = None
    if not place:
        await update.message.reply_text(f"😕 Couldn't find “{escape(text)}”. Try a postal code.", parse_mode="HTML")
    return place


async def go_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = await find_place(update)
    if not place:
        return ASK_FROM
    context.user_data["start"] = place
    await update.message.reply_text(f"From <b>{escape(place['name'])}</b>\n\n🏁 <b>To where?</b>",
                                    parse_mode="HTML", reply_markup=ASK_MENU)
    return ASK_TO


def option_buttons(itineraries, index):
    rows = [[InlineKeyboardButton(f"{round(it.get('duration', 0) / 60)} min · {short_summary(it)}"[:60],
                                  callback_data=f"opt:{i}")]
            for i, it in enumerate(itineraries) if i != index]
    return InlineKeyboardMarkup(rows) if rows else None


async def go_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = await find_place(update)
    if not place:
        return ASK_TO
    start = context.user_data["start"]
    if abs(start["lat"] - place["lat"]) < 0.003 and abs(start["lon"] - place["lon"]) < 0.003:
        await update.message.reply_text("📍 That's where you're starting. 🙂\n\n🏁 <b>To where?</b>",
                                        parse_mode="HTML", reply_markup=ASK_MENU)
        return ASK_TO

    await update.message.reply_text("🔎 Finding routes…", reply_markup=MAIN_MENU)
    try:
        itineraries = await asyncio.to_thread(onemap.plan_route, start, place)
    except Exception as e:
        print("OneMap route error:", e)
        itineraries = None
    if not itineraries:
        await update.message.reply_text("😕 No public transport route found between those places.")
        return ConversationHandler.END

    context.user_data["trip"] = {"start": start, "end": place, "itineraries": itineraries}
    text = await asyncio.to_thread(trip_text, start, place, itineraries, 0)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=option_buttons(itineraries, 0))
    return ConversationHandler.END


async def show_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs when someone taps one of the other route options."""
    query = update.callback_query
    await query.answer()
    trip = context.user_data.get("trip")
    index = int(query.data.split(":")[1])
    if not trip or index >= len(trip["itineraries"]):
        await query.message.reply_text("That trip has expired. Tap 🗺️ Plan a trip again.")
        return
    text = await asyncio.to_thread(trip_text, trip["start"], trip["end"], trip["itineraries"], index)
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=option_buttons(trip["itineraries"], index))


# ---------- Each user's own home and school ----------

def saved(place):
    """Keep only what we need: a short name and the coordinates (no postal code is stored)."""
    return {"name": place["name"], "lat": place["lat"], "lon": place["lon"]}


async def places_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏠 <b>Where's home?</b>\nSend your postal code, an address, or tap 📍 to use where you are now.\n\n"
        "<i>Saved only for you, so 🎒 To school and 🏠 Go home work. /forget deletes it.</i>",
        parse_mode="HTML", reply_markup=ASK_MENU)
    return SET_HOME


async def set_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = await find_place(update)
    if not place:
        return SET_HOME
    context.user_data["home"] = saved(place)
    await update.message.reply_text(f"✅ Home: <b>{escape(place['name'])}</b>\n\n"
                                    "🏫 <b>Where's school (or work)?</b>\nPostal code, address or place name.",
                                    parse_mode="HTML", reply_markup=ASK_MENU)
    return SET_SCHOOL


async def set_school(update: Update, context: ContextTypes.DEFAULT_TYPE):
    place = await find_place(update)
    if not place:
        return SET_SCHOOL
    home = context.user_data["home"]
    if abs(home["lat"] - place["lat"]) < 0.003 and abs(home["lon"] - place["lon"]) < 0.003:
        await update.message.reply_text("📍 That's the same as home. 🙂 Where's school (or work)?")
        return SET_SCHOOL
    context.user_data["school"] = saved(place)
    context.user_data.pop("commute_stations", None)       # their route changed
    await update.message.reply_text(
        f"✅ School: <b>{escape(place['name'])}</b>\n\nAll set! Change them any time with {BTN_PLACES}.",
        parse_mode="HTML", reply_markup=MAIN_MENU)
    pending = context.user_data.pop("pending", None)      # the button they tapped before setting up
    if pending:
        await run_commute(update, context, pending)
    return ConversationHandler.END


def plan_commute(user_data, direction):
    """Plan home → school (or back) with live data. Returns (text, itineraries) or (text, None)."""
    home = {**user_data["home"], "name": "🏠 Home"}
    school = {**user_data["school"], "name": "🏫 School"}
    start, end = (home, school) if direction == "school" else (school, home)
    itineraries = onemap.plan_route(start, end)
    if not itineraries:
        return "😕 No public transport route found between your home and school.", None
    user_data["commute_stations"] = route_stations(itineraries[0]) or user_data.get("commute_stations")
    user_data["trip"] = {"start": start, "end": end, "itineraries": itineraries}
    return trip_text(start, end, itineraries, 0), itineraries


async def run_commute(update: Update, context: ContextTypes.DEFAULT_TYPE, direction):
    try:
        text, itineraries = await asyncio.to_thread(plan_commute, context.user_data, direction)
    except Exception as e:
        print("Commute error:", e)
        text, itineraries = "😕 Couldn't plan that right now. Try again in a moment.", None
    buttons = option_buttons(itineraries, 0) if itineraries else None
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=buttons or MAIN_MENU)


async def commute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🎒 To school / 🏠 Go home. First time: ask for their places, then plan."""
    direction = "school" if update.message.text.strip() in (BTN_SCHOOL, "/school") else "home"
    if not (context.user_data.get("home") and context.user_data.get("school")):
        context.user_data["pending"] = direction
        await update.message.reply_text("First, let's save your home and school. It only takes a moment. 👇")
        return await places_start(update, context)
    await update.message.reply_text("🔎 Checking your route…", reply_markup=MAIN_MENU)
    await run_commute(update, context, direction)
    return ConversationHandler.END


async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in ("home", "school", "commute_stations", "trip", "start", "pending"):
        context.user_data.pop(key, None)
    await update.message.reply_text("🗑️ Done. I've deleted your saved home and school.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def go_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel button or any other command while planning a trip or saving places."""
    context.user_data.pop("pending", None)
    if (update.message.text or "").strip().startswith(("/start", "/help")):
        await start(update, context)
    else:
        await update.message.reply_text("Cancelled.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


# ---------- Other commands ----------

HELP = (
    "👋 <b>SG Transit Helper</b>\n"
    "Plans your trip with live bus times, MRT crowds and crowd predictions.\n\n"
    "🎒 / 🏠  your trip between home and school\n"
    "🗺️ /go  plan a trip anywhere\n"
    "🚇  crowds at the stations on your route\n"
    "⚙️ /places  set or change home and school\n"
    "🚌 /bus 44089  buses at any stop\n"
    "     (or just send the 5-digit code)\n"
    "🗑️ /forget  delete your saved places\n\n"
    "<b>Symbols</b>\n"
    "🟢 quiet / seats   🟡 busy / standing   🔴 packed\n"
    "~ timetable estimate (no live GPS)\n"
    "🔮 prediction from my model"
)


async def reply(update: Update, make_text):
    """Fetch data in the background (so the bot doesn't freeze), then reply."""
    try:
        text = await asyncio.to_thread(make_text)
    except Exception as e:
        print("Error:", e)
        text = "😕 Couldn't reach LTA right now. Try again in a moment."
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=MAIN_MENU)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode="HTML", reply_markup=MAIN_MENU)


async def mrt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stations = context.user_data.get("commute_stations")
    if not stations:
        has_places = context.user_data.get("home") and context.user_data.get("school")
        msg = (f"Tap {BTN_SCHOOL} once first, so I know which stations are on your route." if has_places
               else f"Save your home and school first ({BTN_PLACES}), then I'll show crowds on your route.")
        await update.message.reply_text(msg, reply_markup=MAIN_MENU)
        return ConversationHandler.END
    await reply(update, lambda: mrt_text(stations))
    return ConversationHandler.END


async def bus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not (context.args[0].isdigit() and len(context.args[0]) == 5):
        await update.message.reply_text("Send it like this: /bus 44089")
        return
    stop = context.args[0]
    await reply(update, lambda: bus_block(stop))


async def plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    if msg == BTN_CANCEL:
        await update.message.reply_text("OK.", reply_markup=MAIN_MENU)
    elif msg.isdigit() and len(msg) == 5:
        await reply(update, lambda: bus_block(msg))
    else:
        await start(update, context)


def main():
    if not BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN is missing.")
        return
    # PicklePersistence saves each user's places to DATA_FILE, so they survive restarts
    app = Application.builder().token(BOT_TOKEN).persistence(PicklePersistence(filepath=DATA_FILE)).build()
    menu_button = filters.Text(MENU_BUTTONS)
    place_input = (filters.TEXT & ~filters.COMMAND & ~menu_button) | filters.LOCATION
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("go", go_start), MessageHandler(filters.Text([BTN_TRIP]), go_start),
            CommandHandler(["school", "home"], commute), MessageHandler(filters.Text([BTN_SCHOOL, BTN_HOME]), commute),
            CommandHandler("places", places_start), MessageHandler(filters.Text([BTN_PLACES]), places_start),
            CommandHandler("mrt", mrt), MessageHandler(filters.Text([BTN_MRT]), mrt),
            CommandHandler("forget", forget),
        ],
        states={
            ASK_FROM: [MessageHandler(place_input, go_from)],
            ASK_TO: [MessageHandler(place_input, go_to)],
            SET_HOME: [MessageHandler(place_input, set_home)],
            SET_SCHOOL: [MessageHandler(place_input, set_school)],
        },
        fallbacks=[MessageHandler(filters.COMMAND | filters.Text([BTN_CANCEL]), go_cancel)],
        allow_reentry=True,          # tapping another menu button mid-way just starts that instead
    ))
    app.add_handler(CallbackQueryHandler(show_option, pattern=r"^opt:\d+$"))
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("bus", bus))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, plain_text))
    print("Bot is running. Press Ctrl + C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
