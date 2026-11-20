"""Quest periods and streak transitions for the console game."""

from collections import Counter
from copy import deepcopy
from datetime import date, timedelta

from liferpg.domain.catalog_state import (
    active, available_builtin, archive_state,
)

QUESTS = {
    "sleep": (
        "Тебя даже вчерашний шторм не разбудил", "daily", {"sleep"}, set(),
    ),
    "warmup": ("Проснулись-потянулись", "daily", {"warmup"}, set()),
    "writing": ("Творец миражей", "daily", {"writing"}, set()),
    "training": ("Пробуждение стенда", "daily", {"training"}, set()),
    "meditation": ("Раскрой чакры", "daily", {"meditation"}, set()),
    "run": ("День ног", "daily", {"run"}, set()),
    "weapons": ("Волыну убери", "daily", {"weapons"}, set()),
    "sisyphus": (
        "Сизиф", "daily", {"training", "writing", "meditation"},
        {"chess", "chess-puzzles", "learning-code", "project", "weapons"},
    ),
    "eagle": ("Всегда ещё одну", "weekly", {"eagle"}, set()),
    "project": ("Brain? No, thx, i use AI", "weekly", {"project"}, set()),
    "code": (
        "Я начинаю понимать индусский", "weekly", {"learning-code"}, set(),
    ),
}


def quest_catalog(custom, events=(), *, include_inactive=False):
    builtin = {k: v for k, v in QUESTS.items()
               if include_inactive or available_builtin(events, "quest", k)}
    result = {**builtin, **{
        key: (data["name"], data["period"], {data["activity"]}, set())
        for key, data in custom.items()
    }}
    return {k: v for k, v in result.items()
            if include_inactive or active(events, "quest", k)}


def period_counts(completions, today):
    counts = {"daily": Counter(), "weekly": Counter()}
    monday = week_start(today)
    for day, activity in completions:
        if day == today:
            counts["daily"][activity] += 1
        if monday <= day < monday + timedelta(days=7):
            counts["weekly"][activity] += 1
    return counts


def week_start(day):
    return day - timedelta(days=day.weekday())


def quest_state(events):
    for event in reversed(events):
        if "quest_state" in event["payload"]:
            state = deepcopy(event["payload"]["quest_state"])
            if "completed_periods" not in state:
                periods = {}
                for historical in events:
                    effects = historical["payload"].get("consequences", [])
                    for effect in effects:
                        if effect["type"] == "quest.period_completed":
                            periods.setdefault(effect["quest"], []).append(
                                effect["period"]
                            )
                for key, period in state["completed"].items():
                    periods.setdefault(key, []).append(period)
                state["completed_periods"] = {
                    key: sorted(set(days)) for key, days in periods.items()
                }
            return state
    return {"day": None, "banks": {"daily": 0, "weekly": 0},
            "streaks": {}, "completed": {}}


def advance(state, today, custom=None, *, events=()):
    """Settle closed periods and refill the corresponding banks."""
    quests = quest_catalog(custom or {}, events)
    effects = []
    if state["day"] is None:
        cursor = today
    else:
        cursor = date.fromisoformat(state["day"]) + timedelta(days=1)
    while cursor <= today:
        first = state["day"] is None
        if first or cursor.weekday() == 0:
            refill(state, effects, "daily", 5, cursor)
        if first or cursor.day == 1:
            refill(state, effects, "weekly", 3, cursor)
        if not first:
            settle(state, effects, "daily", cursor - timedelta(days=1), quests)
            if cursor.weekday() == 0:
                settle(state, effects, "weekly",
                       cursor - timedelta(days=7), quests)
        state["day"] = cursor.isoformat()
        cursor += timedelta(days=1)
    return effects


def freeze_settings(state, kind):
    default = {"maximum": 5 if kind == "daily" else 3,
               "amount": 1, "interval": 1}
    return {**default, **state.get("freeze_settings", {}).get(kind, {})}


def refill(state, effects, kind, limit, day):
    config = freeze_settings(state, kind)
    marker = (day - timedelta(days=day.weekday()) if kind == "daily"
              else day.replace(day=1))
    calendars = state.setdefault("freeze_calendar", {})
    last = calendars.get(kind)
    if last:
        previous = date.fromisoformat(last)
        elapsed = ((marker - previous).days // 7 if kind == "daily" else
                   (marker.year - previous.year) * 12
                   + marker.month - previous.month)
        if elapsed < config["interval"]:
            return
    calendars[kind] = marker.isoformat()
    before = state["banks"][kind]
    state["banks"][kind] = min(config["maximum"], before + config["amount"])
    amount = state["banks"][kind] - before
    effects.append({"type": "freeze.credited", "bank": kind,
                    "period": marker.isoformat(), "amount": amount})


def settle(state, effects, kind, period, quests=QUESTS):
    missed = [
        key for key, streak in state["streaks"].items()
        if key in quests and quests[key][1] == kind and streak["duration"]
        and period.isoformat() not in state.get("completed_periods", {}).get(
            key, [state["completed"].get(key, "")]
        )
    ]
    # Draft D-06 supplies a stable provisional order for competing streaks.
    missed.sort(key=lambda key: (-state["streaks"][key]["duration"], key))
    for key in missed:
        streak = state["streaks"][key]
        if state["banks"][kind]:
            state["banks"][kind] -= 1
            transition = "streak.protected_by_freeze"
        elif not streak["last_chance_used"]:
            streak["last_chance_used"] = True
            streak["at_risk"] = True
            transition = "streak.protected_by_last_chance"
        else:
            transition = "streak.reset"

        if transition == "streak.reset":
            streak.pop("bonus_periods", None)
            streak.update(duration=0, successful=0, protected=0,
                          at_risk=False, last_chance_used=False)
        else:
            streak["duration"] += 1
            streak["protected"] += 1
            streak["best"] = max(streak["best"], streak["duration"])
        effects.append({"type": transition, "quest": key,
                        "period": period.isoformat()})


def evaluate(state, today, completions, custom=None, *, only=None, events=()):
    effects = []
    custom = custom or {}
    counts = period_counts(completions, today)
    monday = week_start(today)
    quests = quest_catalog(custom, events)
    for key, (_, kind, required, alternatives) in quests.items():
        if only is not None and key != only:
            continue
        lifecycle = archive_state(events).get(("quest", key), {})
        if lifecycle.get("restored_seq"):
            start = date.fromisoformat(lifecycle["restored_day"])
            start = start if kind == "daily" else week_start(start)
            if today < start:
                continue
            from liferpg.game import effective_completions
            old = Counter(
                (date.fromisoformat(day), item["activity_id"])
                for seq, (day, item) in effective_completions(events).items()
                if seq <= lifecycle["restored_seq"] and item is not None
            )
            fresh = []
            for entry in completions:
                if old[entry]:
                    old[entry] -= 1
                else:
                    fresh.append(entry)
            counts = period_counts(fresh, today)
        else:
            counts = period_counts(completions, today)
        period = (today if kind == "daily" else monday).isoformat()
        done = counts[kind].keys()
        periods = state.setdefault("completed_periods", {}).setdefault(
            key, [state["completed"][key]] if key in state["completed"] else []
        )
        if period in periods:
            continue
        if not required <= done or (alternatives and not alternatives & done):
            continue
        if key in custom:
            row = custom[key]
            if counts[kind][row["activity"]] < row["count"]:
                continue
        periods.append(period)
        periods.sort()
        state["completed"][key] = max(periods)
        streak = state["streaks"].setdefault(
            key, dict(duration=0, successful=0, protected=0, best=0,
                      last_chance_used=False, at_risk=False)
        )
        streak["duration"] += 1
        if "bonus_periods" in streak:
            streak["bonus_periods"] += 1
        streak["successful"] += 1
        streak["best"] = max(streak["best"], streak["duration"])
        streak["at_risk"] = False
        effects.extend([
            {"type": "quest.period_completed", "quest": key, "period": period},
            {"type": "streak.extended", "quest": key, "period": period},
        ])
    return effects
