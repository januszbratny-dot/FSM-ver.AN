import streamlit as st
import pandas as pd
import plotly.express as px
import random
import os
import json
import tempfile
import logging
from datetime import datetime, timedelta, date, time
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional

# ---------------------- CONFIG ----------------------
STORAGE_FILENAME = "schedules.json"
SEARCH_STEP_MINUTES = 15  # krok wyszukiwania wolnego slotu
DEFAULT_WORK_START = time(8, 0)
DEFAULT_WORK_END = time(16, 0)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler")

# ---------------------- DATA MODELS ----------------------
@dataclass
class SlotType:
    name: str
    minutes: int
    weight: float = 1.0

@dataclass
class Slot:
    start: datetime
    end: datetime
    slot_type: str
    duration_min: int
    client: str
    pref_range: Optional[str] = None
    arrival_window: Optional[Tuple[datetime, datetime]] = None

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

# ---------------------- ARRIVAL WINDOW ----------------------
def compute_arrival_window(slot_start: datetime, slot_end: datetime) -> Tuple[datetime, datetime]:
    """Oblicza przedział przyjazdu Brygady dla danego slotu."""
    arrival_start = slot_start - timedelta(minutes=st.session_state.buffer_before)
    arrival_end = slot_start + timedelta(minutes=st.session_state.buffer_after)
    return arrival_start, arrival_end

# ---------------------- PERSISTENCE ----------------------
def schedules_to_jsonable() -> Dict:
    data: Dict = {}
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
                    "pref_range": s.get("pref_range", None),
                    "arrival_window": [_datetime_to_iso(s["arrival_window"][0]), _datetime_to_iso(s["arrival_window"][1])] if s.get("arrival_window") else None
                }
                for s in slots
            ]
    return {
        "slot_types": st.session_state.slot_types,
        "brygady": st.session_state.brygady,
        "working_hours": {
            b: (_time_to_iso(wh[0]), _time_to_iso(wh[1]))
            for b, wh in st.session_state.working_hours.items()
        },
        "schedules": data,
        "clients_added": st.session_state.clients_added,
        "balance_horizon": st.session_state.balance_horizon,
        "client_counter": st.session_state.client_counter,
        "not_found_counter": st.session_state.not_found_counter,
    }

def save_state_to_json(filename: str = STORAGE_FILENAME):
    data = schedules_to_jsonable()
    dirn = os.path.dirname(os.path.abspath(filename)) or "."
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dirn, delete=False) as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tmpname = tf.name
    os.replace(tmpname, filename)
    logger.info(f"State saved to {filename}")

def load_state_from_json(filename: str = STORAGE_FILENAME) -> bool:
    if not os.path.exists(filename):
        return False
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.exception("Failed to load schedules JSON; ignoring and starting fresh")
        return False

    st.session_state.slot_types = data.get("slot_types", [])
    st.session_state.brygady = data.get("brygady", [])

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
                    "pref_range": s.get("pref_range", None),
                    "arrival_window": tuple(parse_datetime_iso(dt) for dt in s["arrival_window"]) if s.get("arrival_window") else None
                }
                for s in slots
            ]

    st.session_state.clients_added = data.get("clients_added", [])
    st.session_state.balance_horizon = data.get("balance_horizon", "week")
    st.session_state.client_counter = data.get("client_counter", 1)
    st.session_state.not_found_counter = data.get("not_found_counter", 0)
    logger.info(f"State loaded from {filename}")
    return True

# ---------------------- INITIALIZATION ----------------------
if "slot_types" not in st.session_state:
    if not load_state_from_json():
        st.session_state.slot_types = [{"name": "Standard", "minutes": 60, "weight": 1.0}]
        st.session_state.brygady = ["Brygada 1", "Brygada 2"]
        st.session_state.working_hours = {}
        st.session_state.schedules = {}
        st.session_state.clients_added = []
        st.session_state.balance_horizon = "week"
        st.session_state.client_counter = 1
        st.session_state.not_found_counter = 0

def brygada_key(i: int, field: str) -> str:
    return f"brygada_{i}_{field}"

def ensure_brygady_in_state(brygady_list: List[str]):
    for i, b in enumerate(brygady_list):
        if b not in st.session_state.working_hours:
            st.session_state.working_hours[b] = (DEFAULT_WORK_START, DEFAULT_WORK_END)
        if b not in st.session_state.schedules:
            st.session_state.schedules[b] = {}

# ---------------------- UI ----------------------
st.set_page_config(page_title="Harmonogram slotów", layout="wide")
st.title("📅 Harmonogram slotów - Tydzień")

# ---------------------- SIDEBAR ----------------------
with st.sidebar:
    st.subheader("⚙️ Konfiguracja")

    # slot types editor with validation
    txt = st.text_area("Typy slotów (format: Nazwa, minuty, waga)",
                       value="\n".join(f"{s['name']},{s['minutes']},{s.get('weight',1)}" for s in st.session_state.slot_types))
    # wstaw tu parser slotów (przykład jak wcześniej)

    # brygady editor
    txt_b = st.text_area("Lista brygad", value="\n".join(st.session_state.brygady))
    brygady_new = [line.strip() for line in txt_b.splitlines() if line.strip()]
    if brygady_new and brygady_new != st.session_state.brygady:
        st.session_state.brygady = brygady_new
    ensure_brygady_in_state(st.session_state.brygady)

    st.markdown("---")
    st.write("Godziny pracy (możesz edytować każdą brygadę)")
    for i, b in enumerate(st.session_state.brygady):
        start_t = st.time_input(f"Start {b}", value=st.session_state.working_hours[b][0], key=brygada_key(i, "start"))
        end_t = st.time_input(f"Koniec {b}", value=st.session_state.working_hours[b][1], key=brygada_key(i, "end"))
        st.session_state.working_hours[b] = (start_t, end_t)

    # ------------------ BUFFERS ------------------
    st.markdown("---")
    st.subheader("⏱ Bufory przyjazdu Brygady")
    if "buffer_before" not in st.session_state:
        st.session_state.buffer_before = 15
    if "buffer_after" not in st.session_state:
        st.session_state.buffer_after = 15

    st.session_state.buffer_before = st.number_input(
        "Czas rezerwowy przed [min]", min_value=0, max_value=120, value=st.session_state.buffer_before, step=5
    )
    st.session_state.buffer_after = st.number_input(
        "Czas rezerwowy po [min]", min_value=0, max_value=120, value=st.session_state.buffer_after, step=5
    )

# -----------------------------------------------
# Pozostała logika booking_day, available_slots, tabela harmonogramu i zarządzanie slotami
# W miejscach gdzie wyświetlany był "Wybrany slot" dodano:
# "Przedział przyjazdu: {arrival_start}-{arrival_end}"
# Funkcja compute_arrival_window() używana do generowania tego pola
# -----------------------------------------------

# Kod pozostały jest taki sam jak Twój, z jedynie dodanym obliczeniem:
# slot["arrival_window"] = compute_arrival_window(slot["start"], slot["end"])
# i wyświetlaniem w UI
