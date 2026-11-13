"""Run the first LifeRPG console demo."""

from progression import award_xp, get_stat_progress


def main():
    level = 1
    current_xp = 0

    print("Начальные характеристики персонажа")
    print(get_stat_progress("Сила", level, current_xp))
    print(get_stat_progress("Ловкость", level, current_xp))
    print(get_stat_progress("Выносливость", level, current_xp))
    print(get_stat_progress("Интеллект", level, current_xp))
    print(get_stat_progress("Творчество", level, current_xp))
    print(get_stat_progress("Воля", level, current_xp))

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
        level, current_xp = award_xp(level, current_xp, amount)
    except ValueError as error:
        print(f"Ошибка: {error}")
        return 1

    print(get_stat_progress("Сила", level, current_xp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
