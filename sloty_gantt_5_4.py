import streamlit as st
import pandas as pd
import plotly.express as px
import random
import os
import json
import tempfile
import logging
from datetime import datetime, timedelta, date, time
from typing import List, Dict, Tuple, Optional

# ---------------------- CONFIG ----------------------
STORAGE_FILENAME = "schedules.json"
SEARCH_STEP_MINUTES = 15
DEFAULT_WORK_START = time(8, 0)
DEFAULT_WORK_END = time(16, 0)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler")

# ---------------------- HELPERS ----------------------
def _datetime_to_iso(dt: datetime) -> str:
    return dt.isoformat()

def _time_to_iso(t: time) -> str:
    return t.isoformat()

def parse_datetime_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)

def parse_time_str(t: str) -> time:
    try:
        return time.fromisoformat(t)
    except Exception:
        for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(t, fmt).time()
            except ValueError:
                continue
    raise ValueError(f"Nie można sparsować czasu: {t}")

def _wh_minutes(wh_start: time, wh_end: time) -> int:
    start_dt = datetime.combine(date.today(), wh_start)
    end_dt = datetime.combine(date.today(), wh_end)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return int((end_dt - start_dt).total_seconds() // 60)

def compute_arrival_window(slot_start: datetime) -> Tuple[datetime, datetime]:
    """Oblicza przedział przyjazdu Brygady dla danego slotu."""
    arrival_start = slot_start - timedelta(minutes=st.session_state.buffer_before)
    arrival_end = slot_start + timedelta(minutes=st.session_state.buffer_after)
    return arrival_start, arrival_end

# ---------------------- PERSISTENCE ----------------------
def save_state_to_json(filename: str = STORAGE_FILENAME):
    data = {}
    for b, days in st.session_state.schedules.items():
        data[b] = {}
        for d, slots in days.items():
            data[b][d] = [
                {
                    "start": _datetime_to_iso(s["start"]),
                    "end": _datetime_to_iso(s["end"]),
                    "slot_type": s["slot_type"],
                    "duration_min": s["duration_min"],
                    "client": s["client"],
                    "arrival_window": [_datetime_to_iso(s["arrival_window"][0]), _datetime_to_iso(s["arrival_window"][1])] if s.get("arrival_window") else None
                }
                for s in slots
            ]
    save_data = {
        "slot_types": st.session_state.slot_types,
        "brygady": st.session_state.brygady,
        "working_hours": {b: (_time_to_iso(wh[0]), _time_to_iso(wh[1])) for b, wh in st.session_state.working_hours.items()},
        "schedules": data,
        "clients_added": st.session_state.clients_added,
        "client_counter": st.session_state.client_counter,
        "not_found_counter": st.session_state.not_found_counter
    }
    dirn = os.path.dirname(os.path.abspath(filename)) or "."
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dirn, delete=False) as tf:
        json.dump(save_data, tf, ensure_ascii=False, indent=2)
        tmpname = tf.name
    os.replace(tmpname, filename)
    logger.info(f"State saved to {filename}")

def load_state_from_json(filename: str = STORAGE_FILENAME) -> bool:
    if not os.path.exists(filename):
        return False
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    st.session_state.slot_types = data.get("slot_types", [{"name":"Standard","minutes":60,"weight":1}])
    st.session_state.brygady = data.get("brygady", ["Brygada 1", "Brygada 2"])
    st.session_state.working_hours = {}
    for b, wh in data.get("working_hours", {}).items():
        st.session_state.working_hours[b] = (parse_time_str(wh[0]), parse_time_str(wh[1]))
    st.session_state.schedules = {}
    for b, days in data.get("schedules", {}).items():
        st.session_state.schedules[b] = {}
        for d, slots in days.items():
            st.session_state.schedules[b][d] = [
                {
                    "start": parse_datetime_iso(s["start"]),
                    "end": parse_datetime_iso(s["end"]),
                    "slot_type": s["slot_type"],
                    "duration_min": s["duration_min"],
                    "client": s["client"],
                    "arrival_window": tuple(parse_datetime_iso(dt) for dt in s["arrival_window"]) if s.get("arrival_window") else None
                }
                for s in slots
            ]
    st.session_state.clients_added = data.get("clients_added", [])
    st.session_state.client_counter = data.get("client_counter", 1)
    st.session_state.not_found_counter = data.get("not_found_counter", 0)
    return True

# ---------------------- INITIALIZATION ----------------------
if "slot_types" not in st.session_state:
    load_state_from_json()

if "working_hours" not in st.session_state:
    st.session_state.working_hours = {b: (DEFAULT_WORK_START, DEFAULT_WORK_END) for b in st.session_state.brygady}

if "schedules" not in st.session_state:
    st.session_state.schedules = {b:{} for b in st.session_state.brygady}

if "buffer_before" not in st.session_state:
    st.session_state.buffer_before = 15
if "buffer_after" not in st.session_state:
    st.session_state.buffer_after = 15

# ---------------------- SIDEBAR ----------------------
with st.sidebar:
    st.subheader("⏱ Bufory przyjazdu Brygady")
    st.session_state.buffer_before = st.number_input("Czas rezerwowy przed [min]", min_value=0, max_value=120, value=st.session_state.buffer_before, step=5)
    st.session_state.buffer_after = st.number_input("Czas rezerwowy po [min]", min_value=0, max_value=120, value=st.session_state.buffer_after, step=5)

# ---------------------- UI ----------------------
st.set_page_config(page_title="Harmonogram slotów", layout="wide")
st.title("📅 Harmonogram slotów - Tydzień")

# --- Rezerwacja slotu ---
st.subheader("➕ Rezerwacja terminu")
if "booking_day" not in st.session_state:
    st.session_state.booking_day = date.today()
booking_day = st.session_state.booking_day

client_name = st.text_input("Nazwa klienta", value=f"Klient {st.session_state.client_counter}")
slot_duration = st.number_input("Czas trwania slotu [min]", min_value=15, max_value=480, value=60, step=15)

# --- Dostępne sloty ---
st.markdown("### 🕒 Dostępne sloty w wybranym dniu")
available_slots = []
for b in st.session_state.brygady:
    wh_start, wh_end = st.session_state.working_hours[b]
    start_dt = datetime.combine(booking_day, wh_start)
    end_dt = datetime.combine(booking_day, wh_end)
    t = start_dt
    while t + timedelta(minutes=slot_duration) <= end_dt:
        # sprawdzenie kolizji
        conflict = False
        for s in st.session_state.schedules.get(b, {}).get(booking_day.strftime("%Y-%m-%d"), []):
            if not (t + timedelta(minutes=slot_duration) <= s["start"] or t >= s["end"]):
                conflict = True
                break
        if not conflict:
            available_slots.append({"brygada":b,"start":t,"end":t+timedelta(minutes=slot_duration)})
        t += timedelta(minutes=SEARCH_STEP_MINUTES)

if not available_slots:
    st.info("Brak dostępnych slotów dla wybranego dnia.")
else:
    for i, s in enumerate(available_slots):
        col1, col2, col3 = st.columns([2,2,1])
        arrival_start, arrival_end = compute_arrival_window(s["start"])
        col1.write(f"🕐 {s['start'].strftime('%H:%M')} – {s['end'].strftime('%H:%M')}")
        col2.write(f"👷 Brygada: {s['brygada']}")
        col2.write(f"🚚 Przedział przyjazdu: {arrival_start.strftime('%H:%M')} – {arrival_end.strftime('%H:%M')}")
        if col3.button("Zarezerwuj w tym slocie", key=f"book_{i}"):
            slot = {
                "start": s["start"],
                "end": s["end"],
                "slot_type": "Standard",
                "duration_min": slot_duration,
                "client": client_name,
                "arrival_window": (arrival_start, arrival_end)
            }
            day_str = booking_day.strftime("%Y-%m-%d")
            st.session_state.schedules.setdefault(s["brygada"], {}).setdefault(day_str, []).append(slot)
            st.session_state.client_counter +=1
            save_state_to_json()
            st.success(f"Zarezerwowano {s['start'].strftime('%H:%M')} – {s['end'].strftime('%H:%M')} w {s['brygada']}")
            st.rerun()

# --- Tabela harmonogramu ---
all_slots = []
for b in st.session_state.brygady:
    for day_str, slots in st.session_state.schedules.get(b, {}).items():
        for s in slots:
            all_slots.append({
                "Brygada":b,
                "Dzień":day_str,
                "Klient":s["client"],
                "Typ":s.get("slot_type",""),
                "Start":s["start"],
                "Koniec":s["end"],
                "Przedział przyjazdu":f"{s['arrival_window'][0].strftime('%H:%M')} – {s['arrival_window'][1].strftime('%H:%M')}" if s.get("arrival_window") else "",
                "_brygada":b,
                "_start_iso":s["start"].isoformat()
            })

st.subheader("📋 Tabela harmonogramu")
if all_slots:
    df = pd.DataFrame(all_slots)
    st.dataframe(df.drop(columns=["_brygada","_start_iso"]))
else:
    st.info("Brak zaplanowanych slotów.")

# --- Zarządzanie slotami ---
st.subheader("🧰 Zarządzaj slotami")
if all_slots:
    for row in all_slots:
        cols = st.columns([1,2,2,2,1])
        cols[0].write(row["Dzień"])
        cols[1].write(f"{row['Klient']} — {row['Typ']}")
        cols[2].write(f"{row['Start'].strftime('%H:%M')} – {row['Koniec'].strftime('%H:%M')}")
        cols[3].write(f"🚚 {row['Przedział przyjazdu']}")
        if cols[4].button("Usuń", key=f"del_{row['_brygada']}_{row['_start_iso']}"):
            day_dict = st.session_state.schedules[row["_brygada"]][row["Dzień"]]
            st.session_state.schedules[row["_brygada"]][row["Dzień"]] = [s for s in day_dict if s["start"].isoformat()!=row["_start_iso"]]
            save_state_to_json()
            st.rerun()
