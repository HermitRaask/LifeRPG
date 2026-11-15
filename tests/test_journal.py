from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from liferpg.domain.activity_xp import completion_xp
from liferpg.domain.activities import catalog
from liferpg.domain.character import ATTRIBUTE_NAMES, Character
from liferpg.game import complete, project
from liferpg.journal import backup_journal, read_events


def test_character_starts_with_six_independent_attributes():
    character = Character()
    character.apply({"strength": 99})
    assert len(ATTRIBUTE_NAMES) == 6
    for key in ATTRIBUTE_NAMES:
        stat = character.attribute(key)
        assert (stat.level, stat.current_xp) == (
            1, 99 if key == "strength" else 0
        )


@pytest.mark.parametrize(
    "intensity, expected", [("1", 5), ("2", 10), ("3", 15)]
)
def test_three_intensities(intensity, expected):
    assert completion_xp({"will": 10}, intensity, 1) == {"will": expected}


@pytest.mark.parametrize(
    "number, expected", [(1, 20), (2, 20), (3, 12), (4, 6), (5, 3), (6, 3)]
)
def test_repeat_factors(number, expected):
    assert completion_xp({"will": 20}, "2", number) == {"will": expected}


def test_positive_minimum_and_zero():
    assert completion_xp({"will": 2, "strength": 0}, "1", 6) == {
        "will": 1, "strength": 0
    }


def test_replay_deduplicates_and_restores_from_recorded_results(tmp_path):
    path = tmp_path / "events.jsonl"
    activity = catalog()["meditation"]
    operation = str(uuid4())
    local = datetime(2026, 11, 13, 3, 59, tzinfo=timezone(timedelta(hours=3)))
    first = complete(path, activity, "2", operation, local)
    original = path.read_bytes()
    assert complete(path, activity, "2", operation, local) == first
    assert path.read_bytes() == original

    with path.open("ab") as stream:
        stream.write(original.splitlines(keepends=True)[-1])
    second = complete(path, activity, "2", str(uuid4()), local)
    third = complete(
        path, activity, "2", str(uuid4()), local + timedelta(minutes=1)
    )
    assert {"type": "stat.leveled_up", "stat": "will", "level": 2} in (
        second["payload"]["consequences"]
    )
    assert first["occurred_at_utc"] == "2026-11-13T00:59:00Z"
    assert first["real_local_date"] == "2026-11-13"
    assert third["payload"]["occurrence_index_in_game_day"] == 1

    events = read_events(path)
    events[0]["payload"]["intensity_factor"] = "999"
    character, counts = project(events)
    will = character.attribute("will")
    assert (will.level, will.current_xp) == (3, 0)
    assert counts == {("2026-11-12", "meditation"): 2,
                      ("2026-11-13", "meditation"): 1}


def test_invalid_intensity_leaves_saved_progress_intact(tmp_path):
    path = tmp_path / "events.jsonl"
    activity = catalog()["training"]
    complete(path, activity, "2", str(uuid4()))
    original = path.read_bytes()
    with pytest.raises(ValueError, match="Интенсивность"):
        complete(path, activity, "x", str(uuid4()))
    assert path.read_bytes() == original


def test_fractional_xp_is_rounded_down_and_restored(tmp_path):
    path = tmp_path / "events.jsonl"
    activity = catalog()["training"]
    event = complete(path, activity, "1", str(uuid4()))

    assert event["rules_version"] == 5
    assert event["payload"]["xp_rounding"] == "floor"
    character, _ = project(read_events(path))
    assert character.attribute("strength").current_xp == 13
    assert character.attribute("endurance").current_xp == 1
    assert character.attribute("will").current_xp == 3


@pytest.mark.parametrize("tail", [b'{"seq":', b"invalid\n"])
def test_damaged_rows_block_writes_but_can_be_backed_up(tmp_path, tail):
    path = tmp_path / "events.jsonl"
    activity = catalog()["meditation"]
    complete(path, activity, "2", str(uuid4()))
    with path.open("ab") as stream:
        stream.write(tail)
    original = path.read_bytes()

    with pytest.raises(ValueError, match="строка 3"):
        complete(path, activity, "2", str(uuid4()))
    assert path.read_bytes() == original

    backup = tmp_path / "events.jsonl.bak"
    backup_journal(path, backup)
    assert backup.read_bytes() == original


def test_default_cli_imports_legacy_once_and_restores(
    tmp_path, monkeypatch, capsys
):
    import sys

    from liferpg import cli
    from liferpg.domain.activities import ActivityLog
    from liferpg.storage import save_log

    legacy = tmp_path / "state.json"
    log = ActivityLog()
    log.record(catalog()["meditation"])
    save_log(legacy, log)
    original = legacy.read_bytes()
    monkeypatch.setattr(cli, "DEFAULT_STATE", legacy)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    commands = iter(["2", "meditation", "", "3", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    assert cli.main() == 0
    assert "Воля: уровень 2, 25/125 XP" in capsys.readouterr().out

    commands = iter(["3", "0"])
    assert cli.main() == 0
    assert "Воля: уровень 2, 25/125 XP" in capsys.readouterr().out
    events = read_events(tmp_path / "events.jsonl")
    assert [event["event_type"] for event in events] == [
        "system.legacy_imported", "rules.updated", "activity.completed",
    ]
    assert events[2]["payload"]["occurrence_index_in_game_day"] == 1
    assert legacy.read_bytes() == original
