"""Apply changes to the activity history and save the result."""


def record_activity(log, activity, save):
    """Return the updated history after recording an activity."""
    updated = log.copy()
    updated.record(activity)
    save(updated)
    return updated


def correct_activity(log, number, activity, save):
    """Correct a completion, or cancel it when activity is None."""
    updated = log.copy()
    updated.correct(number, activity)
    save(updated)
    return updated
