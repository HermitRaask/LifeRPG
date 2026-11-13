"""XP calculations for a single LifeRPG stat."""


def xp_required(level: int) -> int:
    """Return the XP needed to advance from the given level."""
    if level < 1:
        raise ValueError("Уровень должен быть не ниже 1.")
    return 100 + 25 * (level - 1)


def award_xp(level: int, current_xp: int, amount: int) -> tuple[int, int]:
    """Apply XP and return the new level and remaining XP."""
    required = xp_required(level)
    if not 0 <= current_xp < required:
        raise ValueError("Текущий XP должен быть в пределах уровня.")
    if amount < 0:
        raise ValueError("Начисление XP должно быть неотрицательным.")

    current_xp += amount
    while current_xp >= required:
        current_xp -= required
        level += 1
        required = xp_required(level)
    return level, current_xp


def get_stat_progress(name: str, level: int, current_xp: int) -> str:
    """Format a stat's current level and progress toward the next."""
    return f"{name}: уровень {level}, {current_xp}/{xp_required(level)} XP"
