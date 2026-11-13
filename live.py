"""Fadecast - the live walk-in queue and the barber's day."""

from datetime import datetime, timedelta

import streamlit as st

from logic import minutes_until, next_free, plan_queue

LONDON_TZ = "Europe/London"


def _ts(value, tz):
    return datetime.fromisoformat(value).astimezone(tz) if value else None


def load_queue(db, tz):
    """Today's queue from the database, with times turned into Python times."""
    rows = db.rpc("queue_snapshot", {}).execute().data
    for r in rows:
        for key in ("start_time", "end_time", "started_at", "created_at"):
            r[key] = _ts(r[key], tz)
    return rows


def shop_hours_today(db, now):
    """(opens, closes) as times today, or None if the shop is closed today."""
    h = db.table("opening_hours").select("*").eq("day_of_week", now.weekday()).execute().data[0]
    if h["closed"]:
        return None
    opens = now.replace(hour=int(h["opens"][:2]), minute=int(h["opens"][3:5]), second=0, microsecond=0)
    closes = now.replace(hour=int(h["closes"][:2]), minute=int(h["closes"][3:5]), second=0, microsecond=0)
    return opens, closes


def queue_state(db, tz):
    """Everything the queue screens need: queue rows, barbers, the plan and the time now."""
    now = datetime.now(tz)
    barbers = db.table("barbers").select("id, name").eq("active", True).order("id").execute().data
    names = {b["id"]: b["name"] for b in barbers}
    snapshot = load_queue(db, tz)
    plan, free_at, blocks = plan_queue(snapshot, list(names), now)
    return now, barbers, names, snapshot, plan, free_at, blocks


def wait_text(minutes):
    if minutes <= 0:
        return "now"
    if minutes < 60:
        return f"about {minutes} min"
    return f"about {minutes // 60} h {minutes % 60:02d} min"


# ------------------------------------------------------------------ public / customer view

def public_queue(db, tz, user, services):
    """The live queue everyone can see, refreshed every 15 seconds. Logged-in customers can join it."""

    @st.fragment(run_every=15)
    def _live():
        now, barbers, names, snapshot, plan, free_at, blocks = queue_state(db, tz)
        st.subheader("Live walk-in queue")
        hours = shop_hours_today(db, now)
        if not hours or not (hours[0] <= now < hours[1]):
            st.caption("The shop is closed right now, so the queue is not running.")
            return
        if not barbers or not services:
            st.caption("The queue is not running yet.")
            return

        waiting = [r for r in snapshot if r["status"] == "waiting"]
        shortest = min(s["duration_minutes"] for s in services)
        bid, start = next_free(free_at, blocks, shortest)
        c1, c2 = st.columns(2)
        c1.metric("People waiting", len(waiting))
        c2.metric("Wait if you walk in now", wait_text(minutes_until(start, now)))
        st.caption("Next free: " + " · ".join(
            f"{names[b]} {wait_text(minutes_until(next_free(free_at, blocks, shortest, b)[1], now))}"
            for b in names) + f" · updated {now.strftime('%H:%M:%S')}")

        if not user:
            st.caption("Log in to join the queue from your phone.")
            return

        mine = next((r for r in waiting if r["mine"]), None)
        if mine:
            position = [r["id"] for r in sorted(waiting, key=lambda r: r["created_at"])].index(mine["id"]) + 1
            b, t = plan.get(mine["id"], (None, now))
            st.success(f"You are number {position} in the queue. Expected wait: {wait_text(minutes_until(t, now))}"
                       + (f" with {names[b]}." if b in names else "."))
            if st.button("Leave the queue"):
                try:
                    db.rpc("cancel_booking", {"p_id": mine["id"]}).execute()
                except Exception as e:
                    st.error(getattr(e, "message", None) or str(e))
                    return
                st.rerun()
            return

        with st.form("join_queue"):
            st.write("**Join the queue now**")
            c1, c2 = st.columns(2)
            service = c1.selectbox("Service", services, format_func=lambda s: f"{s['name']} ({s['duration_minutes']} min)")
            choice = c2.selectbox("Barber", [None] + barbers,
                                  format_func=lambda b: "Any barber (quickest)" if b is None else b["name"])
            if st.form_submit_button("Join queue", type="primary"):
                b, t = next_free(free_at, blocks, service["duration_minutes"], choice["id"] if choice else None)
                if t + timedelta(minutes=service["duration_minutes"]) > hours[1]:
                    st.error("Sorry, there is no time left today for that. Please book for another day.")
                    return
                try:
                    db.rpc("join_queue", {"p_service": service["id"], "p_barber": choice["id"] if choice else None,
                                          "p_estimate": minutes_until(t, now)}).execute()
                except Exception as e:
                    st.error(getattr(e, "message", None) or str(e))
                    return
                st.rerun()

    _live()


# ------------------------------------------------------------------ staff view

def today_page(db, tz, profile_id):
    """The barber's day: the walk-in queue, each person's status, and adding walk-ins."""
    st.header("Today")
    barbers = db.table("barbers").select("id, name, profile_id").eq("active", True).order("id").execute().data
    services = db.table("services").select("id, name, duration_minutes").eq("active", True).order("id").execute().data
    if not barbers or not services:
        st.info("Add services and barbers in Shop settings first.")
        return
    ids = [b["id"] for b in barbers]
    linked = next((i for i, b in enumerate(barbers) if b["profile_id"] == profile_id), 0)
    me = st.selectbox("Showing the day for", barbers, index=linked, format_func=lambda b: b["name"])

    # Add a walk-in
    with st.expander("➕ Add a walk-in", expanded=False):
        with st.form("add_walk_in", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            name = c1.text_input("Name (first name is fine)")
            service = c2.selectbox("Service", services, format_func=lambda s: s["name"])
            choice = c3.selectbox("Barber", [None] + barbers,
                                  format_func=lambda b: "Any barber" if b is None else b["name"])
            if st.form_submit_button("Add to queue", type="primary"):
                now, _, _, _, _, free_at, blocks = queue_state(db, tz)
                _, t = next_free(free_at, blocks, service["duration_minutes"], choice["id"] if choice else None)
                try:
                    db.rpc("add_walk_in", {"p_name": name, "p_service": service["id"],
                                           "p_barber": choice["id"] if choice else None,
                                           "p_estimate": minutes_until(t, now)}).execute()
                    st.toast(f"Added {name or 'walk-in'}: estimated wait {wait_text(minutes_until(t, now))}.")
                except Exception as e:
                    st.error(getattr(e, "message", None) or str(e))

    @st.fragment(run_every=15)
    def _board():
        now, _, names, snapshot, plan, free_at, blocks = queue_state(db, tz)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = db.table("bookings").select(
            "id, start_time, status, is_walk_in, walk_in_name, barber_id, created_at, services(name), profiles(full_name)") \
            .gte("start_time", start_of_day.isoformat()) \
            .lt("start_time", (start_of_day + timedelta(days=1)).isoformat()) \
            .order("start_time").execute().data
        by_id = {r["id"]: r for r in rows}

        def who(r):
            return r["walk_in_name"] if r["is_walk_in"] else ((r.get("profiles") or {}).get("full_name") or "Customer")

        def act(booking_id, status, barber=None):
            try:
                db.rpc("set_status", {"p_id": booking_id, "p_status": status, "p_barber": barber}).execute()
            except Exception as e:
                st.error(getattr(e, "message", None) or str(e))
                return
            st.rerun(scope="fragment")

        # Walk-in queue
        waiting = sorted((r for r in snapshot if r["status"] == "waiting"), key=lambda r: r["created_at"])
        st.subheader(f"Walk-in queue ({len(waiting)})")
        if not waiting:
            st.caption("Nobody is waiting.")
        for pos, w in enumerate(waiting, 1):
            r = by_id.get(w["id"], {})
            b, t = plan.get(w["id"], (None, now))
            asked = names.get(w["barber_id"], "Any barber")
            c1, c2, c3 = st.columns([5, 2, 2])
            c1.write(f"**{pos}. {who(r) if r else 'Walk-in'}** · {(r.get('services') or {}).get('name', '')} · "
                     f"asked for {asked} · waiting {minutes_until(now, w['created_at'])} min · "
                     f"expected {wait_text(minutes_until(t, now))} ({names.get(b, '?')})")
            if c2.button(f"Start with {me['name']}", key=f"start_{w['id']}"):
                act(w["id"], "in_chair", me["id"])
            if c3.button("Left / no-show", key=f"ns_{w['id']}"):
                act(w["id"], "no_show")

        # This barber's day
        st.subheader(f"{me['name']}'s day")
        mine = [r for r in rows if r["barber_id"] == me["id"] and r["status"] in ("booked", "in_chair")]
        if not mine:
            st.caption("No one booked in or in the chair.")
        for r in mine:
            at = datetime.fromisoformat(r["start_time"]).astimezone(tz).strftime("%H:%M")
            label = "🪑 In the chair" if r["status"] == "in_chair" else ("Walk-in" if r["is_walk_in"] else "Booked")
            c1, c2, c3 = st.columns([5, 2, 2])
            c1.write(f"**{at}** · {who(r)} · {(r.get('services') or {}).get('name', '')} · {label}")
            if r["status"] == "booked":
                if c2.button("In the chair", key=f"in_{r['id']}"):
                    act(r["id"], "in_chair", me["id"])
                if c3.button("No-show", key=f"nsb_{r['id']}"):
                    act(r["id"], "no_show")
            else:
                if c2.button("Finished", key=f"fin_{r['id']}", type="primary"):
                    act(r["id"], "finished")

        done = [r for r in rows if r["barber_id"] == me["id"] and r["status"] in ("finished", "no_show")]
        if done:
            st.caption(f"Done today: {sum(r['status'] == 'finished' for r in done)} finished, "
                       f"{sum(r['status'] == 'no_show' for r in done)} no-show.")
        st.caption(f"Updates every 15 seconds · last {now.strftime('%H:%M:%S')}")

    _board()
