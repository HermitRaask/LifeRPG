from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from liferpg.domain.achievements import unlocked
from liferpg.domain.activities import catalog
from liferpg.domain.quests import quest_state
from liferpg.domain.rust import rust_state
from liferpg.game import (
    complete, developer_batch, developer_update, project, settle_rust,
)
from liferpg.journal import read_events
from liferpg.statistics import manual_player_level, period_statistics


def test_developer_settings_and_batch(tmp_path):
    path = tmp_path / "events.jsonl"
    other = tmp_path / "other.jsonl"
    activity = catalog()["writing"]
    now = datetime.now(timezone.utc)
    for action, key, value in (
        ("level", "player", 42), ("level", "strength", 25),
        ("streak", "writing", 400), ("achievement", "skill-five", None),
    ):
        with pytest.raises(ValueError):
            developer_update(path, action, key, value)
        assert developer_update(path, action, key, value, developer_mode=True)
    with pytest.raises(ValueError):
        developer_batch(path, activity, "2", 2)
    with pytest.raises(ValueError):
        developer_batch(path, activity, "2", 0, developer_mode=True)
    before = path.read_bytes()
    assert not developer_update(
        path, "achievement", "skill-five", developer_mode=True
    )
    assert path.read_bytes() == before
    assert developer_batch(
        path, activity, "2", 2, developer_mode=True, local=now
    )[0] == 2
    for _ in range(2):
        complete(other, activity, "2", str(uuid4()), now)
    events = read_events(path)
    assert manual_player_level(events) == 42
    assert project(events)[0].attribute("strength").level == 25
    assert quest_state(events)["streaks"]["writing"]["duration"] >= 400
    assert "skill-five" in unlocked(events)
    assert period_statistics(events)["xp"] == period_statistics(
        read_events(other)
    )["xp"]


def test_rust_policy_idempotency_and_repayment(tmp_path):
    path = tmp_path / "events.jsonl"
    moment = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    developer_update(path, "level", "will", 25, developer_mode=True)
    activity = catalog()["meditation"]
    complete(path, activity, "2", str(uuid4()), moment)
    policy = dict(first_day=8, debt_cap=1000)
    settle_rust(path, moment + timedelta(days=7), **policy)
    assert not rust_state(read_events(path))[0]
    overdue = moment + timedelta(days=10)
    settle_rust(path, overdue, **policy)
    before = path.read_bytes()
    settle_rust(path, overdue, **policy)
    assert path.read_bytes() == before
    events = read_events(path)
    assert events[-1]["payload"]["xp_by_stat"]["will"]["daily_xp"] == 50
    assert rust_state(events)[0]["will"] == 100
    for day in (overdue, overdue + timedelta(days=1),
                overdue + timedelta(days=2)):
        complete(path, activity, "2", str(uuid4()), day)
    events = read_events(path)
    assert rust_state(events)[0]["will"] == 0
    assert project(events)[0].attribute("will").current_xp == 50
    report = period_statistics(events)
    assert report["paid"] == 100 and report["decay"] == 50
    assert period_statistics(events, "2026-11-14", "2026-11-14")["paid"] == 0
    assert period_statistics([])["history"] == []


def test_achievement_is_granted_once(tmp_path):
    path = tmp_path / "events.jsonl"
    for _ in range(3):
        complete(path, catalog()["meditation"], "2", str(uuid4()))
    events = read_events(path)
    assert "first-level" in unlocked(events)
    assert sum(
        effect.get("achievement") == "first-level"
        for event in events
        for effect in event["payload"].get("consequences", ())
    ) == 1
