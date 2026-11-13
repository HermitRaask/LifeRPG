"""Rust calculations. Automatic scheduling requires an approved policy."""

from datetime import date, timedelta

from liferpg.domain.progression import xp_required


def rust_state(events):
    debts, last_gain, last_decay = {}, {}, {}
    for event in events:
        day = date.fromisoformat(event["game_date"])
        for key, row in event["payload"].get("xp_by_stat", {}).items():
            if "rust_debt_after" in row:
                debts[key] = row["rust_debt_after"]
            if row.get("last_decay"):
                last_decay[key] = date.fromisoformat(row["last_decay"])
            if event["event_type"] == "activity.completed" and (
                row["credited_xp"] or row.get("rust_paid")
            ):
                last_gain[key] = day
    return debts, last_gain, last_decay


def decay_row(stat, debt, last_gain, last_decay, today, first_day, cap):
    """Settle eligible days once, using explicit testable policy parameters."""
    first = last_gain + timedelta(days=first_day)
    if last_decay is not None:
        first = max(first, last_decay + timedelta(days=1))
    if first > today:
        return None
    days = (today - first).days + 1
    per_day = xp_required(stat.level) * 5 // 100
    xp = stat.current_xp
    removed = min(xp, days * per_day)
    new_debt = min(cap, debt + days * per_day - removed)
    return {
        "level_before": stat.level, "level_after": stat.level,
        "xp_before": xp, "xp_after": xp - removed, "credited_xp": 0,
        "decayed_xp": removed, "rust_debt_before": debt,
        "rust_debt_after": new_debt, "first_decay": first.isoformat(),
        "last_decay": today.isoformat(), "days": days, "daily_xp": per_day,
    }
