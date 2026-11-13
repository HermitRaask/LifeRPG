"""Read and write the local JSON activity history."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from activities import Activity, ActivityCompletion, ActivityLog
from character import ATTRIBUTE_NAMES


def load_log(path: Path, activities: dict[str, Activity]) -> ActivityLog:
    """Restore typed records and their catalog references."""
    log = ActivityLog()
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except FileNotFoundError:
        return log

    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
        or not isinstance(data.get("events"), list)
    ):
        raise ValueError("Неверный формат файла истории.")

    for row in data["events"]:
        if not isinstance(row, dict):
            raise ValueError("Неверная запись истории.")
        number = row.get("number")
        key = row.get("activity")
        xp = row.get("xp")
        correction = row.get("correction")
        if (
            type(number) is not int
            or type(correction) is not bool
            or "activity" not in row
            or not isinstance(xp, dict)
            or (key is None and (xp or not correction))
            or (key is not None and (
                not isinstance(key, str) or key not in activities
            ))
            or any(
                stat not in ATTRIBUTE_NAMES
                or type(amount) is not int or amount < 0
                for stat, amount in xp.items()
            )
        ):
            raise ValueError("Неверные данные записи истории.")
        log.append(
            ActivityCompletion(
                number, activities.get(key), xp, correction=correction
            )
        )
    return log


def save_log(path: Path, log: ActivityLog) -> None:
    """Replace the snapshot only after the new JSON is written."""
    data = {
        "version": 1,
        "events": [
            {
                "number": event.number,
                "activity": event.activity.key if event.activity else None,
                "xp": event.xp,
                "correction": event.correction,
            }
            for event in log.events
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
