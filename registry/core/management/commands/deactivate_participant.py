from django.core.management.base import BaseCommand, CommandError

from core import registry_service


class Command(BaseCommand):
    help = (
        "livetracker7.md §2.3: deactivates one (subscriber_id, domain, participant_type) "
        "Registry subscription row — sets status to UNSUBSCRIBED, excluding it from "
        "Gateway's dispatch_search routing. Used to re-scope a participant to fewer "
        "domains than it originally subscribed for."
    )

    def add_arguments(self, parser):
        parser.add_argument("subscriber_id", help="e.g. bpp-backend.local")
        parser.add_argument("domain", help="ONDC domain code, e.g. ONDC:SRV13")
        parser.add_argument(
            "--type",
            dest="participant_type",
            default="sellerApp",
            choices=["sellerApp", "buyerApp", "gateway"],
            help="Participant type (default: sellerApp, i.e. a BPP).",
        )

    def handle(self, *args, **options):
        try:
            participant = registry_service.deactivate_participant_domain(
                subscriber_id=options["subscriber_id"],
                domain=options["domain"],
                participant_type=options["participant_type"],
            )
        except registry_service.ParticipantNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"{participant.subscriber_id} ({participant.domain}, "
                f"{participant.participant_type}): status={participant.status}"
            )
        )
