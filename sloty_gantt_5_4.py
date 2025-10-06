import streamlit as st
import pandas as pd
import plotly.express as px
import random
import os
import json
import tempfile
import logging
import uuid
from datetime import datetime, timedelta, date, time
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional

# ---------------------- CONFIG ----------------------
st.set_page_config(page_title="Sloty Gantt z przedziałem przyjazdu", layout="wide")

# ---------------------- DANE TESTOWE ----------------------
def load_brygady():
    return [
        {"name": "Brygada A", "work_start": time(8, 0), "work_end": time(16, 0)},
        {"name": "Brygada B", "work_start": time(7, 30), "work_end": time(15, 30)}
    ]

def total_work_minutes_for_brygada(brygada, day: date):
    start_dt = datetime.combine(day, brygada["work_start"])
    end_dt = datetime.combine(day, brygada["work_end"])
    return int((end_dt - start_dt).total_seconds() / 60)

# ---------------------- SLOT UTILS ----------------------
def create_slot_dict(brygada: Dict, booking_day: date, slot_start: datetime, slot_end: datetime, czas_rezerwowy_przed: int, czas_rezerwowy_po: int):
    brygada_start_time = datetime.combine(booking_day, brygada["work_start"])
    brygada_end_time = datetime.combine(booking_day, brygada["work_end"])

    arrival_window_start = slot_start - timedelta(minutes=czas_rezerwowy_przed)
    arrival_window_end = slot_start + timedelta(minutes=czas_rezerwowy_po)

    # Korekta przedziału przyjazdu
    if arrival_window_start < brygada_start_time:
        arrival_window_start = brygada_start_time
        arrival_window_end = brygada_start_time + timedelta(minutes=czas_rezerwowy_przed + czas_rezerwowy_po)
    elif arrival_window_end > brygada_end_time:
        arrival_window_end = brygada_end_time
        arrival_window_start = brygada_end_time - timedelta(minutes=czas_rezerwowy_przed + czas_rezerwowy_po)

    return {
        "id": str(uuid.uuid4()),
        "brygada": brygada["name"],
        "day": booking_day.strftime("%Y-%m-%d"),
        "slot_start": slot_start.isoformat(),
        "slot_end": slot_end.isoformat(),
        "arrival_window_start": arrival_window_start.isoformat(),
        "arrival_window_end": arrival_window_end.isoformat()
    }

# ---------------------- GŁÓWNY PROGRAM ----------------------
def main():
    st.sidebar.header("Ustawienia")
    brygady = load_brygady()

    czas_rezerwowy_przed = st.sidebar.number_input("Czas rezerwowy przed (minuty)", min_value=0, max_value=120, value=15)
    czas_rezerwowy_po = st.sidebar.number_input("Czas rezerwowy po (minuty)", min_value=0, max_value=120, value=15)

    booking_day = st.sidebar.date_input("Wybierz dzień", value=date.today())
    brygada = st.sidebar.selectbox("Wybierz brygadę", brygady, format_func=lambda x: x["name"])

    st.sidebar.markdown("---")

    # Tworzenie przykładowych slotów
    slots = []
    for i in range(3):
        slot_start = datetime.combine(booking_day, brygada["work_start"]) + timedelta(minutes=i * 120)
        slot_end = slot_start + timedelta(minutes=90)
        slots.append(create_slot_dict(brygada, booking_day, slot_start, slot_end, czas_rezerwowy_przed, czas_rezerwowy_po))

    df = pd.DataFrame(slots)

    st.header("Dostępne sloty w wybranym dniu")
    st.dataframe(df[["brygada", "day", "slot_start", "slot_end", "arrival_window_start", "arrival_window_end"]])

    # Tabela harmonogramu
    st.header("Tabela harmonogramu")
    gantt_df = df.copy()
    gantt_df["Start"] = pd.to_datetime(gantt_df["slot_start"])
    gantt_df["Koniec"] = pd.to_datetime(gantt_df["slot_end"])

    fig = px.timeline(
        gantt_df,
        x_start="Start",
        x_end="Koniec",
        y="brygada",
        color="brygada",
        title="Harmonogram pracy Brygad",
        hover_data={"arrival_window_start": True, "arrival_window_end": True}
    )
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, use_container_width=True)

    # Zarządzanie slotami
    st.header("Zarządzaj slotami")
    st.dataframe(df)

if __name__ == "__main__":
    main()
