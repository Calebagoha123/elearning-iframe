# E-Learning iframe research app

This is the Django application used for Moodle-embedded research activities. It
collects pseudonymous student responses for:

- a relationship and income-comparison survey
- a housing data-entry activity with maps/place validation
- a student summary page showing the student's own responses alongside cohort
  plots

The app is already deployed on the Hetzner server. The production server keeps
its real passwords and secrets in `.env.production`; that file is intentionally
not committed to Git.

## Start Here

Use the project URL you were given for the live site.

Important paths:

| Page | Path | Used by |
| --- | --- | --- |
| Researcher login | `/research/login/` | Research team |
| Research dashboard | `/research/dashboard/` | Research team |
| Relationship iframe | `/iframe/relationship/` | Moodle activity |
| Housing iframe | `/iframe/housing/` | Moodle activity |
| Student summary iframe | `/iframe/summary/` | Moodle activity |
| Health check | `/healthz` | Technical support |

The root URL redirects to the researcher login. Django admin also exists at
`/django-admin/`, but it is not the normal research-team workflow. Use the
research dashboard unless technical support has specifically set up Django admin
accounts and given instructions.

## Daily Research Workflow

Students open the activities from Moodle. Each iframe first asks for the Moodle
pseudoname; this is stored as `participant_id` and appears in exports.

Researchers log in at `/research/login/` and use the dashboard to:

- view participant and response counts
- edit relationship-survey wording, relationship options, and income scale
- edit the housing activity fields
- view cohort plots
- export CSV data, optionally filtered by activity and date
- see recent export records

Treat CSV exports as sensitive research data. They include pseudonames and
individual response rows.

### Editing Activities

Editing relationship wording/options or housing fields creates a new active
question version. Existing responses are kept, but they stay attached to the old
version and are not included in the current cohort plots.

This is intentional, but it matters operationally: coordinate wording/schema
changes before live teaching where possible. CSV exports include historical
responses and a `question_version` column.

Changing housing plot axes or relationship display mode does not create a new
question version; those are visualization settings.

## Roles And Responsibilities

Research team can normally:

- log in to the dashboard
- inspect counts and plots
- export CSV files
- edit activity wording/schema after agreeing the change with the project lead

Moodle/LMS administrators need to provide the Moodle origin, for example
`https://moodle.example.edu`, to set
`DJANGO_CSP_FRAME_ANCESTORS`.

## Data, Backups, And Retention

Production uses Postgres in Docker. The `backups` container writes compressed
database dumps to the local Docker volume `postgres_backups`.

Current backup behavior:

- one backup is made when the backups container starts
- another backup is made every 24 hours after that
- local backup files older than 14 days are deleted
- off-server backups are not implemented in this repository

Before relying on this for long-term research data retention, add an off-server
copy destination such as S3, Backblaze B2, or another institutional backup
system. A local Docker volume protects against some mistakes, but not server
loss.

## Production Operations

Run production commands on the Hetzner server from the directory containing
`docker-compose.prod.yml`.

Most commands need this full compose prefix:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml
```

Leaving out `--env-file .env.production` can make Compose fail before containers
start, because the database and Caddy services need variables from that file.

### Check Server Health

```bash
curl https://<your-host>/healthz
```

Expected response:

```json
{"status":"ok"}
```

Check containers:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

View web logs:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml logs -f web
```

Useful log events:

- `researcher.login.success`
- `researcher.login.failed`
- `researcher.export.csv`
- `researcher.schema.relationship.updated`
- `researcher.schema.housing.updated`

The current researcher login is a shared password. Logs identify a browser
session, not a named human.

### Deploy Or Restart

The web container runs database migrations and `collectstatic` automatically on
boot.

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

To restart only the web app:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d web
```

### Production Environment Variables

Copy the template only when setting up a new server:

```bash
cp .env.production.example .env.production
```

Important variables:

| Variable | Purpose |
| --- | --- |
| `DJANGO_DEBUG` | `0` in production |
| `DJANGO_SECRET_KEY` | Django signing secret; changing it logs users out |
| `CADDY_SITE_ADDRESS` | Public address Caddy serves; bare hostname gives automatic HTTPS |
| `DJANGO_ALLOWED_HOSTS` | Hostnames/IPs Django may serve |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Public origins with scheme, for example `https://example.edu` |
| `DATABASE_URL` | Postgres connection URL used by Django |
| `POSTGRES_PASSWORD` | Postgres password; must match the password in `DATABASE_URL` |
| `RESEARCH_ADMIN_PASSWORD_HASH` | Researcher password hash generated by `hash_admin_password` |
| `DJANGO_COOKIE_SAMESITE` | Use `None` for cross-origin Moodle iframe deployment |
| `DJANGO_COOKIE_SECURE` | Use `1` for HTTPS iframe deployment |
| `DJANGO_CSP_FRAME_ANCESTORS` | Moodle/LMS origins allowed to embed the app |
| `SENTRY_DSN` | Optional error tracking |
| `NOMINATIM_USER_AGENT` | Optional but recommended contact identifier for OpenStreetMap/Nominatim |

Production startup now refuses the known development default for
`DJANGO_SECRET_KEY`, and refuses a missing or malformed
`RESEARCH_ADMIN_PASSWORD_HASH`.

### Rotate The Researcher Password

Generate a new hash:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml \
  run --rm --no-deps --entrypoint python web \
  manage.py hash_admin_password
```

It prints a value like:

```text
RESEARCH_ADMIN_PASSWORD_HASH=pbkdf2_sha256$...$...$...
```

In `.env.production`, double every `$` as `$$` so Docker Compose does not treat
hash segments as variables:

```text
RESEARCH_ADMIN_PASSWORD_HASH=pbkdf2_sha256$$1000000$$<salt>$$<hash>
```

Then recreate the web container:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d web
```

Confirm the container received the hash intact:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml \
  exec web sh -c 'printf "%s\n" "$RESEARCH_ADMIN_PASSWORD_HASH"'
```

That output should contain single `$` separators inside the container.

### Rotate Other Secrets

Generate long random secrets with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`DJANGO_SECRET_KEY`: changing this invalidates sessions and should be done only
when planned.

`POSTGRES_PASSWORD`: Postgres only applies this automatically when the database
volume is first created. Rotating it on an existing server is a technical
database operation, not a simple `.env.production` edit.

### Restore From Backup

Restoring replaces current production data. Do this only after confirming the
chosen backup and making a fresh copy of current data.

List backup files:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec backups ls -1 /var/backups/db
```

Restore a chosen backup:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T db sh -c \
  "psql -U elearning -d elearning" \
  < <(docker compose --env-file .env.production -f docker-compose.prod.yml exec backups \
    zcat /var/backups/db/elearning-<timestamp>.sql.gz)
```

### Delete A Participant

There is no dashboard button for participant deletion. Technical support can do
it through the Django shell:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec web python manage.py shell
```

Then:

```python
from course_iframe.models import Participant
p = Participant.objects.get(participant_id="<the-pseudoname>")
p.responses.all().delete()
p.delete()
```

Record the request in the project's data-protection log: date, requester, reason,
and what was removed.

### Demo Data

For a local or test database only:

```bash
docker compose exec web python manage.py seed_demo_responses
```

This writes synthetic `demo-*` participants and responses to whichever database
the app is currently configured to use. Do not run it against production unless
the research team explicitly wants synthetic rows in production.

## Local Development

Start local Docker services:

```bash
docker compose up --build
```

Local URLs:

- student iframe entry: `http://127.0.0.1:8000/iframe/housing/`
- researcher login: `http://127.0.0.1:8000/research/login/`

The local researcher password is set in `docker-compose.yml`.

Run tests:

```bash
uv run python manage.py test course_iframe
```

Run Django checks:

```bash
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

## Dependency Updates

Dependabot is configured for weekly dependency PRs. Merge after CI passes.

For an ad hoc update:

```bash
uv lock --upgrade
uv sync
uv run python manage.py test course_iframe
```

The CI audit job uses `pip-audit --strict` against `requirements.txt`.


