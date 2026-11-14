from django.core.management.base import BaseCommand, CommandError

from liferpg.journal import journal_lock, read_events
from webgame.services import journal_path, snapshot


class Command(BaseCommand):
    help = "Проверить веб-журнал без изменения состояния."

    def handle(self, *args, **options):
        try:
            with journal_lock(journal_path()):
                events = read_events(journal_path())
                snapshot(events)
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Журнал корректен: {len(events)} событий."
        ))
