"""Run journal-backed game operations and project recorded results."""

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from liferpg.domain.achievements import (
    achievement_effect, achievement_names, evaluate_achievements,
    evaluate_custom, unlocked,
)
from liferpg.domain.activities import catalog
from liferpg.domain.activity_xp import (
    INTENSITY, completion_xp, game_date, repetition_factor,
)
from liferpg.domain.character import ATTRIBUTE_NAMES, Character
from liferpg.domain.configuration import configuration
from liferpg.domain.perks import (
    EFFECTS, active_perks, effects_for, lazy_replacement,
)
from liferpg.domain.progression import xp_required
from liferpg.domain.content import definitions
from liferpg.domain.quests import advance, evaluate, quest_catalog, quest_state
from liferpg.domain.rust import decay_row, rust_state
from liferpg.domain.skills import award_root_skills
from liferpg.journal import append_event, journal_lock, read_events


def utc_string(moment):
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_event(events, operation, kind, entity_id, local, payload, message):
    config = configuration(events)
    day = game_date(local, config["boundary_minutes"])
    return {
        "schema_version": 1,
        "rules_version": config["version"],
        "time_rules_version": config["version"],
        "boundary_minutes": config["boundary_minutes"],
        "event_id": operation, "transaction_id": operation,
        "seq": len(events) + 1, "event_type": kind,
        "actor": {
            "rules.updated": "system", "system.legacy_imported": "migration",
            "developer.updated": "developer", "rust.decayed": "system",
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
        for key, row in payload.get("xp_by_stat", {}).items():
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


class PreviewChanged(ValueError):
    def __init__(self, day):
        super().__init__(
            "Расчёт изменился. Проверьте награду и подтвердите снова."
        )
        self.day = day


def complete(path, activity, intensity, operation, local=None, *,
             credited_day=None, expected_payload=None):
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
                ) or (credited_day is not None
                      and event["game_date"] != credited_day.isoformat()):
                    raise ValueError("Идентификатор операции уже использован.")
                return event
        before, counts = project(events)
        if activity.key not in catalog(events):
            raise ValueError("Активность недоступна или архивирована.")
        if not activity.available(before):
            raise ValueError(activity.requirement())
        config = configuration(events)
        day = credited_day or game_date(local, config["boundary_minutes"])
        key = (day.isoformat(), activity.key)
        number = counts.get(key, 0) + 1
        perks, victory = active_perks(events)
        debts, _, _ = rust_state(events)
        payload = completion_payload(
            activity, intensity, number, before, perks, debts, config
        )
        if expected_payload is not None and payload != expected_payload:
            raise PreviewChanged(day)
        will = payload["xp_by_stat"].get("will")
        will_level = before.attribute("will").level
        if will is not None:
            will_level = will["level_after"]
        payload["consequences"].extend(
            lazy_replacement(perks, victory, will_level)
        )
        state = quest_state(events)
        custom = definitions(events, "quest")
        payload["consequences"].extend(advance(
            state, min(day, game_date(local, config["boundary_minutes"])),
            custom, events=events,
        ))
        completions = [
            (date.fromisoformat(day_key), item["activity_id"])
            for day_key, item in effective_completions(events).values()
            if item is not None
        ]
        completions.append((day, activity.key))
        payload["consequences"].extend(
            evaluate(state, day, completions, custom, events=events)
        )
        payload["quest_state"] = state
        award_root_skills(events, payload, activity.key)
        evaluate_achievements(events, before, payload)
        evaluate_custom(events, before, payload,
                        Counter(key for _, key in completions), {activity.key})
        credited = credits(payload)
        details = ", ".join(
            f"{ATTRIBUTE_NAMES[key]} +{value} XP"
            for key, value in credited.items()
        )
        event = make_event(
            events, operation, "activity.completed", operation, local, payload,
            f"{activity.name}: {details}",
        )
        event.update(
            game_date=day.isoformat(),
            game_week_start=(day - timedelta(days=day.weekday())).isoformat(),
            game_month=day.strftime("%Y-%m"),
        )
        append_event(path, event)
        return event


def completion_payload(activity, intensity, number, before, perks=(),
                       debts=None, config=None):
    base = activity.rewards()
    effects = effects_for(activity.key, base, perks)
    calculated = completion_xp(base, intensity, number, effects, config)
    debts = debts or {}
    paid = {key: min(value, debts.get(key, 0))
            for key, value in calculated.items()}
    credited = {key: value - paid[key] for key, value in calculated.items()}
    after = deepcopy(before)
    after.apply(credited)
    rows = xp_rows(before, after, base, credited)
    for key, row in rows.items():
        row.update(calculated_xp=calculated[key], rust_paid=paid[key])
        if paid[key]:
            row["rust_debt_before"] = debts[key]
            row["rust_debt_after"] = debts[key] - paid[key]
    return {
        "activity_id": activity.key,
        "activity_name_snapshot": activity.name,
        "activity_version": 1, "intensity": intensity,
        "xp_rounding": "floor", "effect_factors": effects,
        "intensity_factor": (config["intensity"][intensity] if config
                             else str(INTENSITY[intensity])),
        "occurrence_index_in_game_day": number,
        "diminishing_factor": str(repetition_factor(
            number, config["repetition"]
        ) if config else repetition_factor(number)),
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


def state_change(events, kind, entity_id, payload, message, local=None):
    before, _ = project(events)
    after = deepcopy(before)
    if kind == "correction.applied":
        previous = payload["previous"]
        replacement = payload["replacement"]
        old = credits(previous) if previous else {}
        new = credits(replacement) if replacement else {}
        for key in old.keys() | new.keys():
            delta = new.get(key, 0) - old.get(key, 0)
            if delta > 0:
                after.attribute(key).award_xp(delta)
            elif delta < 0:
                stat = after.attribute(key)
                level, remaining = stat.level, stat.current_xp + delta
                while remaining < 0 and level > 1:
                    level -= 1
                    remaining += xp_required(level)
                stat.restore_progress(level, max(0, remaining))
    zeros = dict.fromkeys(ATTRIBUTE_NAMES, 0)
    rows = xp_rows(before, after, zeros, zeros)
    effects = level_changes(rows)
    perks, victory = active_perks(events)
    effects.extend(lazy_replacement(
        perks, victory, after.attribute("will").level
    ))
    payload = {**payload, "xp_by_stat": rows, "consequences": effects}
    return make_event(
        events, str(uuid4()), kind, entity_id,
        local or datetime.now().astimezone(),
        payload, message,
    )


def ensure_progression_rules(path):
    with journal_lock(path):
        events = read_events(path)
        if any(event["event_type"] == "rules.updated" for event in events):
            return
        event = state_change(
            events, "rules.updated", "progression",
            {"previous_rules_version": 1, "base_xp": 100,
             "previous_increment_xp": 100, "increment_xp": 25,
             "level_25_threshold": 1000, "level_25_increment": 100,
             "configuration_snapshot": configuration(events)},
            "Изменены пороги уровней: 100 XP, +25 до уровня 25; "
            "с уровня 25 — 1000 XP, далее +100. Прогресс сохранён.",
        )
        append_event(path, event)


def revise_completion(path, target, activity, intensity, *,
                      operation=None, local=None, max_age=None):
    with journal_lock(path):
        events = read_events(path)
        if operation:
            existing = next((e for e in events
                             if e["transaction_id"] == operation), None)
            if existing:
                if (existing["event_type"] != "correction.applied"
                        or existing["payload"]["target_seq"] != target):
                    raise ValueError("Идентификатор операции уже использован.")
                return existing
        local = local or datetime.now().astimezone()
        entries = effective_completions(events)
        if target not in entries:
            raise ValueError("Выполнение с таким номером не найдено.")
        original = events[target - 1]
        if max_age is not None:
            age = (local - datetime.fromisoformat(
                original["recorded_at_utc"]
            )).total_seconds()
            if not 0 <= age < max_age:
                raise ValueError("Исправление доступно только в первый час.")
        day, previous = entries[target]
        if previous is None and activity is None:
            raise ValueError("Выполнение уже отменено.")
        replacement = None
        if activity is not None:
            if not activity.available(project(events)[0]):
                raise ValueError(activity.requirement())
            number = 1 + sum(
                seq < target and date == day and current is not None
                and current["activity_id"] == activity.key
                for seq, (date, current) in entries.items()
            )
            perks, _ = active_perks(events)
            replacement = completion_payload(
                activity, intensity, number, Character(), perks,
                config=configuration(events)
            )
        action = "Исправление" if replacement is not None else "Отмена"
        event = state_change(
            events, "correction.applied", events[target - 1]["event_id"],
            {"target_seq": target, "replacement": replacement,
             "previous": previous},
            f"{action} выполнения #{target}", local=local,
        )
        affected = {item["activity_id"] for item in (previous, replacement)
                    if item is not None}
        entries[target] = (day, replacement)
        totals = Counter(item["activity_id"] for _, item in entries.values()
                         if item is not None)
        evaluate_custom(events, project(events)[0], event["payload"],
                        totals, affected)
        if operation:
            event["event_id"] = event["transaction_id"] = operation
        append_event(path, event)
        return event


def set_perk(path, perk, enabled, *, developer_mode=False, operation=None,
             local=None):
    if not developer_mode:
        raise ValueError("Переключение доступно в режиме разработчика.")
    if perk not in EFFECTS and perk != "sport":
        raise ValueError("Перк недоступен для выбора.")
    with journal_lock(path):
        events = read_events(path)
        _, victory = active_perks(events)
        character, _ = project(events)
        will = character.attribute("will").level
        if enabled and perk == "lazy" and (victory or will >= 5):
            raise ValueError("Перк Ленивый уже пройден или Воля достигла 5.")
        kind = "perk.assigned" if enabled else "perk.removed"
        payload = {"xp_by_stat": {}, "consequences": [
            {"type": kind, "perk": perk},
        ]}
        event = make_event(
            events, operation or str(uuid4()), "perk.updated", perk,
            local or datetime.now().astimezone(), payload,
            f"{kind}: {perk}",
        )
        append_event(path, event)


def settle_periods(path, local=None):
    local = local if local is not None else datetime.now().astimezone()
    with journal_lock(path):
        events = read_events(path)
        project(events)
        state = quest_state(events)
        old_day = state["day"]
        if old_day is None:
            return
        effects = advance(state, game_date(
            local, configuration(events)["boundary_minutes"]
        ), definitions(events, "quest"), events=events)
        if state["day"] == old_day:
            return
        payload = {"xp_by_stat": {}, "quest_state": state,
                   "consequences": effects}
        evaluate_achievements(events, project(events)[0], payload)
        evaluate_custom(events, project(events)[0], payload)
        event = make_event(
            events, str(uuid4()), "periods.advanced", "local-player", local,
            payload,
            "Обработаны завершённые игровые периоды.",
        )
        append_event(path, event)


def developer_update(path, action, key, value=None, *,
                     developer_mode=False, operation=None, local=None):
    if not developer_mode:
        raise ValueError("Команда доступна в режиме разработчика.")
    if action == "streak":
        if type(value) is not int or value < 0:
            raise ValueError("Укажи код серии и неотрицательную длину.")
    elif action == "level":
        if key not in (*ATTRIBUTE_NAMES, "player"):
            raise ValueError("Неизвестная характеристика.")
        if type(value) is not int or value < 1:
            raise ValueError("Уровень должен быть положительным целым.")
    elif action != "achievement":
        raise ValueError("Неизвестное действие или достижение.")

    with journal_lock(path):
        events = read_events(path)
        character, _ = project(events)
        payload = {"xp_by_stat": {}, "consequences": [],
                   "action": action, "value": value}
        if action == "achievement":
            if key not in achievement_names(events):
                raise ValueError("Неизвестное достижение.")
            if key in unlocked(events):
                return False
            payload["consequences"].append(achievement_effect(key))
        elif action == "streak":
            if key not in quest_catalog(definitions(events, "quest"), events):
                raise ValueError("Неизвестная серия.")
            payload["streak_key"] = key
            state = quest_state(events)
            streak = state["streaks"].setdefault(
                key, dict(duration=0, successful=0, protected=0, best=0,
                          last_chance_used=False, at_risk=False)
            )
            payload["previous_length"] = streak["duration"]
            streak["duration"] = value
            streak["bonus_periods"] = value
            streak["best"] = max(streak["best"], value)
            payload["quest_state"] = state
        elif key == "player":
            payload["player_level"] = value
        else:
            after = deepcopy(character)
            after.attribute(key).restore_progress(value, 0)
            payload["xp_by_stat"] = xp_rows(
                character, after, {key: 0}, {key: 0}
            )
            if key == "will":
                perks, victory = active_perks(events)
                payload["consequences"].extend(
                    lazy_replacement(perks, victory, value)
                )
        evaluate_custom(events, character, payload)
        event = make_event(
            events, operation or str(uuid4()), "developer.updated", key,
            local or datetime.now().astimezone(), payload,
            f"Режим разработчика: {action} {key}",
        )
        append_event(path, event)
        return True


def developer_batch(path, activity, intensity, amount, *,
                    developer_mode=False, local=None):
    if not developer_mode:
        raise ValueError("Команда доступна в режиме разработчика.")
    if type(amount) is not int or amount < 1:
        raise ValueError("Количество должно быть положительным целым.")
    completion_xp(activity.rewards(), intensity, 1)
    moment = local if local is not None else datetime.now().astimezone()
    done, total, skill_xp = 0, {}, 0
    try:
        for _ in range(amount):
            event = complete(path, activity, intensity, str(uuid4()), moment)
            done += 1
            payload = event["payload"]
            for key, xp in credits(payload).items():
                total[key] = total.get(key, 0) + xp
            skill_xp += sum(
                effect["xp"] for effect in payload["consequences"]
                if effect["type"] == "skill.xp_credited"
            )
    except (ValueError, OSError, KeyboardInterrupt) as error:
        reason = str(error) or "прервано"
        return done, total, skill_xp, reason
    return done, total, skill_xp, None


def settle_rust(path, local, *, first_day, debt_cap):
    """Apply an explicit policy. The console leaves scheduling disabled."""
    with journal_lock(path):
        events = read_events(path)
        character, _ = project(events)
        debts, gains, previous = rust_state(events)
        today = game_date(local)
        rows = {}
        for key, last_gain in gains.items():
            row = decay_row(
                character.attribute(key), debts.get(key, 0), last_gain,
                previous.get(key), today, first_day, debt_cap,
            )
            if row is not None:
                rows[key] = row
        if not rows:
            return
        event = make_event(
            events, str(uuid4()), "rust.decayed", "local-player", local,
            {"xp_by_stat": rows, "consequences": []},
            "Списан XP за бездействие.",
        )
        append_event(path, event)
