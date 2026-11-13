"""Fadecast - version 3: shop information, logins with roles, and the owner's settings page."""

from datetime import time

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Fadecast", page_icon="💈")


def get_db():
    """Give each visitor their own connection, so one person's login never leaks to another."""
    if "db" not in st.session_state:
        st.session_state.db = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
    return st.session_state.db


def friendly(error):
    """Turn Supabase's error messages into plain English."""
    text = str(error)
    if "Invalid login credentials" in text:
        return "Wrong email or password."
    if "Email not confirmed" in text:
        return "Please confirm your email first (check your inbox), then log in."
    if "already registered" in text:
        return "There is already an account with this email. Try logging in."
    if "Password should be" in text:
        return "Your password needs at least 6 characters."
    return text


def load_profile(user_id):
    """Read the logged-in person's name and role (customer, barber or owner)."""
    rows = db.table("profiles").select("full_name, role").eq("id", user_id).execute().data
    return rows[0] if rows else {"full_name": None, "role": "customer"}


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def clean(value):
    """Turn a value from an editable table into something the database understands."""
    if value is None:
        return None
    if isinstance(value, time):
        return value.strftime("%H:%M")
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if hasattr(value, "item") else value


def save_changes(table, original, edited, columns, key, editor_key):
    """Compare the table before and after the owner's edits, and save only the rows that changed."""
    changed = 0
    try:
        for i in range(len(edited)):
            update = {}
            for col in columns:
                new, old = clean(edited.iloc[i][col]), clean(original.iloc[i][col])
                if new != old:
                    update[col] = new
            if update:
                res = db.table(table).update(update).eq(key, clean(original.iloc[i][key])).execute()
                if not res.data:
                    st.error("That change was not allowed. Are you logged in as the owner?")
                    return
                changed += 1
    except Exception as e:
        st.error(f"Could not save: {e}")
        return
    st.session_state.pop(editor_key, None)
    st.session_state.flash = f"Saved {changed} change(s)." if changed else "Nothing had changed."
    st.rerun()


def owner_settings():
    """R06: the owner manages services, prices, barbers and opening hours."""
    st.header("Shop settings")
    services_tab, barbers_tab, hours_tab = st.tabs(["Services", "Barbers", "Opening hours"])

    # Services and prices
    with services_tab:
        st.caption("Change anything in the table, then press Save. Untick 'On the website' to hide a service.")
        original = pd.DataFrame(
            db.table("services").select("id, name, price, duration_minutes, active").order("id").execute().data)
        original["price"] = original["price"].astype(float)
        edited = st.data_editor(original, key="services_editor", hide_index=True, use_container_width=True,
                                disabled=["id"], column_config={
                                    "id": None,
                                    "name": st.column_config.TextColumn("Service", required=True),
                                    "price": st.column_config.NumberColumn("Price (£)", min_value=0, step=0.5,
                                                                           format="£%.2f", required=True),
                                    "duration_minutes": st.column_config.NumberColumn(
                                        "Time (minutes)", min_value=5, max_value=240, step=5, required=True),
                                    "active": st.column_config.CheckboxColumn("On the website"),
                                })
        if st.button("Save services"):
            save_changes("services", original, edited, ["name", "price", "duration_minutes", "active"],
                         "id", "services_editor")

        with st.form("add_service", clear_on_submit=True):
            st.write("**Add a new service**")
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Name")
            price = c2.number_input("Price (£)", min_value=0.0, step=0.5)
            minutes = c3.number_input("Time (minutes)", min_value=5, max_value=240, value=30, step=5)
            if st.form_submit_button("Add service"):
                if not name.strip():
                    st.error("Please give the service a name.")
                else:
                    db.table("services").insert(
                        {"name": name.strip(), "price": price, "duration_minutes": int(minutes)}).execute()
                    st.session_state.flash = f"Added {name.strip()}."
                    st.rerun()

    # Barbers
    with barbers_tab:
        st.caption("Rename a barber, or untick 'Working here' if they leave.")
        original = pd.DataFrame(db.table("barbers").select("id, name, active").order("id").execute().data)
        edited = st.data_editor(original, key="barbers_editor", hide_index=True, use_container_width=True,
                                disabled=["id"], column_config={
                                    "id": None,
                                    "name": st.column_config.TextColumn("Barber", required=True),
                                    "active": st.column_config.CheckboxColumn("Working here"),
                                })
        if st.button("Save barbers"):
            save_changes("barbers", original, edited, ["name", "active"], "id", "barbers_editor")

        with st.form("add_barber", clear_on_submit=True):
            name = st.text_input("New barber's name")
            if st.form_submit_button("Add barber"):
                if not name.strip():
                    st.error("Please enter a name.")
                else:
                    db.table("barbers").insert({"name": name.strip()}).execute()
                    st.session_state.flash = f"Added {name.strip()}."
                    st.rerun()

    # Opening hours
    with hours_tab:
        st.caption("Set the opening and closing time for each day, or tick 'Closed'.")
        rows = db.table("opening_hours").select("*").order("day_of_week").execute().data
        original = pd.DataFrame([{
            "day_of_week": h["day_of_week"],
            "day": DAYS[h["day_of_week"]],
            "opens": time.fromisoformat(h["opens"]) if h["opens"] else None,
            "closes": time.fromisoformat(h["closes"]) if h["closes"] else None,
            "closed": h["closed"],
        } for h in rows])
        edited = st.data_editor(original, key="hours_editor", hide_index=True, use_container_width=True,
                                disabled=["day_of_week", "day"], column_config={
                                    "day_of_week": None,
                                    "day": st.column_config.TextColumn("Day"),
                                    "opens": st.column_config.TimeColumn("Opens", format="HH:mm", step=900),
                                    "closes": st.column_config.TimeColumn("Closes", format="HH:mm", step=900),
                                    "closed": st.column_config.CheckboxColumn("Closed"),
                                })
        if st.button("Save opening hours"):
            problems = [r["day"] for _, r in edited.iterrows()
                        if not r["closed"] and (clean(r["opens"]) is None or clean(r["closes"]) is None
                                                or clean(r["closes"]) <= clean(r["opens"]))]
            if problems:
                st.error("Check the times for: " + ", ".join(problems) + ". Closing must be after opening.")
            else:
                save_changes("opening_hours", original, edited, ["opens", "closes", "closed"],
                             "day_of_week", "hours_editor")


db = get_db()
user = st.session_state.get("user")
profile = st.session_state.get("profile")

# ---------- Sidebar: log in, sign up, log out ----------
with st.sidebar:
    if user:
        st.write(f"Signed in as **{profile.get('full_name') or user.email}**")
        st.caption(f"Role: {profile['role']}")
        if st.button("Log out"):
            try:
                db.auth.sign_out()
            except Exception:
                pass
            for key in ("user", "profile", "db"):
                st.session_state.pop(key, None)
            st.rerun()
    else:
        login_tab, signup_tab = st.tabs(["Log in", "Sign up"])

        with login_tab:
            with st.form("login"):
                email = st.text_input("Email")
                password = st.text_input("Password", type="password")
                if st.form_submit_button("Log in"):
                    try:
                        res = db.auth.sign_in_with_password({"email": email.strip(), "password": password})
                        st.session_state.user = res.user
                        st.session_state.profile = load_profile(res.user.id)
                        st.rerun()
                    except Exception as e:
                        st.error(friendly(e))

        with signup_tab:
            with st.form("signup"):
                name = st.text_input("Your name")
                email = st.text_input("Email")
                password = st.text_input("Password (at least 6 characters)", type="password")
                if st.form_submit_button("Create account"):
                    if not name.strip():
                        st.error("Please enter your name.")
                    else:
                        try:
                            res = db.auth.sign_up({
                                "email": email.strip(),
                                "password": password,
                                "options": {"data": {"full_name": name.strip()}},
                            })
                            if res.session:
                                st.session_state.user = res.user
                                st.session_state.profile = load_profile(res.user.id)
                                st.rerun()
                            else:
                                st.success("Account created. Check your email to confirm it, then log in.")
                        except Exception as e:
                            st.error(friendly(e))

# ---------- Main page ----------
st.title("💈 Fadecast")
st.caption("Clear Cuts: online booking and live walk-in queue")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

# What each role gets
role = profile["role"] if user else None
if role == "owner":
    owner_settings()
    st.divider()
    st.caption("Below is what customers see.")
elif role == "barber":
    st.info("Barber area: your bookings for the day and the walk-in queue will appear here.")
elif role == "customer":
    st.info("Booking will appear here: pick a service, a barber and a time.")
else:
    st.info("Log in or sign up using the menu on the left to book a cut.")

# Services and prices
st.subheader("Services")
services = db.table("services").select("name, price, duration_minutes").eq("active", True).order("price").execute().data
if services:
    df = pd.DataFrame(services)
    df["price"] = df["price"].map(lambda p: f"£{float(p):.2f}")
    df["duration_minutes"] = df["duration_minutes"].map(lambda m: f"{m} min")
    st.dataframe(df.rename(columns={"name": "Service", "price": "Price", "duration_minutes": "Time"}),
                 hide_index=True, use_container_width=True)
else:
    st.info("No services yet.")

# Barbers
st.subheader("Our barbers")
barbers = db.table("barbers").select("name").eq("active", True).order("id").execute().data
st.write(", ".join(b["name"] for b in barbers) if barbers else "No barbers yet.")

# Opening hours
st.subheader("Opening hours")
hours = db.table("opening_hours").select("*").order("day_of_week").execute().data
rows = []
for h in hours:
    if h["closed"]:
        rows.append({"Day": DAYS[h["day_of_week"]], "Hours": "Closed"})
    else:
        rows.append({"Day": DAYS[h["day_of_week"]], "Hours": f"{h['opens'][:5]} – {h['closes'][:5]}"})
st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
