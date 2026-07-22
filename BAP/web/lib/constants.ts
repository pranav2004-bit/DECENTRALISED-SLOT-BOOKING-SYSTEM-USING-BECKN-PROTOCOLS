/**
 * This project implements exactly one real domain (livetracker2.md §2.2/§3.9) — the
 * ONDC Beauty & Wellness category code, matching BPP's own `DOMAIN_BEAUTY` setting
 * (BPP/backend/bpp/settings.py). Fixed here rather than exposed as a picker field:
 * there is nothing else for a customer to choose between.
 */
export const BEAUTY_DOMAIN = 'ONDC:RET13';
