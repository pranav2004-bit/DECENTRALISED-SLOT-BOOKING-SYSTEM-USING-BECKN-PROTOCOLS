/**
 * livetracker3.md §6.1: the real, deploy-time-fixed set of domains this project
 * supports, mirroring BPP's own DOMAIN_BEAUTY/DOMAIN_HEALTHCARE/DOMAIN_AUTOMOTIVE
 * settings constants (BPP/backend/bpp/settings.py:78-80). No endpoint anywhere in
 * this codebase exposes a runtime list of supported domains, and every other
 * domain-aware surface already treats the domain set as deploy-time-fixed, not
 * runtime-discoverable (e.g. BPP's business_signup_view validates domain_code
 * against code-registered adapters, not a live list) — so this is a small, explicit
 * frontend list, not a dynamically-fetched one, accepting the same manual-sync-on-
 * deploy tradeoff every other domain-aware part of this project already accepts.
 */
export interface DomainOption {
  code: string;
  label: string;
}

export const SEARCH_DOMAINS: DomainOption[] = [
  { code: 'ONDC:RET13', label: 'Beauty & Wellness' },
  { code: 'ONDC:SRV13', label: 'Healthcare' },
  { code: 'BECKN:AUTO01', label: 'Automotive' },
];
