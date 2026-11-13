import json

import pytest

from activities import ActivityLog, catalog
from storage import load_log, save_log


def test_json_round_trip_restores_links_and_xp_snapshots(tmp_path):
    path = tmp_path / "state.json"
    activities = catalog()
    assert not load_log(path, activities).events
    log = ActivityLog()
    log.record(activities["training"])
    log.record(activities["training"])
    log.correct(2, activities["reading"])
    save_log(path, log)
    restored = load_log(path, activities)
    assert len(restored.events) == 3
    assert restored.history()[1].activity is activities["reading"]
    assert restored.summary() == (2, 64)
    assert restored.character().attribute("strength").current_xp == 50


@pytest.mark.parametrize(
    "content",
    [
        "{",
        '{"version": true, "events": []}',
        json.dumps({
            "version": 1,
            "events": [
                {
                    "number": 1, "activity": "unknown", "xp": {},
                    "correction": False,
                }
            ],
        }),
    ],
)
def test_invalid_json_is_rejected_and_preserved(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_log(path, catalog())
    assert path.read_text(encoding="utf-8") == content


def test_failed_replace_preserves_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    log = ActivityLog()
    save_log(path, log)
    original = path.read_bytes()
    log.record(catalog()["training"])

    def deny_replace(self, target):
        raise PermissionError("Файл занят")

    monkeypatch.setattr(type(path), "replace", deny_replace)
    with pytest.raises(PermissionError):
        save_log(path, log)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
