"""Run journal-backed game operations and project recorded results."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from liferpg.domain.activity_xp import (
    INTENSITY, RULES_VERSION, completion_xp, game_date, repetition_factor,
)
from liferpg.domain.character import ATTRIBUTE_NAMES, Character
from liferpg.journal import append_event, journal_lock, read_events


def utc_string(moment):
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_event(events, operation, kind, entity_id, local, payload, message):
    day = game_date(local)
    return {
        "schema_version": 1,
        "rules_version": 2 if kind == "rules.updated" else RULES_VERSION,
        "time_rules_version": 1, "boundary_minutes": 240,
        "event_id": operation, "transaction_id": operation,
        "seq": len(events) + 1, "event_type": kind,
        "actor": {
            "rules.updated": "system", "system.legacy_imported": "migration",
        }.get(kind, "user"),
        "entity_type": "activity_completion" if kind == "activity.completed"
        else "profile",
        "entity_id": entity_id,
        "occurred_at_utc": utc_string(local),
        "recorded_at_utc": utc_string(datetime.now(timezone.utc)),
        "local_datetime": local.isoformat(),
        "real_local_date": local.date().isoformat(),
        "game_date": day.isoformat(),
        "game_week_start": (day - timedelta(days=day.weekday())).isoformat(),
        "game_month": day.strftime("%Y-%m"),
        "timezone": getattr(local.tzinfo, "key", local.tzname()),
        "payload": payload, "message_ru": message,
    }


def project(events):
    character = Character()
    seen = set()
    for event in events:
        operation = event["transaction_id"]
        if operation in seen:
            continue
        seen.add(operation)
        payload = event["payload"]
        for key, row in payload["xp_by_stat"].items():
            attribute = character.attribute(key)
            if (attribute.level, attribute.current_xp) != (
                row["level_before"], row["xp_before"]
            ):
                raise ValueError(
                    f"Строка события №{event['seq']}: "
                    "снимок XP не соответствует предыдущему состоянию."
                )
            attribute.restore_progress(row["level_after"], row["xp_after"])
    counts = {}
    for day, payload in effective_completions(events).values():
        if payload is not None:
            key = (day, payload["activity_id"])
            counts[key] = counts.get(key, 0) + 1
    return character, counts


def xp_rows(before, after, base, credited):
    rows = {}
    for key, amount in credited.items():
        old, new = before.attribute(key), after.attribute(key)
        rows[key] = {
            "base_xp": base[key], "calculated_xp": amount,
            "credited_xp": amount, "rust_paid": 0,
            "level_before": old.level, "level_after": new.level,
            "xp_before": old.current_xp, "xp_after": new.current_xp,
        }
    return rows


def complete(path, activity, intensity, operation, local=None):
    """Apply a completion once, with the repeat count computed under lock."""
    local = local if local is not None else datetime.now().astimezone()
    if local.utcoffset() is None:
        raise ValueError("Для времени выполнения нужен часовой пояс.")
    ensure_progression_rules(path)
    with journal_lock(path):
        events = read_events(path)
        for event in events:
            if event["transaction_id"] == operation:
                payload = event["payload"]
                if (payload.get("activity_id"), payload.get("intensity")) != (
                    activity.key, intensity
                ):
                    raise ValueError("Идентификатор операции уже использован.")
                return event
        before, counts = project(events)
        key = (game_date(local).isoformat(), activity.key)
        number = counts.get(key, 0) + 1
        payload = completion_payload(activity, intensity, number, before)
        credited = credits(payload)
        details = ", ".join(
            f"{ATTRIBUTE_NAMES[key]} +{value} XP"
            for key, value in credited.items()
        )
        event = make_event(
            events, operation, "activity.completed", operation, local, payload,
            f"{activity.name}: {details}",
        )
        append_event(path, event)
        return event


def completion_payload(activity, intensity, number, before):
    base = activity.rewards()
    credited = completion_xp(base, intensity, number)
    after = deepcopy(before)
    after.apply(credited)
    rows = xp_rows(before, after, base, credited)
    return {
        "activity_id": activity.key,
        "activity_name_snapshot": activity.name,
        "activity_version": 1, "intensity": intensity,
        "xp_rounding": "floor",
        "intensity_factor": str(INTENSITY[intensity]),
        "occurrence_index_in_game_day": number,
        "diminishing_factor": str(repetition_factor(number)),
        "xp_by_stat": rows, "consequences": level_changes(rows),
    }


def import_legacy(path, log):
    """Save a one-time undated baseline from the previous JSON format."""
    with journal_lock(path):
        if read_events(path):
            return
        active_entries = set(log.history())
        history = [
            {
                "text": str(entry), "xp": entry.xp,
                "correction": entry.correction,
                "active": entry in active_entries,
                "canceled": entry.activity is None,
            }
            for entry in log.events
        ]
        count, _ = log.summary()
        credits = {key: 0 for key in ATTRIBUTE_NAMES}
        for entry in log.history():
            for key, amount in entry.xp.items():
                credits[key] += amount
        payload = {
            "history": history, "completion_count": count,
            "xp_by_stat": xp_rows(
                Character(), log.character(), credits, credits
            ),
        }
        operation = str(uuid4())
        event = make_event(
            [], operation, "system.legacy_imported", "local-player",
            datetime.now().astimezone(), payload,
            "Перенесена история JSON v1 без дат выполнения.",
        )
        append_event(path, event)


def effective_completions(events):
    entries = {}
    for event in events:
        payload = event["payload"]
        if event["event_type"] == "activity.completed":
            entries[event["seq"]] = (event["game_date"], payload)
        elif event["event_type"] == "correction.applied":
            target = payload["target_seq"]
            if target not in entries:
                raise ValueError(
                    f"Событие №{event['seq']}: выполнение не найдено."
                )
            day, _ = entries[target]
            entries[target] = (day, payload["replacement"])
    return entries


def recorded_awards(events, entries):
    for event in events:
        if event["event_type"] == "system.legacy_imported":
            yield credits(event["payload"])
    for _, payload in entries.values():
        if payload is not None:
            yield credits(payload)


def credits(payload):
    return {
        key: row["credited_xp"] for key, row in payload["xp_by_stat"].items()
    }


def level_changes(rows):
    return [
        {
            "type": "stat.leveled_up" if delta > 0 else "stat.level_revoked",
            "stat": key,
            "level": level,
        }
        for key, row in rows.items()
        if (delta := row["level_after"] - row["level_before"])
        for level in range(
            row["level_before"] + 1 if delta > 0 else row["level_before"],
            row["level_after"] + 1 if delta > 0 else row["level_after"],
            1 if delta > 0 else -1,
        )
    ]


def state_change(events, kind, entity_id, entries, payload, message):
    before, _ = project(events)
    after = Character()
    for award in recorded_awards(events, entries):
        after.apply(award)
    zeros = dict.fromkeys(ATTRIBUTE_NAMES, 0)
    rows = xp_rows(before, after, zeros, zeros)
    payload = {
        **payload, "xp_by_stat": rows, "consequences": level_changes(rows),
    }
    return make_event(
        events, str(uuid4()), kind, entity_id, datetime.now().astimezone(),
        payload, message,
    )


def ensure_progression_rules(path):
    with journal_lock(path):
        events = read_events(path)
        if any(event["event_type"] == "rules.updated" for event in events):
            return
        event = state_change(
            events, "rules.updated", "progression",
            effective_completions(events),
            {"previous_rules_version": 1, "base_xp": 100,
             "previous_increment_xp": 100, "increment_xp": 25},
            "Изменены пороги уровней: 100 XP, затем +25 XP за уровень.",
        )
        append_event(path, event)


def revise_completion(path, target, activity, intensity):
    with journal_lock(path):
        events = read_events(path)
        entries = effective_completions(events)
        if target not in entries:
            raise ValueError("Выполнение с таким номером не найдено.")
        day, previous = entries[target]
        if previous is None and activity is None:
            raise ValueError("Выполнение уже отменено.")
        replacement = None
        if activity is not None:
            number = 1 + sum(
                seq < target and date == day and current is not None
                and current["activity_id"] == activity.key
                for seq, (date, current) in entries.items()
            )
            replacement = completion_payload(
                activity, intensity, number, Character()
            )
        entries[target] = (day, replacement)
        action = "Исправление" if replacement is not None else "Отмена"
        event = state_change(
            events, "correction.applied", events[target - 1]["event_id"],
            entries,
            {"target_seq": target, "replacement": replacement,
             "previous": previous},
            f"{action} выполнения #{target}",
        )
        append_event(path, event)
        return event
