from django.core.management.base import BaseCommand, CommandError

from core.events import get_event_bus


class Command(BaseCommand):
    help = (
        "Manually requeues one poisoned event from the real DLQ back onto the main "
        "queue for reprocessing (livetracker4.md §2.1's own gap audit — no DLQ "
        "reprocessing tool existed before this). Deliberately requires an explicit "
        "--event-id, not a bulk 'requeue everything' flag: a DLQ entry landed there "
        "for a real reason (see --peek), and blind bulk retry is exactly the "
        "'retrying forever' failure mode the DLQ exists to prevent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--event-id", default=None, help="event_id of the DLQ entry to requeue"
        )
        parser.add_argument(
            "--peek",
            action="store_true",
            help="List DLQ entries (event_id, event_type, error, failed_at) instead of requeuing",
        )
        parser.add_argument(
            "--count", type=int, default=10, help="Max entries to show with --peek (default 10)"
        )

    def handle(self, *args, **options):
        bus = get_event_bus()

        if options["peek"]:
            entries = bus.peek_dlq(count=options["count"])
            if not entries:
                self.stdout.write("DLQ is empty.")
                return
            for entry in entries:
                self.stdout.write(
                    f"{entry['event_id']}  {entry['event_type']}  "
                    f"failed_at={entry['failed_at']}  error={entry['error']}"
                )
            return

        if not options["event_id"]:
            raise CommandError("--event-id is required unless --peek is passed")

        requeued = bus.requeue_from_dlq(options["event_id"])
        if not requeued:
            raise CommandError(
                f"no DLQ entry found with event_id={options['event_id']!r} "
                "(already handled, or a typo — use --peek to list current entries)"
            )
        self.stdout.write(
            self.style.SUCCESS(f"requeued event_id={options['event_id']} back onto the main queue")
        )
