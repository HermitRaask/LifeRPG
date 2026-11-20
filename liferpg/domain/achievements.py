"""Achievement catalog and rules."""

from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.content import definitions
from liferpg.domain.catalog_state import available_builtin, active
from liferpg.domain.skills import active_root_skills, root_progress, WRITING

ACHIEVEMENTS = {
    "first-level": "Путь в тысячи ли начинается с первого шага",
    "all-five": "Дилетант широкого профиля",
    "skill-five": "Удача — это когда подготовка встречается с возможностью",
    "player-fifty": "Ты чудовище",
    "writing-year": "И миры отворятся перед тобой",
    "eagle-year": "Демонический Стрелок Роркха тобой гордится",
    "training-year": "Джотаро Московский",
    "weapons-year": "Джон Уик курильщика. Буквально",
    "meditation-year": "Встретишь Будду — убей Будду",
    "victory-over-self": "Победа над собой",
}
YEARLY = {
    "writing": ("writing-year", 365),
    "eagle": ("eagle-year", 52),
    "training": ("training-year", 365),
    "weapons": ("weapons-year", 365),
    "meditation": ("meditation-year", 365),
}


BUILTIN_CONDITIONS = {
    "first-level": "Повысить уровень любой характеристики.",
    "all-five": "Довести все шесть характеристик до 5-го уровня.",
    "skill-five": "Довести любой базовый навык до 5-го уровня.",
    "player-fifty": ("Достичь общего уровня 50. "
                     "Автоматический расчёт уточняется."),
    "victory-over-self": "Достичь 5-го уровня Воли с перком «Ленивый».",
}


def achievement_names(events):
    return {**{k: v for k, v in ACHIEVEMENTS.items()
               if available_builtin(events, "achievement", k)}, **{
        key: data["name"]
        for key, data in definitions(events, "achievement").items()
    }}


def unlocked_order(events):
    return list(dict.fromkeys(
        effect["achievement"]
        for event in events
        for effect in event["payload"].get("consequences", ())
        if effect["type"] == "achievement.unlocked"
    ))


def unlocked(events):
    return set(unlocked_order(events))


def condition_met(condition, character, streaks, totals, rows, skills=None):
    target = condition["target"]
    mode = condition["type"]
    if mode == "activity":
        value = totals.get(target, 0)
    elif mode == "stat":
        targets = ATTRIBUTE_NAMES if target == "any" else (target,)
        value = max(
            rows[key]["level_after"] if key in rows
            else character.attribute(key).level for key in targets
        )
    elif mode == "skill":
        skills = skills or {}
        targets = skills if target == "any" else (target,)
        value = max((skills[key]["level"] for key in targets if key in skills),
                    default=0)
    else:
        value = streaks.get(target, {}).get("duration", 0)
    return value >= condition["threshold"]


def achievement_skill_progress(events, payload=None):
    progress = root_progress(events)
    payload = payload or {}
    progress.update(payload.get("root_skill_progress", {}))
    if "writing_progress" in payload:
        progress[WRITING] = payload["writing_progress"]
    return {
        key: progress.get(key, {"level": 1, "xp": 0, "mastered": False})
        for key in active_root_skills(events)
    }


def evaluate_custom(events, character, payload, totals=None, activities=()):
    custom = definitions(events, "achievement")
    if not custom:
        return
    opened = unlocked(events)
    rows = payload["xp_by_stat"]
    streaks = payload.get("quest_state", {}).get("streaks", {})
    changed_streaks = {effect["quest"] for effect in payload["consequences"]
                       if effect["type"].startswith("streak.")}
    if payload.get("action") == "streak":
        changed_streaks.add(payload["streak_key"])
    skills_before = achievement_skill_progress(events)
    skills_after = achievement_skill_progress(events, payload)
    changed_skills = {
        key for key in skills_after
        if skills_after[key]["level"] > skills_before[key]["level"]
    }
    changed_stats = {key for key, row in rows.items()
                     if row["level_after"] > row["level_before"]}
    affected = {"activity": activities, "stat": changed_stats,
                "streak": changed_streaks, "skill": changed_skills}
    for key, data in custom.items():
        condition = data["condition"]
        if (condition_available(events, condition) and key not in opened
                and (condition["target"] in affected[condition["type"]]
                     or (condition["target"] == "any"
                         and affected[condition["type"]]))
                and condition_met(condition, character, streaks,
                                  totals or {}, rows, skills_after)):
            payload["consequences"].append(achievement_effect(key))


def achievement_effect(key):
    return {"type": "achievement.unlocked", "achievement": key}


def evaluate_achievements(events, character, payload):
    candidates = []
    changes = payload["xp_by_stat"]
    if any(
        row["level_after"] > row["level_before"] for row in changes.values()
    ):
        candidates.append("first-level")
    levels = (
        changes[key]["level_after"] if key in changes
        else character.attribute(key).level for key in ATTRIBUTE_NAMES
    )
    if all(level >= 5 for level in levels):
        candidates.append("all-five")
    if any(row["level"] >= 5 for row in
           achievement_skill_progress(events, payload).values()):
        candidates.append("skill-five")
    streaks = payload.get("quest_state", {}).get("streaks", {})
    for quest, (key, threshold) in YEARLY.items():
        if (available_builtin(events, "quest", quest)
                and active(events, "quest", quest)
                and streaks.get(quest, {}).get("duration", 0) >= threshold):
            candidates.append(key)
    opened = unlocked(events)
    payload["consequences"].extend(
        achievement_effect(key) for key in candidates
        if key not in opened and key in achievement_names(events)
    )


def condition_available(events, condition):
    mode, target = condition["type"], condition["target"]
    if mode == "stat":
        return True
    if mode == "skill":
        skills = active_root_skills(events)
        return bool(skills) if target == "any" else target in skills
    if mode == "activity":
        from liferpg.domain.activities import catalog
        return target in catalog(events)
    from liferpg.domain.quests import quest_catalog
    return target in quest_catalog(definitions(events, "quest"), events)
