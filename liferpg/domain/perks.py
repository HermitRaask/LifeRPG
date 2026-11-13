"""Configured XP effects for active perks."""

from fractions import Fraction

PERKS = {
    "lazy": "Ленивый",
    "creative": "Творческий",
    "books": "Книги — мой мир",
    "sleep": "Свергнутый режим",
    "chronic": "Хронически болен",
    "sport": "Любовь к спорту (TBD)",
    "victory": "Победа над собой",
}
EFFECTS = {
    "lazy": (({"will"}, None, "1.5"),
             ({"strength", "agility", "endurance", "intelligence",
               "creativity"}, None, "0.75")),
    "creative": (({"creativity"}, None, "1.15"),
                 ({"will"}, {"writing"}, "0.5")),
    "books": (({"intelligence"}, {"reading"}, "2"),),
    "sleep": (({"will"}, {"bedtime", "wake", "sleep"}, "1.5"),),
    "chronic": (({"strength", "agility", "endurance"}, None, "0.7"),),
}


def active_perks(events):
    active = set(PERKS) - {"victory"}
    victory = False
    for event in events:
        for effect in event["payload"].get("consequences", ()):
            kind = effect["type"]
            if kind == "perk.assigned":
                active.add(effect["perk"])
            elif kind == "perk.removed":
                active.discard(effect["perk"])
            elif kind == "achievement.unlocked":
                victory |= effect["achievement"] == "victory-over-self"
    return active, victory


def effects_for(activity, stats, perks):
    return [
        {"perk": perk, "stat": stat, "factor": str(Fraction(factor))}
        for perk in sorted(perks)
        for targets, activities, factor in EFFECTS.get(perk, ())
        if activities is None or activity in activities
        for stat in stats if stat in targets
    ]


def lazy_replacement(active, victory, will_level):
    if "lazy" not in active or will_level < 5:
        return []
    changes = [
        {"type": "perk.removed", "perk": "lazy"},
        {"type": "perk.assigned", "perk": "victory"},
    ]
    if not victory:
        changes.append({
            "type": "achievement.unlocked", "achievement": "victory-over-self",
        })
    return changes


def describe_perk(key, activities):
    if key == "victory":
        return "Чувство глубокого удовлетворения. XP без изменений."
    if key not in EFFECTS:
        return "Правило уточняется"
    from liferpg.domain.character import ATTRIBUTE_NAMES

    parts = []
    for stats, applies_to, factor in EFFECTS[key]:
        names = ", ".join(
            name for stat, name in ATTRIBUTE_NAMES.items() if stat in stats
        )
        scope = "любая активность"
        if applies_to is not None:
            scope = ", ".join(
                activities[key].name for key in sorted(applies_to)
            )
        parts.append(f"{scope}: {names} ×{factor}")
    if key == "lazy":
        parts.append("При Воле 5 заменяется на «Победа над собой».")
    return ". ".join(parts)
