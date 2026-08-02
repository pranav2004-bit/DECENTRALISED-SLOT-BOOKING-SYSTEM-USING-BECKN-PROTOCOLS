import logging
import os
import signal

from django.core.management.base import BaseCommand
from event_bus.worker import run_worker

from core.events import get_event_bus
from core.events_worker import DISPATCH

logger = logging.getLogger("bpp")


class Command(BaseCommand):
    help = (
        "Runs BPP's real, independent event-consumer worker (livetracker4.md §2.1) — "
        "a long-lived process draining core.events.get_event_bus()'s real queue, "
        "genuinely separate from the request-handling process. Stop with SIGTERM/SIGINT "
        "(docker stop's default signal) for a clean exit after the in-flight event finishes."
    )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(f"run_event_worker starting (pid={os.getpid()})")
        )
        stop = {"requested": False}

        def _request_stop(signum, _frame):
            self.stdout.write(
                f"run_event_worker (pid={os.getpid()}): received signal {signum}, stopping…"
            )
            stop["requested"] = True

        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

        bus = get_event_bus()
        run_worker(
            bus, DISPATCH, should_stop=lambda: stop["requested"], on_heartbeat=bus.write_heartbeat
        )
        self.stdout.write(
            self.style.SUCCESS(f"run_event_worker (pid={os.getpid()}): stopped cleanly")
        )
