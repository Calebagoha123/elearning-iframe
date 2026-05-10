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

Student activity URLs must be signed. For local testing, create the signature as:

```python
import hmac, hashlib
import json
secret = b"change-this-iframe-secret"
message = json.dumps(
    {"course_id": "course-a", "participant_id": "student-1"},
    separators=(",", ":"),
    sort_keys=True,
).encode()
print(hmac.new(secret, message, hashlib.sha256).hexdigest())
```

Then open:

```text
/iframe/?course_id=course-a&participant_id=student-1&sig=<signature>
```

## MVP Scope

- Signed iframe identity capture.
- Relationship survey with dropdown plus Other.
- Housing entry form with multiple entries per participant.
- Locked aggregate visualizations until five cohort participants exist.
- Researcher password gate, dashboard counts, and CSV export.

Deferred to later phases: opt-in research consent flow, deletion request workflow, XLSX export, production GDPR process, and deployment hardening.
