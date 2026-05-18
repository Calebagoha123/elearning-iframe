# E-Learning Course iFrame MVP

Django/PostgreSQL implementation of the functional MVP described in the design and technical docs.

## Local Setup

1. Run the app:

```bash
docker compose up --build
```

2. Open the researcher dashboard at `/research/login/`.

Use `.env.example` as the reference for deployment overrides.

For direct local testing over `http://127.0.0.1`, the default settings use non-secure `Lax`
cookies so the CSRF and session cookies are accepted by the browser. For HTTPS iframe
deployment, set `DJANGO_COOKIE_SAMESITE=None` and `DJANGO_COOKIE_SECURE=1`.

The Moodle-facing activity iframes are shared per activity. Students enter their
Moodle-generated pseudoname at the start; that value is used as `participant_id`
and ties their responses together across both activities. The app is configured
for a single course/cohort; no course identifier is required in the student URLs.

Use these iframe URLs:

```text
/iframe/relationship/
/iframe/housing/
```

The older all-activities signed entry point is still available for compatibility.
For local testing, create the signature as:

```python
import hmac, hashlib
import json
secret = b"change-this-iframe-secret"
message = json.dumps(
    {"participant_id": "student-1"},
    separators=(",", ":"),
    sort_keys=True,
).encode()
print(hmac.new(secret, message, hashlib.sha256).hexdigest())
```

Then open:

```text
/iframe/?participant_id=student-1&sig=<signature>
```

## MVP Scope

- Moodle pseudoname identity capture for separate relationship and housing iframes.
- Relationship survey with dropdown plus Other.
- Configurable 3, 5, or 7 point income-comparison scale for the relationship survey.
- Housing entry form with multiple entries per participant.
- Aggregate visualizations for the active question version.
- Researcher password gate, dashboard counts, cohort plot views, and CSV export.
- Researcher edits create a new active question version; prior responses remain attached
  to the previous version and are not pooled into current cohort plots.

Deferred to later phases: opt-in research consent flow, deletion request workflow, XLSX export, production GDPR process, and deployment hardening.
