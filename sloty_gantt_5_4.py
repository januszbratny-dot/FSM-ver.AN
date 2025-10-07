import streamlit as st
import pandas as pd
import plotly.express as px
import json
import os
import uuid
import tempfile
import logging
from datetime import datetime, timedelta, date, time
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

# ==========================================
# 🔧 KONFIGURACJA
# ==========================================
STORAGE_FILENAME = "schedules.json"
SEARCH_STEP_MINUTES = 15
DEFAULT_WORK_START = time(8, 0)
DEFAULT_WORK_END = time(16, 0)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scheduler")

# ==========================================
# 🧱 MODELE DANYCH
# ==========================================
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

# ==========================================
# 🧩 FUNKCJE POMOCNICZE
# ==========================================
def _datetime_to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None

def _time_to_iso(t: time) -> str:
    return t.isoformat()

def parse_datetime_iso(s: Optional[str]) -> Optional[datetime]:
    if s is None:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)

def parse_time_str(t: str) -> time:
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(t, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Nie można sparsować czasu: {t}")

# ==========================================
# 💾 ZAPIS / ODCZYT JSON
# ==========================================
def schedules_to_jsonable() -> Dict:
    data = {}
    for b, days in st.session_state.schedules.items():
        data[b] = {}
        for d, slots in days.items():
            data[b][d] = [
                {
                    "id": s.get("id"),
                    "start": _datetime_to_iso(s["start"]),
                    "end": _datetime_to_iso(s["end"]),
                    "slot_type": s["slot_type"],
                    "duration_min": s["duration_min"],
                    "client": s["client"],
                    "pref_range": s.get("pref_range"),
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
    except Exception:
        logger.exception("Failed to load schedules JSON")
        return False

    st.session_state.slot_types = data.get("slot_types", [])
    st.session_state.brygady = data.get("brygady", [])
    st.session_state.working_hours = {
        b: (parse_time_str(wh[0]), parse_time_str(wh[1]))
        for b, wh in data.get("working_hours", {}).items()
    }
    st.session_state.schedules = data.get("schedules", {})
    return True

# ==========================================
# 🚀 INICJALIZACJA
# ==========================================
if "slot_types" not in st.session_state:
    if not load_state_from_json():
        st.session_state.slot_types = [{"name": "Standard", "minutes": 60, "weight": 1.0}]
        st.session_state.brygady = ["Brygada 1", "Brygada 2"]
        st.session_state.working_hours = {}
        st.session_state.schedules = {}

def brygada_key(i: int, field: str) -> str:
    """Tworzy stabilny klucz dla widżetów Streamlit."""
    return f"brygada_{i}_{field}"

def ensure_brygady_in_state(brygady_list: List[str]):
    for i, b in enumerate(brygady_list):
        if b not in st.session_state.working_hours:
            st.session_state.working_hours[b] = (DEFAULT_WORK_START, DEFAULT_WORK_END)
        if b not in st.session_state.schedules:
            st.session_state.schedules[b] = {}

ensure_brygady_in_state(st.session_state.brygady)

# ==========================================
# ⚙️ KONFIGURACJA W SIDEBARZE
# ==========================================
st.sidebar.subheader("⚙️ Konfiguracja")

with st.sidebar.form("config_form"):
    st.markdown("**Typy slotów (format: Nazwa, minuty, waga)**")
    default_slot_types_text = "\n".join(
        f"{s['name']},{s['minutes']},{s['weight']}"
        for s in st.session_state.slot_types
    )
    slot_types_input = st.text_area(
        " ",
        value=default_slot_types_text,
        key="slot_types_input",
        height=100
    )

    st.markdown("**Lista brygad**")
    default_brygady_text = "\n".join(st.session_state.brygady)
    brygady_input = st.text_area(
        " ",
        value=default_brygady_text,
        key="brygady_input",
        height=80
    )

    submitted = st.form_submit_button("💾 Zapisz konfigurację")
    reset_default = st.form_submit_button("♻️ Przywróć ustawienia domyślne")

if submitted:
    new_slot_types = []
    for line in slot_types_input.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            name, minutes, weight = [x.strip() for x in line.split(",")]
            new_slot_types.append({
                "name": name,
                "minutes": int(minutes),
                "weight": float(weight)
            })
        except Exception as e:
            st.warning(f"Błąd w linii: `{line}` → {e}")

    st.session_state.slot_types = new_slot_types
    st.session_state.brygady = [b.strip() for b in brygady_input.splitlines() if b.strip()]
    ensure_brygady_in_state(st.session_state.brygady)

    save_state_to_json()
    st.success("✅ Konfiguracja zapisana pomyślnie.")
    st.rerun()

if reset_default:
    st.session_state.slot_types = [{"name": "Standard", "minutes": 60, "weight": 1.0}]
    st.session_state.brygady = ["Brygada 1", "Brygada 2"]
    st.session_state.working_hours = {}
    st.session_state.schedules = {}
    save_state_to_json()
    st.success("🔄 Przywrócono ustawienia domyślne.")
    st.rerun()

# ==========================================
# 🕓 GODZINY PRACY
# ==========================================
st.header("🕓 Godziny pracy brygad")
for i, b in enumerate(st.session_state.brygady):
    start, end = st.session_state.working_hours.get(b, (DEFAULT_WORK_START, DEFAULT_WORK_END))
    col1, col2 = st.columns(2)
    with col1:
        start_t = st.time_input(f"{b} – początek", start, key=brygada_key(i, "start"))
    with col2:
        end_t = st.time_input(f"{b} – koniec", end, key=brygada_key(i, "end"))
    st.session_state.working_hours[b] = (start_t, end_t)
save_state_to_json()

# ==========================================
# 📅 HARMONOGRAM (przykładowy widok)
# ==========================================
st.header("📅 Harmonogram")
for brygada, days in st.session_state.schedules.items():
    st.subheader(f"📍 {brygada}")
    for d, slots in days.items():
        st.markdown(f"**{d}:**")
        for s in slots:
            st.write(f"{s['start']} - {s['end']} ({s['slot_type']}) - {s['client']}")

st.info("💡 Tu możesz dodać moduł planowania klientów, wizualizacje i raporty.")

