from django.core.management.base import BaseCommand

from webgame.services import refresh


class Command(BaseCommand):
    help = "Восстановить текущую SQLite из веб-журнала без пересчёта XP."

    def handle(self, *args, **options):
        events, _ = refresh()
        self.stdout.write(self.style.SUCCESS(
            f"Проекция восстановлена: {len(events)} событий."
        ))
