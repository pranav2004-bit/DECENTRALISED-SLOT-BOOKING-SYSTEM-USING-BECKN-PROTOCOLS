# Pending Production Decisions

Real gaps left in `livetracker8.md` (Registry & Gateway production-readiness) that are **not** cloud-provider or infrastructure work — decisions someone has to make, not code to write. Tracked separately here since they don't belong to any one component and don't get resolved by picking a cloud provider.

---

## 1. On-Call Rotation

**What's missing:** nobody gets alerted when Registry or Gateway goes down in real production. `RUNBOOK.md` already flags this as "not yet formalized — foundation stage."

**Decide:** who is on-call and when, and how they get paged (phone alert, Slack, email — pick one that's actually reliable).

**Why it matters:** right now, an outage is only noticed by someone happening to look.

---

## 2. External Security Audit

**What's missing:** only internal, automated checks exist today (dependency scanning, static analysis). No outside party has ever tried to break in.

**Decide:** whether to hire an external security firm for a real penetration test before going live, and when.

**Why it matters:** automated tools catch known patterns; a real attacker (or a paid pentester) finds things tools miss.

---

## 3. Production Admin Workflow

**What's missing:** approving a new company to join the network today means someone runs a raw command in a terminal (`onboarding_approve`). That's fine for a handful of known local participants; it's not obviously fine once real companies you don't personally know are asking to join.

**Decide:** is a terminal command still an acceptable approval process for real production, or does this need actual admin tooling (even a minimal internal screen) before strangers can request to join the network.

---

## 4. Cost / Budget Guardrail

**What's missing:** no budget number exists anywhere. Several of the remaining infrastructure items (KMS/managed secrets, multi-instance HA, a managed Redis provider, an external audit) each cost real money, and none of them have been checked against what's actually affordable.

**Decide:** a real budget ceiling, then confirm each infrastructure choice against it *before* building it — not after.

**Why it matters:** this is the direct check against building more than the project can actually sustain paying for on an ongoing basis.

---

## 5. Edge Protection (WAF / DDoS)

**What's missing:** zero mentions anywhere in the project of protecting Registry/Gateway's public endpoints at the network edge. Once deployed, these become reachable by anyone on the internet, not just this project's own known participants.

**Decide:** rely on the eventual cloud provider's default protections, or add a dedicated layer (e.g. Cloudflare, a cloud-native WAF) on top.

**Why it's separate from picking a cloud provider:** the *decision* to have edge protection at all is independent of *which* provider ends up offering it — worth deciding on purpose rather than defaulting silently to "whatever the provider happens to include."

---

None of these require code changes to close — each one closes when a decision gets made and (where relevant) acted on.
