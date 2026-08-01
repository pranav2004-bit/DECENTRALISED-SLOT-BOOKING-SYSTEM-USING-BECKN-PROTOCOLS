/**
 * livetracker3.md §7.1: reuses the exact same domain list §6.1 already decided on
 * (BAP/web/lib/constants.ts's `SEARCH_DOMAINS`) rather than making a second,
 * independent, possibly-drifting decision — ADR-0004 (duplicated, not shared) means
 * this can't be a literal import across apps, so the same {code, label} pairs are
 * repeated here, mirroring BPP's own DOMAIN_BEAUTY/DOMAIN_HEALTHCARE/DOMAIN_AUTOMOTIVE
 * settings constants (BPP/backend/bpp/settings.py:78-80) exactly as §6.1 already does.
 */
export interface DomainOption {
  code: string;
  label: string;
}

export const BUSINESS_DOMAINS: DomainOption[] = [
  { code: 'ONDC:RET13', label: 'Beauty & Wellness' },
  { code: 'ONDC:SRV13', label: 'Healthcare' },
  { code: 'BECKN:AUTO01', label: 'Automotive' },
];

/**
 * Mirrors each domain adapter's own real `RESOURCE_TYPES` tuple
 * (beauty_adapter.py/healthcare_adapter.py/automotive_adapter.py) — the same small,
 * explicit, deploy-time-fixed list precedent as `BUSINESS_DOMAINS` above, not a
 * runtime-discoverable one (no endpoint exposes this either).
 */
export const RESOURCE_TYPES_BY_DOMAIN: Record<string, { value: string; label: string }[]> = {
  'ONDC:RET13': [
    { value: 'stylist', label: 'Stylist' },
    { value: 'chair', label: 'Chair' },
  ],
  'ONDC:SRV13': [{ value: 'doctor', label: 'Doctor' }],
  'BECKN:AUTO01': [
    { value: 'bay', label: 'Bay' },
    { value: 'mechanic', label: 'Mechanic' },
  ],
};
