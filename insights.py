"""Fadecast - the owner's dashboard and the Holt-Winters demand forecast."""

import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from logic import mape, mase, open_days, seasonal_naive

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TEST_WEEKS = 4      # the last 4 weeks are held back to test the forecast
FUTURE_DAYS = 28    # how far ahead to forecast: 4 weeks (R10)


@st.cache_data(ttl=600, show_spinner="Running the forecast…")
def run_forecast(values, season, horizon_test, horizon_future):
    """Fit Holt-Winters on all but the last few weeks, test it against the simple baseline,
    then refit on everything and forecast ahead."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    y = np.asarray(values, dtype=float)
    train, test = y[:-horizon_test], y[-horizon_test:]

    def fit(data):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ExponentialSmoothing(data, trend="add", damped_trend=True, seasonal="add",
                                        seasonal_periods=season, initialization_method="estimated").fit()

    hw_test = fit(train).forecast(horizon_test)
    naive_test = seasonal_naive(train, horizon_test, season)
    final = fit(y)
    future = final.forecast(horizon_future)
    spread = float(np.std(final.resid)) if len(final.resid) else 0.0
    return {
        "hw_test": np.maximum(hw_test, 0).tolist(),
        "naive_test": naive_test.tolist(),
        "future": np.maximum(future, 0).tolist(),
        "spread": spread,
        "scores": {
            "Holt-Winters": (mase(test, hw_test, train, season), mape(test, hw_test)),
            "Same as last week (baseline)": (mase(test, naive_test, train, season), mape(test, naive_test)),
        },
    }


def fetch_all(build_query, page_size=1000):
    """Supabase sends at most 1,000 rows at a time, so ask page by page until everything has arrived."""
    rows, start = [], 0
    while True:
        chunk = build_query().range(start, start + page_size - 1).execute().data
        rows += chunk
        if len(chunk) < page_size:
            return rows
        start += page_size


def revenue_tab(db, tz, today):
    """R07: revenue per barber and customers per service for a chosen date range, with drill-down by day."""
    c1, c2 = st.columns(2)
    start = c1.date_input("From", value=today - timedelta(days=30), format="DD/MM/YYYY", key="rev_from")
    end = c2.date_input("To", value=today, format="DD/MM/YYYY", key="rev_to")
    if end < start:
        st.error("The 'To' date must be on or after the 'From' date.")
        return
    frm = datetime.combine(start, datetime.min.time(), tz)
    to = datetime.combine(end + timedelta(days=1), datetime.min.time(), tz)
    rows = fetch_all(lambda: db.table("bookings").select("start_time, services(name, price), barbers(name)")
                     .eq("status", "finished").gte("start_time", frm.isoformat())
                     .lt("start_time", to.isoformat()).order("start_time"))
    if not rows:
        st.caption("No finished appointments in these dates.")
        return
    df = pd.DataFrame([{
        "day": datetime.fromisoformat(r["start_time"]).astimezone(tz).date(),
        "Barber": (r["barbers"] or {}).get("name", "Any"),
        "Service": (r["services"] or {}).get("name"),
        "Revenue": float((r["services"] or {}).get("price") or 0),
    } for r in rows])

    k1, k2, k3 = st.columns(3)
    k1.metric("Revenue", f"£{df['Revenue'].sum():,.0f}")
    k2.metric("Customers served", len(df))
    k3.metric("Average per customer", f"£{df['Revenue'].mean():,.2f}")

    left, right = st.columns(2)
    per_barber = df.groupby("Barber", as_index=False).agg(Revenue=("Revenue", "sum"), Customers=("Revenue", "size"))
    fig = px.bar(per_barber, x="Barber", y="Revenue", text_auto=".0f", labels={"Revenue": "Revenue (£)"},
                 title="Revenue per barber")
    fig.update_layout(height=320, margin=dict(t=40, b=10))
    left.plotly_chart(fig, use_container_width=True)
    per_service = df.groupby("Service", as_index=False).agg(Customers=("Revenue", "size"), Revenue=("Revenue", "sum"))
    fig = px.bar(per_service, x="Service", y="Customers", text_auto=True, title="Customers per service")
    fig.update_layout(height=320, margin=dict(t=40, b=10))
    right.plotly_chart(fig, use_container_width=True)

    st.write("**Revenue by day**")
    by_day = df.groupby(["day", "Barber"], as_index=False)["Revenue"].sum()
    fig = px.bar(by_day, x="day", y="Revenue", color="Barber", labels={"day": "", "Revenue": "Revenue (£)"})
    fig.update_layout(height=320, margin=dict(t=10, b=10), legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)

    days = sorted(df["day"].unique(), reverse=True)
    pick = st.selectbox("Look at one day", days, format_func=lambda d: d.strftime("%a %d %b %Y"))
    one = df[df["day"] == pick].groupby(["Barber", "Service"], as_index=False) \
        .agg(Customers=("Revenue", "size"), Revenue=("Revenue", "sum"))
    one["Revenue"] = one["Revenue"].map(lambda v: f"£{v:,.2f}")
    st.dataframe(one, hide_index=True, use_container_width=True)
    st.caption("Revenue uses each service's current price, for appointments marked finished.")


def dashboard(db, tz):
    st.header("Dashboard and forecast")
    daily = pd.DataFrame(db.rpc("daily_stats", {"p_days": 180}).execute().data)
    if daily.empty:
        st.info("No finished appointments yet. The dashboard fills up as customers are marked finished.")
        return
    daily["day"] = pd.to_datetime(daily["day"]).dt.date
    today = datetime.now(tz).date()

    # ---------- Headline numbers for the last 30 days
    last30 = daily[daily["day"] >= today - timedelta(days=30)]
    served, no_shows = int(last30["served"].sum()), int(last30["no_shows"].sum())
    online_total = int(last30["online"].sum()) + no_shows
    waits = pd.DataFrame(db.rpc("wait_accuracy", {"p_days": 30}).execute().data)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Customers (30 days)", served)
    c2.metric("Walk-ins", f"{(last30['walk_ins'].sum() / served * 100 if served else 0):.0f}%")
    c3.metric("No-show rate", f"{(no_shows / online_total * 100 if online_total else 0):.1f}%",
              help="Out of online bookings")
    if not waits.empty:
        err = (waits["actual_minutes"].astype(float) - waits["estimated_minutes"]).abs()
        c4.metric("Wait estimate error", f"±{err.mean():.1f} min", help="Average difference between the wait "
                  "shown to a walk-in and how long they really waited (RQ2)")
    else:
        c4.metric("Wait estimate error", "–")

    tab_trend, tab_revenue, tab_hours, tab_waits, tab_forecast = st.tabs(
        ["Customers per day", "Revenue and services", "Busiest times", "Wait estimates", "Forecast"])

    # ---------- Revenue per barber and bookings per service, for any date range (R07)
    with tab_revenue:
        revenue_tab(db, tz, today)

    # ---------- Customers per day
    with tab_trend:
        chart = daily.melt(id_vars="day", value_vars=["online", "walk_ins"], var_name="Type", value_name="Customers")
        chart["Type"] = chart["Type"].map({"online": "Booked online", "walk_ins": "Walk-in"})
        fig = px.bar(chart, x="day", y="Customers", color="Type", labels={"day": ""})
        fig.update_layout(height=360, margin=dict(t=10, b=10), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)

    # ---------- Busiest times (heatmap of weekday x hour)
    with tab_hours:
        hourly = pd.DataFrame(db.rpc("hourly_stats", {"p_days": 90}).execute().data)
        if hourly.empty:
            st.caption("Not enough data yet.")
        else:
            grid = hourly.pivot_table(index="day_of_week", columns="hour", values="served", aggfunc="sum").fillna(0)
            grid.index = [DAYS[i] for i in grid.index]
            fig = px.imshow(grid, aspect="auto", color_continuous_scale="Blues",
                            labels=dict(x="Hour of the day", y="", color="Customers"))
            fig.update_layout(height=320, margin=dict(t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Customers served in the last 90 days, by day and hour. Darker means busier.")

    # ---------- Wait estimate accuracy
    with tab_waits:
        waits = pd.DataFrame(db.rpc("wait_accuracy", {"p_days": 90}).execute().data)
        if waits.empty:
            st.caption("No walk-ins with a recorded start time yet.")
        else:
            waits["actual_minutes"] = waits["actual_minutes"].astype(float)
            top = max(waits["estimated_minutes"].max(), waits["actual_minutes"].max()) + 5
            fig = px.scatter(waits, x="estimated_minutes", y="actual_minutes", opacity=0.6,
                             labels={"estimated_minutes": "Wait we showed (min)",
                                     "actual_minutes": "Real wait (min)"})
            fig.add_trace(go.Scatter(x=[0, top], y=[0, top], mode="lines", name="Perfect estimate",
                                     line=dict(dash="dash", color="grey")))
            fig.update_layout(height=360, margin=dict(t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            err = waits["actual_minutes"] - waits["estimated_minutes"]
            st.caption(f"{len(waits)} walk-ins in the last 90 days. On average the estimate was off by "
                       f"{err.abs().mean():.1f} min; {(err.abs() <= 10).mean() * 100:.0f}% were within 10 minutes. "
                       "Dots on the dashed line were spot on.")

    # ---------- Forecast (RQ3)
    with tab_forecast:
        hours = db.table("opening_hours").select("day_of_week, closed").execute().data
        closed = {h["day_of_week"] for h in hours if h["closed"]}
        season = 7 - len(closed)
        history_days = open_days(daily["day"].min(), today - timedelta(days=1), closed)
        counts = daily.set_index("day")["served"].reindex(history_days, fill_value=0)
        horizon_test = TEST_WEEKS * season
        if len(counts) < horizon_test + 3 * season:
            st.info(f"The forecast needs at least {horizon_test + 3 * season} open days of history "
                    f"(there are {len(counts)}).")
            return
        future_days = open_days(today, today + timedelta(days=FUTURE_DAYS - 1), closed)
        result = run_forecast(tuple(counts.tolist()), season, horizon_test, len(future_days))

        st.write("**How many customers to expect in the next four weeks**")
        future = pd.DataFrame({"day": future_days, "forecast": result["future"]})
        fig = go.Figure()
        recent = counts.iloc[-8 * season:]
        fig.add_trace(go.Scatter(x=list(recent.index), y=recent.values, name="Actual", mode="lines+markers",
                                 line=dict(color="#1f77b4")))
        test_days = list(counts.index[-horizon_test:])
        fig.add_trace(go.Scatter(x=test_days, y=result["hw_test"], name="Holt-Winters (test weeks)",
                                 line=dict(color="#ff7f0e", dash="dot")))
        fig.add_trace(go.Scatter(x=test_days, y=result["naive_test"], name="Baseline (test weeks)",
                                 line=dict(color="grey", dash="dot")))
        band = 1.28 * result["spread"]  # roughly an 80% range
        fig.add_trace(go.Scatter(x=future_days + future_days[::-1],
                                 y=[v + band for v in result["future"]] + [max(v - band, 0) for v in result["future"]][::-1],
                                 fill="toself", fillcolor="rgba(255,127,14,0.15)", line=dict(width=0),
                                 name="Likely range", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=future_days, y=result["future"], name="Forecast", mode="lines+markers",
                                 line=dict(color="#ff7f0e")))
        fig.update_layout(height=420, margin=dict(t=10, b=10), yaxis_title="Customers per day",
                          legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, use_container_width=True)

        future["Day"] = [d.strftime("%a %d %b") for d in future["day"]]
        future["Expected customers"] = future["forecast"].round().astype(int)
        future["Suggested barbers"] = np.clip(np.ceil(future["forecast"] / 7), 1, None).astype(int)
        st.dataframe(future[["Day", "Expected customers", "Suggested barbers"]], hide_index=True,
                     use_container_width=True)
        st.caption("Suggested barbers assumes each barber can see about 7 customers a day.")

        st.write("**How accurate is it?** Tested on the last 4 weeks, which the model did not see.")
        scores = pd.DataFrame([{"Model": k, "MASE": round(v[0], 2), "MAPE": f"{v[1]:.1f}%"}
                               for k, v in result["scores"].items()])
        st.dataframe(scores, hide_index=True, use_container_width=True)
        st.caption("MASE below 1 means better than simply guessing 'same as last week'. "
                   "MAPE is how far off the daily forecast was on average, in percent.")
