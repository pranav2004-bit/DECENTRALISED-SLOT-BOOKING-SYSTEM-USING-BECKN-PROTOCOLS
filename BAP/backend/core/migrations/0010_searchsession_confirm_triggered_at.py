# Generated for livetracker4.md §2.4

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_searchsession_rating_error_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchsession",
            name="confirm_triggered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
