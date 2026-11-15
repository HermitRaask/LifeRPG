import pytest

from liferpg.domain.progression import award_xp, get_stat_progress, xp_required


@pytest.mark.parametrize("level, required", [(1, 100), (2, 125), (3, 150)])
def test_xp_required(level, required):
    assert xp_required(level) == required


def test_xp_required_rejects_invalid_level():
    with pytest.raises(ValueError):
        xp_required(0)


@pytest.mark.parametrize(
    "level, current_xp, amount, expected",
    [
        (1, 0, 0, (1, 0)),
        (1, 0, 99, (1, 99)),
        (1, 0, 100, (2, 0)),
        (1, 0, 125, (2, 25)),
        (1, 0, 350, (3, 125)),
        (2, 110, 40, (3, 25)),
    ],
)
def test_award_xp(level, current_xp, amount, expected):
    assert award_xp(level, current_xp, amount) == expected


@pytest.mark.parametrize("current_xp, amount", [(0, -1), (-1, 10), (100, 10)])
def test_award_xp_rejects_invalid_input(current_xp, amount):
    with pytest.raises(ValueError):
        award_xp(1, current_xp, amount)


def test_get_stat_progress():
    assert get_stat_progress("Сила", 3, 50) == "Сила: уровень 3, 50/150 XP"
