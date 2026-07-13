"""Validation Service — stub for Phase 1 (per registry_details_v1.1.md §12). Real payload
validators against the confirmed Subscribe/Lookup schemas (protocol_compliance_notes_v1.1.md
§A.2, §B.3) land in Phase 2.1, using the JSON Schema files under
shared/testing/contract_schemas/ as the source of truth — do not duplicate schema definitions
here, validate against those files.
"""

import json
from pathlib import Path

import jsonschema

SCHEMA_DIR = (
    Path(__file__).resolve().parent.parent.parent / "shared" / "testing" / "contract_schemas"
)


class PayloadValidationError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def validate_against_schema(payload: dict, schema_filename: str) -> None:
    """Raises PayloadValidationError if `payload` doesn't conform to the named schema
    in shared/testing/contract_schemas/. Currently covers subscribe_request.schema.json
    (Phase 2.1); more schemas are added there as Phase 2 confirms them, not here."""
    schema_path = SCHEMA_DIR / schema_filename
    schema = json.loads(schema_path.read_text())
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as exc:
        field = ".".join(str(p) for p in exc.path) or None
        raise PayloadValidationError(exc.message, field=field) from exc
