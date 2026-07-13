"""Validation Service — stub for Phase 1 (per BPP_details_v1.1.md §10). Real payload
validators for provider onboarding and catalog/inventory/transaction workflows land
in the future business-workflow tracker (out of scope for this foundation tracker).
"""


class PayloadValidationError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field
