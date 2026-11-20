"""Root skill progression and replay of recorded snapshots."""

from liferpg.domain.content import definitions
from liferpg.domain.catalog_state import active, available_builtin

ROOT_LEVEL_LIMIT = 15
MASTERY_XP = 15_000
WRITING = "writing"
WRITING_XP = 50


def advance_root(level, xp, amount, mastered=False):
    if not 1 <= level <= ROOT_LEVEL_LIMIT or min(xp, amount) < 0:
        raise ValueError("Некорректный прогресс навыка.")
    xp += amount
    while level < ROOT_LEVEL_LIMIT and xp >= level * 1000:
        xp -= level * 1000
        level += 1
    mastered = mastered or (level == ROOT_LEVEL_LIMIT and xp >= MASTERY_XP)
    return {"level": level, "xp": xp, "mastered": mastered}


def writing_progress(events):
    for event in reversed(events):
        payload = event["payload"]
        if "writing_progress" in payload:
            return payload["writing_progress"]
    return {"level": 1, "xp": 0, "mastered": False}


def award_writing(events, payload):
    before = writing_progress(events)
    after = advance_root(
        before["level"], before["xp"], WRITING_XP, before["mastered"]
    )
    payload["writing_progress"] = after
    payload["consequences"].append(
        {"type": "skill.xp_credited", "skill": WRITING, "xp": WRITING_XP}
    )
    if after["mastered"] and not before["mastered"]:
        payload["consequences"].append(
            {"type": "skill.mastered", "skill": WRITING}
        )


def root_skills(events):
    builtin = {WRITING: {
        "name": "Писательство",
        "description": "Одна история начинается с одного слова.",
        "links": {WRITING: WRITING_XP},
    }} if available_builtin(events, "skill", WRITING) else {}
    return {**builtin, **definitions(events, "skill")}


def active_root_skills(events):
    return {key: data for key, data in root_skills(events).items()
            if not data.get("archived", False)
            and active(events, "skill", key)}


def root_progress(events):
    progress = {}
    for event in events:
        progress.update(event["payload"].get("root_skill_progress", {}))
    progress[WRITING] = writing_progress(events)
    return progress


def award_root_skills(events, payload, activity):
    if activity == WRITING and WRITING in active_root_skills(events):
        award_writing(events, payload)
    progress = root_progress(events)
    for key, skill in active_root_skills(events).items():
        if key == WRITING:
            continue
        amount = skill["links"].get(activity)
        if amount is None:
            continue
        before = progress.get(key, {"level": 1, "xp": 0, "mastered": False})
        after = advance_root(before["level"], before["xp"], amount,
                             before["mastered"])
        payload.setdefault("root_skill_progress", {})[key] = after
        payload["consequences"].append({
            "type": "skill.xp_credited", "skill": key, "xp": amount,
        })
        if after["mastered"] and not before["mastered"]:
            payload["consequences"].append({
                "type": "skill.mastered", "skill": key,
            })
