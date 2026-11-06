# Generated for the real-gap fix found live 2026-11-05 — see SearchSession.selected_order_at's
# own docstring (models.py) and _count_in_flight_holds's (select_service.py).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_paymenttransaction"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchsession",
            name="selected_order_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
