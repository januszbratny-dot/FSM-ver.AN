import os
import tempfile
import json
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Optional, Tuple
import streamlit as st
import pandas as pd
import plotly.express as px
from loguru import logger

STATE_FILE = "fsm_state.json"  # lub "state.json", jeśli używasz innej nazwy

st.sidebar.markdown("### 🔧 Ustawienia aplikacji")

if st.sidebar.button("🗑️ Resetuj dane (usuń plik JSON)"):
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            st.sidebar.success("Plik stanu został usunięty.")
        else:
            st.sidebar.info("Plik już nie istnieje.")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Nie udało się usunąć pliku: {e}")


STATE_FILE = "schedules.json"

# --- Pomocnicze funkcje czasu ---

def parse_time(s: str) -> time:
    try:
        parts = s.split(":")
        if len(parts) == 2:
            return time(int(parts[0]), int(parts[1]))
        elif len(parts) == 3:
            return time(int(parts[0]), int(parts[1]), int(parts[2].split(".")[0]))
    except Exception:
        pass
    raise ValueError(f"Invalid time format: {s}")

def parse_datetime_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")

def time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute

# --- Slot types parser ---

def parse_slot_types(slot_types_text: str) -> List[Dict]:
    result = []
    for line in slot_types_text.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 3:
            raise ValueError(f"Invalid line: {line}")
        name = parts[0]
        minutes = int(parts[1])
        weight = float(parts[2])
        result.append({"name": name, "minutes": minutes, "weight": weight})
    return result

# --- Zapis/odczyt JSON ---

def save_state_to_json(filename: str = STATE_FILE):
    data = {
        "slot_types": st.session_state.slot_types,
        "brygady": st.session_state.brygady,
        "working_hours": [
            st.session_state.working_hours[0].isoformat(),
            st.session_state.working_hours[1].isoformat(),
        ],
        "schedules": {},
    }
    for b, days in st.session_state.schedules.items():
        data["schedules"][b] = {}
        for day_str, slots in days.items():
            data["schedules"][b][day_str] = [
                {**s, "start": s["start"].isoformat(), "end": s["end"].isoformat()}
                for s in slots
            ]
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tempname = tf.name
    os.replace(tempname, filename)

def load_state_from_json(filename: str = STATE_FILE) -> bool:
    if not os.path.exists(filename):
        return False
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Nie można wczytać pliku JSON: {e}")
        return False

    st.session_state.slot_types = data.get("slot_types", [])
    st.session_state.brygady = data.get("brygady", [])
    wh = data.get("working_hours", ["08:00", "16:00"])
    st.session_state.working_hours = (parse_time(wh[0]), parse_time(wh[1]))

    schedules = {}
    for b, days in data.get("schedules", {}).items():
        schedules[b] = {}
        for day_str, slots in days.items():
            schedules[b][day_str] = [
                {
                    **s,
                    "start": parse_datetime_iso(s["start"]),
                    "end": parse_datetime_iso(s["end"]),
                }
                for s in slots
            ]
    st.session_state.schedules = schedules
    return True

# --- Pomocnicze operacje ---

def add_slot_to_brygada(brygada: str, day: date, slot: Dict, save: bool = True):
    st.session_state.schedules.setdefault(brygada, {})
    day_str = day.isoformat()
    st.session_state.schedules[brygada].setdefault(day_str, []).append(slot)
    st.session_state.schedules[brygada][day_str].sort(key=lambda s: s["start"])
    if save:
        save_state_to_json()

# --- Inicjalizacja ---

def initialize_state():
    if "slot_types" not in st.session_state:
        if not load_state_from_json():
            st.session_state.slot_types = [
                {"name": "Standard", "minutes": 60, "weight": 1.0}
            ]
            st.session_state.brygady = ["Brygada A", "Brygada B"]
            st.session_state.working_hours = (time(8, 0), time(16, 0))
            st.session_state.schedules = {}
    if "schedules" not in st.session_state:
        st.session_state.schedules = {}

# --- Aplikacja Streamlit ---

st.set_page_config(page_title="Planowanie slotów", layout="wide")
st.title("📅 System planowania slotów")
initialize_state()

tab_admin, tab_schedule, tab_stats = st.tabs(["⚙️ Ustawienia", "📆 Rezerwacje", "📊 Statystyki"])

# --- Zakładka Ustawienia ---
with tab_admin:
    st.header("Parametry systemu")

    st.subheader("Typy slotów")
    default_text = "\n".join(
        f"{s['name']}, {s['minutes']}, {s['weight']}"
        for s in st.session_state.slot_types
    )
    new_text = st.text_area("Wprowadź typy slotów (nazwa, minuty, waga):", value=default_text, height=120)
    if st.button("💾 Zapisz typy slotów"):
        try:
            st.session_state.slot_types = parse_slot_types(new_text)
            save_state_to_json()
            st.success("Typy slotów zapisane.")
        except Exception as e:
            st.error(f"Błąd: {e}")

    st.subheader("Brygady")
    st.session_state.brygady = st.text_area(
        "Lista brygad (jedna na linię):", value="\n".join(st.session_state.brygady)
    ).splitlines()

    st.subheader("Godziny pracy")
    c1, c2 = st.columns(2)
    with c1:
        start_str = st.text_input("Start", st.session_state.working_hours[0].isoformat(timespec="minutes"))
    with c2:
        end_str = st.text_input("Koniec", st.session_state.working_hours[1].isoformat(timespec="minutes"))

    if st.button("💾 Zapisz ustawienia pracy"):
        try:
            st.session_state.working_hours = (parse_time(start_str), parse_time(end_str))
            save_state_to_json()
            st.success("Ustawienia zapisane.")
        except Exception as e:
            st.error(f"Błąd: {e}")

# --- Zakładka Rezerwacje ---
with tab_schedule:
    st.header("Rezerwacja terminu")

    slot_names = [s["name"] for s in st.session_state.slot_types]
    slot_type_name = st.selectbox("Typ slotu", slot_names)
    slot_type = next(s for s in st.session_state.slot_types if s["name"] == slot_type_name)
    slot_duration = timedelta(minutes=slot_type["minutes"])

    # Nawigator dni
    if "current_day" not in st.session_state:
        st.session_state.current_day = date.today()

    col_prev, col_day, col_next = st.columns([1,2,1])
    with col_prev:
        if st.button("⬅️ Poprzedni dzień"):
            st.session_state.current_day -= timedelta(days=1)
    with col_next:
        if st.button("Następny dzień ➡️"):
            st.session_state.current_day += timedelta(days=1)

    with col_day:
        st.markdown(f"### {st.session_state.current_day.strftime('%A, %d %B %Y')}")

    day = st.session_state.current_day
    available_slots = []

    for brygada in st.session_state.brygady:
        wh_start, wh_end = st.session_state.working_hours
        day_start = datetime.combine(day, wh_start)
        day_end = datetime.combine(day, wh_end)
        if day_end <= day_start:
            day_end += timedelta(days=1)
        current = day_start
        while current + slot_duration <= day_end:
            overlap = False
            for s in st.session_state.schedules.get(brygada, {}).get(day.isoformat(), []):
                if not (current + slot_duration <= s["start"] or current >= s["end"]):
                    overlap = True
                    break
            if not overlap:
                available_slots.append({
                    "brygada": brygada,
                    "start": current,
                    "end": current + slot_duration,
                })
            current += timedelta(minutes=30)

    # Sortujemy wszystkie sloty razem
    available_slots.sort(key=lambda s: s["start"])

    if not available_slots:
        st.info("Brak wolnych terminów w tym dniu.")
    else:
        for s in available_slots:
            col1, col2, col3, col4 = st.columns([2,2,3,2])
            with col1:
                st.write(s["start"].strftime("%H:%M") + " – " + s["end"].strftime("%H:%M"))
            with col2:
                st.write(f"**{s['brygada']}**")
            with col3:
                if st.button("Rezerwuj", key=f"book_{s['brygada']}_{s['start']}"):
                    slot = {
                        "client": "Nowy klient",
                        "brygada": s["brygada"],
                        "day": s["start"].date(),
                        "slot_type": slot_type_name,
                        "start": s["start"],
                        "end": s["end"],
                        "duration_min": slot_type["minutes"],
                        "weight": slot_type["weight"],
                    }
                    add_slot_to_brygada(s["brygada"], day, slot, save=True)
                    st.success(f"Zarezerwowano {s['brygada']} o {s['start'].strftime('%H:%M')}")
                    st.rerun()

# --- Zakładka Statystyki ---
with tab_stats:
    st.header("Harmonogram i statystyki")

    data_rows = []
    for b, days in st.session_state.schedules.items():
        for d, slots in days.items():
            for s in slots:
                data_rows.append({
                    "Brygada": b,
                    "Dzień": d,
                    "Start": s["start"],
                    "Koniec": s["end"],
                    "Typ": s["slot_type"],
                })

    if not data_rows:
        st.info("Brak danych do wyświetlenia.")
    else:
        df = pd.DataFrame(data_rows)
        st.dataframe(df)

        fig = px.timeline(
            df,
            x_start="Start", x_end="Koniec",
            y="Brygada", color="Typ",
            title="Harmonogram (Gantt)"
        )
        st.plotly_chart(fig, use_container_width=True)
