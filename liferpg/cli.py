"""Console entry point for LifeRPG."""

import argparse
from pathlib import Path

from liferpg.application import correct_activity, record_activity
from liferpg.domain.activities import (
    Activity, ActivityLog, ManualXPAward, apply_action, catalog,
    find_activities,
)
from liferpg.domain.character import ATTRIBUTE_NAMES, Character
from liferpg.storage import load_log, save_log

DEFAULT_STATE = Path(__file__).resolve().parents[1] / "data" / "state.json"

MENU = """
1. Каталог, поиск и фильтр
2. Выполнить активность
3. Показать характеристики
4. Показать историю
5. Исправить выполнение
6. Отменить выполнение
7. Сводка
8. Показать исходные записи и исправления
0. Выход"""


def demo_pr01() -> int:
    """Run the original manual Strength award on a fresh character."""
    character = Character()
    print("Начальные характеристики персонажа")
    print(character)
    try:
        raw_amount = input("Сколько XP начислить Силе? ")
    except EOFError:
        print("Ошибка: ввод завершён. Укажи целое количество XP.")
        return 1
    try:
        amount = int(raw_amount)
    except ValueError:
        print("Ошибка: введи целое количество XP.")
        return 1
    try:
        apply_action(character, ManualXPAward(amount))
    except ValueError as error:
        print(f"Ошибка: {error}")
        return 1
    print(character.attribute("strength"))
    return 0


def choose_activity(activities: dict[str, Activity]) -> Activity:
    key = input("Код активности (см. каталог): ").strip().casefold()
    if key not in activities:
        raise ValueError("Активность с таким кодом не найдена.")
    return activities[key]


def show_history(log: ActivityLog, audit: bool = False) -> None:
    entries = log.events if audit else log.history()
    if not entries:
        print("История пока пуста.")
    for entry in entries:
        label = "Исправление" if entry.correction else "Выполнение"
        print(f"{label}: {entry}" if audit else str(entry))
        breakdown = ", ".join(
            f"{ATTRIBUTE_NAMES[stat]} +{amount} XP"
            for stat, amount in entry.xp.items()
        )
        print(f"  {breakdown or 'Без начисления XP'}")


def change_history(
    command: str, log: ActivityLog, activities: dict[str, Activity],
    path: Path
) -> ActivityLog:
    def save(updated: ActivityLog) -> None:
        save_log(path, updated)

    if command == "2":
        updated = record_activity(log, choose_activity(activities), save)
    else:
        raw_number = input("Номер записи из истории: ").strip()
        try:
            number = int(raw_number)
        except ValueError:
            raise ValueError("Введи целый номер записи.") from None
        activity = choose_activity(activities) if command == "5" else None
        updated = correct_activity(log, number, activity, save)
    print("История сохранена.")
    return updated


def run_menu(path: Path) -> int:
    activities = catalog()
    try:
        log = load_log(path, activities)
    except (ValueError, OSError) as error:
        print(f"Ошибка чтения истории: {error}")
        return 1

    while True:
        print(MENU)
        try:
            command = input("Выбор: ").strip()
            if command == "0":
                return 0
            if command == "1":
                query = input("Название (Enter — все): ").strip()
                category = input("Категория (Enter — все): ").strip()
                matches = find_activities(activities, query, category)
                if not matches:
                    print("Подходящих активностей нет.")
                for activity in matches:
                    print(activity)
            elif command in {"2", "5", "6"}:
                log = change_history(command, log, activities, path)
            elif command == "3":
                print(log.character())
            elif command in {"4", "8"}:
                show_history(log, audit=command == "8")
            elif command == "7":
                count, xp = log.summary()
                print(f"Выполнений: {count}, всего XP: {xp}")
            else:
                print("Неизвестная команда.")
        except EOFError:
            return 0
        except (ValueError, OSError) as error:
            print(f"Ошибка: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="LifeRPG")
    parser.add_argument("--demo-pr01", action="store_true")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    return demo_pr01() if args.demo_pr01 else run_menu(args.state)
