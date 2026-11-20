"""Persistent notification identities derived from recorded consequences."""
from datetime import datetime

from liferpg.domain.achievements import achievement_names
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.content import definitions
from liferpg.domain.quests import quest_catalog


def history(events):
    achievements = achievement_names(events)
    quests = quest_catalog(definitions(events, "quest"))
    rows, seen = [], set()
    for event in events:
        if event["event_id"] in seen:
            continue
        seen.add(event["event_id"])
        payload = event["payload"]
        for index, effect in enumerate(payload.get("consequences", ())):
            kind = effect["type"]
            if kind == "achievement.unlocked":
                name = achievements[effect["achievement"]]
                text = f"Достижение «{name}»"
            elif kind == "quest.period_completed":
                text = "Задание «" + quests[effect["quest"]][0] + "» выполнено"
            elif kind == "stat.leveled_up":
                text = (ATTRIBUTE_NAMES[effect["stat"]]
                        + f': уровень {effect["level"]}')
            else:
                continue
            rows.append({"id": f'{event["event_id"]}:{index}',
                         "event": event["event_id"],
                         "time": event["local_datetime"], "text": text})
        if payload.get("action") == "level":
            if "player_level" in payload:
                rows.append({"id": event["event_id"] + ":player",
                             "event": event["event_id"],
                             "time": event["local_datetime"],
                             "text": "Общий уровень: "
                             + str(payload["player_level"])})
            else:
                # Developer level changes store snapshots instead of effects.
                for key, row in payload.get("xp_by_stat", {}).items():
                    if (row["level_after"] > row["level_before"]
                            and not any(e.get("stat") == key and
                                        e["type"] == "stat.leveled_up"
                                        for e in payload["consequences"])):
                        rows.append({
                            "id": event["event_id"] + ":" + key,
                            "event": event["event_id"],
                            "time": event["local_datetime"],
                            "text": ATTRIBUTE_NAMES[key]
                            + f': уровень {row["level_after"]}',
                        })
    for row in rows:
        row["text"] = row["text"].rstrip("!") + "!"
        moment = datetime.fromisoformat(row["time"])
        row["time"] = f"{moment.hour}:{moment:%M}, {moment:%d.%m.%y}"
    return list(reversed(rows))
