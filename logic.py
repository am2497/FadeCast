"""Fadecast - the maths behind the live queue and the forecast.

Kept separate from the web pages so it can be tested on its own.
"""

from datetime import datetime, timedelta

import numpy as np


# ---------------------------------------------------------------- live queue (wait estimates)

def earliest_start(free_from, minutes, blocks):
    """The first time from `free_from` when a barber has `minutes` clear, given the
    times already taken (`blocks` = list of (start, end))."""
    t = free_from
    for start, end in sorted(blocks):
        if end <= t:
            continue  # already over
        if t + timedelta(minutes=minutes) <= start:
            break  # fits before this one
        t = max(t, end)  # clashes, so try after it
    return t


def plan_queue(snapshot, barber_ids, now):
    """Work out who each waiting walk-in will go to and when.

    How it works:
      1. Each barber is free now, or when the person in their chair should finish.
      2. Online bookings for the rest of today are times the barber is already taken.
      3. Walk-ins are served in the order they arrived. Each goes to the barber they asked for,
         or to whichever barber can start them soonest.
    Returns (plan, free_at, blocks): plan maps booking id -> (barber id, expected start time).
    """
    free_at = {b: now for b in barber_ids}
    blocks = {b: [] for b in barber_ids}

    for r in snapshot:
        bid = r["barber_id"]
        if bid not in free_at:
            continue
        if r["status"] == "in_chair":
            started = r["started_at"] or r["start_time"]
            done = started + timedelta(minutes=r["duration_minutes"])
            free_at[bid] = max(free_at[bid], done)
        elif r["status"] == "booked" and not r["is_walk_in"]:
            blocks[bid].append((r["start_time"], r["end_time"]))

    plan = {}
    waiting = sorted((r for r in snapshot if r["status"] == "waiting"), key=lambda r: r["created_at"])
    for w in waiting:
        options = [w["barber_id"]] if w["barber_id"] in free_at else list(free_at)
        if not options:
            continue
        best = None
        for bid in options:
            t = earliest_start(free_at[bid], w["duration_minutes"], blocks[bid])
            if best is None or t < best[1]:
                best = (bid, t)
        bid, t = best
        plan[w["id"]] = best
        free_at[bid] = t + timedelta(minutes=w["duration_minutes"])
    return plan, free_at, blocks


def next_free(free_at, blocks, minutes, barber_id=None):
    """When a new walk-in would start: with a chosen barber, or the soonest of any barber.
    Returns (barber id, start time)."""
    options = [barber_id] if barber_id in free_at else list(free_at)
    best = None
    for bid in options:
        t = earliest_start(free_at[bid], minutes, blocks[bid])
        if best is None or t < best[1]:
            best = (bid, t)
    return best


def minutes_until(t, now):
    return max(0, round((t - now).total_seconds() / 60))


# ---------------------------------------------------------------- forecast accuracy

def seasonal_naive(train, horizon, season):
    """The simple baseline: each day is the same as the same weekday last week."""
    last = np.asarray(train[-season:], dtype=float)
    return np.resize(last, horizon)


def mase(actual, predicted, train, season):
    """Mean Absolute Scaled Error: below 1 means better than the simple 'same as last week' guess
    (measured on the training data)."""
    actual, predicted, train = (np.asarray(x, dtype=float) for x in (actual, predicted, train))
    scale = np.mean(np.abs(train[season:] - train[:-season]))
    return float(np.mean(np.abs(actual - predicted)) / scale) if scale else float("nan")


def mape(actual, predicted):
    """Mean Absolute Percentage Error: on average, how far off in % (days with 0 customers skipped)."""
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    mask = actual > 0
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100) if mask.any() else float("nan")


def open_days(start, end, closed_weekdays):
    """Every date from start to end (inclusive) when the shop is open."""
    days, d = [], start
    while d <= end:
        if d.weekday() not in closed_weekdays:
            days.append(d)
        d += timedelta(days=1)
    return days
