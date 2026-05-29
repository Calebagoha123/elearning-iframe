# E-Learning Course iFrame

Django/PostgreSQL application backing the Moodle-embedded relationship survey
and housing data-entry activities described in the design + technical spec.

---

## Local development

```bash
docker compose up --build
```

- Student iframe entry: `http://127.0.0.1:8000/iframe/housing/`
- Researcher login: `http://127.0.0.1:8000/research/login/` (password from `docker-compose.yml`)

For HTTPS iframe deployment behind a reverse proxy, set
`DJANGO_COOKIE_SAMESITE=None` and `DJANGO_COOKIE_SECURE=1`.

### Student iframe URLs

```
/iframe/relationship/
/iframe/housing/
/iframe/summary/
```

Each URL first asks the student to enter their Moodle pseudoname. The two
activity URLs then carry them through the relevant form. `/iframe/summary/`
opens a standalone, read-only overview of that student's own responses to both
activities — the relationship stacked bar and the housing scatter — plotted
alongside the cohort. The summary is also linked from the in-app activities
landing page (`/activities/`).

### Legacy signed entry point

The older signed entry point (`/iframe/?participant_id=...&sig=...`) still
works. Generate a signature locally with:

```python
import hmac, hashlib, json
secret = b"change-this-iframe-secret"
message = json.dumps(
    {"participant_id": "student-1"},
    separators=(",", ":"),
    sort_keys=True,
).encode()
print(hmac.new(secret, message, hashlib.sha256).hexdigest())
```

---

## Production deployment

The production compose file (`docker-compose.prod.yml`) brings up four
services: `web` (Django + gunicorn), `db` (Postgres 16), `caddy` (auto-TLS
reverse proxy + static file serving), and `backups` (nightly local `pg_dump`
with 14-day retention to a Docker volume).

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

The web container runs migrations and `collectstatic` automatically on boot
via `docker-entrypoint.sh`, then serves with gunicorn. Caddy fetches a TLS
certificate from Let's Encrypt for the hostname in `Caddyfile`.

### Required production env vars (`.env.production`)

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django cryptographic key (rotate to invalidate sessions) |
| `CADDY_SITE_ADDRESS` | Public HTTPS site address for Caddy/Let's Encrypt, e.g. `elearning.example.edu` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated list including your public hostname |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated, e.g. `https://elearning.example.edu` |
| `DJANGO_COOKIE_SAMESITE` | `None` for cross-origin iframe embedding |
| `DJANGO_COOKIE_SECURE` | `1` for HTTPS |
| `DATABASE_URL` | `postgres://elearning:<pw>@db:5432/elearning` |
| `POSTGRES_PASSWORD` | Read by the `db` and `backups` services |
| `IFRAME_SIGNING_SECRET` | HMAC secret for legacy signed iframe entry |
| `RESEARCH_ADMIN_PASSWORD_HASH` | Generated with `manage.py hash_admin_password` (see below) |
| `DJANGO_CSP_FRAME_ANCESTORS` | Required in production. Space- or comma-separated Moodle/LMS origins, e.g. `https://moodle.example.edu` |
| `DJANGO_REQUIRE_CSP_FRAME_ANCESTORS` | Optional. Defaults to `1` when `DJANGO_DEBUG=0`; set `0` for non-production test servers before you know the Moodle origin |
| `SENTRY_DSN` (optional) | Enable Sentry error tracking by setting this |
| `NOMINATIM_USER_AGENT` | Polite identifier including contact email |

---

## Operations runbook

Common admin tasks. Run all commands from the host that has the compose file
unless noted otherwise.

### Health checks

```bash
# Liveness — should return {"status":"ok"} from inside or outside the network.
curl https://<your-host>/healthz

# View container health (uses the Dockerfile HEALTHCHECK).
docker compose -f docker-compose.prod.yml ps
```

### Rotate the researcher password

The shared password is stored as a **hash** in `RESEARCH_ADMIN_PASSWORD_HASH`.
The plaintext only ever lives in your password manager.

```bash
# Generate a new hash interactively (prompts twice for the new password):
docker compose -f docker-compose.prod.yml run --rm web \
    python manage.py hash_admin_password

# Paste the printed hash into .env.production as RESEARCH_ADMIN_PASSWORD_HASH,
# then restart the web container:
docker compose -f docker-compose.prod.yml up -d web
```

### Rotate the iframe signing secret

Changing `IFRAME_SIGNING_SECRET` invalidates all legacy signed iframe links.
Tell Moodle admins beforehand if they're using legacy URLs.

```bash
# Generate a new secret:
python -c "import secrets; print(secrets.token_urlsafe(48))"

# Update .env.production and restart:
docker compose -f docker-compose.prod.yml up -d web
```

### Apply database migrations manually

Migrations run automatically on container start, but to re-run on demand:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py migrate
```

### Restore from a backup

Backups land in the local `postgres_backups` Docker volume as nightly
`elearning-<timestamp>.sql.gz` files (retained 14 days).

```bash
# List available backups:
docker compose -f docker-compose.prod.yml exec backups ls -1 /var/backups/db

# Restore a specific dump (replaces current data):
docker compose -f docker-compose.prod.yml exec -T db sh -c \
    "psql -U elearning -d elearning" \
    < <(docker compose -f docker-compose.prod.yml exec backups \
        zcat /var/backups/db/elearning-<timestamp>.sql.gz)
```

Before relying on this in production, pick an off-host destination (S3, B2, or
an `rclone` remote) and copy the freshly written `$$target` there from the
`backups` service, for example:

```sh
rclone copy "$$target" "$${BACKUP_REMOTE}"
```

Use a backup image that includes the chosen CLI and mount its credentials.

### Anonymise / delete a participant on request

Until a researcher UI exists for this, do it from the Django shell:

```bash
docker compose -f docker-compose.prod.yml exec web python manage.py shell <<'PY'
from course_iframe.models import Participant
p = Participant.objects.get(participant_id="<the-pseudoname>")
p.responses.all().delete()          # remove their responses
p.delete()                          # remove the participant row
PY
```

Document the request in your data-protection log (date, who asked, what was
removed) — that's the audit trail.

### View structured logs

The web container logs to stdout in JSON format (in production). Stream them
with:

```bash
docker compose -f docker-compose.prod.yml logs -f web
```

Useful log events to grep for:
- `researcher.login.success` / `researcher.login.failed`
- `researcher.export.csv`
- `researcher.schema.relationship.updated`
- `researcher.schema.housing.updated`

Researcher logs include a per-login-session `actor` id. With the current shared
researcher password this identifies a session, not a human; per-researcher
Django auth accounts remain the path for human-level audit attribution.

### Seed demo responses

To preview visualizations and CSV export in a local/test database, seed
synthetic `demo-*` participants and responses:

```bash
docker compose exec web python manage.py seed_demo_responses
```

By default this replaces prior `demo-*` participants, then creates 36 demo
participants, relationship responses, and 90 housing entries. Adjust the size
with:

```bash
docker compose exec web python manage.py seed_demo_responses \
    --participants 60 \
    --housing-entries 180
```

The command writes to whichever database the app is configured to use. With
`docker-compose.yml`, that is the local Postgres service.

### Update dependencies

Dependabot opens weekly PRs grouped into "django" (Django + psycopg) and
"tooling" (everything else). Merge them after CI passes. The CI dependency
audit runs `pip-audit --strict`, so new known vulnerabilities block the audit
job until the dependency is upgraded or a reviewed suppression is added.

For an ad-hoc bump:

```bash
uv lock --upgrade
uv sync
```

---

## Researcher dashboard

After logging in at `/research/login/`, the dashboard links to per-activity
tools:

- Participant and response counts
- **Edit relationship survey** — question wording, relationship options, and the
  Likert scale
- **Edit housing schema** — add, remove, reorder, and retype the housing fields
- **Plots** — the aggregated visualization for each activity (see below)
- **CSV export** — filterable by activity and date range, with an export log

Editing a question creates a new active version. Existing responses stay tied
to the previous version and are excluded from current cohort plots.

### Plot pages

Each activity has its own plot page, reached from the dashboard:

- **Relationship** — a word cloud plus a stacked bar chart of income comparison
  by relationship type. A "What students see" control chooses the student-facing
  display mode: cohort only, the student's own only, or side-by-side.
- **Housing** — a scatter plot. Its X and Y axes are configured here (a single
  numeric field, or a ratio of two such as rent ÷ m²); the schema editor only
  manages which fields exist.

Both plot pages also have a **respondent overlay** dropdown. Selecting a
pseudoname overlays that one student's responses on the cohort chart so a
researcher can inspect an individual in context. It is a researcher-only
preview — never shown to students, and it does not change the student-facing
display.
