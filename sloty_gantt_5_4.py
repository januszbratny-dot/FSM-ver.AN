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

# ---------------------- HELPERS: SERIALIZATION ----------------------

def _datetime_to_iso(dt: datetime) -> str:
    return dt.isoformat()


def _time_to_iso(t: time) -> str:
    return t.isoformat()


def parse_datetime_iso(s: str) -> datetime:
    """Parse ISO datetimes; support trailing 'Z' by converting to +00:00."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def parse_time_str(t: str) -> time:
    """Robust parsing for time strings (H:M, H:M:S, H:M:S.sss)."""
    try:
        # Prefer time.fromisoformat if available
        return time.fromisoformat(t)
    except Exception:
        for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(t, fmt).time()
            except ValueError:
                continue
    raise ValueError(f"Nie można sparsować czasu: {t}")


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
    """Save state atomically to avoid file corruption on concurrent writes."""
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
        st.session_state.slot_types = [
            {"name": "Standard", "minutes": 60, "weight": 1.0}
        ]
        st.session_state.brygady = ["Brygada 1", "Brygada 2"]
        st.session_state.working_hours = {}
        st.session_state.schedules = {}
        st.session_state.clients_added = []
        st.session_state.balance_horizon = "week"
        st.session_state.client_counter = 1
        st.session_state.not_found_counter = 0

# stable keys for widgets (avoid using raw brygada names as keys)
def brygada_key(i: int, field: str) -> str:
    return f"brygada_{i}_{field}"

# ensure brygady presence in working_hours and schedules

def ensure_brygady_in_state(brygady_list: List[str]):
    for i, b in enumerate(brygady_list):
        if b not in st.session_state.working_hours:
            st.session_state.working_hours[b] = (DEFAULT_WORK_START, DEFAULT_WORK_END)
        if b not in st.session_state.schedules:
            st.session_state.schedules[b] = {}


# ---------------------- PARSERS & VALIDATION ----------------------

def parse_slot_types(text: str) -> List[Dict]:
    out: List[Dict] = []
    for i, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        try:
            name = parts[0]
            minutes = int(parts[1]) if len(parts) > 1 else None
            weight = float(parts[2]) if len(parts) > 2 else 1.0
            if minutes is None or minutes <= 0:
                raise ValueError("minutes must be > 0")
            if weight < 0:
                raise ValueError("weight must be >= 0")
            out.append({"name": name, "minutes": minutes, "weight": weight})
        except Exception as e:
            st.warning(f"Linia {i} pominieta w 'Typy slotów': {e}")
    return out


def weighted_choice(slot_types: List[Dict]) -> Optional[str]:
    if not slot_types:
        return None
    names = [s["name"] for s in slot_types]
    weights = [s.get("weight", 1) for s in slot_types]
    return random.choices(names, weights=weights, k=1)[0]


# ---------------------- SCHEDULE MANAGEMENT ----------------------

def get_day_slots_for_brygada(brygada: str, day: date) -> List[Dict]:
    d = day.strftime("%Y-%m-%d")
    return sorted(st.session_state.schedules.get(brygada, {}).get(d, []), key=lambda s: s["start"])


def add_slot_to_brygada(brygada: str, day: date, slot: Dict, save: bool = True):
    d = day.strftime("%Y-%m-%d")
    if brygada not in st.session_state.schedules:
        st.session_state.schedules[brygada] = {}
    if d not in st.session_state.schedules[brygada]:
        st.session_state.schedules[brygada][d] = []
    st.session_state.schedules[brygada][d].append(slot)
    st.session_state.schedules[brygada][d].sort(key=lambda s: s["start"])
    if save:
        save_state_to_json()


def delete_slot(brygada: str, day_str: str, start_iso: str):
    st.session_state.schedules.setdefault(brygada, {})
    slots = st.session_state.schedules[brygada].get(day_str, [])
    before = len(slots)
    st.session_state.schedules[brygada][day_str] = [s for s in slots if s["start"].isoformat() != start_iso]
    after = len(st.session_state.schedules[brygada][day_str])
    if before != after:
        save_state_to_json()
        logger.info(f"Deleted slot {start_iso} on {brygada} {day_str}")


def _wh_minutes(wh_start: time, wh_end: time) -> int:
    """Return minutes in working hours. Support overnight shifts (end <= start) by wrapping to next day."""
    start_dt = datetime.combine(date.today(), wh_start)
    end_dt = datetime.combine(date.today(), wh_end)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return int((end_dt - start_dt).total_seconds() // 60)


def schedule_client_immediately(client_name: str, slot_type_name: str, day: date, pref_start: time, pref_end: time) -> Tuple[bool, Optional[Dict]]:
    slot_type = next((s for s in st.session_state.slot_types if s["name"] == slot_type_name), None)
    if not slot_type:
        return False, None
    dur = timedelta(minutes=slot_type["minutes"])
    candidates: List[Tuple[str, datetime, datetime]] = []

    for b in st.session_state.brygady:
        existing = get_day_slots_for_brygada(b, day)
        wh_start, wh_end = st.session_state.working_hours.get(b, (DEFAULT_WORK_START, DEFAULT_WORK_END))

        # compute pref constrained by working hours - support overnight
        # we represent start/end as datetimes; if wh_end <= wh_start treat as next day
        day_start_dt = datetime.combine(day, wh_start)
        day_end_dt = datetime.combine(day, wh_end)
        if day_end_dt <= day_start_dt:
            day_end_dt += timedelta(days=1)

        pref_start_dt = datetime.combine(day, pref_start)
        pref_end_dt = datetime.combine(day, pref_end)
        if pref_end_dt <= pref_start_dt:
            pref_end_dt += timedelta(days=1)

        start_dt = max(day_start_dt, pref_start_dt)
        end_dt = min(day_end_dt, pref_end_dt)

        t = start_dt
        while t + dur <= end_dt:
            overlap = any(not (t + dur <= s["start"] or t >= s["end"]) for s in existing)
            if not overlap:
                candidates.append((b, t, t + dur))
            t += timedelta(minutes=SEARCH_STEP_MINUTES)

    if not candidates:
        return False, None

    # wybierz najwcześniejszy start; przy równych starts -> najmniej wykorzystana brygada
    candidates.sort(key=lambda x: (x[1], sum(s["duration_min"] for d in st.session_state.schedules.get(x[0], {}).values() for s in d)))
    brygada, start, end = candidates[0]
    slot = {"start": start, "end": end, "slot_type": slot_type_name, "duration_min": slot_type["minutes"], "client": client_name}

    add_slot_to_brygada(brygada, day, slot)
    return True, slot


# ---------------------- PREDEFINED SLOTS & UTIL ----------------------
PREFERRED_SLOTS = {
    "8:00-11:00": (time(8, 0), time(11, 0)),
    "11:00-14:00": (time(11, 0), time(14, 0)),
    "14:00-17:00": (time(14, 0), time(17, 0)),
    "17:00-20:00": (time(17, 0), time(20, 0)),
}


def get_week_days(reference_day: date) -> List[date]:
    monday = reference_day - timedelta(days=reference_day.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


# ---------------------- UI ----------------------
st.set_page_config(page_title="Harmonogram slotów", layout="wide")
st.title("📅 Harmonogram slotów - Tydzień")

with st.sidebar:
    st.subheader("⚙️ Konfiguracja")

    # slot types editor with validation
    txt = st.text_area("Typy slotów (format: Nazwa, minuty, waga)",
                       value="\n".join(f"{s['name']},{s['minutes']},{s.get('weight',1)}" for s in st.session_state.slot_types))
    parsed = parse_slot_types(txt)
    if parsed:
        st.session_state.slot_types = parsed

    # brygady editor
    txt_b = st.text_area("Lista brygad", value="\n".join(st.session_state.brygady))
    brygady_new = [line.strip() for line in txt_b.splitlines() if line.strip()]
    if brygady_new and brygady_new != st.session_state.brygady:
        st.session_state.brygady = brygady_new
    ensure_brygady_in_state(st.session_state.brygady)

    st.markdown("---")
    st.write("Godziny pracy (możesz edytować każdą brygadę)")
    for i, b in enumerate(st.session_state.brygady):
        # stable keys so widgets don't lose state when name changes
        start_t = st.time_input(f"Start {b}", value=st.session_state.working_hours[b][0], key=brygada_key(i, "start"))
        end_t = st.time_input(f"Koniec {b}", value=st.session_state.working_hours[b][1], key=brygada_key(i, "end"))
        st.session_state.working_hours[b] = (start_t, end_t)

    st.markdown("---")
    if st.button("🗑️ Wyczyść harmonogram"):
        st.session_state.schedules = {b: {} for b in st.session_state.brygady}
        st.session_state.clients_added = []
        st.session_state.client_counter = 1
        st.session_state.not_found_counter = 0
        save_state_to_json()
        st.success("Harmonogram wyczyszczony.")

# week navigation
if "week_offset" not in st.session_state:
    st.session_state.week_offset = 0

with st.sidebar:
    st.subheader("⬅️ Wybór tygodnia")
    col1, col2 = st.columns(2)
    if col1.button("‹ Poprzedni tydzień"):
        st.session_state.week_offset -= 1
    if col2.button("Następny tydzień ›"):
        st.session_state.week_offset += 1

week_ref = date.today() + timedelta(weeks=st.session_state.week_offset)
week_days = get_week_days(week_ref)
st.sidebar.write(f"Tydzień: {week_days[0].strftime('%d-%m-%Y')} – {week_days[-1].strftime('%d-%m-%Y')}")

# ---------------------- Dodaj klienta (zmieniony UI: wybór dostępnego slotu) ----------------------
st.subheader("➕ Rezerwacja terminu")

# Imię klienta
with st.container():
    default_client = f"Klient {st.session_state.client_counter}"
    client_name = st.text_input("Nazwa klienta", value=default_client)

# Wybór typu slotu (pozostawiamy)
slot_names = [s["name"] for s in st.session_state.slot_types]
if not slot_names:
    slot_names = ["Standard"]
    st.session_state.slot_types = [{"name": "Standard", "minutes": 60, "weight": 1.0}]
auto_type = weighted_choice(st.session_state.slot_types) or slot_names[0]
idx = slot_names.index(auto_type) if auto_type in slot_names else 0
slot_type_name = st.selectbox("Typ slotu", slot_names, index=idx)
slot_type = next((s for s in st.session_state.slot_types if s["name"] == slot_type_name), slot_names[0])
slot_duration = timedelta(minutes=slot_type["minutes"])

# Navigator dni dla rezerwacji (pojedynczy dzień, z możliwością przejścia)
if "booking_day" not in st.session_state:
    st.session_state.booking_day = date.today()

col_prev, col_mid, col_next = st.columns([1, 2, 1])
with col_prev:
    if st.button("⬅️ Poprzedni dzień", key="booking_prev"):
        st.session_state.booking_day -= timedelta(days=1)
with col_next:
    if st.button("Następny dzień ➡️", key="booking_next"):
        st.session_state.booking_day += timedelta(days=1)
with col_mid:
    st.markdown(f"### {st.session_state.booking_day.strftime('%A, %d %B %Y')}")

booking_day = st.session_state.booking_day

# funkcja do generowania dostępnych slotów dla danego dnia i typu slotu
def get_available_slots_for_day(day: date, slot_minutes: int, step_minutes: int = SEARCH_STEP_MINUTES) -> List[Dict]:
    available = []
    for b in st.session_state.brygady:
        wh_start, wh_end = st.session_state.working_hours.get(b, (DEFAULT_WORK_START, DEFAULT_WORK_END))
        day_start = datetime.combine(day, wh_start)
        day_end = datetime.combine(day, wh_end)
        if day_end <= day_start:
            day_end += timedelta(days=1)
        # pobierz istniejące sloty (posortowane)
        existing = get_day_slots_for_brygada(b, day)
        t = day_start
        while t + timedelta(minutes=slot_minutes) <= day_end:
            t_end = t + timedelta(minutes=slot_minutes)
            overlap = any(not (t_end <= s["start"] or t >= s["end"]) for s in existing)
            if not overlap:
                available.append({
                    "brygada": b,
                    "start": t,
                    "end": t_end,
                })
            t += timedelta(minutes=step_minutes)
    # sortuj według czasu, potem brygady
    available.sort(key=lambda x: (x["start"], x["brygada"]))
    return available

available_slots = get_available_slots_for_day(booking_day, slot_type["minutes"], SEARCH_STEP_MINUTES)

st.markdown("---")
if not available_slots:
    st.info("Brak wolnych terminów w tym dniu.")
else:
    st.write("Dostępne sloty:")
    for s in available_slots:
        cols = st.columns([2, 2, 2, 1])
        cols[0].markdown(f"**{s['brygada']}**")
        cols[1].write(f"{s['start'].strftime('%H:%M')} — {s['end'].strftime('%H:%M')}")
        cols[2].write(f"Typ: {slot_type_name} ({slot_type['minutes']} min)")
        # unikalny klucz dla przycisku rezerwacji
        btn_key = f"book_{s['brygada']}_{s['start'].isoformat()}"
        if cols[3].button("Rezerwuj", key=btn_key):
            # przygotuj slot zgodny ze strukturą
            slot = {
                "start": s["start"],
                "end": s["end"],
                "slot_type": slot_type_name,
                "duration_min": slot_type["minutes"],
                "client": client_name,
                "pref_range": None,
            }
            add_slot_to_brygada(s["brygada"], booking_day, slot, save=True)
            st.session_state.clients_added.append({
                "client": client_name,
                "slot_type": slot_type_name,
                "pref_range": None
            })
            st.session_state.client_counter += 1
            st.success(f"✅ Zarezerwowano: {client_name} — {s['brygada']} {s['start'].strftime('%d-%m %H:%M')}")
            # odśwież, żeby slot przestał być widoczny i pojawił się w harmonogramie
            st.experimental_rerun()

# ---------------------- AUTO-FILL FULL DAY (BEZPIECZNY) ----------------------
st.subheader("⚡ Automatyczne dociążenie wszystkich brygad")

# wybór dnia do autofill
day_autofill = st.date_input(
    "Dzień do wypełnienia (pełny dzień)",
    value=date.today(),
    key="autofill_day_full"
)

# przycisk uruchamiający autofill
if st.button("🚀 Wypełnij cały dzień do 100%"):
    added_total = 0
    max_iterations = 5000
    iteration = 0
    slots_added_in_last_iteration = True

    # główna pętla dodawania slotów dopóki coś się udało dodać
    while iteration < max_iterations and slots_added_in_last_iteration:
        iteration += 1
        slots_added_in_last_iteration = False

        for b in st.session_state.brygady:
            wh_start, wh_end = st.session_state.working_hours[b]
            daily_minutes = _wh_minutes(wh_start, wh_end)
            d_str = day_autofill.strftime("%Y-%m-%d")

            # BEZPIECZNIE – upewniamy się, że istnieje słownik dla brygady i dnia
            st.session_state.schedules.setdefault(b, {})
            st.session_state.schedules[b].setdefault(d_str, [])
            slots = st.session_state.schedules[b][d_str]

            used_minutes = sum(s["duration_min"] for s in slots)
            if used_minutes >= daily_minutes:
                continue  # brygada pełna, pomijamy

            # losujemy typ slotu i preferowany przedział
            auto_type = weighted_choice(st.session_state.slot_types) or "Standard"
            auto_pref_label = random.choice(list(PREFERRED_SLOTS.keys()))
            pref_start, pref_end = PREFERRED_SLOTS[auto_pref_label]
            client_name = f"AutoKlient {st.session_state.client_counter}"

            # próbujemy dodać slot (bez zapisu przy każdym dodaniu dla performance)
            ok, info = schedule_client_immediately(client_name, auto_type, day_autofill, pref_start, pref_end)
            if ok:
                # oznaczenie pref_range w slotach
                for s in st.session_state.schedules[b][d_str]:
                    if s["client"] == client_name and s["start"] == info["start"]:
                        s["pref_range"] = auto_pref_label

                # aktualizacja session_state
                st.session_state.clients_added.append({
                    "client": client_name,
                    "slot_type": auto_type,
                    "pref_range": auto_pref_label
                })
                st.session_state.client_counter += 1
                added_total += 1
                slots_added_in_last_iteration = True

    # ustawiamy flagę, która będzie przetworzona w kolejnym renderze
    st.session_state["autofill_done"] = True
    st.session_state["added_total"] = added_total

# ---------------------- BLOK OBSŁUGI RERUN (BEZPIECZNY) ----------------------
if st.session_state.get("autofill_done"):
    added_total = st.session_state.pop("added_total", 0)
    st.session_state.pop("autofill_done", None)

    if added_total > 0:
        st.success(f"✅ Dodano {added_total} klientów – dzień {day_autofill.strftime('%d-%m-%Y')} wypełniony do 100% we wszystkich brygadach.")
    else:
        st.info("ℹ️ Wszystkie brygady są już w pełni obciążone w tym dniu.")

    # BEZPIECZNE wywołanie rerun po zakończeniu renderu
    st.rerun()


# ---------------------- Harmonogram (tabela) ----------------------
all_slots = []
for b in st.session_state.brygady:
    for d in week_days:
        d_str = d.strftime("%Y-%m-%d")
        slots = st.session_state.schedules.get(b, {}).get(d_str, [])
        for s in slots:
            all_slots.append({
                "Brygada": b,
                "Dzień": d_str,
                "Klient": s["client"],
                "Typ": s["slot_type"],
                "Wybrany slot": s.get("pref_range", ""),
                "Start": s["start"],
                "Koniec": s["end"],
                "Czas [min]": s["duration_min"],
                "_start_iso": s["start"].isoformat(),
            })

df = pd.DataFrame(all_slots)
st.subheader("📋 Tabela harmonogramu")
if df.empty:
    st.info("Brak zaplanowanych slotów w tym tygodniu.")
else:
    st.dataframe(df.drop(columns=["_start_iso"]))

# management: delete individual slots
st.subheader("🧰 Zarządzaj slotami")
if not df.empty:
    for idx, row in df.iterrows():
        cols = st.columns([1, 3, 2, 2, 2, 1])
        cols[0].write(row["Dzień"])
        cols[1].write(f"**{row['Klient']}** — {row['Typ']}")
        cols[2].write(f"{row['Start'].strftime('%H:%M')} - {row['Koniec'].strftime('%H:%M')}")
        cols[3].write(row["Brygada"])
        if cols[4].button("Usuń", key=f"del_{row['Brygada']}_{row['_start_iso']}"):
            delete_slot(row["Brygada"], row["Dzień"], row["_start_iso"])
            st.rerun()

# ---------------------- GANTT ----------------------
if not df.empty:
    st.subheader("📊 Wykres Gantta - tydzień")
    fig = px.timeline(df, x_start="Start", x_end="Koniec", y="Brygada", color="Klient", hover_data=["Typ", "Wybrany slot"])
    fig.update_yaxes(autorange="reversed")

    for d in week_days:
        for label, (s, e) in PREFERRED_SLOTS.items():
            fig.add_vrect(x0=datetime.combine(d, s), x1=datetime.combine(d, e), fillcolor="rgba(200,200,200,0.15)", opacity=0.2, layer="below", line_width=0)
            fig.add_vline(x=datetime.combine(d, s), line_width=1, line_dash="dot")
            fig.add_vline(x=datetime.combine(d, e), line_width=1, line_dash="dot")

    st.plotly_chart(fig, use_container_width=True)

# ---------------------- PODSUMOWANIE ----------------------
st.subheader("📌 Podsumowanie")
st.write(f"✅ Dodano klientów: {len(st.session_state.clients_added)}")
st.write(f"❌ Brak slotu dla: {st.session_state.not_found_counter}")

# ---------------------- UTILIZATION PER DAY ----------------------
st.subheader("📊 Wykorzystanie brygad w podziale na dni (%)")
util_data = []
for b in st.session_state.brygady:
    row = {"Brygada": b}
    wh_start, wh_end = st.session_state.working_hours[b]
    daily_minutes = _wh_minutes(wh_start, wh_end)
    for d in week_days:
        d_str = d.strftime("%Y-%m-%d")
        slots = st.session_state.schedules.get(b, {}).get(d_str, [])
        used = sum(s["duration_min"] for s in slots)
        row[d_str] = round(100 * used / daily_minutes, 1) if daily_minutes > 0 else 0
    util_data.append(row)
st.dataframe(pd.DataFrame(util_data))

# ---------------------- TOTAL UTILIZATION ----------------------
st.subheader("📊 Wykorzystanie brygad (sumarycznie)")
rows = []
for b in st.session_state.brygady:
    total = sum(s["duration_min"] for d in st.session_state.schedules.get(b, {}).values() for s in d)
    wh_start, wh_end = st.session_state.working_hours[b]
    daily_minutes = _wh_minutes(wh_start, wh_end)
    available = daily_minutes * len(week_days)
    utilization = round(100 * total / available, 1) if available > 0 else 0
    rows.append({"Brygada": b, "Zajętość [min]": total, "Dostępne [min]": available, "Wykorzystanie [%]": utilization})
st.table(pd.DataFrame(rows))

# ---------------------- OPTIONAL: BASIC TESTS ----------------------

def _run_basic_tests():
    """Uruchom prosty sanity test parsers i scheduler logic jeśli uruchomione manualnie.
    Aby uruchomić: RUN_SCHEDULE_TESTS=1 streamlit run this_file.py
    """
    errors = []
    # parse time
    try:
        assert parse_time_str("08:00").hour == 8
        assert parse_time_str("23:59:59").hour == 23
    except Exception as e:
        errors.append(f"parse_time_str failed: {e}")

    # schedule overlapping test
    test_day = date.today()
    st.session_state.slot_types = [{"name": "T30", "minutes": 30, "weight": 1}]
    st.session_state.brygady = ["T1"]
    st.session_state.working_hours = {"T1": (time(8, 0), time(10, 0))}
    st.session_state.schedules = {"T1": {}}

    ok1, slot1 = schedule_client_immediately("A", "T30", test_day, time(8, 0), time(10, 0))
    ok2, slot2 = schedule_client_immediately("B", "T30", test_day, time(8, 0), time(10, 0))
    ok3, slot3 = schedule_client_immediately("C", "T30", test_day, time(8, 0), time(10, 0))
    # 2 slots fit in 2 hours if step 30 -> actually 4 slots, depending on step; just check no crash
    if not ok1 or not ok2:
        errors.append("Scheduling basic failed")

    if errors:
        st.error('Testy wykryły błędy: ' + '; '.join(errors))
    else:
        st.success('Podstawowe testy przeszły pomyślnie ✅')

if os.environ.get("RUN_SCHEDULE_TESTS"):
    _run_basic_tests()
