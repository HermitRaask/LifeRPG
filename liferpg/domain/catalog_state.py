"""Profile selection and catalog lifecycle replay; independent of storage."""
from copy import deepcopy

from liferpg.domain.preset_catalog import PRESET

EMPTY_ACHIEVEMENTS = {
    "first-level", "all-five", "skill-five", "player-fifty",
    "user-4479689d-51d3-4073-8d6b-2bbc89ccbd14",
}


def profile(events):
    for event in reversed(events):
        if event["event_type"] == "profile.initialized":
            return event["payload"]
    return None


def configured(events):
    return profile(events) is not None or any(
        e["event_type"] not in ("onboarding.updated", "rules.updated")
        for e in events
    )


def empty_profile(events):
    return (profile(events) or {}).get("preset") == "empty"


def seed_definitions(events, kind):
    seed = (profile(events) or {}).get("seed", PRESET)
    rows = seed.get(kind, {})
    if empty_profile(events):
        return ({k: v for k, v in rows.items() if k in EMPTY_ACHIEVEMENTS}
                if kind == "achievement" else {})
    return rows


def archive_state(events):
    result = {}
    for event in events:
        payload = event["payload"]
        if event["event_type"] == "catalog.archived":
            for row in payload["archives"]:
                result[(row["kind"], row["key"])] = {
                    **deepcopy(row), "group": event["transaction_id"],
                    "archived": True, "seq": event["seq"],
                }
        elif event["event_type"] == "catalog.restored":
            for row in payload["restored"]:
                key = (row["kind"], row["key"])
                if key in result:
                    result[key].update(
                        archived=False, restored_seq=event["seq"],
                        restored_day=event["game_date"],
                    )
    return result


def active(events, kind, key):
    row = archive_state(events).get((kind, key), {})
    return not row.get("archived", False)


def available_builtin(events, kind, key):
    if empty_profile(events):
        return kind == "achievement" and key in EMPTY_ACHIEVEMENTS
    return True


def tutorial_state(events):
    for event in reversed(events):
        if event["event_type"] == "onboarding.updated":
            return event["payload"]["tutorial"]
    return {"active": False, "step": 0, "finished": False}
