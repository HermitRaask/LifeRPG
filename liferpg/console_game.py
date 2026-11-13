"""Console commands backed by the event journal."""

from datetime import datetime
from uuid import uuid4

from liferpg.domain.activity_xp import game_date, repetition_factor
from liferpg.domain.activities import find_activities
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.game import (
    complete, credits, effective_completions, ensure_progression_rules,
    project, recorded_awards, revise_completion,
)
from liferpg.journal import journal_lock, read_events


def show_entries(events, audit):
    if not events:
        print("История пока пуста.")
    entries = effective_completions(events)
    for event in events:
        payload = event["payload"]
        if event["event_type"] == "system.legacy_imported":
            for entry in payload["history"]:
                if audit or entry["active"]:
                    label = (
                        "Исправление" if entry["correction"] else "Выполнение"
                    )
                    text = entry["text"]
                    print(f"{label}: {text}" if audit else text)
                    show_xp(entry["xp"])
        elif event["event_type"] == "activity.completed":
            number = event["seq"]
            if not audit:
                _, payload = entries[number]
            if payload is None:
                print(f"#{number} — Отменено")
            else:
                print(f"#{number} — {payload['activity_name_snapshot']}")
                show_xp(credits(payload))
        elif audit:
            print(event["message_ru"])
            replacement = payload.get("replacement")
            if replacement is not None:
                print(replacement["activity_name_snapshot"])
                show_xp(credits(replacement))
            show_levels(payload["consequences"])


def show_xp(amounts):
    breakdown = ", ".join(
        f"{ATTRIBUTE_NAMES[key]} +{amount} XP"
        for key, amount in amounts.items()
    )
    print(breakdown or "Без начисления XP")


def read_intensity():
    return input(
        "Интенсивность (1 — лёгкая, 2 — обычная, "
        "3 — тяжёлая; Enter — обычная): "
    ).strip() or "2"


def show_levels(consequences):
    for effect in consequences:
        name = ATTRIBUTE_NAMES[effect["stat"]]
        if effect["type"] == "stat.level_revoked":
            print(f"{name}: уровень {effect['level']} снят")
        else:
            print(f"{name}: уровень {effect['level']}")


def journal_command(command, path, activities, choose_activity):
    if command in {"5", "6"}:
        try:
            target = int(input("Номер выполнения из истории: "))
        except ValueError:
            raise ValueError("Введи целый номер выполнения.") from None
        activity = choose_activity(activities) if command == "5" else None
        intensity = read_intensity() if activity else None
        event = revise_completion(path, target, activity, intensity)
        print(event["message_ru"])
        show_levels(event["payload"]["consequences"])
        return
    if command == "2":
        activity = choose_activity(activities)
        intensity = read_intensity()
        event = complete(path, activity, intensity, str(uuid4()))
        payload = event["payload"]
        print(event["message_ru"])
        number = payload["occurrence_index_in_game_day"]
        print(f"Повтор за игровой день: {number}")
        show_levels(payload["consequences"])
        return

    with journal_lock(path):
        events = read_events(path)
    character, counts = project(events)
    if command == "1":
        query = input("Название (Enter — все): ").strip()
        category = input("Категория (Enter — все): ").strip()
        matches = find_activities(activities, query, category)
        if not matches:
            print("Подходящих активностей нет.")
        today = game_date(datetime.now().astimezone()).isoformat()
        for activity in matches:
            number = counts.get((today, activity.key), 0)
            factor = float(repetition_factor(number + 1))
            print(f"{activity} — выполнено {number}; следующее ×{factor:g}")
    elif command == "3":
        print(character)
    elif command in {"4", "8"}:
        show_entries(events, command == "8")
    elif command == "7":
        entries = effective_completions(events)
        count = sum(
            event["payload"].get("completion_count", 0) for event in events
        ) + sum(payload is not None for _, payload in entries.values())
        xp = sum(
            sum(award.values()) for award in recorded_awards(events, entries)
        )
        print(f"Выполнений: {count}, всего XP: {xp}")
    else:
        print("Неизвестная команда.")


def run_journal_menu(path, activities, menu, choose_activity):
    ensure_progression_rules(path)
    while True:
        print(menu)
        try:
            command = input("Выбор: ").strip()
            if command == "0":
                return 0
            journal_command(command, path, activities, choose_activity)
        except EOFError:
            return 0
        except (ValueError, OSError) as error:
            print(f"Ошибка: {error}")
