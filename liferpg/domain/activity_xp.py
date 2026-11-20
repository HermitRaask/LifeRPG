"""Versioned XP rules for recorded activity completions."""

from datetime import timedelta
from fractions import Fraction

RULES_VERSION = 7
INTENSITY = {"1": Fraction(1, 2), "2": Fraction(1), "3": Fraction(3, 2)}
REPETITION = tuple(Fraction(n, 100) for n in (100, 100, 60, 30, 15))


def game_date(local_time, boundary_minutes=240):
    local = local_time.replace(tzinfo=None)
    return (local - timedelta(minutes=boundary_minutes)).date()


def repetition_factor(number, factors=REPETITION):
    return Fraction(factors[min(number, len(factors)) - 1])


def completion_xp(base, intensity, number, effects=(), config=None):
    intensities = config["intensity"] if config else INTENSITY
    repetitions = config["repetition"] if config else REPETITION
    if intensity not in intensities:
        raise ValueError("Интенсивность должна быть 1, 2 или 3.")
    if not base:
        raise ValueError("TBD: базовый XP активности не согласован.")
    result = {}
    for stat, amount in base.items():
        value = (amount * Fraction(intensities[intensity])
                 * repetition_factor(number, repetitions))
        for effect in effects:
            if effect["stat"] == stat:
                value *= Fraction(effect["factor"])
        if amount > 0:
            value = max(1, value)
        result[stat] = value.numerator // value.denominator
    return result
