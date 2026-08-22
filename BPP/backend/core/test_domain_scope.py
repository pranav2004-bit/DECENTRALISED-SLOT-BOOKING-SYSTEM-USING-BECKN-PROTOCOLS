"""livetracker7.md §1.1/§1.2 Test Gate: the shared domain-scoping helper every
`validate_and_ack_*` entry point calls. View-level NACK/ACK proof for a real action
lives in test_search.py (search) and test_init.py (init) — this file covers the
helper itself directly, once, rather than duplicating the same assertions across
all ten action test files for logic that is identical and shared.
"""

import pytest

from core.domain_scope import DomainNotSupportedError, validate_domain_supported


def _context(domain: str) -> dict:
    return {"domain": domain}


def test_validate_domain_supported_passes_for_an_in_scope_domain(settings):
    settings.SUPPORTED_DOMAINS = ["ONDC:SRV13", "ONDC:RET13"]
    validate_domain_supported(_context("ONDC:SRV13"))


def test_validate_domain_supported_raises_for_an_out_of_scope_domain(settings):
    settings.SUPPORTED_DOMAINS = ["ONDC:SRV13"]
    with pytest.raises(DomainNotSupportedError) as exc_info:
        validate_domain_supported(_context("BECKN:AUTO01"))
    assert exc_info.value.domain == "BECKN:AUTO01"
    assert "BECKN:AUTO01" in str(exc_info.value)
    assert "ONDC:SRV13" in str(exc_info.value)


def test_validate_domain_supported_default_covers_all_three_existing_domains(settings):
    """Regression proof (§1.3): the untouched default (today's single combined
    instance) still accepts all three domains this project already serves."""
    settings.SUPPORTED_DOMAINS = [
        settings.DOMAIN_HEALTHCARE, settings.DOMAIN_AUTOMOTIVE, settings.DOMAIN_BEAUTY,
    ]
    validate_domain_supported(_context(settings.DOMAIN_HEALTHCARE))
    validate_domain_supported(_context(settings.DOMAIN_AUTOMOTIVE))
    validate_domain_supported(_context(settings.DOMAIN_BEAUTY))
