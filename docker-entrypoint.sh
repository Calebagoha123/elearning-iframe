#!/usr/bin/env sh
set -euo pipefail

# Wait for the database to accept TCP connections before running migrations.
# Cheap (no extra deps): poll the host/port from DATABASE_URL.
python <<'PY'
import os, socket, sys, time
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit(0)
parsed = urlparse(url)
host = parsed.hostname or "localhost"
port = parsed.port or 5432
deadline = time.time() + 30
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            sys.exit(0)
    except OSError:
        time.sleep(0.5)
print(f"Database {host}:{port} not reachable after 30s", file=sys.stderr)
sys.exit(1)
PY

python manage.py migrate --noinput
python manage.py collectstatic --noinput

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --access-logfile - \
    --error-logfile -
