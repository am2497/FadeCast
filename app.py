"""Fadecast - version 2: shop information plus logins with roles (customer, barber, owner)."""

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

# What each role will get (filled in by later steps)
role = profile["role"] if user else None
if role == "owner":
    st.info("Owner area: shop settings and the demand forecast dashboard will appear here.")
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
days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
hours = db.table("opening_hours").select("*").order("day_of_week").execute().data
rows = []
for h in hours:
    if h["closed"]:
        rows.append({"Day": days[h["day_of_week"]], "Hours": "Closed"})
    else:
        rows.append({"Day": days[h["day_of_week"]], "Hours": f"{h['opens'][:5]} – {h['closes'][:5]}"})
st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
