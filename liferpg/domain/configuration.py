"""Versioned future-only settings, independent of Django."""
from copy import deepcopy
from fractions import Fraction
from zoneinfo import ZoneInfo

from liferpg.domain.activity_xp import INTENSITY, REPETITION, RULES_VERSION

DEFAULTS = {
    "timezone": "Europe/Moscow", "boundary_minutes": 240,
    "intensity": {key: str(value) for key, value in INTENSITY.items()},
    "repetition": [str(value) for value in REPETITION],
    "version": RULES_VERSION, "backup_on_change": False,
    "perk_rules_version": 3,
}


def configuration(events):
    result = deepcopy(DEFAULTS)
    for event in reversed(events):
        if event["event_type"] == "settings.updated":
            result = deepcopy(event["payload"]["after"])
            break
        if "configuration_snapshot" in event["payload"]:
            result = deepcopy(event["payload"]["configuration_snapshot"])
            break
    if result.get("perk_rules_version", 1) < 3:
        result["version"] = max(RULES_VERSION, result["version"] + 1)
        result["perk_rules_version"] = 3
    return result


def validate_configuration(config):
    if (type(config["version"]) is not int or config["version"] < 1
            or type(config["boundary_minutes"]) is not int
            or not 0 <= config["boundary_minutes"] < 1440
            or type(config["backup_on_change"]) is not bool):
        raise ValueError("некорректные настройки игрового времени")
    ZoneInfo(config["timezone"])
    if (set(config["intensity"]) != {"1", "2", "3"}
            or len(config["repetition"]) != 5):
        raise ValueError("неполный набор коэффициентов")
    factors = [*config["intensity"].values(), *config["repetition"]]
    try:
        if any(not 0 < Fraction(value) <= 100 for value in factors):
            raise ValueError("коэффициент вне допустимого диапазона")
    except ZeroDivisionError as exc:
        raise ValueError("некорректный коэффициент") from exc
