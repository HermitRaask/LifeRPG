import pytest

from liferpg.application import correct_activity, record_activity
from liferpg.domain.activities import ActivityLog, catalog
from liferpg.storage import load_log


@pytest.mark.parametrize("operation", ["record", "correct", "cancel"])
def test_failed_save_keeps_original_history(operation):
    activities = catalog()
    log = ActivityLog()
    log.record(activities["training"])
    original = log.events

    def fail_save(updated):
        assert len(updated.events) == 2
        raise OSError("Файл занят")

    with pytest.raises(OSError, match="Файл занят"):
        if operation == "record":
            record_activity(log, activities["reading"], fail_save)
        else:
            replacement = None
            if operation == "correct":
                replacement = activities["reading"]
            correct_activity(log, 1, replacement, fail_save)

    assert log.events == original
    assert log.summary() == (1, 60)


def test_load_existing_version_one_history(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        '''
        {"version": 1, "events": [
          {"number": 1, "activity": "training", "xp": {"strength": 47},
           "correction": false},
          {"number": 2, "activity": "training", "xp": {"strength": 105},
           "correction": false},
          {"number": 1, "activity": "reading", "xp": {"intelligence": 3},
           "correction": true}
        ]}
        ''',
        encoding="utf-8",
    )

    log = load_log(path, catalog())

    assert [event.number for event in log.events] == [1, 2, 1]
    assert log.events[0].xp == {"strength": 47}
    assert log.history()[0].activity.key == "reading"
    assert log.summary() == (2, 108)
    strength = log.character().attribute("strength")
    assert (strength.level, strength.current_xp) == (2, 5)
