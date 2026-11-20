"""Replay local preferences from the game journal."""

DISPLAY_DEFAULTS = {
    "daily_mode": "all", "weekly_mode": "all",
    "daily_pins": [], "weekly_pins": [],
    "hide_done": True, "show_next": False, "show_perks": False,
    "show_modifiers": False, "show_developer": False,
    "developer_revision": "",
}
SYSTEM_DEFAULTS = {"tray": False, "autostart": False, "quiet": False}


def preferences(events, kind):
    defaults = (DISPLAY_DEFAULTS if kind == "display.updated"
                else SYSTEM_DEFAULTS)
    result = dict(defaults)
    for event in events:
        if event["event_type"] == kind:
            result.update(event["payload"]["preferences"])
        elif (kind == "display.updated"
              and event["event_type"] == "catalog.archived"):
            hidden = {r["key"] for r in event["payload"]["archives"]
                      if r["kind"] == "quest"}
            for field in ("daily_pins", "weekly_pins"):
                result[field] = [k for k in result[field] if k not in hidden]
        elif (kind == "display.updated"
              and event["payload"].get("hide_developer")):
            result.update(show_developer=False,
                          developer_revision=event["transaction_id"])
    return result


def quick_activity(events):
    for event in reversed(events):
        if event["event_type"] == "display.activity":
            return event["entity_id"]
    return "meditation"
