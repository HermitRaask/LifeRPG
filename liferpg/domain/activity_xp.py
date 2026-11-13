"""Versioned XP rules for recorded activity completions."""

from datetime import timedelta
from fractions import Fraction

RULES_VERSION = 5
INTENSITY = {"1": Fraction(1, 2), "2": Fraction(1), "3": Fraction(3, 2)}
REPETITION = tuple(Fraction(n, 100) for n in (100, 100, 60, 30, 15))


def game_date(local_time):
    return (local_time.replace(tzinfo=None) - timedelta(hours=4)).date()


def repetition_factor(number):
    return REPETITION[min(number, len(REPETITION)) - 1]


def completion_xp(base, intensity, number, effects=()):
    if intensity not in INTENSITY:
        raise ValueError("Интенсивность должна быть 1, 2 или 3.")
    if not base:
        raise ValueError("TBD: базовый XP активности не согласован.")
    result = {}
    for stat, amount in base.items():
        value = amount * INTENSITY[intensity] * repetition_factor(number)
        for effect in effects:
            if effect["stat"] == stat:
                value *= Fraction(effect["factor"])
        if amount > 0:
            value = max(1, value)
        result[stat] = value.numerator // value.denominator
    return result
