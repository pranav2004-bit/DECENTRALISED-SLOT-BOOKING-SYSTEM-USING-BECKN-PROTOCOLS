"""
ASGI config for bpp project.

Routes HTTP to Django's normal ASGI handler (all existing views/middleware unchanged) and
`/ws/` to the real-time foundation consumer (livetracker2.md §2.4) — see
shared/realtime/consumers.py for why this is shared with BAP and deliberately transport-only.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bpp.settings")

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402
from django.urls import path  # noqa: E402

# Must be created before importing anything that touches Django models/apps (e.g. the
# consumer below) — this is Channels' own documented ordering, not incidental.
django_asgi_app = get_asgi_application()

from realtime.consumers import FoundationConsumer  # noqa: E402

from core.consumers import BusinessOrdersConsumer, ResourceAvailabilityConsumer  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(
            URLRouter(
                [
                    path("ws/", FoundationConsumer.as_asgi()),
                    # Phase 4.4 (livetracker2.md §4.4): BPP-only live availability push,
                    # see core/consumers.py's module docstring for why this isn't shared
                    # with BAP's identical-looking FoundationConsumer route above.
                    path(
                        "ws/resources/<uuid:resource_id>/availability",
                        ResourceAvailabilityConsumer.as_asgi(),
                    ),
                    # livetracker6.md §2.2: no resource_id in the route — this consumer
                    # resolves its own connecting account's real resource set itself.
                    path("ws/business/orders/", BusinessOrdersConsumer.as_asgi()),
                ]
            )
        ),
    }
)
