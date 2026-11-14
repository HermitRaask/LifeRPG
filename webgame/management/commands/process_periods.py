from django.core.management.base import BaseCommand

from webgame.services import refresh


class Command(BaseCommand):
    help = "Обработать пропущенные периоды и восстановить проекции SQLite."

    def handle(self, *args, **options):
        events, _ = refresh(advance=True)
        self.stdout.write(self.style.SUCCESS(
            f"Периоды обработаны. Применено событий: {len(events)}"
        ))
