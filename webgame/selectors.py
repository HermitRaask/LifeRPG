"""Presentation data derived from saved results, without writing events."""
from datetime import date, datetime, timedelta

from liferpg.domain.activities import catalog
from liferpg.domain.activity_xp import game_date
from liferpg.domain.achievements import (
    BUILTIN_CONDITIONS, YEARLY, achievement_names, unlocked_order,
    condition_available,
)
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.perks import PERKS, describe_perk
from liferpg.domain.progression import xp_required
from liferpg.domain.content import (
    MODIFIERS, definitions, display_texts,
)
from liferpg.domain.quests import period_counts, quest_catalog, week_start
from liferpg.game import effective_completions
from liferpg.domain.skills import root_skills, active_root_skills
from liferpg.domain.catalog_state import available_builtin
from webgame import services

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


def effect_messages(payload, *, developer=True, events=()):
    achievements = achievement_names(events)
    quests = quest_catalog(definitions(events, "quest"), events,
                           include_inactive=True)
    skills = root_skills(events)
    result = []
    for effect in payload.get("consequences", []):
        kind = effect["type"]
        if kind == "achievement.unlocked":
            result.append("Достижение: " + achievements[effect["achievement"]])
        elif kind.startswith("stat.level") and developer:
            label = ("новый уровень" if kind == "stat.leveled_up"
                     else "снят уровень")
            result.append(
                f"{ATTRIBUTE_NAMES[effect['stat']]}: {label} {effect['level']}"
            )
        elif kind == "skill.xp_credited":
            name = skills.get(effect["skill"], {}).get("name", effect["skill"])
            result.append(f"{name} +{effect['xp']} XP")
        elif kind == "skill.mastered":
            name = skills.get(effect["skill"], {}).get("name", effect["skill"])
            result.append(f"{name}: получено мастерство")
        elif kind == "quest.period_completed":
            result.append("Задание выполнено: " + quests[effect["quest"]][0])
    return result


def context(events, state):
    config = state["config"]
    now = services.local_now(config)
    today = game_date(now, config["boundary_minutes"])
    activities = catalog(events)
    all_activities = catalog(events, include_inactive=True)
    custom_quests = definitions(events, "quest")
    quest_names = quest_catalog(custom_quests, events)
    entries = effective_completions(events)
    counts_by_period = period_counts(
        [(date.fromisoformat(day), item["activity_id"])
         for day, item in entries.values() if item is not None], today,
    )
    quest_order = completion_order(events, today)
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
        "rule": describe_perk(key, activities),
        "description": display_texts(
            events, "perk", key, describe_perk(key, activities)
        )["condition_text"],
    } for key, name in PERKS.items()
        if available_builtin(events, "perk", key) or key in state["perks"]]
    quests = []
    qs = state["quests"]
    for key, (name, kind, required, alternatives) in quest_names.items():
        period = (today if kind == "daily" else week_start(today)).isoformat()
        streak = qs["streaks"].get(key, {})
        rule = " + ".join(all_activities[k].name for k in sorted(required))
        if alternatives:
            rule += " + одно из: " + ", ".join(
                all_activities[k].name for k in sorted(alternatives)
            )
        if key in custom_quests:
            row = custom_quests[key]
            activity = row["activity"]
            count = min(row["count"], counts_by_period[kind][activity])
            rule = (f'{all_activities[activity].name}: '
                    f'{count} из {row["count"]}')
        parameters = rule
        if key in custom_quests:
            row = custom_quests[key]
            activity_name = all_activities[row["activity"]].name
            rule = f'{activity_name}: {row["count"]} выполнений'
        text = display_texts(events, "quest", key, rule)["condition_text"]
        quests.append({
            "key": key, "name": name, "kind": kind, "rule": text,
            "parameters": parameters, "description": "",
            "condition_text": text,
            "progress": (parameters if key in custom_quests else ""),
            "done": period in qs.get("completed_periods", {}).get(
                key, [qs["completed"].get(key)]
            ),
            "length": streak.get("duration", 0),
            "best": streak.get("best", 0),
            "bonus": min(25, streak.get(
                "bonus_periods", streak.get("successful", 0)
            )),
            "last_chance": streak.get("last_chance_used", False),
            "at_risk": streak.get("at_risk", False),
        })
    quests.sort(key=lambda q: quest_order.get(q["key"], len(quest_order)))
    counts = {(d, k): n for d, k, n in state["counts"]}
    activity_rows = [{
        "key": key, "name": a.name, "category": a.category,
        "description": a.description, "requirement": a.requirement(),
        "locked": bool(a.requirement() and
                       state["stats"][a.condition["target"]]["level"]
                       < a.condition["threshold"]),
        "count": counts.get((today.isoformat(), key), 0),
        "rewards": ", ".join(f"{ATTRIBUTE_NAMES[k]} +{v}"
                             for k, v in a.rewards().items()),
    } for key, a in activities.items()]
    history = []
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
    skills = []
    for key, item in active_root_skills(events).items():
        skill = {"key": key, "level": 1, "xp": 0, "mastered": False,
                 **state["root_skills"].get(key, {}), **item}
        skill["required"] = (skill["level"] * 1000
                             if skill["level"] < 15 else 15000)
        skill["percent"] = min(100, skill["xp"] * 100 // skill["required"])
        skill["activities"] = [
            (a, activities[a].name, xp) for a, xp in item["links"].items()
            if a in activities
        ]
        skills.append(skill)
    return {
        "state": state, "stats": stats, "perks": perks, "quests": quests,
        "modifiers": [{
            "key": key, "name": name, "rule": rule,
            "description": display_texts(events, "modifier", key, rule)
            ["condition_text"],
        } for key, (name, rule) in MODIFIERS.items()],
        "daily": [q for q in quests if q["kind"] == "daily"],
        "completed_today": sum(q["done"] for q in quests
                               if q["kind"] == "daily"),
        "activities": activity_rows, "history": history, "skills": skills,
        "active_activity_keys": list(activities),
        "achievements": achievement_rows(events, all_activities, quest_names),
        "weekly": [q for q in quests if q["kind"] == "weekly"],
        "game_date": today, "clock": now, "events": list(events),
        "total_today": sum(n for d, _, n in state["counts"]
                           if d == today.isoformat()),
        "boundary_display": (
            f"{config['boundary_minutes'] // 60:02}:"
            f"{config['boundary_minutes'] % 60:02}"
        ),
    }


def completion_order(events, today):
    current = {today.isoformat(), week_start(today).isoformat()}
    quests = quest_catalog(definitions(events, "quest"), events)
    order = {}
    for event in events:
        for effect in event["payload"].get("consequences", ()):
            if effect["type"] != "quest.period_completed":
                continue
            key = effect["quest"]
            if key in quests and effect["period"] in current:
                period = (today if quests[key][1] == "daily"
                          else week_start(today))
                if effect["period"] == period.isoformat():
                    order.setdefault(key, len(order))
    return order


def achievement_rows(events, activities, quests):
    names = achievement_names(events)
    quests = quest_catalog(definitions(events, "quest"), events,
                           include_inactive=True)
    opened = [key for key in unlocked_order(events) if key in names]
    custom = definitions(events, "achievement")
    rules = dict(BUILTIN_CONDITIONS)
    for quest, (key, count) in YEARLY.items():
        rules[key] = f'Серия «{quests[quest][0]}»: {count} периодов подряд.'
    for key, data in custom.items():
        condition = data["condition"]
        target, count = condition["target"], condition["threshold"]
        mode = condition["type"]
        if mode == "activity":
            name = activities[target].name
            rule = f'Выполнить «{name}»: {count} выполнений.'
        elif mode == "stat":
            name = ("Любая характеристика" if target == "any"
                    else ATTRIBUTE_NAMES[target])
            rule = f'{name}: достичь уровня {count}.'
        elif mode == "skill":
            name = ("Любой базовый навык" if target == "any" else
                    root_skills(events).get(target, {}).get("name", target))
            rule = f'{name}: достичь уровня {count}.'
        else:
            rule = f'Довести серию «{quests[target][0]}» до {count} периодов.'
        rules[key] = rule
    texts = {key: display_texts(events, "achievement", key, rules[key])
             for key in names}
    opened_set = set(opened)
    order = opened + [key for key in names if key not in opened_set]
    return [
        {"key": key, "name": names[key], "open": key in opened_set,
         "condition": texts[key]["condition_text"],
         "condition_text": texts[key]["condition_text"],
         "parameters": rules[key],
         "unavailable": (key in custom and not condition_available(
             events, custom[key]["condition"]
         )) or (key == "skill-five" and not active_root_skills(events))
         or any(achievement == key and quest not in quest_catalog(
             definitions(events, "quest"), events
         ) for quest, (achievement, _) in YEARLY.items()),
         "description": texts[key]["description"]}
        for key in order
    ]


def statistics_labels(statistics, events=()):
    quests = quest_catalog(definitions(events, "quest"), events)
    achievements = achievement_names(events)
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
        (root_skills(events).get(key, {}).get("name", key), amount)
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
         quests[key][0] if key in quests else key)
        for day, kind, key in statistics["quests"]
    ]
    result["achievement_labels"] = [
        {"date": item["date"],
         "name": achievements.get(item["name"], item["name"])}
        for item in statistics.get("achievements", [])
    ]
    return result
