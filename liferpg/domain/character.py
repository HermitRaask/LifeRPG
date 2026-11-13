"""Character and attribute state."""

from liferpg.domain.progression import award_xp, get_stat_progress, xp_required

ATTRIBUTE_NAMES = {
    "strength": "Сила",
    "agility": "Ловкость",
    "endurance": "Выносливость",
    "intelligence": "Интеллект",
    "creativity": "Творчество",
    "will": "Воля",
}


class Attribute:
    """A character attribute whose XP changes through awards."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._level = 1
        self._current_xp = 0

    @property
    def level(self) -> int:
        return self._level

    @property
    def current_xp(self) -> int:
        return self._current_xp

    @property
    def required_xp(self) -> int:
        return xp_required(self._level)

    def award_xp(self, amount: int) -> None:
        self._level, self._current_xp = award_xp(
            self._level, self._current_xp, amount
        )

    def __str__(self) -> str:
        return get_stat_progress(self.name, self.level, self.current_xp)


class Character:
    """Owns an independent set of six attributes."""

    def __init__(self) -> None:
        self._attributes = {
            key: Attribute(name) for key, name in ATTRIBUTE_NAMES.items()
        }

    def attribute(self, key: str) -> Attribute:
        return self._attributes[key]

    def apply(self, rewards: dict[str, int]) -> None:
        """Validate the entire award before changing attributes."""
        for key, amount in rewards.items():
            if key not in self._attributes:
                raise ValueError(f"Неизвестная характеристика: {key}")
            if type(amount) is not int or amount < 0:
                raise ValueError("Начисление XP должно быть неотрицательным.")
        for key, amount in rewards.items():
            self.attribute(key).award_xp(amount)

    def __str__(self) -> str:
        return "\n".join(map(str, self._attributes.values()))
