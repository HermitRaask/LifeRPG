from datetime import datetime, timezone
from uuid import uuid4

import pytest

from liferpg.domain.activities import catalog
from liferpg.domain.perks import active_perks
from liferpg.domain.skills import advance_root, writing_progress
from liferpg.game import complete, make_event, set_perk
from liferpg.journal import append_event, read_events


def test_root_thresholds_and_mastery():
    assert advance_root(1, 0, 105_000) == dict(
        level=15, xp=0, mastered=False
    )
    assert advance_root(1, 0, 120_000) == dict(
        level=15, xp=15_000, mastered=True
    )
    assert not advance_root(15, 0, 14_999)["mastered"]
    assert advance_root(15, 14_999, 1)["mastered"]
    assert advance_root(15, 15_000, 50, True)["xp"] == 15_050
    assert advance_root(14, 13_975, 50) == dict(
        level=15, xp=25, mastered=False
    )


def test_skill_award_preserves_legacy_xp_and_deduplicates_mastery(tmp_path):
    path = tmp_path / "events.jsonl"
    moment = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    old = make_event(
        [], str(uuid4()), "subskill.leveled_up", "story", moment,
        {"writing_progress": dict(level=15, xp=14_975, mastered=False),
         "level": 2, "xp": 10},
        "Исторический поднавык",
    )
    append_event(path, old)
    activity = catalog()["writing"]
    operation = str(uuid4())
    first = complete(path, activity, "2", operation, moment)
    assert complete(path, activity, "2", operation, moment) == first
    complete(path, activity, "2", str(uuid4()), moment)
    events = read_events(path)
    assert events[0] == old
    assert writing_progress(events) == dict(
        level=15, xp=15_075, mastered=True
    )
    assert sum(
        effect["type"] == "skill.mastered"
        for event in events
        for effect in event["payload"].get("consequences", ())
    ) == 1


def test_perk_handler_requires_developer_mode(tmp_path):
    path = tmp_path / "events.jsonl"
    assert "lazy" in active_perks([])[0]
    with pytest.raises(ValueError, match="режиме разработчика"):
        set_perk(path, "lazy", False)
    assert not path.exists()
    set_perk(path, "lazy", False, developer_mode=True)
    assert "lazy" not in active_perks(read_events(path))[0]
