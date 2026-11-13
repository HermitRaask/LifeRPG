"""Console commands backed by the event journal."""

from datetime import datetime
from uuid import uuid4

from liferpg.domain.activity_xp import game_date, repetition_factor
from liferpg.domain.activities import find_activities
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.perks import PERKS, active_perks, describe_perk
from liferpg.domain.quests import QUESTS, quest_state
from liferpg.domain.skills import (
    MASTERY_XP, ROOT_LEVEL_LIMIT, writing_progress,
)
from liferpg.game import (
    complete, credits, effective_completions, ensure_progression_rules,
    project, recorded_awards, revise_completion, set_perk, settle_periods,
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
        elif audit and not event["event_type"].startswith(
            ("skill.", "subskill.")
        ):
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
        if effect["type"] == "skill.xp_credited" and (
            effect.get("skill") == "writing"
        ):
            print(f"Писательство +{effect['xp']} XP навыка")
        if effect["type"] == "skill.mastered" and (
            effect.get("skill") == "writing"
        ):
            print("Писательство: мастерский навык!")
        if effect["type"] == "quest.period_completed":
            print(f"Задание выполнено: {QUESTS[effect['quest']][0]}")
        if effect["type"] == "perk.assigned" and effect["perk"] == "victory":
            print("Получен перк «Победа над собой».")
        if effect["type"] not in {"stat.leveled_up", "stat.level_revoked"}:
            continue
        name = ATTRIBUTE_NAMES[effect["stat"]]
        if effect["type"] == "stat.level_revoked":
            print(f"{name}: уровень {effect['level']} снят")
        else:
            print(f"{name}: уровень {effect['level']}")


def journal_command(command, path, activities, choose_activity):
    if command == "9":
        show_perks(path, activities)
        return
    if command == "12":
        developer_menu(path, activities)
        return
    if command == "11":
        with journal_lock(path):
            skill = writing_progress(read_events(path))
        level, xp = skill["level"], skill["xp"]
        limit = MASTERY_XP if level == ROOT_LEVEL_LIMIT else level * 1000
        print(f"Писательство: уровень {level}/{ROOT_LEVEL_LIMIT}")
        print(f"Опыт: {xp}/{limit} XP")
        status = "мастерский" if skill["mastered"] else "в развитии"
        print(f"Статус: {status}")
        if level == ROOT_LEVEL_LIMIT:
            print(f"К мастерству: {min(xp, MASTERY_XP)}/{MASTERY_XP} XP")
        print("Работа над текстом: +50 XP навыка")
        return
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

    settle_periods(path)
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
    elif command == "10":
        state = quest_state(events)
        for key, (name, _, _, _) in QUESTS.items():
            period = state["completed"].get(key, "—")
            streak = state["streaks"].get(key, {})
            duration = streak.get("duration", 0)
            bonus = min(25, streak.get("successful", 0))
            status = " (под угрозой)" if streak.get("at_risk") else ""
            series = f"серия {duration}{status}, +{bonus}% TBD"
            print(f"{name}: {period}, {series}")
        banks = state["banks"]
        daily, weekly = banks["daily"], banks["weekly"]
        print(f"Заморозки: день {daily}/5, неделя {weekly}/3")
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
    settle_periods(path)
    menu = menu.replace(
        "0. Выход",
        "9. Перки\n10. Задания и серии\n11. Навыки\n"
        "12. Режим разработчика\n0. Выход",
    )
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


def show_perks(path, activities):
    with journal_lock(path):
        perks, _ = active_perks(read_events(path))
    for key, name in PERKS.items():
        status = "включён" if key in perks else "выключен"
        print(f"{key}: {name} ({status}). {describe_perk(key, activities)}")


def developer_menu(path, activities):
    print("Режим разработчика включён.")
    try:
        while True:
            show_perks(path, activities)
            perk = input("Код перка (0 — выйти из режима): ").strip()
            if perk == "0":
                return
            choice = input("1 — включить, 0 — выключить: ").strip()
            if choice not in {"0", "1"}:
                print("Выбери 1 или 0.")
                continue
            try:
                set_perk(path, perk, choice == "1", developer_mode=True)
            except ValueError as error:
                print(f"Ошибка: {error}")
    finally:
        print("Режим разработчика выключен.")
