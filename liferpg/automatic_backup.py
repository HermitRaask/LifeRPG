"""Validated atomic startup snapshot in the existing JSONL format."""
import os
from pathlib import Path
from uuid import uuid4

from liferpg.journal import journal_lock, read_events


def startup_backup(journal):
    journal = Path(journal)
    destination = journal.parent / "automatic" / "profile.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{uuid4().hex}.jsonl")
    try:
        with journal_lock(journal):
            read_events(journal)
            data = journal.read_bytes() if journal.exists() else b""
            with temporary.open("xb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            read_events(temporary)
            os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
