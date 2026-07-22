"""Regression test for a real gap found live during §3.9's own browser
verification: `CORS_ALLOWED_ORIGINS` was read into settings since Phase 2.1 but
`django-cors-headers` itself was never installed/wired into INSTALLED_APPS/
MIDDLEWARE, so the setting was inert — every cross-origin call from BAP/web
(a genuinely different origin, localhost:3000 vs localhost:8001) was silently
blocked by the browser's own CORS enforcement, confirmed live via a real
`net::ERR_FAILED` on `POST /api/v1/search` before this fix. No prior phase's
tests caught this because pytest's Django test client never enforces or checks
CORS — this failure mode only exists from a real browser's perspective."""

from django.test import Client, TestCase


class CorsHeadersTests(TestCase):
    def test_allowed_origin_gets_cors_header_on_a_real_endpoint(self):
        client = Client()
        response = client.options(
            "/api/v1/search",
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:3000")

    def test_disallowed_origin_gets_no_cors_header(self):
        client = Client()
        response = client.options(
            "/api/v1/search",
            HTTP_ORIGIN="http://evil.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_idempotency_key_header_is_allowed_for_the_confirm_preflight(self):
        """A second real gap found the same way, one layer deeper: django-cors-headers'
        own DEFAULT_HEADERS doesn't include this project's custom `Idempotency-Key`
        request header (§3.6), so even with CORS wired up, the browser's preflight for
        `POST /api/v1/confirm` still refused to grant it — confirmed live via a real
        `net::ERR_FAILED` on that specific POST (the OPTIONS itself still returned 200)."""
        client = Client()
        response = client.options(
            "/api/v1/confirm",
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,idempotency-key",
        )
        self.assertIn("idempotency-key", response["Access-Control-Allow-Headers"])
