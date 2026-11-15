"""Presentation data derived from saved results, without writing events."""
from datetime import datetime, timedelta

from liferpg.domain.activities import catalog
from liferpg.domain.activity_xp import game_date
from liferpg.domain.achievements import ACHIEVEMENTS
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.perks import PERKS, describe_perk
from liferpg.domain.progression import xp_required
from liferpg.domain.quests import QUESTS, week_start
from liferpg.game import effective_completions
from webgame.services import local_now

STAT_SYMBOLS = ["↟", "◇", "▥", "◎", "✧", "◈"]


def reward_rows(payload):
    result = []
    correction = "replacement" in payload
    previous = (payload.get("previous") or {}).get("xp_by_stat", {})
    replacement = (payload.get("replacement") or {}).get("xp_by_stat", {})
    for key, row in payload.get("xp_by_stat", {}).items():
        amount = row["credited_xp"]
        if correction:
            amount = (replacement.get(key, {}).get("credited_xp", 0)
                      - previous.get(key, {}).get("credited_xp", 0))
            if amount == 0 and row["level_before"] == row["level_after"]:
                continue
        result.append({
            "name": ATTRIBUTE_NAMES[key], **row,
            "display_xp": f"{amount:+d}", "correction": correction,
        })
    return result


def effect_messages(payload):
    result = []
    for effect in payload.get("consequences", []):
        kind = effect["type"]
        if kind == "achievement.unlocked":
            result.append("Достижение: " + ACHIEVEMENTS[effect["achievement"]])
        elif kind.startswith("stat.level"):
            label = ("новый уровень" if kind == "stat.leveled_up"
                     else "снят уровень")
            result.append(
                f"{ATTRIBUTE_NAMES[effect['stat']]}: {label} {effect['level']}"
            )
        elif kind == "skill.xp_credited":
            result.append(f"Писательство +{effect['xp']} XP")
        elif kind == "skill.mastered":
            result.append("Писательство: получено мастерство")
        elif kind == "quest.period_completed":
            result.append("Задание выполнено: " + QUESTS[effect["quest"]][0])
    return result


def context(events, state):
    config = state["config"]
    now = local_now(config)
    today = game_date(now, config["boundary_minutes"])
    activities = catalog()
    stats = []
    for (key, name), symbol in zip(ATTRIBUTE_NAMES.items(), STAT_SYMBOLS):
        row = state["stats"][key]
        required = xp_required(row["level"])
        stats.append({
            **row, "key": key, "name": name, "required": required,
            "percent": min(100, row["xp"] * 100 // required), "symbol": symbol,
        })
    perks = [{
        "key": key, "name": name.replace(" (TBD)", ""),
        "active": key in state["perks"],
        "description": describe_perk(key, activities),
    } for key, name in PERKS.items()]
    quests = []
    qs = state["quests"]
    for key, (name, kind, required, alternatives) in QUESTS.items():
        period = (today if kind == "daily" else week_start(today)).isoformat()
        streak = qs["streaks"].get(key, {})
        rule = " + ".join(activities[k].name for k in sorted(required))
        if alternatives:
            rule += " + одно из: " + ", ".join(
                activities[k].name for k in sorted(alternatives)
            )
        quests.append({
            "key": key, "name": name, "kind": kind, "rule": rule,
            "done": qs["completed"].get(key) == period,
            "length": streak.get("duration", 0),
            "best": streak.get("best", 0),
            "bonus": min(25, streak.get(
                "bonus_periods", streak.get("successful", 0)
            )),
            "last_chance": streak.get("last_chance_used", False),
            "at_risk": streak.get("at_risk", False),
        })
    counts = {(d, k): n for d, k, n in state["counts"]}
    activity_rows = [{
        "key": key, "name": a.name, "category": a.category,
        "count": counts.get((today.isoformat(), key), 0),
        "rewards": ", ".join(f"{ATTRIBUTE_NAMES[k]} +{v}"
                             for k, v in a.rewards().items()),
    } for key, a in activities.items()]
    history = []
    entries = effective_completions(events)
    for event in reversed(events):
        if event["event_type"] != "activity.completed":
            continue
        _, current = entries[event["seq"]]
        registered = datetime.fromisoformat(event["recorded_at_utc"])
        deadline = registered + timedelta(hours=1)
        history.append({
            "seq": event["seq"], "name": (
                current["activity_name_snapshot"] if current else
                event["payload"]["activity_name_snapshot"]
            ),
            "time": datetime.fromisoformat(event["local_datetime"]).strftime(
                "%d.%m.%Y · %H:%M"
            ), "day": event["game_date"],
            "canceled": current is None,
            "revised": current != event["payload"],
            "rows": reward_rows(current) if current else [],
            "editable": registered <= now < deadline,
            "remaining": max(0, int((deadline - now).total_seconds() // 60)),
            "activity": current["activity_id"] if current else
            event["payload"]["activity_id"],
            "intensity": current["intensity"] if current else
            event["payload"]["intensity"],
        })
    skill = {**state["writing"]}
    skill["required"] = (skill["level"] * 1000
                         if skill["level"] < 15 else 15000)
    skill["percent"] = min(100, skill["xp"] * 100 // skill["required"])
    return {
        "state": state, "stats": stats, "perks": perks, "quests": quests,
        "daily": [q for q in quests if q["kind"] == "daily"],
        "completed_today": sum(q["done"] for q in quests
                               if q["kind"] == "daily"),
        "activities": activity_rows, "history": history, "skill": skill,
        "achievements": [
            {"key": k, "name": v, "open": k in state["achievements"]}
            for k, v in ACHIEVEMENTS.items()
        ],
        "game_date": today, "clock": now, "events": list(reversed(events)),
        "total_today": sum(n for d, _, n in state["counts"]
                           if d == today.isoformat()),
        "boundary_display": (
            f"{config['boundary_minutes'] // 60:02}:"
            f"{config['boundary_minutes'] % 60:02}"
        ),
    }


def statistics_labels(statistics):
    result = dict(statistics)
    result["xp_labels"] = [
        (ATTRIBUTE_NAMES[key], amount)
        for key, amount in statistics["xp"].items()
    ]
    result["level_labels"] = [
        (day, ATTRIBUTE_NAMES[key], before, after)
        for day, key, before, after in statistics["levels"]
    ]
    result["skill_labels"] = [
        ("Писательство" if key == "writing" else key, amount)
        for key, amount in statistics["skills"].items()
    ]
    kinds = {
        "quest.period_completed": "Задание выполнено",
        "streak.extended": "Серия продлена",
        "streak.reset": "Серия прервана",
        "streak.protected_by_freeze": "Использована заморозка",
        "streak.protected_by_last_chance": "Использован последний шанс",
    }
    result["quest_labels"] = [
        (day, kinds.get(kind, "Состояние серии изменено"),
         QUESTS[key][0] if key in QUESTS else key)
        for day, kind, key in statistics["quests"]
    ]
    result["achievement_labels"] = [
        {"date": item["date"], "name": ACHIEVEMENTS[item["name"]]}
        for item in statistics.get("achievements", [])
    ]
    return result
