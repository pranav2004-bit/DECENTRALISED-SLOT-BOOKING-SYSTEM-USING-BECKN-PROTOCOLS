# Bug Tracker

Real bugs found and fixed during deployment/customization work. Each entry: what broke, why, how it was fixed.

---

## 1. Registry redirected internal HTTP traffic to an unreachable HTTPS URL

**Bug:** Every Registry request except `/health` hung for 30s then got SIGKILLed by gunicorn's worker timeout, once deployed behind Caddy.

**Cause:** `SECURE_SSL_REDIRECT=True` redirected *all* traffic to HTTPS on the same port — including internal container-to-container calls like `http://registry:8000/...`, which have no TLS listener at all. A first-pass fix (`SECURE_PROXY_SSL_HEADER`) only addressed the public-facing symptom, not this internal traffic, since Registry is backend-only and never reached by a browser.

**Fix:** `registry/registry/settings.py` — set `SECURE_SSL_REDIRECT = False` unconditionally. Real TLS enforcement stays entirely at Caddy, the actual edge — matching that no other app in this codebase sets this flag.

---

## 2. Hairpin NAT blocked Registry's own on_subscribe verification call

**Bug:** Registry's `/on_subscribe` verification challenge to a participant's own public HTTPS URL hung when both Registry and the participant were containers on the same EC2 host.

**Cause:** AWS VPC hairpin NAT — a container can't reliably call its own host's public IP and route back in.

**Fix:** `docker-compose.prod.yml` — gave Caddy's service a `networks.default.aliases: ["${PUBLIC_HOSTNAME}"]` entry, so Docker's embedded DNS resolves the public hostname straight to Caddy's internal container IP for every other container on the same network, while real external callers still resolve it normally.

---

## 3. Cross-domain signup/login returned 403 (SameSite fix alone wasn't enough)

**Bug:** Business signup/login on the deployed Vercel frontends (`*.vercel.app`) failed with 403, even after setting `SESSION_COOKIE_SAMESITE`/`CSRF_COOKIE_SAMESITE = "None"`.

**Cause:** Confirmed via a direct browser fetch vs. an identical `curl` call — browsers block the `csrftoken`/`sessionid` cookie as a **third-party cookie** on genuinely cross-site requests (`vercel.app` frontend, `nip.io` backend), independent of `SameSite`. `curl` isn't subject to that policy, so it worked while the real browser didn't.

**Fix:** Added a Next.js rewrite (`/api/:path*` → the real backend) in both `BAP/web/next.config.ts` and `BPP/web/next.config.ts`, and changed `api-client.ts` in both apps to call same-origin (relative path) from the browser. The cookie becomes first-party since the browser only ever talks to its own origin; server-side (RSC) calls still hit the backend directly.

---

## 4. Local dev signup 403'd with a stale CORS/CSRF origin

**Bug:** Signup on `localhost:3000` (BPP-Medical web run locally, outside Docker) 403'd with `Origin checking failed`.

**Cause:** `BPP/backend/.env.medical`'s `CORS_ALLOWED_ORIGINS`/`CSRF_TRUSTED_ORIGINS` only listed `http://localhost:3003` (an old convention), not `http://localhost:3000` (Next.js's actual default dev port).

**Fix:** Added `http://localhost:3000` to both env vars. Note: `docker restart` does **not** reload `env_file` changes — the container must be recreated (`docker rm` + recreate), not just restarted, for an `.env` edit to take effect.
