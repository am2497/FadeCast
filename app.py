"""Fadecast - version 5: shop info, logins with roles, owner settings, booking, cancel and move."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

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
LONDON = ZoneInfo("Europe/London")
SLOT_STEP = 15  # booking times are offered every 15 minutes


def when(timestamp):
    """Show a database time as UK time, e.g. 'Sat 14 Nov, 10:30'."""
    return datetime.fromisoformat(timestamp).astimezone(LONDON).strftime("%a %d %b, %H:%M")


def free_slots(barber_id, day, minutes, ignore=None):
    """List the start times on a day when this barber is free for the whole service (R02, R03).
    'ignore' is a booking being moved, so its own current time does not count as busy."""
    h = db.table("opening_hours").select("*").eq("day_of_week", day.weekday()).execute().data[0]
    if h["closed"]:
        return []
    opens = datetime.combine(day, time.fromisoformat(h["opens"]), LONDON)
    closes = datetime.combine(day, time.fromisoformat(h["closes"]), LONDON)
    busy = db.rpc("busy_times", {"p_barber": barber_id, "p_from": opens.isoformat(),
                                 "p_to": closes.isoformat()}).execute().data
    busy = [(datetime.fromisoformat(b["start_time"]), datetime.fromisoformat(b["end_time"])) for b in busy]
    if ignore:
        busy = [b for b in busy if b != ignore]
    now = datetime.now(LONDON)
    slots, start = [], opens
    while start + timedelta(minutes=minutes) <= closes:
        end = start + timedelta(minutes=minutes)
        if start > now and all(not (start < b_end and end > b_start) for b_start, b_end in busy):
            slots.append(start)
        start += timedelta(minutes=SLOT_STEP)
    return slots


def booking_page():
    """R01: a customer picks a service, a barber, a day and a free time."""
    st.header("Book a cut")
    services = db.table("services").select("id, name, price, duration_minutes").eq("active", True) \
        .order("price").execute().data
    barbers = db.table("barbers").select("id, name").eq("active", True).order("id").execute().data
    if not services or not barbers:
        st.info("Booking is not open yet.")
        return

    c1, c2 = st.columns(2)
    service = c1.selectbox("Service", services, format_func=lambda s:
                           f"{s['name']} – £{float(s['price']):.2f} ({s['duration_minutes']} min)")
    barber = c2.selectbox("Barber", barbers, format_func=lambda b: b["name"])
    today = datetime.now(LONDON).date()
    day = st.date_input("Day", value=today, min_value=today, max_value=today + timedelta(days=28),
                        format="DD/MM/YYYY")

    slots = free_slots(barber["id"], day, service["duration_minutes"])
    if not slots:
        st.warning(f"{barber['name']} has no free times on {day.strftime('%A %d %B')}. Try another day or barber.")
        return
    slot = st.selectbox("Time", slots, format_func=lambda t: t.strftime("%H:%M"))

    if st.button("Confirm booking", type="primary"):
        try:
            db.table("bookings").insert({
                "customer_id": user.id,
                "barber_id": barber["id"],
                "service_id": service["id"],
                "start_time": slot.isoformat(),
            }).execute()
            st.session_state.flash = (f"Booked: {service['name']} with {barber['name']} on "
                                      f"{slot.strftime('%A %d %B at %H:%M')}.")
            st.rerun()
        except Exception as e:
            if "no_double_booking" in str(e) or "23P01" in str(e):
                st.error("Sorry, someone has just booked that time. Please pick another.")
            else:
                st.error(f"Could not book: {e}")


def db_message(error):
    """Pull the plain message out of a database error."""
    text = getattr(error, "message", None) or str(error)
    if "no_double_booking" in text or "23P01" in text:
        return "Sorry, that time has just been taken. Please pick another."
    return text


def my_bookings():
    """R02: the customer's upcoming bookings, each one can be cancelled or moved."""
    st.subheader("My upcoming bookings")
    rows = db.table("bookings").select(
        "id, start_time, end_time, status, barber_id, services(name, price, duration_minutes), barbers(name)") \
        .eq("customer_id", user.id).eq("status", "booked") \
        .gte("start_time", datetime.now(LONDON).isoformat()).order("start_time").execute().data
    if not rows:
        st.caption("You have no upcoming bookings.")
        return
    barbers = db.table("barbers").select("id, name").eq("active", True).order("id").execute().data

    for r in rows:
        title = f"{when(r['start_time'])} · {r['services']['name']} with {r['barbers']['name']}" \
                f" · £{float(r['services']['price']):.2f}"
        with st.expander(title):
            if st.button("Cancel this booking", key=f"cancel_{r['id']}"):
                try:
                    db.rpc("cancel_booking", {"p_id": r["id"]}).execute()
                    st.session_state.flash = "Your booking has been cancelled."
                    st.rerun()
                except Exception as e:
                    st.error(db_message(e))

            st.write("**Move to another time**")
            current = datetime.fromisoformat(r["start_time"]).astimezone(LONDON)
            c1, c2 = st.columns(2)
            ids = [b["id"] for b in barbers]
            barber = c1.selectbox("Barber", barbers, key=f"mb_{r['id']}", format_func=lambda b: b["name"],
                                  index=ids.index(r["barber_id"]) if r["barber_id"] in ids else 0)
            today = datetime.now(LONDON).date()
            day = c2.date_input("Day", value=current.date(), min_value=today, max_value=today + timedelta(days=28),
                                format="DD/MM/YYYY", key=f"md_{r['id']}")
            ignore = (datetime.fromisoformat(r["start_time"]), datetime.fromisoformat(r["end_time"])) \
                if barber["id"] == r["barber_id"] else None
            slots = [s for s in free_slots(barber["id"], day, r["services"]["duration_minutes"], ignore)
                     if not (s == current and barber["id"] == r["barber_id"])]
            if not slots:
                st.caption(f"{barber['name']} has no free times that day.")
                continue
            slot = st.selectbox("New time", slots, key=f"mt_{r['id']}", format_func=lambda t: t.strftime("%H:%M"))
            if st.button("Move booking", key=f"move_{r['id']}", type="primary"):
                try:
                    db.rpc("move_booking", {"p_id": r["id"], "p_barber": barber["id"],
                                            "p_start": slot.isoformat()}).execute()
                    st.session_state.flash = (f"Moved to {slot.strftime('%A %d %B at %H:%M')} "
                                              f"with {barber['name']}.")
                    st.rerun()
                except Exception as e:
                    st.error(db_message(e))


def all_bookings():
    """Barbers and the owner see every booking from today onwards."""
    st.header("Bookings")
    start_of_today = datetime.combine(datetime.now(LONDON).date(), time(0), LONDON)
    rows = db.table("bookings").select(
        "start_time, end_time, status, is_walk_in, walk_in_name, services(name), barbers(name), profiles(full_name)") \
        .gte("start_time", start_of_today.isoformat()).order("start_time").execute().data
    if not rows:
        st.caption("No bookings yet.")
        return
    st.dataframe(pd.DataFrame([{
        "When": when(r["start_time"]),
        "Until": datetime.fromisoformat(r["end_time"]).astimezone(LONDON).strftime("%H:%M"),
        "Customer": r["walk_in_name"] if r["is_walk_in"] else (r["profiles"] or {}).get("full_name"),
        "Service": r["services"]["name"],
        "Barber": r["barbers"]["name"],
        "Type": "Walk-in" if r["is_walk_in"] else "Booked online",
        "Status": r["status"].replace("_", " ").capitalize(),
    } for r in rows]), hide_index=True, use_container_width=True)


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
        if profile["role"] == "owner":
            owner_page = st.radio("Go to", ["Bookings", "Shop settings", "Make a test booking"])
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
    if owner_page == "Bookings":
        all_bookings()
    elif owner_page == "Shop settings":
        owner_settings()
    else:
        booking_page()
        my_bookings()
    st.divider()
    st.caption("Below is what customers see.")
elif role == "barber":
    all_bookings()
    st.divider()
elif role == "customer":
    booking_page()
    my_bookings()
    st.divider()
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
