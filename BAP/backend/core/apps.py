import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # livetracker4.md §2.4: starts BAP's own real periodic reconciliation
        # loop (stale-confirmation resync) — mirrors BPP's own core/apps.py
        # ready() hook. No-ops under settings.TESTING — see reconciliation.py's
        # own docstring for why.
        from .reconciliation import start_reconciliation_loop

        start_reconciliation_loop()

        # livetracker5.md Phase 2.1: registers the real Razorpay adapter with
        # shared/payment_gateway's registry, matching BPP's own core/apps.py
        # pattern for domain adapters. Skipped gracefully — not a startup crash —
        # when sandbox credentials aren't configured (this tracker's own Rule 4:
        # no real credentials until explicitly authorized; none exist in this
        # codebase or CI today). `payment_service.py`'s existing
        # PAYMENT_GATEWAY_UNAVAILABLE handling (Phase 1.6) already covers the
        # "no adapter registered" case cleanly, so skipping here is a normal,
        # expected state, not a gap.
        self._register_razorpay_adapter()

    def _register_razorpay_adapter(self) -> None:
        from django.conf import settings

        key_id = settings.RAZORPAY_KEY_ID
        key_secret_path = settings.PAYMENT_GATEWAY_API_KEY_PATH
        webhook_secret_path = settings.PAYMENT_GATEWAY_WEBHOOK_SECRET_PATH
        if not (key_id and key_secret_path and webhook_secret_path):
            logger.info(
                "Razorpay adapter not registered: sandbox credentials not configured "
                "(RAZORPAY_KEY_ID / PAYMENT_GATEWAY_API_KEY_PATH / "
                "PAYMENT_GATEWAY_WEBHOOK_SECRET_PATH)."
            )
            return

        from pathlib import Path

        import payment_gateway
        import redis
        from payment_gateway.razorpay_adapter import RazorpayAdapter
        from resilient_http import ResilientHttpClient

        try:
            key_secret = Path(key_secret_path).read_text().strip()
            webhook_secret = Path(webhook_secret_path).read_text().strip()
        except OSError:
            logger.warning(
                "Razorpay adapter not registered: secret file(s) configured but "
                "unreadable at the given paths."
            )
            return

        http_client = ResilientHttpClient(
            timeout_seconds=settings.HTTP_CLIENT_TIMEOUT_MS / 1000,
            max_retries=settings.HTTP_CLIENT_MAX_RETRIES,
            circuit_breaker_threshold=settings.HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD,
            redis_client=redis.Redis.from_url(
                settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
            ),
            circuit_breaker_key="bap-razorpay",
        )
        adapter_kwargs = {
            "key_id": key_id,
            "key_secret": key_secret,
            "webhook_secret": webhook_secret,
            "http_client": http_client,
        }
        if settings.PAYMENT_GATEWAY_BASE_URL:
            adapter_kwargs["base_url"] = settings.PAYMENT_GATEWAY_BASE_URL
        payment_gateway.register_adapter("razorpay", RazorpayAdapter(**adapter_kwargs))
        logger.info("Razorpay adapter registered.")
