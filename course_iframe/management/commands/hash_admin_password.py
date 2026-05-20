import getpass

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Generate a hashed value for RESEARCH_ADMIN_PASSWORD_HASH. "
        "Run interactively; paste the output into your production env. "
        "Use --password to pass the plaintext non-interactively (e.g. from a secrets manager)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=None,
            help="Plaintext password to hash. If omitted, prompts interactively.",
        )

    def handle(self, *args, **options):
        password = options["password"]
        if not password:
            password = getpass.getpass("Researcher password: ")
            confirm = getpass.getpass("Confirm: ")
            if password != confirm:
                self.stderr.write(self.style.ERROR("Passwords did not match."))
                return
        if not password:
            self.stderr.write(self.style.ERROR("Password cannot be empty."))
            return
        hashed = make_password(password)
        self.stdout.write(self.style.SUCCESS("Set this env var on the server:"))
        self.stdout.write(f"RESEARCH_ADMIN_PASSWORD_HASH={hashed}")
        self.stdout.write(
            self.style.WARNING(
                "Once the hash is in place, you can unset RESEARCH_ADMIN_PASSWORD."
            )
        )
