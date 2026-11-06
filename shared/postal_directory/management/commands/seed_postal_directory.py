import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from postal_directory.models import Circle, Division, Pincode


class Command(BaseCommand):
    help = (
        "One-time seed of the Circle/Division/Pincode hierarchy from a CSV with "
        "'Circle', 'Division' and 'Pincode' columns (e.g. an India Post pincode "
        "export). Idempotent — safe to re-run: existing Circles/Divisions are "
        "reused via get_or_create, and existing Pincodes are left untouched via "
        "bulk_create's ignore_conflicts, never re-inserted or duplicated."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path", help="Path to a CSV file with Circle, Division, Pincode columns"
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        required = {"Circle", "Division", "Pincode"}
        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if not required.issubset(set(reader.fieldnames or [])):
                    raise CommandError(f"CSV must have columns: {sorted(required)}")
                rows = [
                    {
                        "circle": row["Circle"].strip(),
                        "division": row["Division"].strip(),
                        "code": row["Pincode"].strip(),
                    }
                    for row in reader
                    if row.get("Circle", "").strip()
                    and row.get("Division", "").strip()
                    and row.get("Pincode", "").strip()
                ]
        except FileNotFoundError as exc:
            raise CommandError(f"No such file: {csv_path}") from exc

        # One (circle, division) -> the pincodes that fall under it, keeping only
        # the first division seen for a given pincode — a genuine India Post pincode
        # belongs to exactly one division, but the source rows are per-*office*, so
        # the same pincode can repeat under the same division many times.
        division_pincodes: dict[tuple[str, str], set[str]] = {}
        for row in rows:
            key = (row["circle"], row["division"])
            division_pincodes.setdefault(key, set()).add(row["code"])

        created_circles = created_divisions = 0
        with transaction.atomic():
            division_objs: dict[tuple[str, str], Division] = {}
            for (circle_name, division_name) in division_pincodes:
                circle, was_created = Circle.objects.get_or_create(name=circle_name)
                created_circles += was_created
                division, was_created = Division.objects.get_or_create(
                    circle=circle, name=division_name
                )
                created_divisions += was_created
                division_objs[(circle_name, division_name)] = division

            pincode_objs = [
                Pincode(division=division_objs[key], code=code)
                for key, codes in division_pincodes.items()
                for code in codes
            ]
            Pincode.objects.bulk_create(pincode_objs, ignore_conflicts=True)

        total_pincodes = Pincode.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created_circles} circle(s), {created_divisions} division(s), "
                f"{len(pincode_objs)} pincode(s) from {csv_path} "
                f"({total_pincodes} pincodes total now in the table)."
            )
        )
