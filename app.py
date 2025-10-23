"""
Streamlit Tidsapp – Homebase/Deputy-liknande MVP
================================================

Körning lokalt:
  streamlit run streamlit_tidsapp.py

Feature-översikt (MVP):
- Inloggning med roller (Admin, Manager, Employee)
- Stämpelklocka (in/ut) + anteckning + frivillig platsangivelse
- Schema (veckoöversikt) – skapa/ändra skift, tilldela person
- Personalregister (namn, roll, timlön, PIN för kiosk)
- Godkännande/justering av tider (Manager/Admin)
- Rapporter + export till CSV
- "Kiosk-läge" (PIN-stämpling på delad enhet)
- Enkel regelmotor för OB/övertid (MVP: tröskel per dag)
- SQLite persistence (datafil: tidsapp.db i arbetskatalogen)

OBS: Detta är en grund att bygga vidare på. Lägg gärna till Single Sign-On, geofencing,
passregler per kollektivavtal, integration till lön, m.m.
"""

from __future__ import annotations
import streamlit as st
import pandas as pd
import sqlite3
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
import hashlib
from typing import Optional, List, Tuple

DB_PATH = "tidsapp.db"

# ------------------------------
# Hjälpfunktioner
# ------------------------------

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        # Users
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('Admin','Manager','Employee')),
                hourly_rate REAL DEFAULT 0,
                pin TEXT DEFAULT NULL
            )
            """
        )
        # Punches (stämpelklocka)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS punches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                clock_in TEXT NOT NULL,
                clock_out TEXT,
                note TEXT,
                location TEXT,
                approved INTEGER DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        # Shifts (schema)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                shift_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                position TEXT,
                location TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.commit()


def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def get_user_by_username(username: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, full_name, password_hash, role, hourly_rate, pin FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        if row:
            keys = ["id","username","full_name","password_hash","role","hourly_rate","pin"]
            return dict(zip(keys, row))
        return None


def get_user_by_pin(pin: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, full_name, role FROM users WHERE pin=?", (pin,))
        row = cur.fetchone()
        if row:
            keys = ["id","username","full_name","role"]
            return dict(zip(keys, row))
        return None


def ensure_seed_admin():
    """Skapar en första admin om databasen saknar användare."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        (count,) = cur.fetchone()
        if count == 0:
            cur.execute(
                "INSERT INTO users (username, full_name, password_hash, role, hourly_rate, pin) VALUES (?,?,?,?,?,?)",
                ("admin", "System Admin", hash_pw("admin123"), "Admin", 0, "0000"),
            )
            cur.execute(
                "INSERT INTO users (username, full_name, password_hash, role, hourly_rate, pin) VALUES (?,?,?,?,?,?)",
                ("anna", "Anna Andersson", hash_pw("chef123"), "Manager", 165, "1111"),
            )
            cur.execute(
                "INSERT INTO users (username, full_name, password_hash, role, hourly_rate, pin) VALUES (?,?,?,?,?,?)",
                ("erik", "Erik Ek", hash_pw("server123"), "Employee", 145, "2222"),
            )
            conn.commit()


# ------------------------------
# Auth
# ------------------------------

def login_ui():
    st.subheader("🔐 Logga in")
    col1, col2 = st.columns(2)
    with col1:
        username = st.text_input("Användarnamn", placeholder="t.ex. admin")
        password = st.text_input("Lösenord", type="password")
        if st.button("Logga in", type="primary"):
            user = get_user_by_username(username)
            if user and user["password_hash"] == hash_pw(password):
                st.session_state["user"] = {k: user[k] for k in ("id","username","full_name","role","hourly_rate")}
                st.success(f"Välkommen {user['full_name']}!")
                st.rerun()
            else:
                st.error("Felaktigt användarnamn eller lösenord.")
    with col2:
        st.markdown("**Kiosk-läge (PIN)**")
        pin = st.text_input("PIN (4–6 siffror)")
        if st.button("Logga in via PIN"):
            user = get_user_by_pin(pin)
            if user:
                st.session_state["user"] = {k: user[k] for k in ("id","username","full_name","role")}
                st.success(f"Kiosk: inloggad som {user['full_name']}")
                st.rerun()
            else:
                st.error("Ogiltig PIN.")


def require_login():
    if "user" not in st.session_state:
        login_ui()
        st.stop()


# ------------------------------
# Stämpelklocka
# ------------------------------

def clock_view():
    st.header("🕒 Stämpelklocka")
    user = st.session_state["user"]

    note = st.text_input("Anteckning (valfritt)")
    location = st.text_input("Plats (valfritt)", placeholder="Bar, Kök, Matsal ...")

    # Hämta pågående stämpling
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, clock_in FROM punches WHERE user_id=? AND clock_out IS NULL ORDER BY id DESC LIMIT 1",
            (user["id"],),
        )
        active = cur.fetchone()

    if active:
        punch_id, clock_in_str = active
        clock_in_dt = datetime.fromisoformat(clock_in_str)
        st.info(f"Pågående pass sedan {clock_in_dt.strftime('%Y-%m-%d %H:%M')}")
        if st.button("Stämpla UT", type="primary"):
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE punches SET clock_out=?, note=COALESCE(note,'' ) || ?, location=COALESCE(location,'') || ? WHERE id=?",
                    (datetime.now().isoformat(), f"\n{note}" if note else "", f"\n{location}" if location else "", punch_id),
                )
                conn.commit()
            st.success("Utstämpling registrerad.")
            st.rerun()
    else:
        if st.button("Stämpla IN", type="primary"):
            with get_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO punches (user_id, clock_in, note, location) VALUES (?,?,?,?)",
                    (user["id"], datetime.now().isoformat(), note, location),
                )
                conn.commit()
            st.success("Instämpling registrerad.")
            st.rerun()

    st.divider()
    st.subheader("Dagens stämplingar (mina)")
    today = date.today()
    day_start = datetime.combine(today, time(0,0))
    day_end = day_start + timedelta(days=1)
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT p.id, p.clock_in, p.clock_out, p.note, p.location, p.approved FROM punches p WHERE p.user_id=? AND datetime(p.clock_in) >= ? AND datetime(p.clock_in) < ? ORDER BY p.clock_in DESC",
            conn,
            params=(user["id"], day_start.isoformat(), day_end.isoformat()),
        )
    if not df.empty:
        df["clock_in"] = pd.to_datetime(df["clock_in"]).dt.strftime("%Y-%m-%d %H:%M")
        df["clock_out"] = df["clock_out"].apply(lambda x: pd.to_datetime(x).strftime("%Y-%m-%d %H:%M") if x else "—")
        st.dataframe(df, use_container_width=True)
    else:
        st.caption("Inga stämplingar ännu idag.")


# ------------------------------
# Schema (Manager/Admin)
# ------------------------------

def schedule_view():
    st.header("📅 Schema (vecka)")
    user = st.session_state["user"]
    if user["role"] not in ("Manager","Admin"):
        st.warning("Behörighet krävs (Manager/Admin).")
        return

    # Datumintervall för veckan
    base = st.date_input("Välj datum i aktuell vecka", value=date.today())
    start_of_week = base - timedelta(days=base.weekday())
    days = [start_of_week + timedelta(days=i) for i in range(7)]

    with get_conn() as conn:
        users_df = pd.read_sql_query("SELECT id, full_name, role FROM users ORDER BY full_name", conn)

    with st.expander("➕ Lägg till skift"):
        col1, col2 = st.columns(2)
        with col1:
            person = st.selectbox("Medarbetare", users_df["full_name"].tolist())
            position = st.text_input("Position", placeholder="Server, Kök, Bar ...")
            loc = st.text_input("Plats", placeholder="Matsal, Bar ...")
        with col2:
            day = st.selectbox("Dag", days, format_func=lambda d: d.strftime("%a %Y-%m-%d"))
            start_t = st.time_input("Start", time(10,0))
            end_t = st.time_input("Slut", time(18,0))
        if st.button("Spara skift"):
            with get_conn() as conn:
                cur = conn.cursor()
                uid = int(users_df.loc[users_df["full_name"]==person, "id"].iloc[0])
                cur.execute(
                    "INSERT INTO shifts (user_id, shift_date, start_time, end_time, position, location) VALUES (?,?,?,?,?,?)",
                    (uid, day.isoformat(), start_t.strftime("%H:%M"), end_t.strftime("%H:%M"), position, loc),
                )
                conn.commit()
            st.success("Skift sparat.")
            st.rerun()

    # Visa vecka
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT s.id, u.full_name, s.shift_date, s.start_time, s.end_time, s.position, s.location FROM shifts s LEFT JOIN users u ON s.user_id=u.id WHERE date(s.shift_date) >= ? AND date(s.shift_date) <= ? ORDER BY s.shift_date, s.start_time",
            conn,
            params=(days[0].isoformat(), days[-1].isoformat()),
        )
    if df.empty:
        st.caption("Inga skift inlagda för vald vecka.")
    else:
        df["Dag"] = pd.to_datetime(df["shift_date"]).dt.strftime("%a %Y-%m-%d")
        df = df[["Dag","full_name","start_time","end_time","position","location","id"]]
        st.dataframe(df, use_container_width=True, hide_index=True)
        # Ta bort skift
        with st.expander("🗑️ Ta bort skift"):
            sel = st.multiselect("Välj skift-ID", df["id"].astype(str).tolist())
            if st.button("Radera valda") and sel:
                with get_conn() as conn:
                    cur = conn.cursor()
                    cur.executemany("DELETE FROM shifts WHERE id=?", [(int(x),) for x in sel])
                    conn.commit()
                st.success("Raderat.")
                st.rerun()


# ------------------------------
# Personal (Admin)
# ------------------------------

def staff_view():
    st.header("👥 Personalregister")
    user = st.session_state["user"]
    if user["role"] != "Admin":
        st.warning("Behörighet krävs (Admin).")
        return

    with get_conn() as conn:
        df = pd.read_sql_query("SELECT id, username, full_name, role, hourly_rate, pin FROM users ORDER BY full_name", conn)
    st.dataframe(df, use_container_width=True)

    with st.expander("➕ Lägg till/uppdatera person"):
        col1, col2, col3 = st.columns(3)
        with col1:
            mode = st.radio("Läge", ["Ny", "Uppdatera"], horizontal=True)
            username = st.text_input("Användarnamn")
            full_name = st.text_input("Namn")
        with col2:
            role = st.selectbox("Roll", ["Employee","Manager","Admin"], index=0)
            hourly = st.number_input("Timlön (SEK)", min_value=0.0, value=150.0, step=1.0)
            pin = st.text_input("PIN (kiosk)")
        with col3:
            pw = st.text_input("Lösenord", type="password")
            if st.button("Spara person", type="primary"):
                with get_conn() as conn:
                    cur = conn.cursor()
                    if mode == "Ny":
                        cur.execute(
                            "INSERT INTO users (username, full_name, password_hash, role, hourly_rate, pin) VALUES (?,?,?,?,?,?)",
                            (username, full_name, hash_pw(pw or "changeme"), role, float(hourly), pin or None),
                        )
                    else:
                        # Uppdatera – lösenord uppdateras om angivet
                        if pw:
                            cur.execute(
                                "UPDATE users SET full_name=?, role=?, hourly_rate=?, pin=?, password_hash=? WHERE username=?",
                                (full_name, role, float(hourly), pin or None, hash_pw(pw), username),
                            )
                        else:
                            cur.execute(
                                "UPDATE users SET full_name=?, role=?, hourly_rate=?, pin=? WHERE username=?",
                                (full_name, role, float(hourly), pin or None, username),
                            )
                    conn.commit()
                st.success("Sparat.")
                st.rerun()


# ------------------------------
# Godkänn/justera tider (Manager/Admin)
# ------------------------------

def approvals_view():
    st.header("✅ Godkänn tider")
    user = st.session_state["user"]
    if user["role"] not in ("Manager","Admin"):
        st.warning("Behörighet krävs (Manager/Admin).")
        return

    start = st.date_input("Från", value=date.today()-timedelta(days=7))
    end = st.date_input("Till", value=date.today())

    with get_conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT p.id, u.full_name, p.clock_in, p.clock_out, p.note, p.location, p.approved
            FROM punches p
            LEFT JOIN users u ON p.user_id=u.id
            WHERE datetime(p.clock_in) >= ? AND datetime(p.clock_in) < datetime(?,'+1 day')
            ORDER BY p.clock_in DESC
            """,
            conn,
            params=(datetime.combine(start, time.min).isoformat(), end.isoformat()),
        )
    if df.empty:
        st.caption("Inga tider i intervallet.")
        return

    # Beräkna timmar och enkel övertid
    def duration_hours(row):
        if pd.isna(row["clock_out"]) or not row["clock_out"]:
            return 0.0
        start_dt = pd.to_datetime(row["clock_in"]) ; end_dt = pd.to_datetime(row["clock_out"]) 
        return (end_dt - start_dt).total_seconds()/3600

    df["clock_in"] = pd.to_datetime(df["clock_in"])
    df["clock_out"] = pd.to_datetime(df["clock_out"]) 
    df["Timmar"] = df.apply(duration_hours, axis=1).round(2)

    st.dataframe(df[["id","full_name","clock_in","clock_out","Timmar","note","location","approved"]], use_container_width=True)

    with st.expander("✏️ Justera/uppdatera"):
        sel_id = st.text_input("Rad-ID att uppdatera")
        new_in = st.text_input("Ny IN (YYYY-MM-DD HH:MM)")
        new_out = st.text_input("Ny UT (YYYY-MM-DD HH:MM)")
        approve = st.checkbox("Godkänn")
        if st.button("Spara ändring") and sel_id:
            try:
                with get_conn() as conn:
                    cur = conn.cursor()
                    if new_in:
                        cur.execute("UPDATE punches SET clock_in=? WHERE id=?", (pd.to_datetime(new_in).isoformat(), int(sel_id)))
                    if new_out:
                        cur.execute("UPDATE punches SET clock_out=? WHERE id=?", (pd.to_datetime(new_out).isoformat(), int(sel_id)))
                    cur.execute("UPDATE punches SET approved=? WHERE id=?", (1 if approve else 0, int(sel_id)))
                    conn.commit()
                st.success("Uppdaterat.")
                st.rerun()
            except Exception as e:
                st.error(f"Fel: {e}")


# ------------------------------
# Rapporter & export
# ------------------------------

def reports_view():
    st.header("📊 Rapporter & Export")
    start = st.date_input("Från datum", value=date.today()-timedelta(days=14))
    end = st.date_input("Till datum", value=date.today())

    with get_conn() as conn:
        df = pd.read_sql_query(
            """
            SELECT p.id, u.full_name, u.hourly_rate, p.clock_in, p.clock_out, p.approved
            FROM punches p
            LEFT JOIN users u ON p.user_id=u.id
            WHERE datetime(p.clock_in) >= ? AND datetime(p.clock_in) < datetime(?,'+1 day')
            ORDER BY u.full_name, p.clock_in
            """,
            conn,
            params=(datetime.combine(start, time.min).isoformat(), end.isoformat()),
        )

    if df.empty:
        st.caption("Ingen data i intervallet.")
        return

    df["clock_in"] = pd.to_datetime(df["clock_in"]) ; df["clock_out"] = pd.to_datetime(df["clock_out"]) 
    df["hours"] = ((df["clock_out"] - df["clock_in"]).dt.total_seconds()/3600).fillna(0).round(2)

    # Enkel övertidsregel: >8h på en dag => 50% OT på överskjutande
    df["date"] = df["clock_in"].dt.date
    daily = df.groupby(["full_name","date"]).agg({"hours":"sum","hourly_rate":"max"}).reset_index()
    daily["ot_hours"] = (daily["hours"] - 8).clip(lower=0)
    daily["reg_hours"] = daily["hours"] - daily["ot_hours"]
    daily["pay"] = daily["reg_hours"]*daily["hourly_rate"] + daily["ot_hours"]*daily["hourly_rate"]*1.5

    st.subheader("Summering per dag & person")
    st.dataframe(daily, use_container_width=True)

    # Export
    csv = daily.to_csv(index=False).encode("utf-8")
    st.download_button("Ladda ner CSV", data=csv, file_name="rapport.csv", mime="text/csv")


# ------------------------------
# Huvudapp
# ------------------------------

def main():
    st.set_page_config(page_title="Tidsapp", page_icon="⏱️", layout="wide")
    init_db()
    ensure_seed_admin()

    st.sidebar.title("Tidsapp")

    if "user" not in st.session_state:
        login_ui()
    else:
        user = st.session_state["user"]
        st.sidebar.success(f"Inloggad: {user['full_name']} ({user['role']})")
        choice = st.sidebar.radio(
            "Meny",
            [
                "Stämpelklocka",
                "Schema",
                "Godkänn tider",
                "Rapporter",
                "Personal",
                "Logga ut",
            ],
        )

        if choice == "Stämpelklocka":
            clock_view()
        elif choice == "Schema":
            schedule_view()
        elif choice == "Godkänn tider":
            approvals_view()
        elif choice == "Rapporter":
            reports_view()
        elif choice == "Personal":
            staff_view()
        elif choice == "Logga ut":
            st.session_state.pop("user", None)
            st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.caption("MVP byggd i Streamlit – utveckla vidare med SSO, geofencing, lönesystem mm.")


if __name__ == "__main__":
    main()
