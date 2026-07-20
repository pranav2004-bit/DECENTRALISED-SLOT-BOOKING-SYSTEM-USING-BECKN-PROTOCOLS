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

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(
            URLRouter([path("ws/", FoundationConsumer.as_asgi())])
        ),
    }
)
