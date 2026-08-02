from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime
from inventory_core.events import process_event
from inventory_core.replay import replay_events

from core.metrics import booking_lifecycle_consumer, hold_created_consumer
from core.realtime import broadcast_slot_update_consumer

# livetracker4.md §2.2: deliberately a small, explicit registry of individually
# replayable consumers, not the full combined multi-consumer core.events_worker.
# DISPATCH — replaying "audit_log" would re-create a duplicate
# BookingAuditLogEntry every run (it isn't itself idempotent on repeated calls;
# its live, once-only correctness comes from only ever being called once per
# genuine transition, a property replay would break), and audit_log is also the
# very *source* replay reads from, not something meaningfully "caught up" from
# it. This registry only exposes the consumers that make sense to independently
# recover: metrics (Redis counters) and the WebSocket broadcast.
REPLAYABLE_CONSUMERS = {
    "hold_created_metrics": hold_created_consumer,
    "booking_lifecycle_metrics": booking_lifecycle_consumer,
    "websocket_broadcast": broadcast_slot_update_consumer,
}


class Command(BaseCommand):
    help = (
        "Re-drives real BookingAuditLogEntry rows in a time range through a real, "
        "individually-named consumer (livetracker4.md §2.2) — for recovering a "
        "consumer that was down, or a newly-registered one, not a general resend."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--start", required=True, help="ISO 8601 datetime, e.g. 2026-08-02T00:00:00Z"
        )
        parser.add_argument(
            "--end", required=True, help="ISO 8601 datetime, e.g. 2026-08-02T01:00:00Z"
        )
        parser.add_argument(
            "--consumer",
            required=True,
            choices=sorted(REPLAYABLE_CONSUMERS),
            help="Which real consumer to re-drive events through",
        )
        parser.add_argument("--booking-id", default=None, help="Scope to one booking (optional)")

    def handle(self, *args, **options):
        start = parse_datetime(options["start"])
        end = parse_datetime(options["end"])
        if start is None or end is None:
            raise CommandError("--start/--end must be real ISO 8601 datetimes")

        raw_handler = REPLAYABLE_CONSUMERS[options["consumer"]]

        def idempotent_handler(event):
            process_event(event, raw_handler)

        count = replay_events(
            start=start,
            end=end,
            booking_id=options["booking_id"],
            handler=idempotent_handler,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"replay_events: replayed {count} entr{'y' if count == 1 else 'ies'} "
                f"through {options['consumer']!r}"
            )
        )
