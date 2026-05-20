"""Lightweight middleware. Keep it scoped to things Django doesn't ship."""
from django.conf import settings


class ContentSecurityPolicyMiddleware:
    """Add a minimal Content-Security-Policy header to every response.

    The MVP only needs ``frame-ancestors`` configured so Moodle (or another LMS)
    can embed the iframe while preventing arbitrary third parties from doing so.
    The list of allowed parent origins comes from ``CSP_FRAME_ANCESTORS`` in
    settings (set via ``DJANGO_CSP_FRAME_ANCESTORS`` env var as a comma-
    separated list, e.g. ``https://moodle.example.edu``).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        ancestors = getattr(settings, "CSP_FRAME_ANCESTORS", ["'self'"]) or ["'self'"]
        self.header_value = f"frame-ancestors {' '.join(ancestors)}"

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.header_value)
        return response
