# Generated for the real-gap fix found live 2026-11-05 — see SearchSession.confirmed_order_at's
# own docstring (models.py), confirm_service.record_on_confirm_result, select_service.
# _count_in_flight_holds, and reconciliation.sweep_unpaid_confirmed_bookings.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_searchsession_selected_order_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchsession",
            name="confirmed_order_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
