"""Journal-first commands and transactional, disposable SQLite projections."""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import signing
from django.db import transaction

from liferpg.domain.activities import catalog
from liferpg.domain.activity_xp import game_date
from liferpg.domain.achievements import (
    achievement_effect, achievement_names, achievement_skill_progress,
    condition_met, evaluate_custom,
    unlocked,
)
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.configuration import configuration
from liferpg.domain.perks import PERKS, active_perks
from liferpg.domain.content import (
    CONTENT_COMMANDS, MODIFIERS, definitions, validate_definition,
    validate_texts,
)
from liferpg.domain.quests import (
    advance, evaluate, quest_catalog, quest_state, week_start,
)
from liferpg.domain.rust import rust_state
from liferpg.domain.skills import root_progress, writing_progress
from liferpg.game import (
    PreviewChanged, complete, completion_payload, developer_update,
    effective_completions, make_event, project, revise_completion, set_perk,
    settle_periods,
)
from liferpg.journal import (
    append_event, backup_journal, event_context, journal_lock, read_events,
)
from liferpg.preferences import preferences
from liferpg.statistics import manual_player_level, period_statistics
from webgame.models import GameState, RecordedEvent, Report


def journal_path():
    return Path(settings.DATA_DIR) / "events.jsonl"


_web_threads = Lock()


@contextmanager
def web_lock():
    lock = journal_path().with_name("web-operations")
    with _web_threads, journal_lock(lock):
        yield


def local_now(config):
    return datetime.now(timezone.utc).astimezone(ZoneInfo(config["timezone"]))


def snapshot(events):
    character, counts = project(events)
    debts, _, _ = rust_state(events)
    perks, _ = active_perks(events)
    return {
        "stats": {
            key: {"level": character.attribute(key).level,
                  "xp": character.attribute(key).current_xp,
                  "debt": debts.get(key, 0)}
            for key in ATTRIBUTE_NAMES
        },
        "counts": [[day, key, value] for (day, key), value in counts.items()],
        "perks": sorted(perks), "quests": quest_state(events),
        "writing": writing_progress(events),
        "root_skills": root_progress(events),
        "achievements": sorted(unlocked(events)),
        "player_level": manual_player_level(events),
        "config": configuration(events),
    }


def synchronize():
    """Caller holds web_lock. Reading and SQLite commit share journal lock."""
    with journal_lock(journal_path()):
        events = read_events(journal_path())
        data = snapshot(events)
        with transaction.atomic():
            RecordedEvent.objects.all().delete()
            RecordedEvent.objects.bulk_create([
                RecordedEvent(event_id=e["event_id"], seq=e["seq"], data=e)
                for e in events
            ])
            GameState.objects.update_or_create(
                pk=1, defaults={"last_applied_seq": len(events), "data": data}
            )
            Report.objects.all().delete()
            for event in events:
                if event["event_type"] == "report.generated":
                    report = event["payload"]["report"]
                    Report.objects.update_or_create(
                        period=report["period"],
                        defaults={k: report[k] for k in
                                  ("start", "end", "kind", "data")},
                    )
        return events, GameState.objects.get(pk=1).data


def next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def report_data(events, start, end):
    result = period_statistics(events, start.isoformat(), end.isoformat())
    result["achievements"] = [
        {"name": effect["achievement"], "date": e["game_date"]}
        for e in events
        if start.isoformat() <= e["game_date"] <= end.isoformat()
        for effect in e["payload"].get("consequences", ())
        if effect["type"] == "achievement.unlocked"
    ]
    result["corrections"] = [
        {"date": e["game_date"], "target": e["payload"]["target_seq"]}
        for e in events
        if start.isoformat() <= e["game_date"] <= end.isoformat()
        and e["event_type"] == "correction.applied"
    ]
    return result


def generate_reports(moment):
    with journal_lock(journal_path()):
        events = read_events(journal_path())
        dates = [e["game_date"] for e in events
                 if e["event_type"] == "activity.completed"]
        if not dates:
            return
        first = date.fromisoformat(min(dates))
        today = game_date(moment, configuration(events)["boundary_minutes"])
        existing = {
            e["payload"]["report"]["period"] for e in events
            if e["event_type"] == "report.generated"
        }
        for kind, initial in (
            ("weekly", week_start(first)), ("monthly", first.replace(day=1))
        ):
            start = initial
            while True:
                following = (start + timedelta(days=7) if kind == "weekly"
                             else next_month(start))
                if following > today:
                    break
                period = f"{kind}:{start.isoformat()}"
                if period not in existing:
                    end = following - timedelta(days=1)
                    report = {
                        "period": period, "kind": kind,
                        "start": start.isoformat(), "end": end.isoformat(),
                        "data": report_data(events, start, end),
                    }
                    event = make_event(
                        events, str(uuid4()), "report.generated", period,
                        moment, {"xp_by_stat": {}, "report": report},
                        f"Сформирован отчёт {start} — {end}.",
                    )
                    event["actor"] = "system"
                    append_event(journal_path(), event)
                    events.append(event)
                    existing.add(period)
                start = following


def refresh(*, advance=False, moment=None):
    with web_lock():
        if advance:
            with journal_lock(journal_path()):
                config = configuration(read_events(journal_path()))
            moment = moment or local_now(config)
            settle_periods(journal_path(), moment)
            generate_reports(moment)
        return synchronize()


def day_choices(config, moment=None):
    moment = moment or local_now(config)
    current = game_date(moment, config["boundary_minutes"])
    return {"current": current, "previous": current - timedelta(days=1)}


def preview(activity, intensity, credited_day=None):
    with web_lock():
        events, _ = synchronize()
        return preview_from_events(events, activity, intensity, credited_day)


def preview_from_events(events, activity, intensity, credited_day=None):
    if activity.key not in catalog(events):
        raise ValueError("Активность недоступна или архивирована.")
    config = configuration(events)
    day = credited_day or day_choices(config)["current"]
    character, counts = project(events)
    if not activity.available(character):
        raise ValueError(activity.requirement())
    number = counts.get((day.isoformat(), activity.key), 0) + 1
    return completion_payload(
        activity, intensity, number, character, active_perks(events)[0],
        rust_state(events)[0], config,
    )


def quote(values, operation, *, day=None):
    with web_lock():
        events, _ = synchronize()
        choices = day_choices(configuration(events))
        choice = values.get("day_choice") or "current"
        if choice not in choices:
            raise ValueError("Недопустимый выбор дня.")
        day = day or choices[choice]
        payload = preview_from_events(
            events, catalog(events)[values["activity"]],
            values["intensity"], day,
        )
        token = signing.dumps({
            "operation": str(operation), "activity": values["activity"],
            "intensity": values["intensity"], "day": day.isoformat(),
            "day_choice": choice, "payload": payload,
        }, salt="activity-preview", compress=True)
        return payload, day, token


def verified_quote(values, operation):
    try:
        if values.get("day_choice") not in (None, "", "current", "previous"):
            raise ValueError
        data = signing.loads(values.get("quote", ""), salt="activity-preview")
        if any(data[key] != expected for key, expected in (
            ("operation", operation), ("activity", values["activity"]),
            ("intensity", values["intensity"]),
            ("day_choice", values.get("day_choice") or "current"),
        )):
            raise ValueError
        return data
    except (signing.BadSignature, KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            "Сначала рассчитайте награду для выбранного дня."
        ) from exc


def save_settings(events, values, operation, developer):
    before = configuration(events)
    after = deepcopy(before)
    if "intensity" in values:
        if not developer:
            raise ValueError("Коэффициенты доступны в режиме разработчика.")
        after.update(intensity=values["intensity"],
                     repetition=values["repetition"])
    else:
        after.update(values)
    after["version"] = before["version"] + 1
    moment = local_now(before)
    event = make_event(
        events, operation, "settings.updated", "settings", moment,
        {"xp_by_stat": {}, "before": before, "after": after,
         "effective_at_utc": moment.astimezone(timezone.utc).isoformat(),
         "recalculate_history": False},
        "Настройки сохранены для будущих операций.",
    )
    event["actor"] = "developer" if "intensity" in values else "user"
    with journal_lock(journal_path()):
        append_event(journal_path(), event)
    return event


def backup():
    path = journal_path()
    if not path.exists():
        raise ValueError("Журнал ещё пуст. Сначала выполните действие.")
    folder = path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-")
        + str(uuid4())[:8] + ".jsonl.bak"
    )
    backup_journal(path, destination)
    return destination


def execute(command, values, operation, *, developer=False,
            developer_revision=None):
    """Serialize web commands; UUID and request data survive DB failure."""
    operation = str(UUID(str(operation)))
    privileged = command in {
        "perk", "level", "streak", "achievement", "batch", "rules",
        "freezes", "restore",
    }
    if privileged and not developer:
        raise ValueError("Команда доступна в режиме разработчика.")
    signature = {"command": command, "values": values}
    with web_lock():
        events, _ = synchronize()
        display = preferences(events, "display.updated")
        if privileged and developer_revision is not None and (
            not display["show_developer"]
            or developer_revision != display["developer_revision"]
        ):
            raise ValueError("Режим разработчика выключен.")
        prior = next((e for e in events
                      if e["transaction_id"] == operation), None)
        if prior:
            if prior["payload"].get("web_request") != signature:
                raise ValueError("Идентификатор операции уже использован.")
            return prior
        config = configuration(events)
        moment = local_now(config)
        result = None
        with event_context(signature):
            if command == "complete":
                if values["activity"] not in catalog(events):
                    raise ValueError("Активность недоступна или архивирована.")
                day = None
                quoted = None
                if "quote" in values:
                    quoted = verified_quote(values, operation)
                    day = date.fromisoformat(quoted["day"])
                    current = preview_from_events(
                        events, catalog(events)[values["activity"]],
                        values["intensity"], day,
                    )
                    if current != quoted["payload"]:
                        raise PreviewChanged(day)
                result = complete(
                    journal_path(), catalog(events)[values["activity"]],
                    values["intensity"], operation, moment,
                    credited_day=day,
                    expected_payload=quoted["payload"] if quoted else None,
                )
            elif command in ("profile", "tutorial", "archive", "restore"):
                from webgame.catalog_services import change
                result = change(events, command, values, operation, moment)
            elif command in CONTENT_COMMANDS:
                result = create_content(events, command, values, operation,
                                        moment)
            elif command in ("display", "system", "description",
                             "quick-activity"):
                result = update_preferences(
                    events, command, values, operation, moment, display
                )
            elif command == "freezes":
                state = quest_state(events)
                kind = values["kind"]
                if kind not in ("daily", "weekly") or any(
                    type(values[key]) is not int or values[key] < minimum
                    for key, minimum in (("stock", 0), ("maximum", 0),
                                         ("amount", 0), ("interval", 1))
                ):
                    raise ValueError("Некорректные параметры заморозок.")
                if values["stock"] > values["maximum"]:
                    raise ValueError(
                        "Сначала уменьшите запас до нового максимума."
                    )
                state["banks"][kind] = values["stock"]
                state.setdefault("freeze_settings", {})[kind] = {
                    key: values[key]
                    for key in ("maximum", "amount", "interval")
                }
                # Do not reissue the currently open calendar period.
                day = day_choices(config, moment)["current"]
                marker = (week_start(day) if kind == "daily"
                          else day.replace(day=1))
                state.setdefault("freeze_calendar", {}).setdefault(
                    kind, marker.isoformat()
                )
                result = make_event(
                    events, operation, "developer.updated", "freezes", moment,
                    {"xp_by_stat": {}, "consequences": [],
                     "quest_state": state, "action": "freezes"},
                    "Параметры заморозок сохранены.",
                )
                result["actor"] = "developer"
                append_event(journal_path(), result)
            elif command == "pin":
                pins = set(pinned_activities(events))
                if values["activity"] not in catalog(events):
                    raise ValueError("Неизвестная активность.")
                if values["enabled"]:
                    pins.add(values["activity"])
                else:
                    pins.discard(values["activity"])
                result = make_event(
                    events, operation, "preferences.updated", "activities",
                    moment, {"xp_by_stat": {},
                             "pinned_activities": sorted(pins)},
                    "Закреплённые активности сохранены.",
                )
                append_event(journal_path(), result)
            elif command == "revise":
                activity = (None if values["cancel"] else
                            catalog(events)[values["activity"]])
                result = revise_completion(
                    journal_path(), values["target"], activity,
                    values["intensity"], operation=operation, local=moment,
                    max_age=3600,
                )
            elif command == "perk":
                set_perk(journal_path(), values["perk"], values["enabled"],
                         developer_mode=developer, operation=operation,
                         local=moment)
            elif command in ("level", "streak", "achievement"):
                developer_update(
                    journal_path(), command, values["key"],
                    values.get("value"), developer_mode=developer,
                    operation=operation, local=moment,
                )
            elif command in ("settings", "rules"):
                result = save_settings(
                    events, values, operation, developer
                )
            elif command == "batch":
                result = run_batch(values, operation, moment, events)
            else:
                raise ValueError("Неизвестная команда.")
        synchronize()
        if config["backup_on_change"]:
            backup()
        return result


def update_preferences(events, command, data, operation, moment, display):
    entity = command
    payload = {"xp_by_stat": {}}
    if command == "display":
        quests = quest_catalog(definitions(events, "quest"), events)
        settings_data = dict(data)
        for period in ("daily", "weekly"):
            field = period + "_pins"
            mode = settings_data[period + "_mode"]
            if mode not in ("all", "manual") or any(
                key not in quests or quests[key][1] != period
                for key in settings_data[field]
            ):
                raise ValueError("Некорректный выбор заданий.")
            if mode == "all":
                settings_data[field] = display[field]
        settings_data["developer_revision"] = (
            display["developer_revision"] if data["show_developer"]
            else operation
        )
        payload["preferences"] = settings_data
        kind = "display.updated"
    elif command == "system":
        values = {**data, "quiet": data["autostart"] and data["quiet"]}
        apply = getattr(settings, "LIFERPG_APPLY_SYSTEM", None)
        if apply is None:
            raise ValueError("Настройка доступна в приложении Windows.")
        try:
            apply(values)
        except OSError as exc:
            raise ValueError("Не удалось изменить автозапуск.") from exc
        kind = "system.preferences"
        payload["preferences"] = values
    elif command == "description":
        if set(data) != {"kind", "target", "texts"}:
            raise ValueError("Разрешено изменять только тексты.")
        validate_texts(data["kind"], data["texts"])
        catalogs = {
            "quest": quest_catalog(definitions(events, "quest"), events),
            "achievement": achievement_names(events),
            "perk": PERKS, "modifier": MODIFIERS,
        }
        if data["target"] not in catalogs.get(data["kind"], {}):
            raise ValueError("Запись не найдена.")
        kind = "texts.updated"
        entity = data["target"]
        payload.update(kind=data["kind"], texts=data["texts"])

    else:
        kind = "display.activity"
        entity = data["activity"]
        if entity not in catalog(events):
            raise ValueError("Активность не найдена.")
    event = make_event(events, operation, kind, entity, moment, payload,
                       "Настройки сохранены.")
    with journal_lock(journal_path()):
        append_event(journal_path(), event)
    return event


def create_content(events, command, data, operation, moment):
    kind = command.removeprefix("create-")
    validate_definition(kind, data)
    if kind in ("quest", "achievement"):
        text = data.get("condition_text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Описание условия обязательно.")
    activities = catalog(events)
    custom_quests = definitions(events, "quest")
    key = f"user-{operation}"
    payload = {"definition": data, "xp_by_stat": {}, "consequences": []}
    character, _ = project(events)
    state = quest_state(events)
    completions = [
        (date.fromisoformat(day), item["activity_id"])
        for day, item in effective_completions(events).values()
        if item is not None
    ]
    if kind == "achievement":
        condition = data["condition"]
        catalogs = {"activity": activities,
                    "streak": quest_catalog(custom_quests, events),
                    "stat": {*ATTRIBUTE_NAMES, "any"},
                    "skill": {*achievement_skill_progress(events), "any"}}
        if condition["target"] not in catalogs[condition["type"]]:
            raise ValueError("Объект условия не найден.")
        totals = Counter(activity for _, activity in completions)
        if condition_met(
            condition, character, state["streaks"], totals, {},
            achievement_skill_progress(events),
        ):
            payload["consequences"].append(achievement_effect(key))
    elif kind == "skill":
        if not data["links"].keys() <= activities.keys():
            raise ValueError("Активность не найдена.")
    elif kind == "quest":
        if data["activity"] not in activities:
            raise ValueError("Активность не найдена.")
        day = day_choices(configuration(events), moment)["current"]
        effects = advance(state, day, custom_quests, events=events)
        custom_quests[key] = data
        effects.extend(evaluate(
            state, day, completions, custom_quests, only=key, events=events
        ))
        payload.update(quest_state=state, consequences=effects)
        evaluate_custom(events, character, payload)
    event = make_event(events, operation, f"{kind}.created", key, moment,
                       payload, f"Создано: {data['name']}")
    event["actor"] = "user"
    with journal_lock(journal_path()):
        append_event(journal_path(), event)
    return event


def run_batch(values, operation, moment, events):
    """Deterministic child IDs allow retrying a partly committed batch."""
    first_id = str(uuid5(UUID(operation), "0"))
    first = next((e for e in events if e["event_id"] == first_id), None)
    if first:
        if first["payload"].get("web_request") != {
            "command": "batch", "values": values
        }:
            raise ValueError("Параметры записанной партии изменены.")
        moment = datetime.fromisoformat(first["local_datetime"])
    done, totals, skill = 0, {}, 0
    error = None
    try:
        for index in range(values["amount"]):
            event = complete(
                journal_path(), catalog(events)[values["activity"]],
                values["intensity"], str(uuid5(UUID(operation), str(index))),
                moment,
            )
            done += 1
            for key, row in event["payload"]["xp_by_stat"].items():
                totals[key] = totals.get(key, 0) + row["credited_xp"]
            skill += sum(e["xp"] for e in event["payload"]["consequences"]
                         if e["type"] == "skill.xp_credited")
    except (OSError, ValueError) as exc:
        error = str(exc)
    return {"batch": True, "done": done, "total": values["amount"],
            "xp": totals, "skill": skill, "error": error}


def pinned_activities(events):
    for event in reversed(events):
        if "pinned_activities" in event["payload"]:
            return event["payload"]["pinned_activities"]
    return []
