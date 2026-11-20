"""Journal operations for initial setup and catalog lifecycle."""
from collections import Counter
from copy import deepcopy

from liferpg.domain.activities import catalog
from liferpg.domain.achievements import (
    ACHIEVEMENTS, YEARLY,
    achievement_effect, achievement_skill_progress, condition_available,
    condition_met, evaluate_achievements, unlocked,
)
from liferpg.domain.catalog_state import archive_state, configured
from liferpg.domain.content import definitions
from liferpg.domain.preset_catalog import PRESET
from liferpg.domain.quests import quest_catalog, quest_state
from liferpg.domain.skills import active_root_skills, root_progress
from liferpg.game import effective_completions, make_event, project
from liferpg.journal import append_event, journal_lock


def records(events):
    result = {}
    for key, item in catalog(events).items():
        result[("activity", key)] = {
            "name": item.name, "category": item.category,
            "rewards": item.rewards(), "condition": item.condition,
        }
    quests = quest_catalog(definitions(events, "quest"), events)
    for key, item in quests.items():
        result[("quest", key)] = {
            "name": item[0], "period": item[1],
            "required": sorted(item[2]), "alternatives": sorted(item[3]),
            "definition": definitions(events, "quest").get(key),
        }
    for key, item in active_root_skills(events).items():
        result[("skill", key)] = deepcopy(item)
    return result


def related(events, key):
    rows = records(events)
    tasks = [(kind, target) for (kind, target), item in rows.items()
             if kind == "quest" and key in
             item["required"] + item["alternatives"]]
    skills = [item["name"] for (kind, _), item in rows.items()
              if kind == "skill" and key in item["links"]]
    achievements = [
        data["name"] for data in definitions(events, "achievement").values()
        if data["condition"]["target"] == key
        or (data["condition"]["type"] == "streak"
            and ("quest", data["condition"]["target"]) in tasks)
    ]
    achievements.extend(
        ACHIEVEMENTS[achievement] for quest, (achievement, _) in YEARLY.items()
        if ("quest", quest) in tasks
    )
    return tasks, skills, achievements


def change(events, command, values, operation, moment):
    from webgame.services import journal_path
    payload = {"xp_by_stat": {}, "consequences": []}
    entity = "local-player"
    if command == "profile":
        if configured(events):
            raise ValueError("Профиль уже настроен; его данные сохранены.")
        if values.get("preset") not in ("preset", "empty"):
            raise ValueError("Выберите начальный профиль.")
        payload.update(preset=values["preset"], seed=deepcopy(PRESET))
        kind, message = "profile.initialized", "Начальный профиль выбран."
    elif command == "tutorial":
        step = values.get("step")
        if (type(step) is not int or not 0 <= step < 25
                or type(values.get("active")) is not bool
                or type(values.get("finished")) is not bool):
            raise ValueError("Некорректный шаг обучения.")
        payload["tutorial"] = dict(values)
        kind, message = "onboarding.updated", "Обучение обновлено."
        if not values["active"]:
            payload["hide_developer"] = True
    elif command == "archive":
        target = (values.get("kind"), values.get("key"))
        rows = records(events)
        if target not in rows:
            raise ValueError("Элемент недоступен или уже архивирован.")
        targets = [target]
        if target[0] == "activity":
            targets.extend(related(events, target[1])[0])
        state, skills = quest_state(events), root_progress(events)
        from webgame.services import pinned_activities
        payload["pinned_activities"] = [
            key for key in pinned_activities(events)
            if ("activity", key) not in targets
        ]
        payload["archives"] = []
        for content_kind, key in targets:
            progress = {}
            if content_kind == "skill":
                progress = skills.get(
                    key, {"level": 1, "xp": 0, "mastered": False}
                )
            elif content_kind == "quest":
                progress = {
                    "streak": state["streaks"].get(key, {}),
                    "completed_periods": state.get(
                        "completed_periods", {}
                    ).get(key, []),
                }
            payload["archives"].append({
                "kind": content_kind, "key": key,
                "snapshot": deepcopy(rows[(content_kind, key)]),
                "progress": deepcopy(progress),
            })
        kind, message = "catalog.archived", "Элементы архивированы."
        entity = target[1]
    elif command == "restore":
        archived = archive_state(events)
        chosen = values.get("targets")
        if (not isinstance(chosen, list) or not chosen
                or any(not isinstance(t, str) or ":" not in t
                       for t in chosen)):
            raise ValueError("Выберите элементы для восстановления.")
        targets = list(dict.fromkeys(tuple(t.split(":", 1)) for t in chosen))
        if any(not archived.get(t, {}).get("archived") for t in targets):
            raise ValueError("Выбранный элемент отсутствует в архиве.")
        for content_kind, key in list(targets):
            if content_kind != "quest":
                continue
            snapshot = archived[(content_kind, key)]["snapshot"]
            for activity in snapshot["required"] + snapshot["alternatives"]:
                if archived.get(("activity", activity), {}).get("archived"):
                    if ("activity", activity) not in targets:
                        raise ValueError(
                            ('Восстановите связанную активность вместе с '
                                'заданием.')
                        )
        payload["restored"] = [
            {"kind": k, "key": key} for k, key in targets
        ]
        state = quest_state(events)
        for content_kind, key in targets:
            if content_kind != "quest":
                continue
            old = state["streaks"].get(key, {})
            state["streaks"][key] = dict(
                duration=0, successful=0, protected=0, bonus_periods=0,
                best=old.get("best", 0), last_chance_used=False, at_risk=False,
            )
            state["completed"].pop(key, None)
            state.setdefault("completed_periods", {})[key] = []
        payload["quest_state"] = state
        kind, message = "catalog.restored", "Элементы восстановлены."
    else:
        raise ValueError("Неизвестная операция каталога.")
    event = make_event(
        events, operation, kind, entity, moment, payload, message
    )
    if command == "restore":
        event["actor"] = "developer"
        future = [*events, event]
        character = project(events)[0]
        evaluate_achievements(future, character, payload)
        totals = Counter(
            item["activity_id"] for _, item in effective_completions(events)
            .values() if item is not None
        )
        opened = unlocked(events)
        for key, data in definitions(future, "achievement").items():
            condition = data["condition"]
            if (key not in opened and condition_available(future, condition)
                    and condition_met(
                        condition, character, state["streaks"], totals, {},
                        achievement_skill_progress(future),
                    )):
                payload["consequences"].append(achievement_effect(key))
    with journal_lock(journal_path()):
        append_event(journal_path(), event)
    return event
