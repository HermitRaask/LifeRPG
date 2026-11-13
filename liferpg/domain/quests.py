"""Quest periods and streak transitions for the console game."""

from copy import deepcopy
from datetime import date, timedelta

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


def week_start(day):
    return day - timedelta(days=day.weekday())


def quest_state(events):
    for event in reversed(events):
        if "quest_state" in event["payload"]:
            return deepcopy(event["payload"]["quest_state"])
    return {"day": None, "banks": {"daily": 0, "weekly": 0},
            "streaks": {}, "completed": {}}


def advance(state, today):
    """Settle closed periods and refill the corresponding banks."""
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
        settle(state, effects, "daily", cursor - timedelta(days=1))
        if cursor.weekday() == 0:
            settle(state, effects, "weekly", cursor - timedelta(days=7))
        state["day"] = cursor.isoformat()
        cursor += timedelta(days=1)
    return effects


def refill(state, effects, kind, limit, day):
    if state["banks"][kind] < limit:
        state["banks"][kind] += 1
        effects.append({"type": "freeze.credited", "bank": kind,
                        "period": day.isoformat()})


def settle(state, effects, kind, period):
    missed = [
        key for key, streak in state["streaks"].items()
        if QUESTS[key][1] == kind and streak["duration"]
        and state["completed"][key] < period.isoformat()
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
            streak.update(duration=0, successful=0, protected=0,
                          at_risk=False, last_chance_used=False)
        else:
            streak["duration"] += 1
            streak["protected"] += 1
            streak["best"] = max(streak["best"], streak["duration"])
        effects.append({"type": transition, "quest": key,
                        "period": period.isoformat()})


def evaluate(state, today, completions):
    effects = []
    sets = {"daily": set(), "weekly": set()}
    monday = week_start(today)
    for day, activity in completions:
        if day == today:
            sets["daily"].add(activity)
        if monday <= day <= today:
            sets["weekly"].add(activity)
    for key, (_, kind, required, alternatives) in QUESTS.items():
        period = (today if kind == "daily" else monday).isoformat()
        done = sets[kind]
        if state["completed"].get(key, "") >= period:
            continue
        if not required <= done or (alternatives and not alternatives & done):
            continue
        state["completed"][key] = period
        streak = state["streaks"].setdefault(
            key, dict(duration=0, successful=0, protected=0, best=0,
                      last_chance_used=False, at_risk=False)
        )
        streak["duration"] += 1
        streak["successful"] += 1
        streak["best"] = max(streak["best"], streak["duration"])
        streak["at_risk"] = False
        effects.extend([
            {"type": "quest.period_completed", "quest": key, "period": period},
            {"type": "streak.extended", "quest": key, "period": period},
        ])
    return effects
