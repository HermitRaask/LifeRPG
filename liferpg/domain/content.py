"""User definitions stored in the game journal, in creation order."""
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.catalog_state import seed_definitions

CONTENT_COMMANDS = {
    "create-activity", "create-achievement", "create-quest", "create-skill",
}
DESCRIPTION_KINDS = ("activity", "quest", "achievement")


def descriptions(events, kind):
    return {
        event["entity_id"]: event["payload"]["description"]
        for event in events
        if event["event_type"] == "description.updated"
        and event["payload"]["kind"] == kind
    }


def definitions(events, kind):
    return {**seed_definitions(events, kind), **{
        event["entity_id"]: event["payload"]["definition"]
        for event in events if event["event_type"] == f"{kind}.created"
    }}


def positive(value):
    return type(value) is int and value > 0


def validate_definition(kind, data):
    if not isinstance(data["name"], str) or not data["name"].strip():
        raise ValueError("Укажите название.")
    if kind != "quest" and not isinstance(data["description"], str):
        raise ValueError("Некорректное описание.")
    if kind == "skill":
        links = data["links"]
        if (not isinstance(links, dict) or not links
                or any(not key or not positive(xp)
                       for key, xp in links.items())):
            raise ValueError("Укажите активность с положительным XP.")
        return
    if kind == "activity":
        category = data["category"]
        if not isinstance(category, str) or not category.strip():
            raise ValueError("Укажите категорию.")
        rewards = data["rewards"]
        if not rewards or any(key not in ATTRIBUTE_NAMES or not positive(xp)
                              for key, xp in rewards.items()):
            raise ValueError("Укажите положительный базовый XP.")
    if kind == "quest":
        if (data["period"] not in ("daily", "weekly")
                or not positive(data["count"])):
            raise ValueError("Некорректный период или число выполнений.")
        if not isinstance(data["activity"], str) or not data["activity"]:
            raise ValueError("Укажите активность.")
        return
    condition = data["condition"]
    modes = (("none", "stat") if kind == "activity"
             else ("activity", "stat", "skill", "streak"))
    mode = condition["type"]
    if mode not in modes:
        raise ValueError("Неизвестное условие.")
    if mode == "none":
        return
    target = condition["target"]
    if not positive(condition["threshold"]):
        raise ValueError("Порог должен быть положительным целым.")
    if (not isinstance(target, str) or not target
            or (mode == "stat" and target not in ATTRIBUTE_NAMES
                and not (kind == "achievement" and target == "any"))
            or (mode == "skill" and condition["threshold"] > 15)):
        raise ValueError("Некорректные параметры условия.")


MODIFIERS = {
    "rust": ("Заржавел", "Новый XP сначала погашает долг характеристики. "
             "Автоматическое списание выключено: правило уточняется."),
    "streak": ("В ударе", "Бонус серии +1% за период до 25%. "
               "Бонус пока не применяется к XP: область действия "
               "и сложение уточняются."),
}


def validate_texts(kind, texts):
    allowed = {"quest": {"condition_text"},
               "achievement": {"description", "condition_text"},
               "perk": {"condition_text"}, "modifier": {"condition_text"}}
    if kind not in allowed or set(texts) != allowed[kind]:
        raise ValueError("Разрешено изменять только тексты элемента.")
    if any(not isinstance(value, str) or len(value) > 2000
           for value in texts.values()):
        raise ValueError("Некорректный текст.")
    if not texts["condition_text"].strip():
        raise ValueError("Описание условия обязательно.")


def display_texts(events, kind, key, condition, description=""):
    created = definitions(events, kind).get(key, {})
    result = {
        "condition_text": created.get("condition_text") or condition,
        "description": descriptions(events, kind).get(
            key, created.get("description", description)
        ),
    }
    for event in events:
        if (event["event_type"] == "texts.updated"
                and event["entity_id"] == key
                and event["payload"]["kind"] == kind):
            result.update(event["payload"]["texts"])
    return result
