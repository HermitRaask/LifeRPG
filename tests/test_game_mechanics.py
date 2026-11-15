from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from liferpg.domain.activities import catalog
from liferpg.domain.character import ATTRIBUTE_NAMES, Character
from liferpg.domain.perks import active_perks
from liferpg.domain.progression import award_xp
from liferpg.domain.quests import advance, evaluate, quest_state
from liferpg.game import complete, project, set_perk, settle_periods
from liferpg.journal import read_events


@pytest.mark.parametrize("key", ATTRIBUTE_NAMES)
def test_xp_curve_across_boundary(key):
    stat = Character().attribute(key)
    stat.restore_progress(24, 0)
    stat.award_xp(1700)
    assert (stat.level, stat.current_xp, stat.required_xp) == (26, 25, 1100)
    assert award_xp(25, 0, 999) == (25, 999)
    assert award_xp(25, 0, 1000) == (26, 0)


def test_perk_quest_and_streak_replay(tmp_path):
    path = tmp_path / "events.jsonl"
    set_perk(path, "lazy", True, developer_mode=True)
    activity = catalog()["meditation"]
    monday = datetime(2026, 11, 9, 4, tzinfo=timezone.utc)

    for offset in range(8):
        event = complete(
            path, activity, "2", str(uuid4()), monday + timedelta(days=offset)
        )
        assert event["payload"]["xp_by_stat"]["will"]["credited_xp"] == 75

    events = read_events(path)
    assert project(events)[0].attribute("will").level == 5
    perks, victory = active_perks(events)
    assert victory and "victory" in perks and "lazy" not in perks
    assert quest_state(events)["streaks"]["meditation"]["duration"] == 8
    final = complete(
        path, activity, "2", str(uuid4()), monday + timedelta(days=8)
    )
    assert final["payload"]["xp_by_stat"]["will"]["credited_xp"] == 50
    with pytest.raises(ValueError, match="уже пройден"):
        set_perk(path, "lazy", True, developer_mode=True)


def test_freeze_then_last_chance_then_reset(tmp_path):
    path = tmp_path / "events.jsonl"
    day = datetime(2026, 11, 9, 4, tzinfo=timezone.utc)
    complete(path, catalog()["meditation"], "2", str(uuid4()), day)
    settle_periods(path, day + timedelta(days=3))
    state = quest_state(read_events(path))
    assert state["banks"]["daily"] == 0
    streak = state["streaks"]["meditation"]
    assert streak["duration"] == 3
    assert streak["protected"] == 2
    assert streak["last_chance_used"] is True
    complete(
        path, catalog()["meditation"], "2", str(uuid4()),
        day + timedelta(days=3),
    )
    settle_periods(path, day + timedelta(days=5))
    original = path.read_bytes()
    settle_periods(path, day + timedelta(days=5))
    assert path.read_bytes() == original
    state = quest_state(read_events(path))
    assert state["streaks"]["meditation"]["duration"] == 0


def test_quest_and_or_and_bank_caps():
    day = datetime(2026, 11, 9).date()
    state = quest_state([])
    advance(state, day)
    items = [(day, key) for key in ("training", "writing", "meditation")]
    evaluate(state, day, items)
    assert "sisyphus" not in state["completed"]
    items.extend([(day, "chess"), (day, "eagle")])
    evaluate(state, day, items)
    assert state["completed"]["sisyphus"] == day.isoformat()
    assert state["completed"]["eagle"] == day.isoformat()
    assert evaluate(state, day, items) == []
    advance(state, day + timedelta(days=250))
    assert state["banks"] == {"daily": 5, "weekly": 3}


@pytest.mark.parametrize(
    "perk, activity, stat, expected",
    [
        ("creative", "writing", "will", 2),
        ("creative", "writing", "creativity", 57),
        ("books", "reading", "intelligence", 4),
        ("sleep", "sleep", "will", 22),
        ("chronic", "training", "strength", 35),
    ],
)
def test_perk_effects(tmp_path, perk, activity, stat, expected):
    path = tmp_path / "events.jsonl"
    for other in {"lazy", "creative", "books", "sleep", "chronic"} - {perk}:
        set_perk(path, other, False, developer_mode=True)
    event = complete(path, catalog()[activity], "2", str(uuid4()))
    assert event["payload"]["xp_by_stat"][stat]["credited_xp"] == expected


def test_old_snapshot_survives_and_next_award_uses_new_curve(tmp_path):
    from liferpg.game import make_event
    from liferpg.journal import append_event

    path = tmp_path / "events.jsonl"
    day = datetime(2026, 11, 9, 4, tzinfo=timezone.utc)
    row = dict(level_before=1, xp_before=0, level_after=25, xp_after=950,
               credited_xp=10250)
    old = make_event(
        [], str(uuid4()), "system.legacy_imported", "local-player", day,
        {"history": [], "xp_by_stat": {"will": row}}, "Старое сохранение",
    )
    old["rules_version"] = 3
    append_event(path, old)
    will = project(read_events(path))[0].attribute("will")
    assert (will.level, will.current_xp) == (25, 950)
    event = complete(path, catalog()["meditation"], "2", str(uuid4()), day)
    assert event["rules_version"] == 5
    will = project(read_events(path))[0].attribute("will")
    assert (will.level, will.current_xp) == (26, 0)


def test_weekly_quest_uses_monday_at_four(tmp_path):
    path = tmp_path / "events.jsonl"
    activity = catalog()["eagle"]
    before = datetime(2026, 11, 9, 3, 59, tzinfo=timezone.utc)
    complete(path, activity, "2", str(uuid4()), before)
    complete(path, activity, "2", str(uuid4()), before + timedelta(minutes=1))
    events = read_events(path)
    periods = [
        effect["period"]
        for event in events
        for effect in event["payload"].get("consequences", ())
        if effect["type"] == "quest.period_completed"
    ]
    assert periods == ["2026-11-02", "2026-11-09"]
    assert quest_state(events)["streaks"]["eagle"]["duration"] == 2
