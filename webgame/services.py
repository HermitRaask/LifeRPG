"""Journal-first commands and transactional, disposable SQLite projections."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction

from liferpg.domain.activities import catalog
from liferpg.domain.activity_xp import game_date
from liferpg.domain.achievements import unlocked
from liferpg.domain.character import ATTRIBUTE_NAMES
from liferpg.domain.configuration import configuration
from liferpg.domain.perks import active_perks
from liferpg.domain.quests import quest_state, week_start
from liferpg.domain.rust import rust_state
from liferpg.domain.skills import writing_progress
from liferpg.game import (
    complete, completion_payload, developer_update,
    make_event, project, revise_completion, set_perk, settle_periods,
)
from liferpg.journal import (
    append_event, backup_journal, event_context, journal_lock, read_events,
)
from liferpg.statistics import manual_player_level, period_statistics
from webgame.models import GameState, RecordedEvent, Report


def journal_path():
    return Path(settings.DATA_DIR) / "events.jsonl"


def web_lock():
    return journal_lock(journal_path().with_name("web-operations"))


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


def preview(activity, intensity):
    with web_lock():
        events, _ = synchronize()
        config = configuration(events)
        moment = local_now(config)
        character, counts = project(events)
        number = counts.get((
            game_date(moment, config["boundary_minutes"]).isoformat(),
            activity.key,
        ), 0) + 1
        return completion_payload(
            activity, intensity, number, character, active_perks(events)[0],
            rust_state(events)[0], config,
        )


def save_settings(events, values, operation, developer):
    before = configuration(events)
    after = deepcopy(before)
    if "intensity" in values:
        if not developer:
            raise ValueError("Коэффициенты доступны в режиме разработчика.")
        after.update(intensity=values["intensity"],
                     repetition=values["repetition"])
    else:
        clock_changed = any(values[k] != before[k] for k in
                            ("timezone", "boundary_minutes"))
        started = any(e["event_type"] in (
            "activity.completed", "periods.advanced"
        ) for e in events)
        if clock_changed and started:
            raise ValueError(
                "Правило смены игровых суток после начала игры уточняется."
            )
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


def execute(command, values, operation, *, developer=False):
    """Serialize web commands; UUID and request data survive DB failure."""
    operation = str(UUID(str(operation)))
    if command in ("perk", "level", "streak", "achievement", "batch", "rules"):
        if not developer:
            raise ValueError("Команда доступна в режиме разработчика.")
    signature = {"command": command, "values": values}
    with web_lock():
        events, _ = synchronize()
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
                result = complete(
                    journal_path(), catalog()[values["activity"]],
                    values["intensity"], operation, moment,
                )
            elif command == "revise":
                activity = (None if values["cancel"] else
                            catalog()[values["activity"]])
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
                journal_path(), catalog()[values["activity"]],
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
