"""Statistics from recorded journal results, by inclusive game date."""

from collections import Counter

DIRECTIONS = {
    "Тело": ("strength", "agility", "endurance"),
    "Разум": ("intelligence", "creativity"),
    "Социальность": (),
    "Скрытое": ("will",),
}


def period_statistics(events, start=None, end=None):
    if start and end and start > end:
        raise ValueError("Начало периода позже конца.")
    xp, productive, skills = Counter(), Counter(), Counter()
    history, levels, quest_events = [], [], []
    decay = paid = 0
    seen = set()
    for event in events:
        day = event["game_date"]
        operation = event["transaction_id"]
        if (start and day < start) or (end and day > end) or operation in seen:
            continue
        seen.add(operation)
        payload = event["payload"]
        if event["event_type"] == "activity.completed":
            history.append((day, payload["activity_name_snapshot"]))
        for key, row in payload.get("xp_by_stat", {}).items():
            amount = row["credited_xp"]
            xp[key] += amount
            if amount:
                productive[day] += amount
            decay += row.get("decayed_xp", 0)
            paid += row.get("rust_paid", 0)
            if row["level_before"] != row["level_after"]:
                levels.append(
                    (day, key, row["level_before"], row["level_after"])
                )
        for effect in payload.get("consequences", ()):
            kind = effect["type"]
            if kind == "skill.xp_credited":
                skills[effect["skill"]] += effect["xp"]
            if kind.startswith(("quest.", "streak.")):
                key = effect.get("quest", effect.get("quest_id", ""))
                quest_events.append((day, kind, key))
    return {
        "history": history, "xp": dict(xp), "decay": decay, "paid": paid,
        "top_days": productive.most_common(), "levels": levels,
        "skills": dict(skills), "quests": quest_events,
        "directions": {
            name: {key: xp[key] for key in keys}
            for name, keys in DIRECTIONS.items()
        },
    }


def manual_player_level(events):
    for event in reversed(events):
        if "player_level" in event["payload"]:
            return event["payload"]["player_level"]
    return None
