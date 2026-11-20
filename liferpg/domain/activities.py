"""Catalog, XP actions and editable completion history."""

from abc import ABC, abstractmethod

from liferpg.domain.character import ATTRIBUTE_NAMES, Character
from liferpg.domain.content import definitions, descriptions
from liferpg.domain.catalog_state import active, available_builtin


class XPAction(ABC):
    """Common interface for a source of attribute XP."""

    @abstractmethod
    def rewards(self) -> dict[str, int]:
        """Return XP by attribute ID."""


class ManualXPAward(XPAction):
    def __init__(self, amount: int) -> None:
        self.amount = amount

    def rewards(self) -> dict[str, int]:
        return {"strength": self.amount}

    def __str__(self) -> str:
        return f"Ручное начисление Силе: {self.amount} XP"


class Activity(XPAction):
    def __init__(
        self, key: str, name: str, category: str, rewards: dict[str, int]
    ) -> None:
        self.key = key
        self.name = name
        self.category = category
        self._rewards = rewards.copy()

    description = ""
    condition = None

    def requirement(self):
        if self.condition and self.condition["type"] == "stat":
            name = ATTRIBUTE_NAMES[self.condition["target"]]
            return f"{name}: требуется уровень {self.condition['threshold']}"
        return ""

    def available(self, character):
        return not self.requirement() or (
            character.attribute(self.condition["target"]).level
            >= self.condition["threshold"]
        )

    def rewards(self) -> dict[str, int]:
        return self._rewards.copy()

    def __str__(self) -> str:
        return f"{self.key} — {self.name} ({self.category})"


def apply_action(character: Character, action: XPAction) -> None:
    """Apply either catalog rewards or the PR1 manual award."""
    character.apply(action.rewards())


class ActivityCompletion:
    """A recorded award or a correction to an existing completion."""

    def __init__(
        self, number: int, activity: Activity | None, xp: dict[str, int],
        *, correction: bool = False
    ) -> None:
        self.number = number
        self.activity = activity
        self._xp = xp.copy()
        self.correction = correction

    @property
    def xp(self) -> dict[str, int]:
        return self._xp.copy()

    def __str__(self) -> str:
        name = self.activity.name if self.activity else "Отменено"
        return f"#{self.number} — {name} (+{sum(self._xp.values())} XP)"


def catalog(events=(), *, include_inactive=False) -> dict[str, Activity]:
    """Build the initial catalog from section 2.2 of the specification."""
    rows = [
        ("bedtime", "Лёг спать до 02:00", "Сон и режим", {"will": 15}),
        ("wake", "Встал вовремя", "Сон и режим", {"will": 15}),
        ("sleep", "Сон 7+ часов", "Сон и режим", {"will": 15}),
        ("warmup", "Разминка", "Тело",
         {"strength": 5, "agility": 5, "endurance": 5, "will": 5}),
        ("eagle", "Стойка орла", "Тело", {"will": 50, "endurance": 15}),
        ("training", "Тренировка", "Тело",
         {"strength": 50, "endurance": 5, "will": 5}),
        ("stretch", "Растяжка", "Тело", {"agility": 50, "will": 5}),
        ("run", "Пробежка", "Тело",
         {"endurance": 50, "agility": 15, "will": 5}),
        ("walk", "Прогулка", "Тело", {"endurance": 10}),
        ("weapons", "Тренировка с оружием", "Физические навыки",
         {"agility": 50, "will": 5}),
        ("chess", "Шахматные партии", "Ум и творчество",
         {"intelligence": 50, "will": 5}),
        ("chess-puzzles", "Шахматные задачи", "Ум и творчество",
         {"intelligence": 50, "will": 5}),
        ("writing", "Работа над текстом", "Ум и творчество",
         {"creativity": 50, "will": 5}),
        ("learning-code", "Изучение программирования", "Код",
         {"intelligence": 50, "will": 5}),
        ("project", "Работа над проектом", "Код",
         {"intelligence": 50, "creativity": 5, "will": 5}),
        ("meditation", "Медитация", "Медитация", {"will": 50}),
        ("games", "Видеоигры", "Отдых", {"intelligence": 2}),
        ("reading", "Чтение", "Отдых", {"intelligence": 2, "creativity": 2}),
        ("film", "Просмотр фильма", "Отдых",
         {"intelligence": 2, "creativity": 2}),
    ]
    result = {row[0]: Activity(*row) for row in rows
              if include_inactive
              or available_builtin(events, "activity", row[0])}
    for key, data in definitions(events, "activity").items():
        activity = Activity(
            key, data["name"], data["category"], data["rewards"]
        )
        activity.description = data["description"]
        activity.condition = data["condition"]
        result[key] = activity
    for key, description in descriptions(events, "activity").items():
        if key in result:
            result[key].description = description
    return {key: row for key, row in result.items()
            if include_inactive or active(events, "activity", key)}


def find_activities(
    activities: dict[str, Activity], query: str = "", category: str = ""
) -> list[Activity]:
    """Find matching names and optionally filter by an exact category."""
    matches = [
        activity for activity in activities.values()
        if query.casefold() in activity.name.casefold()
        and (
            not category or category.casefold() == activity.category.casefold()
        )
    ]
    return sorted(matches, key=lambda activity: activity.name.casefold())


class ActivityLog:
    """Retains original awards and applies explicit corrections."""

    def __init__(self) -> None:
        self._events: list[ActivityCompletion] = []
        self._current: dict[int, ActivityCompletion] = {}

    @property
    def events(self) -> tuple[ActivityCompletion, ...]:
        return tuple(self._events)

    def append(self, event: ActivityCompletion) -> None:
        """Record a completion or correction in chronological order."""
        if event.correction:
            if event.number not in self._current:
                raise ValueError("Запись для исправления не найдена.")
        elif event.number != len(self._current) + 1 or event.activity is None:
            raise ValueError("Неверный номер или активность выполнения.")
        self._events.append(event)
        self._current[event.number] = event

    def copy(self) -> "ActivityLog":
        result = ActivityLog()
        for event in self.events:
            result.append(event)
        return result

    def record(self, activity: Activity) -> None:
        event = ActivityCompletion(
            len(self._current) + 1, activity, activity.rewards()
        )
        self.append(event)

    def correct(self, number: int, activity: Activity | None) -> None:
        xp = activity.rewards() if activity else {}
        self.append(ActivityCompletion(number, activity, xp, correction=True))

    def history(self) -> tuple[ActivityCompletion, ...]:
        return tuple(self._current.values())

    def character(self) -> Character:
        """Rebuild progress from the effective recorded XP snapshots."""
        character = Character()
        for entry in self.history():
            character.apply(entry.xp)
        return character

    def summary(self) -> tuple[int, int]:
        active = [entry for entry in self.history() if entry.activity]
        return len(active), sum(sum(entry.xp.values()) for entry in active)
