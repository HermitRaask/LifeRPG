from datetime import datetime, timezone
from uuid import uuid4
import json

from liferpg.domain.activities import catalog
from liferpg.domain.activity_xp import completion_xp
from liferpg.domain.perks import active_perks, effects_for
from liferpg.game import complete, project, set_perk
from liferpg.journal import read_events


def test_llm_default_effect_and_explicit_disable(tmp_path):
    assert "llm" in active_perks([])[0]
    effects = effects_for("project", {"intelligence": 100}, {"llm"})
    assert completion_xp({"intelligence": 100}, "2", 1, effects) == {
        "intelligence": 75,
    }
    assert effects_for("reading", {"intelligence": 100}, {"llm"}) == []
    path = tmp_path / "events.jsonl"
    set_perk(path, "llm", False, developer_mode=True)
    assert "llm" not in active_perks(read_events(path))[0]


def test_lazy_quarter_bonus_and_recorded_legacy_replay(tmp_path):
    effects = effects_for("meditation", {"will": 100}, {"lazy"})
    assert completion_xp({"will": 100}, "2", 1, effects) == {"will": 125}
    path = tmp_path / "events.jsonl"
    moment = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    operation = str(uuid4())
    complete(path, catalog()["meditation"], "2", operation, moment)
    events = read_events(path)
    for event in events:
        event["rules_version"] = 5
        config = event["payload"].get("configuration_snapshot")
        if config:
            config["version"] = 5
            config.pop("perk_rules_version", None)
    row = events[-1]["payload"]["xp_by_stat"]["will"]
    row["credited_xp"] = row["calculated_xp"] = row["xp_after"] = 75
    path.write_text("".join(json.dumps(e) + "\n" for e in events),
                    encoding="utf8")
    restored = read_events(path)
    assert project(restored)[0].attribute("will").current_xp == 75
    assert complete(path, catalog()["meditation"], "2", operation,
                    moment)["payload"]["xp_by_stat"]["will"]["xp_after"] == 75
    next_event = complete(path, catalog()["meditation"], "2",
                          str(uuid4()), moment)
    row = next_event["payload"]["xp_by_stat"]["will"]
    progress = (row["credited_xp"], row["level_after"], row["xp_after"])
    assert progress == (62, 2, 37)
    assert next_event["rules_version"] == 7


def test_creative_only_penalizes_writing_will():
    from liferpg.domain.perks import describe_perk
    base = {"will": 100, "creativity": 100}
    effects = effects_for("writing", base, {"creative"})
    assert completion_xp(base, "2", 1, effects) == {
        "will": 50, "creativity": 100,
    }
    assert effects_for("project", base, {"creative"}) == []
    assert effects_for("writing", base, set()) == []
    description = describe_perk("creative", catalog())
    assert "Работа над текстом" in description
    assert "Творчество" not in description


def test_creative_legacy_bonus_is_replayed_without_recalculation(tmp_path):
    path = tmp_path / "events.jsonl"
    moment = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    for perk in ("lazy", "chronic", "sleep", "books", "llm"):
        set_perk(path, perk, False, developer_mode=True, local=moment)
    operation = str(uuid4())
    complete(path, catalog()["writing"], "2", operation, moment)
    events = read_events(path)
    old = events[-1]
    old["rules_version"] = 6
    from liferpg.domain.configuration import configuration
    old["payload"]["configuration_snapshot"] = {
        **configuration(events), "version": 6, "perk_rules_version": 2,
    }
    row = old["payload"]["xp_by_stat"]["creativity"]
    row.update(credited_xp=57, calculated_xp=57, xp_after=57)
    old["payload"]["effect_factors"].append({
        "perk": "creative", "stat": "creativity", "factor": "23/20",
    })
    path.write_text("".join(json.dumps(e) + "\n" for e in events),
                    encoding="utf-8")
    restored = project(read_events(path))[0]
    assert restored.attribute("creativity").current_xp == 57
    assert complete(path, catalog()["writing"], "2", operation, moment) == old
    new = complete(path, catalog()["writing"], "2", str(uuid4()), moment)
    assert new["rules_version"] == 7
    row = new["payload"]["xp_by_stat"]["creativity"]
    result = (row["credited_xp"], row["level_after"], row["xp_after"])
    assert result == (50, 2, 7)
    assert read_events(path)[-2] == old
