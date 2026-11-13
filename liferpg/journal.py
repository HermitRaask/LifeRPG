"""Append and replay the local event journal."""

import json
import os
from contextlib import contextmanager
from datetime import date, datetime
from shutil import copyfileobj
from uuid import UUID

from liferpg.domain.character import ATTRIBUTE_NAMES


@contextmanager
def journal_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def validate(event):
    version = event["schema_version"]
    if type(version) is not int or version != 1:
        raise ValueError("неподдерживаемая версия схемы")
    for field in ("event_id", "transaction_id"):
        if str(UUID(event[field])) != event[field]:
            raise ValueError("некорректный UUID")
    if type(event["seq"]) is not int or event["seq"] < 1:
        raise ValueError("некорректный номер события")
    for field in ("rules_version", "time_rules_version", "boundary_minutes"):
        if type(event[field]) is not int:
            raise ValueError(f"некорректное поле {field}")
    for field in ("occurred_at_utc", "recorded_at_utc", "local_datetime"):
        if datetime.fromisoformat(event[field]).utcoffset() is None:
            raise ValueError(f"отсутствует часовой пояс в {field}")
    for field in ("real_local_date", "game_date", "game_week_start"):
        date.fromisoformat(event[field])
    for field in ("actor", "entity_type", "entity_id", "timezone",
                  "message_ru", "game_month"):
        if not isinstance(event[field], str) or not event[field]:
            raise ValueError(f"некорректное поле {field}")
    payload = event["payload"]
    kind = event["event_type"]
    if isinstance(kind, str) and kind.startswith(("skill.", "subskill.")):
        # Historical skill events can contain only skill-specific data.
        if not isinstance(payload, dict):
            raise ValueError("некорректная запись навыка")
        if "xp_by_stat" not in payload:
            return
    elif kind not in (
        "activity.completed", "system.legacy_imported", "rules.updated",
        "correction.applied", "perk.updated", "periods.advanced",
    ):
        raise ValueError("неизвестный тип события")
    for key, row in payload["xp_by_stat"].items():
        if key not in ATTRIBUTE_NAMES:
            raise ValueError("неизвестная характеристика")
        for field in ("level_before", "level_after", "xp_before", "xp_after",
                      "credited_xp"):
            if type(row[field]) is not int or row[field] < 0:
                raise ValueError("некорректный XP или уровень")
        if min(row["level_before"], row["level_after"]) < 1:
            raise ValueError("некорректный переход уровня")
    if kind == "activity.completed":
        if (not isinstance(payload["activity_id"], str)
                or type(payload["occurrence_index_in_game_day"]) is not int
                or payload["occurrence_index_in_game_day"] < 1
                or not isinstance(payload["activity_name_snapshot"], str)
                or not isinstance(payload["consequences"], list)):
            raise ValueError("некорректное выполнение")
    elif kind == "correction.applied":
        if not 1 <= payload["target_seq"] < event["seq"]:
            raise ValueError("некорректная ссылка исправления")
    elif kind == "system.legacy_imported" and not isinstance(
        payload["history"], list
    ):
        raise ValueError("некорректный снимок истории")


def read_events(path):
    try:
        source = path.open("rb")
    except FileNotFoundError:
        return []
    events = []
    by_id = {}
    operations = set()
    with source:
        for line_number, line in enumerate(source, 1):
            try:
                if not line.endswith(b"\n"):
                    raise ValueError("прерванная запись, нет конца строки")
                event = json.loads(line.decode("utf-8"))
                validate(event)
                event_id = event["event_id"]
                if event_id in by_id:
                    if by_id[event_id] != event:
                        raise ValueError("конфликт идентификаторов событий")
                    continue
                operation = event["transaction_id"]
                if operation in operations:
                    raise ValueError("конфликт идентификаторов операций")
                if event["seq"] != len(events) + 1:
                    raise ValueError("нарушена последовательность событий")
                events.append(event)
                by_id[event_id] = event
                operations.add(operation)
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise ValueError(
                    f"{path}, строка {line_number}: {error}"
                ) from error
    return events


def append_event(path, event):
    """Append one fully prepared operation while holding journal_lock."""
    validate(event)
    data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    with path.open("ab") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def backup_journal(path, destination):
    with journal_lock(path):
        with path.open("rb") as source, destination.open("xb") as target:
            copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
