import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Writes a consistent online backup of the SQLite database to var/backups and prunes old copies."

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=14, help="Number of most recent backups to keep.")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        if "sqlite3" not in db["ENGINE"]:
            raise CommandError("This command handles SQLite only. Use pg_dump for PostgreSQL.")
        target_dir = Path(settings.VAR_DIR) / "backups"
        target_dir.mkdir(exist_ok=True)
        target = target_dir / f"encelis-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        source = sqlite3.connect(str(db["NAME"]))
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        backups = sorted(target_dir.glob("encelis-*.sqlite3"))
        for old in backups[: -options["keep"]] if options["keep"] > 0 else []:
            old.unlink()
        self.stdout.write(self.style.SUCCESS(f"Backup written: {target}"))
