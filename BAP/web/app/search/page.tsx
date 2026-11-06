'use client';

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { Dropdown } from '@/components/ui/Dropdown';
import { DateStrip, dateToIso } from '@/components/ui/DateStrip';
import { usePageHeader } from '@/components/shell/HeaderContext';
import { ALL_INDIA_STATES, SEARCH_DOMAINS } from '@/lib/constants';
import { usePoll } from '@/lib/usePoll';
import {
  checkAvailability,
  getSearchResults,
  getSelectResult,
  listAvailableSlots,
  triggerSearch,
  triggerSelect,
  type AvailableSlot,
} from '@/lib/booking-api';
import { fetchDivisions, lookupByPincode, type DivisionOption } from '@/lib/postal-api';
import { detectPincode } from '@/lib/geo-api';
import { ApiError } from '@/lib/api-client';
import { formatPrice, formatTime } from '@/lib/format';
import { useRealtimeConnection } from '@/lib/realtime/useRealtimeConnection';
import type { CatalogItem, Provider, SearchResultsResponse } from '@/lib/beckn-types';

interface ProviderGroup {
  provider: Provider;
  items: CatalogItem[];
}

// What to call a provider/item for each domain — purely cosmetic copy (search
// placeholders, empty-state text), not sent anywhere. "Doctor"/"hospital" is
// right for Healthcare but wrong for Wellness/Automotive, and this same page
// renders all three, so it's keyed by domain rather than hardcoded once.
const DOMAIN_NOUNS: Record<string, { provider: string; item: string }> = {
  'ONDC:SRV13': { provider: 'hospital', item: 'doctor' },
  'ONDC:RET13': { provider: 'salon', item: 'stylist' },
  'BECKN:AUTO01': { provider: 'garage', item: 'mechanic' },
};
const DEFAULT_NOUNS = { provider: 'provider', item: 'professional' };

// How long a tapped-but-not-yet-confirmed slot stays reserved against a 2nd
// customer (BookMyShow-style seat hold) — deliberately much shorter than the
// platform's own normal RESERVATION_HOLD_TTL_SECONDS default: this window
// only needs to cover "fill in name/email/phone and tap Confirm," not a full
// browse session. Backend-enforced (BAP/backend/core/views.py's
// select_trigger_view clamps/honors this), not just a client-side countdown.
const HOLD_SECONDS = 180;

/** Polls `fetchFn` until `isDone` is true — same shape as select/page.tsx's
 * own `pollUntil` (not shared between the two files only because this one
 * predates introducing a shared lib for it; both do the exact same thing). */
async function pollUntil<T>(
  fetchFn: () => Promise<T>,
  isDone: (result: T) => boolean,
  { intervalMs = 800, maxAttempts = 20 }: { intervalMs?: number; maxAttempts?: number } = {}
): Promise<T> {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const result = await fetchFn();
    if (isDone(result)) return result;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('This is taking longer than expected — please try again.');
}

/** Looks up one named value out of an item's real `tags` (TagGroup[], BPP/backend/
 * core/catalog.py's `_resource_to_tags()`) — e.g. `getTagValue(item, 'specialisation')`.
 * Returns undefined when the item has no tags, or no tag with that code (a non-doctor
 * resource, or a resource whose business never filled in domain_data). */
function getTagValue(item: CatalogItem, code: string): string | undefined {
  for (const group of item.tags ?? []) {
    const tag = group.list.find((t) => t.descriptor.code === code);
    if (tag) return tag.value;
  }
  return undefined;
}

/** "Dr." is a real title, not decoration — only prepended for the healthcare
 * domain's own items (doctors), never a stylist/mechanic, and never doubled
 * when a business already entered it as part of the name themselves. */
function doctorDisplayName(name: string, isDoctorDomain: boolean): string {
  if (!isDoctorDomain || /^dr\.?\s/i.test(name)) return name;
  return `Dr. ${name}`;
}

/** Grouped by provider (hospital), not flattened to individual items — the
 * user picks a provider first, then sees that provider's own items (doctors).
 * Keyed by provider.id in case the same provider somehow appears in more than
 * one BPP's results (merges rather than duplicating the card). */
function groupByProvider(results: SearchResultsResponse | null): ProviderGroup[] {
  if (!results) return [];
  const byId = new Map<string, ProviderGroup>();
  for (const result of results.results) {
    for (const provider of result.catalog.providers) {
      const existing = byId.get(provider.id);
      if (existing) {
        existing.items.push(...provider.items);
      } else {
        byId.set(provider.id, { provider, items: [...provider.items] });
      }
    }
  }
  return [...byId.values()];
}

function SearchPageInner() {
  // Deep-linked from the home page's category tiles (`?domain=`) — falls back to
  // the first real domain when absent or invalid.
  const params = useSearchParams();
  const [domain] = useState(() => {
    const fromUrl = params.get('domain');
    return SEARCH_DOMAINS.some((d) => d.code === fromUrl) ? (fromUrl as string) : SEARCH_DOMAINS[0].code;
  });

  // Pure location browsing (BookMyShow-style): picking a domain tile already shows
  // everything in that domain; picking a division narrows it further. No typed
  // query — this is a browse surface, not a text search.
  const [state, setState] = useState('');
  const [divisions, setDivisions] = useState<DivisionOption[]>([]);
  const [division, setDivision] = useState('');
  const [loadingDivisions, setLoadingDivisions] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [detectError, setDetectError] = useState<string | null>(null);
  // Set by handleDetectLocation just before it calls setState() — once that
  // state change's own division-fetch effect (below) finishes loading the real
  // options, it auto-selects this one and clears it. A ref, not state: it must
  // survive from one effect run into the next without itself triggering a render.
  const pendingDivisionRef = useRef<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [transactionId, setTransactionId] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  // Drill-down state: null shows the provider (hospital) list; set, shows that
  // one provider's own items (doctors). Reset whenever the division changes so
  // a fresh browse never opens on a stale provider's items.
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null);
  // Which day of the doctor-list's date strip is active — resets to today
  // alongside selectedProviderId so a fresh provider never opens on a stale pick.
  const [selectedDate, setSelectedDate] = useState(() => dateToIso(new Date()));
  useEffect(() => {
    setSelectedProviderId(null);
    setSelectedDate(dateToIso(new Date()));
  }, [division]);

  // Header search: one query/open flag shared by both the provider list and
  // the doctor list, since only one of those two screens is ever visible at a
  // time (selectedProviderId picks which). Reset whenever selectedProviderId
  // changes — covers both "picked/left a provider" and "division changed"
  // (which already clears selectedProviderId above) — so a stale query or an
  // open search bar never carries over onto a screen it wasn't typed for.
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  // Which item's info popup is open (null = none). A popup, not a separate page,
  // since it's supplementary detail for a card already on screen.
  const [infoItemId, setInfoItemId] = useState<string | null>(null);
  useEffect(() => {
    setSearchOpen(false);
    setSearchQuery('');
    setInfoItemId(null);
  }, [selectedProviderId]);

  // Per-date availability: which of the selected provider's items genuinely have an
  // open slot on selectedDate — a doctor card only renders once this confirms `true`
  // for it (BPP/backend/core/availability_service.py's own module docstring). Keyed
  // by `${providerId}:${date}` so flipping back to an already-checked date is
  // instant, no refetch. `null` means "not checked yet / no data" — distinct from
  // `{}` (checked, genuinely nothing available), so the loading state never gets
  // confused with a real empty result.
  const [availabilityMap, setAvailabilityMap] = useState<Record<string, boolean> | null>(null);
  const [availabilityLoading, setAvailabilityLoading] = useState(false);
  const [availabilityError, setAvailabilityError] = useState<string | null>(null);
  const [availabilityRetryNonce, setAvailabilityRetryNonce] = useState(0);
  const availabilityCacheRef = useRef<Map<string, Record<string, boolean>>>(new Map());

  // Real bookable time windows per item on selectedDate — shown as slot chips
  // directly on each card (replaces a separate "Book appointment" click-through,
  // matching the BPP dashboard's own availability-calendar preview). Keyed by
  // item id; an item with no entry yet means its slots are still loading.
  // `${itemId}:${date}` cache mirrors availabilityCacheRef's own pattern.
  const [slotsByItem, setSlotsByItem] = useState<Record<string, AvailableSlot[]>>({});
  const slotsCacheRef = useRef<Map<string, AvailableSlot[]>>(new Map());

  // BookMyShow-style selection: tapping a slot immediately attempts a real,
  // short-lived backend hold (not just local UI state) — this is the one
  // real fix for the actual race this whole feature exists to close: without
  // it, two customers could both reach the final Confirm step for the same
  // slot, because nothing server-side was reserved until that last click.
  // `status` tracks where this specific attempt is; `expiresAt` (epoch ms,
  // this browser's own clock throughout, so no clock-skew concern) is only
  // set once the hold is genuinely confirmed held.
  const [selectedSlot, setSelectedSlot] = useState<{
    item: CatalogItem;
    slot: AvailableSlot;
    status: 'holding' | 'held' | 'error';
    expiresAt: number | null;
    errorMessage: string | null;
  } | null>(null);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  // A brief, dismissible explanation shown the instant a customer's own hold
  // lapses and the UI pulls them back to an unselected state — deliberately
  // no visible countdown anywhere (product decision), so this is the one
  // moment the customer is told *why* their selection just disappeared,
  // instead of it silently vanishing.
  const [expiredNotice, setExpiredNotice] = useState(false);
  useEffect(() => {
    if (!expiredNotice) return;
    const timer = setTimeout(() => setExpiredNotice(false), 4000);
    return () => clearTimeout(timer);
  }, [expiredNotice]);
  // Bumped to force a fresh slots refetch (bypassing the cache) after a hold
  // attempt fails or expires — the slot that was just contested must not
  // keep showing stale "available" data from before that happened.
  const [slotsRetryNonce, setSlotsRetryNonce] = useState(0);
  // Identifies the *current* hold attempt so a slower, earlier attempt's
  // response can never overwrite a newer one (the same race-safety pattern
  // every other async effect in this file already uses, just via a plain
  // ref instead of an effect's own cleanup flag, since this is triggered by
  // a click handler, not an effect).
  const holdAttemptRef = useRef(0);

  function invalidateSlotsCache() {
    slotsCacheRef.current.clear();
    setSlotsRetryNonce((n) => n + 1);
  }

  async function handleSlotTap(item: CatalogItem, slot: AvailableSlot) {
    if (!transactionId) return;
    // Defense in depth — the slot button is already `disabled` for this case
    // (see its own className logic below), so this only guards a stale closure
    // calling in after a slot flips to booked out from under it.
    if (slot.status === 'booked') return;
    if (selectedSlot?.item.id === item.id && selectedSlot.slot.start_time === slot.start_time) {
      // Tapping the already-selected slot again deselects it. The real hold
      // isn't explicitly released by this (no such "give it back early" call
      // exists) — it simply lapses on its own, within HOLD_SECONDS, same as
      // any other abandoned hold. Short enough that this is an acceptable,
      // deliberate tradeoff rather than a gap.
      setSelectedSlot(null);
      return;
    }
    const attemptId = ++holdAttemptRef.current;
    setSelectedSlot({ item, slot, status: 'holding', expiresAt: null, errorMessage: null });
    try {
      await triggerSelect({
        transactionId,
        itemId: item.id,
        requestedTimestamp: slot.start_time,
        holdSeconds: HOLD_SECONDS,
      });
      const result = await pollUntil(
        () => getSelectResult(transactionId),
        (r) => Boolean(r.selected_order || r.selected_error)
      );
      if (holdAttemptRef.current !== attemptId) return; // superseded by a newer tap
      if (result.selected_error) {
        setSelectedSlot({
          item,
          slot,
          status: 'error',
          expiresAt: null,
          errorMessage:
            result.selected_error.code === 'SLOT_UNAVAILABLE'
              ? 'Someone else just took this slot.'
              : result.selected_error.message || 'Could not hold this slot.',
        });
        invalidateSlotsCache();
        return;
      }
      setSelectedSlot({ item, slot, status: 'held', expiresAt: Date.now() + HOLD_SECONDS * 1000, errorMessage: null });
    } catch (err) {
      if (holdAttemptRef.current !== attemptId) return;
      setSelectedSlot({
        item,
        slot,
        status: 'error',
        expiresAt: null,
        errorMessage: err instanceof ApiError || err instanceof Error ? err.message : 'Could not hold this slot.',
      });
    }
  }

  // The visible countdown, and the real enforcement that a lapsed hold
  // actually gives the slot back — ticks every second while genuinely
  // `held`; the instant it reaches zero, this clears the selection (so the
  // "Book this slot" bar disappears) and forces a fresh slots fetch (so the
  // chip itself reflects live reality again, not a stale "still selected"
  // view this browser alone believed).
  //
  // Backgrounded-tab edge case (found live, production build, 2026-11-04):
  // browsers throttle — and this browser pane's background tabs can fully
  // suspend — a hidden tab's own setInterval, so a customer who switches
  // away mid-hold and comes back after it's genuinely lapsed would otherwise
  // keep seeing their own stale "held" chip until *something else* happened
  // to trigger a refetch (another tap, a date change). Never a double-booking
  // risk either way — BPP's own Redis TTL is the real, independent
  // enforcement — but a customer's own view of their own hold should still
  // self-correct the instant they look back at it, not wait on an unrelated
  // action. The `visibilitychange` listener below recomputes from the real
  // wall-clock deadline immediately on regaining visibility, which the next
  // throttled interval tick would otherwise only do eventually.
  useEffect(() => {
    if (!selectedSlot || selectedSlot.status !== 'held' || selectedSlot.expiresAt === null) {
      setSecondsLeft(null);
      return;
    }
    const expiresAt = selectedSlot.expiresAt;
    function tick() {
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      setSecondsLeft(remaining);
      if (remaining <= 0) {
        setSelectedSlot(null);
        invalidateSlotsCache();
        setExpiredNotice(true);
      }
    }
    tick();
    const interval = setInterval(tick, 1000);
    function onVisible() {
      if (document.visibilityState === 'visible') tick();
    }
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSlot?.expiresAt, selectedSlot?.status]);

  useEffect(() => {
    setSelectedSlot(null);
  }, [selectedProviderId, selectedDate]);

  // Live "someone else just took this" — a pure UX improvement layered on
  // top of the real guarantee above (BPP's own atomic Slot/Booking hold),
  // never a substitute for it: even with zero messages ever arriving here,
  // a 2nd customer's own tap still safely fails server-side. This only
  // saves them the tap. One connection per open provider card (closes the
  // instant a different provider/division is picked, via the `null` path
  // below), joining `provider-{id}-availability` — see
  // BAP/backend/core/consumers.py's `AvailabilityConsumer` and
  // core/realtime.py's `broadcast_slot_held()`/`broadcast_slot_confirmed()`
  // for the server side.
  const availabilityWsPath = selectedProviderId ? `/ws/availability/${selectedProviderId}/` : null;
  const { lastMessage: availabilityMessage } = useRealtimeConnection(availabilityWsPath);
  const [heldByOthers, setHeldByOthers] = useState<Set<string>>(new Set());
  // Each grey-out is self-correcting: `slot.held` carries the real deadline
  // its hold is due to lapse (search/page.tsx and BPP's own Redis TTL are
  // counting down against the same real clock), so this schedules its own
  // removal rather than waiting on a 2nd server push for "it's free again"
  // — there is no such push (BPP never calls back for a passive TTL lapse).
  // A later `slot.confirmed` for the same slot cancels the pending removal
  // permanently: the one case a timer alone would get wrong is a hold that
  // turns into a real booking *before* its original countdown ends — left
  // alone, that slot would incorrectly un-grey itself for everyone the
  // instant the old countdown hit zero.
  // Keeps each pending entry's real wall-clock deadline alongside its
  // setTimeout handle (not just the handle alone) — a backgrounded tab can
  // throttle or fully suspend that setTimeout (found live, production
  // build, 2026-11-04 — same root cause as the own-hold countdown above),
  // so the visibilitychange sweep below needs the real deadline to
  // self-correct immediately on regaining visibility, rather than trusting
  // the timer to have fired on schedule while hidden.
  const heldTimeoutsRef = useRef<Map<string, { handle: ReturnType<typeof setTimeout>; expiresAt: number }>>(
    new Map()
  );
  function clearAllHeldTimeouts() {
    heldTimeoutsRef.current.forEach(({ handle }) => clearTimeout(handle));
    heldTimeoutsRef.current.clear();
  }
  useEffect(() => {
    setHeldByOthers(new Set());
    clearAllHeldTimeouts();
  }, [selectedProviderId, selectedDate]);
  useEffect(() => clearAllHeldTimeouts, []); // also clear on unmount
  // Sweeps every pending held-by-other entry the instant the tab regains
  // visibility, releasing any whose real deadline already passed while
  // hidden — the counterpart to the own-hold visibilitychange fix above,
  // for the *other* customers' holds this browser is merely displaying.
  useEffect(() => {
    function onVisible() {
      if (document.visibilityState !== 'visible') return;
      const now = Date.now();
      const expiredKeys: string[] = [];
      heldTimeoutsRef.current.forEach(({ handle, expiresAt }, key) => {
        if (expiresAt <= now) {
          clearTimeout(handle);
          heldTimeoutsRef.current.delete(key);
          expiredKeys.push(key);
        }
      });
      if (expiredKeys.length === 0) return;
      setHeldByOthers((prev) => {
        const next = new Set(prev);
        expiredKeys.forEach((key) => next.delete(key));
        return next;
      });
    }
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, []);
  useEffect(() => {
    if (
      typeof availabilityMessage !== 'object' ||
      availabilityMessage === null ||
      !('type' in availabilityMessage)
    ) {
      return;
    }
    const msg = availabilityMessage as {
      type: string;
      item_id: string;
      start_time: string;
      expires_at?: string;
    };
    if (msg.type !== 'slot.held' && msg.type !== 'slot.confirmed') return;
    // A push about the slot *this* browser itself just held/confirmed is
    // not "someone else" — that one already renders via selectedSlot's own
    // brand-colored state, never the grey "taken" one.
    if (selectedSlot?.item.id === msg.item_id && selectedSlot.slot.start_time === msg.start_time) return;

    const key = `${msg.item_id}:${msg.start_time}`;
    const existing = heldTimeoutsRef.current.get(key);
    if (existing) {
      clearTimeout(existing.handle);
      heldTimeoutsRef.current.delete(key);
    }
    setHeldByOthers((prev) => new Set(prev).add(key));

    if (msg.type === 'slot.held' && msg.expires_at) {
      const expiresAt = new Date(msg.expires_at).getTime();
      const msUntilExpiry = Math.max(0, expiresAt - Date.now());
      heldTimeoutsRef.current.set(key, {
        expiresAt,
        handle: setTimeout(() => {
          setHeldByOthers((prev) => {
            const next = new Set(prev);
            next.delete(key);
            return next;
          });
          heldTimeoutsRef.current.delete(key);
        }, msUntilExpiry),
      });
    }
    // slot.confirmed: no timeout scheduled — stays grey permanently (until
    // the next provider/date change clears it, same as everything else).
  }, [availabilityMessage, selectedSlot]);

  // Once a division is picked, the picker collapses into a compact "Division,
  // State" summary under the title (tap it to reopen and change) — keeps the
  // results the actual focus instead of a permanently-open form. Collapses
  // automatically the moment a division is chosen; starts (and stays) open
  // whenever there's no division yet.
  const [editingLocation, setEditingLocation] = useState(true);
  useEffect(() => {
    if (division) setEditingLocation(false);
  }, [division]);

  useEffect(() => {
    if (!state) {
      setDivisions([]);
      setDivision('');
      return;
    }
    let cancelled = false;
    setLoadingDivisions(true);
    setDivision('');
    fetchDivisions(state)
      .then((options) => {
        if (cancelled) return;
        setDivisions(options);
        const pending = pendingDivisionRef.current;
        if (pending) {
          pendingDivisionRef.current = null;
          if (options.some((o) => o.name === pending)) setDivision(pending);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingDivisions(false);
      });
    return () => {
      cancelled = true;
    };
  }, [state]);

  async function handleDetectLocation() {
    setDetecting(true);
    setDetectError(null);
    try {
      const pincode = await detectPincode();
      const result = await lookupByPincode(pincode);
      if (!result) {
        setDetectError("That location isn't in our coverage yet");
        return;
      }
      pendingDivisionRef.current = result.division.name;
      setState(result.state);
    } catch (err) {
      setDetectError(err instanceof Error ? err.message : 'Could not detect your location');
    } finally {
      setDetecting(false);
    }
  }

  // Browsing only starts once a division is actually picked — no division means
  // nothing to show yet, not an error (see the "pick a location" prompt below).
  // Re-browses whenever the chosen division changes.
  useEffect(() => {
    if (!division) {
      setTransactionId(null);
      setSubmitError(null);
      return;
    }
    let cancelled = false;
    setSubmitting(true);
    setSubmitError(null);
    triggerSearch('', domain, division)
      .then((txId) => {
        if (cancelled) return;
        setTransactionId(txId);
        setRetryNonce(0);
      })
      .catch((err) => {
        if (cancelled) return;
        setSubmitError(err instanceof ApiError ? err.message : 'Could not load providers');
      })
      .finally(() => {
        if (!cancelled) setSubmitting(false);
      });
    return () => {
      cancelled = true;
    };
  }, [domain, division]);

  const pollKey = transactionId ? `${transactionId}:${retryNonce}` : null;
  const { data, error, loading } = usePoll(
    pollKey,
    () => getSearchResults(transactionId as string),
    () => false,
    { intervalMs: 1500, maxAttempts: 12 }
  );

  // Memoized, not recomputed inline: groupByProvider builds a fresh array (and
  // selectedProvider a fresh object) on every call. Without this, both would get
  // a new reference on every render even when `data`/`selectedProviderId` hadn't
  // actually changed — and since headerContent's own useMemo below depends on
  // selectedProvider, that false "change" would retrigger it every render,
  // which retriggers usePageHeader's setContent, which re-renders this
  // component — the exact infinite-loop failure mode HeaderContext.tsx's own
  // docstring documents, now caused one level removed instead of directly.
  const providerGroups = useMemo(() => groupByProvider(data), [data]);
  const selectedProvider = useMemo(
    () => providerGroups.find((g) => g.provider.id === selectedProviderId) ?? null,
    [providerGroups, selectedProviderId]
  );
  const stillLoading = submitting || loading;
  const domainLabel = SEARCH_DOMAINS.find((d) => d.code === domain)?.label ?? '';
  const nouns = DOMAIN_NOUNS[domain] ?? DEFAULT_NOUNS;
  const isDoctorDomain = domain === 'ONDC:SRV13';

  const trimmedQuery = searchQuery.trim().toLowerCase();
  const filteredProviderGroups = useMemo(() => {
    if (!trimmedQuery) return providerGroups;
    return providerGroups.filter((g) => g.provider.descriptor.name.toLowerCase().includes(trimmedQuery));
  }, [providerGroups, trimmedQuery]);

  // selectedProvider gets a brand-new object reference on every search-poll tick
  // (providerGroups rebuilds from a fresh `data` object each time usePoll's interval
  // fires, even when the content is unchanged) — a ref holding the latest value lets
  // the effect below read the current provider without taking the object itself as
  // a dependency, which would otherwise re-fire this fetch roughly every 1.5s while
  // the background search poll is still running, not just on a genuine provider/date
  // change. `selectedProviderId` (the primitive state that actually drives which
  // provider is open) is the real, stable dependency.
  const selectedProviderRef = useRef(selectedProvider);
  useEffect(() => {
    selectedProviderRef.current = selectedProvider;
  }, [selectedProvider]);

  // Fetches fresh the moment the provider or the selected date changes; reuses an
  // already-fetched (provider, date) pair instead of refetching it — flipping back
  // to a date already checked this session is instant. Race-safe (`cancelled`) the
  // same way every other async effect in this file already is: tapping through
  // dates quickly must never let an earlier, slower response overwrite a later one.
  useEffect(() => {
    const provider = selectedProviderRef.current;
    if (!provider || !transactionId) {
      setAvailabilityMap(null);
      setAvailabilityError(null);
      setAvailabilityLoading(false);
      return;
    }
    const providerId = provider.provider.id;
    const itemIds = provider.items.map((item) => item.id);
    if (itemIds.length === 0) {
      setAvailabilityMap({});
      setAvailabilityError(null);
      setAvailabilityLoading(false);
      return;
    }
    const cacheKey = `${providerId}:${selectedDate}`;
    const cached = availabilityCacheRef.current.get(cacheKey);
    if (cached) {
      setAvailabilityMap(cached);
      setAvailabilityError(null);
      setAvailabilityLoading(false);
      return;
    }
    let cancelled = false;
    setAvailabilityLoading(true);
    setAvailabilityError(null);
    checkAvailability(transactionId, providerId, itemIds, selectedDate)
      .then((map) => {
        if (cancelled) return;
        availabilityCacheRef.current.set(cacheKey, map);
        setAvailabilityMap(map);
      })
      .catch((err) => {
        if (cancelled) return;
        setAvailabilityError(err instanceof ApiError ? err.message : 'Could not check availability');
      })
      .finally(() => {
        if (!cancelled) setAvailabilityLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedProviderId, selectedDate, transactionId, availabilityRetryNonce]);

  // Fetches each available item's real slot list for selectedDate, once
  // availabilityMap itself settles — same selectedProviderRef pattern as the
  // effect above (reads the live provider, but only re-fires on the stable
  // selectedProviderId/selectedDate/transactionId/availabilityMap, never on a
  // background poll tick's own new-but-equivalent provider object).
  useEffect(() => {
    const provider = selectedProviderRef.current;
    if (!provider || !transactionId || !availabilityMap) {
      setSlotsByItem({});
      return;
    }
    const providerId = provider.provider.id;
    const itemsToFetch = provider.items.filter((item) => availabilityMap[item.id] === true);
    if (itemsToFetch.length === 0) {
      setSlotsByItem({});
      return;
    }
    let cancelled = false;
    Promise.all(
      itemsToFetch.map(async (item) => {
        const cacheKey = `${item.id}:${selectedDate}`;
        const cached = slotsCacheRef.current.get(cacheKey);
        if (cached) return [item.id, cached] as const;
        try {
          const slots = await listAvailableSlots(transactionId, providerId, item.id, selectedDate);
          slotsCacheRef.current.set(cacheKey, slots);
          return [item.id, slots] as const;
        } catch {
          return [item.id, []] as const;
        }
      })
    ).then((entries) => {
      if (cancelled) return;
      setSlotsByItem(Object.fromEntries(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [selectedProviderId, selectedDate, transactionId, availabilityMap, slotsRetryNonce]);

  // Only an item availabilityMap genuinely marks `true` ever renders — a doctor
  // with no open slot on the selected date is hidden outright, not shown disabled
  // (the user's own explicit instruction: "no need to show the expired doctors
  // slots"). `availabilityMap === null` (still loading / not yet checked) yields no
  // items rather than briefly showing everyone-as-available before the real
  // answer arrives.
  const availableItems = useMemo(() => {
    if (!selectedProvider || !availabilityMap) return [];
    return selectedProvider.items.filter((item) => availabilityMap[item.id] === true);
  }, [selectedProvider, availabilityMap]);
  const filteredItems = useMemo(() => {
    if (!trimmedQuery) return availableItems;
    return availableItems.filter((item) => item.descriptor.name.toLowerCase().includes(trimmedQuery));
  }, [availableItems, trimmedQuery]);
  const infoItem = selectedProvider?.items.find((item) => item.id === infoItemId) ?? null;

  // Replaces AppShell's default header content for as long as this page is
  // mounted — title plus (once a location is picked) the collapsed "Division,
  // State" summary, tappable to reopen the picker below. Memoized (see
  // usePageHeader's own docstring for why this isn't optional): without a
  // stable reference here, this caused a real infinite render loop.
  const headerContent = useMemo(() => {
    if (searchOpen) {
      const placeholder = selectedProvider ? `Search by ${nouns.item} name` : `Search by ${nouns.provider} name`;
      return (
        <div className="flex w-full flex-1 items-center gap-2">
          <input
            type="text"
            autoFocus
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={placeholder}
            aria-label={placeholder}
            className="min-w-0 flex-1 rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
          />
          <button
            type="button"
            onClick={() => {
              setSearchOpen(false);
              setSearchQuery('');
            }}
            aria-label="Close search"
            className="flex-none rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="m18 6-12 12M6 6l12 12" />
            </svg>
          </button>
        </div>
      );
    }

    const searchButton = (showSearch: boolean) =>
      showSearch && (
        <button
          type="button"
          onClick={() => setSearchOpen(true)}
          aria-label={selectedProvider ? `Search by ${nouns.item} name` : `Search by ${nouns.provider} name`}
          className="flex-none self-start rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="m21 21-4.3-4.3" />
          </svg>
        </button>
      );

    if (selectedProvider) {
      return (
        <div className="flex flex-1 items-center justify-between gap-2">
          <div className="flex flex-col">
            <button
              type="button"
              onClick={() => setSelectedProviderId(null)}
              className="flex items-center gap-1 self-start text-sm text-neutral-600 hover:text-neutral-900"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4 flex-none" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m15 6-6 6 6 6" />
              </svg>
              All {domainLabel.toLowerCase()} providers
            </button>
            <span className="font-[family-name:var(--font-title)] text-base font-bold leading-tight text-neutral-900 sm:text-lg">
              {selectedProvider.provider.descriptor.name}
            </span>
          </div>
          {searchButton(selectedProvider.items.length > 0)}
        </div>
      );
    }
    return (
      <div className="flex flex-1 items-center justify-between gap-2">
        <div className="flex flex-col">
          <span className="font-[family-name:var(--font-title)] text-base font-bold leading-tight text-neutral-900 sm:text-lg">
            Find {domainLabel} service
          </span>
          {!editingLocation && division && (
            <button
              type="button"
              onClick={() => setEditingLocation(true)}
              className="flex items-center gap-1 self-start text-xs text-neutral-600 hover:text-neutral-900"
            >
              <span>
                {division}, {state}
              </span>
              <svg viewBox="0 0 24 24" className="h-3 w-3 flex-none" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m6 9 6 6 6-6" />
              </svg>
            </button>
          )}
        </div>
        {searchButton(providerGroups.length > 0)}
      </div>
    );
  }, [
    domainLabel,
    editingLocation,
    division,
    state,
    selectedProvider,
    providerGroups,
    searchOpen,
    searchQuery,
    nouns,
  ]);
  usePageHeader(headerContent);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-6 sm:px-6 sm:py-8 lg:px-8">
      {editingLocation && (
        <div className="mt-4 flex flex-col gap-3 sm:mt-6 sm:max-w-sm">
          <button
            type="button"
            onClick={handleDetectLocation}
            disabled={detecting}
            className="flex items-center gap-2 self-start text-sm font-medium text-[var(--brand-700)] disabled:opacity-50"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4 flex-none" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7Z" />
              <circle cx="12" cy="9" r="2.5" />
            </svg>
            {detecting ? 'Detecting…' : 'Detect my location'}
          </button>
          {detectError && <p className="-mt-1.5 text-xs text-red-600">{detectError}</p>}

          <Dropdown
            id="search-state"
            label="State"
            value={state}
            onChange={setState}
            placeholder="Any state"
            options={[
              { value: '', label: 'Any state' },
              ...ALL_INDIA_STATES.map((name) => ({ value: name, label: name })),
            ]}
          />
          <div className="flex flex-col gap-1.5">
            <Dropdown
              id="search-division"
              label="Division"
              value={division}
              onChange={setDivision}
              placeholder={loadingDivisions ? 'Loading…' : 'Any division'}
              disabled={!state || loadingDivisions}
              options={[
                { value: '', label: 'Any division' },
                ...divisions.map((d) => ({ value: d.name, label: d.name })),
              ]}
            />
            {state && !loadingDivisions && divisions.length === 0 && (
              <p className="text-xs text-neutral-500">No divisions listed yet for {state}.</p>
            )}
          </div>
        </div>
      )}

      <div className={`flex flex-col gap-6 ${editingLocation ? 'mt-6' : ''}`}>
        {!division && (
          <EmptyState
            title="Select a location"
            description={`Choose a state and division above to discover ${domainLabel.toLowerCase()} providers near you.`}
          />
        )}

        {division && submitError && (
          <ErrorState
            title="Couldn't load providers"
            description={submitError}
            onRetry={() => setRetryNonce((n) => n + 1)}
          />
        )}

        {division && !submitError && error && (
          <ErrorState
            title="Couldn't load results"
            description={error.message}
            onRetry={() => setRetryNonce((n) => n + 1)}
          />
        )}

        {division && !submitError && !error && stillLoading && providerGroups.length === 0 && (
          <LoadingState label="Finding providers…" />
        )}

        {division && !submitError && !error && providerGroups.length === 0 && !stillLoading && (
          <EmptyState
            title="No providers found"
            description={`Nobody in ${division} yet. Try a different division.`}
          />
        )}

        {division &&
          !submitError &&
          !error &&
          providerGroups.length > 0 &&
          !selectedProvider &&
          trimmedQuery &&
          filteredProviderGroups.length === 0 && (
            <EmptyState
              title="No matches"
              description={`No ${nouns.provider}s match "${searchQuery.trim()}".`}
              action={
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
                >
                  Clear search
                </button>
              }
            />
          )}

        {division && !submitError && !error && filteredProviderGroups.length > 0 && !selectedProvider && (
          <ul className="flex flex-col gap-3">
            {filteredProviderGroups.map(({ provider }) => (
              <li key={provider.id}>
                <button
                  type="button"
                  onClick={() => setSelectedProviderId(provider.id)}
                  className="flex w-full items-center justify-between gap-4 rounded-xl border border-neutral-200 bg-white p-4 text-left shadow-sm transition-shadow hover:shadow-md"
                >
                  <div>
                    <p className="font-medium text-neutral-900">{provider.descriptor.name}</p>
                    {provider.rating && (
                      <p className="mt-1 text-sm text-amber-600">
                        ★ {provider.rating}
                        <span className="text-neutral-500">
                          {' '}
                          ({provider.rating_count} {provider.rating_count === 1 ? 'rating' : 'ratings'})
                        </span>
                      </p>
                    )}
                  </div>
                  <svg viewBox="0 0 24 24" className="h-4 w-4 flex-none text-neutral-400" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m9 6 6 6-6 6" />
                  </svg>
                </button>
              </li>
            ))}
          </ul>
        )}

        {division && !submitError && !error && selectedProvider && (
          <div className="flex flex-col gap-4">
            <div className="-mx-4 -mt-6 sm:-mx-6 sm:-mt-8 lg:-mx-8">
              <DateStrip value={selectedDate} onChange={setSelectedDate} edgeToEdge />
            </div>
            {availabilityLoading && (
              <LoadingState label="Checking availability…" />
            )}
            {!availabilityLoading && availabilityError && (
              <ErrorState
                title="Couldn't check availability"
                description={availabilityError}
                onRetry={() => setAvailabilityRetryNonce((n) => n + 1)}
              />
            )}
            {!availabilityLoading && !availabilityError && availabilityMap && availableItems.length === 0 && (
              <EmptyState
                title="Availability not updated"
                description={`${nouns.provider.charAt(0).toUpperCase() + nouns.provider.slice(1)} hasn't updated ${nouns.item} availability for this date yet. Try a different date.`}
              />
            )}
            {!availabilityLoading &&
              !availabilityError &&
              availabilityMap &&
              availableItems.length > 0 &&
              trimmedQuery &&
              filteredItems.length === 0 && (
                <EmptyState
                  title="No matches"
                  description={`No ${nouns.item}s match "${searchQuery.trim()}".`}
                  action={
                    <button
                      type="button"
                      onClick={() => setSearchQuery('')}
                      className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
                    >
                      Clear search
                    </button>
                  }
                />
              )}
            {!availabilityLoading && !availabilityError && filteredItems.length > 0 && (
              <ul className="flex flex-col gap-3">
                {filteredItems.map((item) => {
                  const specialisation = getTagValue(item, 'specialisation');
                  const name = doctorDisplayName(item.descriptor.name, isDoctorDomain);
                  const itemSlots = slotsByItem[item.id];
                  return (
                    <li
                      key={item.id}
                      className="relative flex flex-col gap-3 rounded-xl border border-neutral-200 bg-white p-4 shadow-sm"
                    >
                      <button
                        type="button"
                        onClick={() => setInfoItemId(item.id)}
                        aria-label={`View details for ${name}`}
                        className="absolute right-2 top-2 flex-none rounded-full p-2 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-700"
                      >
                        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                          <circle cx="12" cy="12" r="9" />
                          <path d="M12 11v5" strokeLinecap="round" />
                          <circle cx="12" cy="8" r="0.75" fill="currentColor" stroke="none" />
                        </svg>
                      </button>
                      <div className="flex flex-wrap items-start justify-between gap-2 pr-8">
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                          <span className="text-base font-semibold text-neutral-900">{name}</span>
                          {specialisation && (
                            <>
                              <span className="text-xs text-neutral-300">•</span>
                              <span className="text-sm font-medium text-[var(--brand-600)]">{specialisation}</span>
                            </>
                          )}
                        </div>
                        <span className="flex-none rounded-md bg-neutral-100 px-2 py-0.5 text-xs font-semibold text-neutral-900">
                          {formatPrice(item.price)}
                        </span>
                      </div>
                      <div className="flex flex-col gap-1.5">
                        {itemSlots === undefined && (
                          <p className="text-xs text-neutral-500">Loading times…</p>
                        )}
                        {itemSlots && itemSlots.length === 0 && (
                          <p className="text-xs text-neutral-500">No open times for this date.</p>
                        )}
                        {itemSlots && itemSlots.length > 0 && (
                          <div className="flex flex-wrap gap-2">
                            {itemSlots.map((slot) => {
                              const isThisSlot =
                                selectedSlot?.item.id === item.id &&
                                selectedSlot.slot.start_time === slot.start_time;
                              const status = isThisSlot ? selectedSlot.status : null;
                              // Unified "can't have this one" state — a slot already
                              // booked (or currently held by someone else, in real
                              // time, before the backend's own record would reflect
                              // it) renders identically: dark grey, disabled, still
                              // visible. See the legend bar below for what each
                              // color means.
                              const isBooked =
                                !isThisSlot &&
                                (slot.status === 'booked' ||
                                  heldByOthers.has(`${item.id}:${slot.start_time}`));
                              const className = isBooked
                                ? 'cursor-not-allowed rounded-md border border-neutral-500 bg-neutral-500 px-3 py-1.5 text-xs font-medium text-neutral-100'
                                : status === 'held'
                                  ? 'rounded-md border border-[var(--brand-600)] bg-[var(--brand-600)] px-3 py-1.5 text-xs font-semibold text-white transition-colors'
                                  : status === 'holding'
                                    ? 'rounded-md border border-[var(--brand-600)] bg-[var(--brand-100)] px-3 py-1.5 text-xs font-semibold text-[var(--brand-700)] opacity-70 transition-colors'
                                    : 'rounded-md border border-neutral-300 bg-neutral-100 px-3 py-1.5 text-xs font-medium text-neutral-700 transition-colors hover:border-[var(--brand-600)] hover:bg-[var(--brand-50)] hover:text-[var(--brand-700)]';
                              return (
                                <button
                                  key={slot.start_time}
                                  type="button"
                                  aria-pressed={status === 'held'}
                                  aria-disabled={isBooked}
                                  disabled={status === 'holding' || isBooked}
                                  onClick={() => handleSlotTap(item, slot)}
                                  className={className}
                                >
                                  {status === 'holding' ? 'Holding…' : formatTime(slot.start_time)}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}

        {division && !submitError && !error && loading && providerGroups.length > 0 && (
          <p className="text-xs text-neutral-500" aria-live="polite">
            Still checking for more providers…
          </p>
        )}
      </div>

      {infoItem && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={() => setInfoItemId(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={`Details for ${doctorDisplayName(infoItem.descriptor.name, isDoctorDomain)}`}
            className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-neutral-900">
                  {doctorDisplayName(infoItem.descriptor.name, isDoctorDomain)}
                </h2>
                {getTagValue(infoItem, 'specialisation') && (
                  <p className="mt-0.5 text-sm font-medium text-[var(--brand-600)]">
                    {getTagValue(infoItem, 'specialisation')}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setInfoItemId(null)}
                aria-label="Close"
                className="flex-none rounded p-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900"
              >
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m18 6-12 12M6 6l12 12" />
                </svg>
              </button>
            </div>
            <dl className="mt-4 flex flex-col gap-2.5 text-sm">
              {getTagValue(infoItem, 'education') && (
                <div className="flex justify-between gap-4">
                  <dt className="text-neutral-500">Education</dt>
                  <dd className="text-right font-medium text-neutral-900">{getTagValue(infoItem, 'education')}</dd>
                </div>
              )}
              {getTagValue(infoItem, 'years_of_experience') && (
                <div className="flex justify-between gap-4">
                  <dt className="text-neutral-500">Experience</dt>
                  <dd className="text-right font-medium text-neutral-900">
                    {getTagValue(infoItem, 'years_of_experience')} years
                  </dd>
                </div>
              )}
              {getTagValue(infoItem, 'gender') && (
                <div className="flex justify-between gap-4">
                  <dt className="text-neutral-500">Gender</dt>
                  <dd className="text-right font-medium capitalize text-neutral-900">
                    {getTagValue(infoItem, 'gender')}
                  </dd>
                </div>
              )}
              <div className="flex justify-between gap-4">
                <dt className="text-neutral-500">Consultation fee</dt>
                <dd className="text-right font-medium text-neutral-900">{formatPrice(infoItem.price)}</dd>
              </div>
            </dl>
          </div>
        </div>
      )}

      {selectedSlot && selectedProvider && (
        <div className="fixed inset-x-0 bottom-16 z-50 border-t border-neutral-200 bg-white px-4 py-3 shadow-[0_-4px_12px_rgba(0,0,0,0.08)] sm:bottom-0">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-neutral-900">
                {doctorDisplayName(selectedSlot.item.descriptor.name, isDoctorDomain)}
              </p>
              {selectedSlot.status === 'error' ? (
                <p className="text-xs text-red-600">{selectedSlot.errorMessage}</p>
              ) : (
                <p className="text-xs text-neutral-500">
                  {selectedProvider.provider.descriptor.name} · {formatTime(selectedSlot.slot.start_time)}
                </p>
              )}
            </div>
            {selectedSlot.status === 'holding' && (
              <span className="flex-none rounded-md bg-neutral-100 px-5 py-2.5 text-sm font-semibold text-neutral-400">
                Holding…
              </span>
            )}
            {selectedSlot.status === 'error' && (
              <button
                type="button"
                onClick={() => setSelectedSlot(null)}
                className="flex-none rounded-md border border-neutral-300 px-5 py-2.5 text-sm font-medium text-neutral-700"
              >
                Dismiss
              </button>
            )}
            {selectedSlot.status === 'held' && (
              <Link
                href={{
                  pathname: '/select',
                  query: {
                    transaction_id: transactionId,
                    item_id: selectedSlot.item.id,
                    item_name: selectedSlot.item.descriptor.name,
                    provider_id: selectedProvider.provider.id,
                    provider_name: selectedProvider.provider.descriptor.name,
                    slot_start: selectedSlot.slot.start_time,
                    slot_end: selectedSlot.slot.end_time,
                    price_value: selectedSlot.item.price.value,
                    price_currency: selectedSlot.item.price.currency,
                    hold_expires_at: String(selectedSlot.expiresAt),
                  },
                }}
                className="flex-none rounded-md bg-[var(--brand-600)] px-5 py-2.5 text-sm font-semibold text-white"
              >
                Book this slot
              </Link>
            )}
          </div>
        </div>
      )}

      {/* Sticky color-key for the slot grid above — fixed in the same spot the
          "Book this slot" bar uses (bottom-16 on mobile, above the bottom nav;
          bottom-0 once that nav is gone at sm:), and only one of the two is ever
          shown at once, so they never fight for the same space. Scrolling the
          doctor cards never moves this, same as the bottom nav itself. */}
      {selectedProvider && !selectedSlot && (
        <div className="fixed inset-x-0 bottom-16 z-50 border-t border-neutral-200 bg-white px-4 py-2.5 sm:bottom-0">
          <div className="mx-auto flex max-w-3xl items-center justify-center gap-5 text-xs text-neutral-600">
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 flex-none rounded-sm bg-neutral-500" aria-hidden="true" />
              Booked
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 flex-none rounded-sm bg-[var(--brand-600)]" aria-hidden="true" />
              Selected
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 flex-none rounded-sm border border-neutral-300 bg-neutral-100" aria-hidden="true" />
              Available
            </span>
          </div>
        </div>
      )}

      {expiredNotice && (
        <div
          role="status"
          aria-live="polite"
          className="fixed inset-x-0 top-0 z-[60] flex justify-center px-4 pt-3"
        >
          <div className="flex items-center gap-2 rounded-lg bg-neutral-900 px-4 py-2.5 text-sm font-medium text-white shadow-lg">
            Time expired — please reselect your slot.
          </div>
        </div>
      )}
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <SearchPageInner />
    </Suspense>
  );
}
