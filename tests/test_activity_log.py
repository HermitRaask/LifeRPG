import pytest

from liferpg.domain.activities import (
    ActivityLog, ManualXPAward, apply_action, catalog, find_activities,
)
from liferpg.domain.character import Character


def test_catalog_search_filter_and_sort():
    activities = catalog()
    assert len(activities) == 19
    assert [
        activity.key for activity in find_activities(activities, "ТРЕНИР")
    ] == ["training", "weapons"]
    assert [
        activity.key for activity in find_activities(
            activities, "ТРЕНИР", "тело"
        )
    ] == ["training"]


def test_actions_use_same_interface_and_characters_are_independent():
    character = Character()
    apply_action(character, ManualXPAward(350))
    apply_action(character, catalog()["training"])
    strength = character.attribute("strength")
    assert str(strength) == "Сила: уровень 4, 25/175 XP"
    assert character.attribute("endurance").current_xp == 5
    assert Character().attribute("strength").level == 1
    with pytest.raises(AttributeError):
        strength.level = 10


def test_corrections_preserve_originals_and_update_only_target():
    activities = catalog()
    log = ActivityLog()
    log.record(activities["training"])
    log.record(activities["training"])
    assert log.character().attribute("strength").level == 2
    log.correct(1, activities["reading"])
    assert log.events[0].xp["strength"] == 50
    assert log.history()[1] is log.events[1]
    assert log.character().attribute("strength").current_xp == 50
    assert log.summary() == (2, 64)
    log.correct(2, None)
    assert log.character().attribute("strength").current_xp == 0
    assert log.summary() == (1, 4)
    with pytest.raises(ValueError):
        log.correct(99, None)
    assert len(log.events) == 4


def test_invalid_award_does_not_partially_change_character():
    character = Character()
    with pytest.raises(ValueError):
        character.apply({"strength": 50, "unknown": 5})
    assert character.attribute("strength").current_xp == 0
