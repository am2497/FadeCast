"""Fadecast - version 1: shows the shop's services, barbers and opening hours from Supabase."""

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Fadecast", page_icon="💈")


@st.cache_resource
def get_db():
    """Connect to Supabase once, using the keys stored in Streamlit's secrets."""
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


db = get_db()

st.title("💈 Fadecast")
st.caption("Clear Cuts: online booking and live walk-in queue")

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
