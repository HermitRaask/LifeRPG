"""Root skill progression and replay of recorded snapshots."""

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
