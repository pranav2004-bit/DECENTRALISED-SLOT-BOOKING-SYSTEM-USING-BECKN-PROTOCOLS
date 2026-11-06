"""India Post's own administrative hierarchy for pincodes — Circle (a state/UT-level
postal territory) contains Divisions, and each Division contains the individual
Pincodes routed through it. Seeded once via `seed_postal_directory` from a real
India Post pincode export (a PDF, extracted to CSV); not user-editable at runtime.

Lives under `shared/`, not inside one project's own app, because both BAP and BPP
need the same reference data (BPP: a business's own postal location; BAP: filtering
by state/division/pincode) — each project adds `"postal_directory"` to its own
`INSTALLED_APPS` and runs its own migration against its own database, the same
sharing pattern `shared/inventory_core` already established for BPP-only data.

Deliberately 3 levels (Circle -> Division -> Pincode), not 4: the source data also
carries a Region between Circle and Division, but the actual ask here was for
state -> division -> pincode grouping specifically, so Region is left out rather
than modeled and then unused.
"""

from django.db import models


class Circle(models.Model):
    """A state/UT-level postal circle, e.g. "Andhra Pradesh Circle"."""

    name = models.CharField(max_length=255, unique=True)

    def __str__(self) -> str:
        return self.name


class Division(models.Model):
    """One postal division within a Circle, e.g. "Anantapur Division"."""

    circle = models.ForeignKey(Circle, on_delete=models.CASCADE, related_name="divisions")
    name = models.CharField(max_length=255)

    class Meta:
        constraints = [
            # The same division name can recur under a different circle in a genuine
            # all-India dataset — uniqueness is scoped to (circle, name), not name alone.
            models.UniqueConstraint(fields=["circle", "name"], name="unique_division_per_circle")
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.circle.name})"


class Pincode(models.Model):
    """One 6-digit pincode, routed through exactly one Division. A pincode is
    globally unique across all of India Post (not scoped to its division), matching
    the real postal system — two different divisions never share a pincode."""

    division = models.ForeignKey(Division, on_delete=models.CASCADE, related_name="pincodes")
    code = models.CharField(max_length=10, unique=True, db_index=True)

    def __str__(self) -> str:
        return self.code
