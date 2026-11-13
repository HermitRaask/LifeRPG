"""Achievement catalog and rules."""

from liferpg.domain.character import ATTRIBUTE_NAMES

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


def unlocked(events):
    return {
        effect["achievement"]
        for event in events
        for effect in event["payload"].get("consequences", ())
        if effect["type"] == "achievement.unlocked"
    }


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
    if payload.get("writing_progress", {}).get("level", 0) >= 5:
        candidates.append("skill-five")
    streaks = payload.get("quest_state", {}).get("streaks", {})
    for quest, (key, threshold) in YEARLY.items():
        if streaks.get(quest, {}).get("duration", 0) >= threshold:
            candidates.append(key)
    opened = unlocked(events)
    payload["consequences"].extend(
        achievement_effect(key) for key in candidates if key not in opened
    )
