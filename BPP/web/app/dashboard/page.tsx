'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { EmptyState } from '@/components/ui/EmptyState';
import { FormField } from '@/components/ui/FormField';
import { me, type BusinessAccount } from '@/lib/auth-api';
import { type ResourceInfo } from '@/lib/availability-api';
import { createResource, createAvailability, fetchOwnedResources } from '@/lib/resources-api';
import { ApiError } from '@/lib/api-client';
import { RESOURCE_TYPES_BY_DOMAIN } from '@/lib/constants';

/**
 * livetracker3.md §7.1: the resource-list dashboard-home a logged-in owner lands on
 * after login/signup — previously the only real page (`/resources/[id]/availability`)
 * required already knowing a resourceId by hand-typed URL. Reuses the existing
 * `getSlots()` (Phase 4.4) to fetch each owned resource's name/rating instead of
 * building a second, bulk-listing endpoint purely for this — a business's own
 * resource count is small, and every resource here is already individually fetched
 * again on its own availability page regardless.
 */

function CreateResourceForm({
  domainCode,
  onCreated,
}: {
  domainCode: string;
  onCreated: (resource: { id: string; name: string }) => void;
}) {
  const resourceTypes = RESOURCE_TYPES_BY_DOMAIN[domainCode] ?? [];
  const [name, setName] = useState('');
  const [resourceType, setResourceType] = useState(resourceTypes[0]?.value ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!resourceType) return;
    setSubmitting(true);
    setError(null);
    try {
      const resource = await createResource(name.trim(), resourceType);
      setName('');
      onCreated(resource);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create this resource');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border border-neutral-200 p-4">
      <h2 className="text-sm font-semibold text-neutral-900">Add a resource</h2>
      <FormField label="Name" value={name} onChange={(e) => setName(e.target.value)} required />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="resource-type" className="text-sm font-medium text-neutral-900">
          Type
        </label>
        <select
          id="resource-type"
          value={resourceType}
          onChange={(e) => setResourceType(e.target.value)}
          className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
        >
          {resourceTypes.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={submitting || !name.trim()}
        className="self-start rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
      >
        {submitting ? 'Adding…' : 'Add resource'}
      </button>
    </form>
  );
}

function CreateAvailabilityForm({
  resource,
  onCreated,
}: {
  resource: { id: string; name: string };
  onCreated: () => void;
}) {
  const [rangeStart, setRangeStart] = useState('');
  const [rangeEnd, setRangeEnd] = useState('');
  const [times, setTimes] = useState('09:00');
  const [slotDurationMinutes, setSlotDurationMinutes] = useState(30);
  const [slotCapacity, setSlotCapacity] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!rangeStart || !rangeEnd) return;
    setSubmitting(true);
    setError(null);
    try {
      await createAvailability(resource.id, {
        rangeStart: new Date(rangeStart).toISOString(),
        rangeEnd: new Date(rangeEnd).toISOString(),
        times: times
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        frequencyDays: 1,
        slotDurationMinutes,
        slotCapacity,
      });
      setDone(true);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not generate availability');
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="flex flex-col gap-2 rounded-lg border border-neutral-200 p-4">
        <p className="text-sm text-neutral-900">
          Availability generated for <span className="font-medium">{resource.name}</span>.
        </p>
        <Link
          href={`/resources/${resource.id}/availability`}
          className="self-start text-sm text-neutral-600 underline"
        >
          View availability dashboard
        </Link>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border border-neutral-200 p-4">
      <h2 className="text-sm font-semibold text-neutral-900">
        Set up availability for {resource.name}
      </h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormField
          label="From"
          type="date"
          value={rangeStart}
          onChange={(e) => setRangeStart(e.target.value)}
          required
        />
        <FormField
          label="To"
          type="date"
          value={rangeEnd}
          onChange={(e) => setRangeEnd(e.target.value)}
          required
        />
      </div>
      <FormField
        label="Times (comma-separated, e.g. 09:00, 14:00)"
        value={times}
        onChange={(e) => setTimes(e.target.value)}
        required
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormField
          label="Slot duration (minutes)"
          type="number"
          min={1}
          value={slotDurationMinutes}
          onChange={(e) => setSlotDurationMinutes(Number(e.target.value))}
          required
        />
        <FormField
          label="Capacity per slot"
          type="number"
          min={1}
          value={slotCapacity}
          onChange={(e) => setSlotCapacity(Number(e.target.value))}
          required
        />
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={submitting}
        className="self-start rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
      >
        {submitting ? 'Generating…' : 'Generate availability'}
      </button>
    </form>
  );
}

function OwnerDashboard({ account }: { account: BusinessAccount }) {
  const [resources, setResources] = useState<ResourceInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Real gap found on this phase's own audit, after closing it: a single pending
  // resource (not a list) meant creating a second resource before finishing the
  // first's availability setup silently discarded the first one's own "set up
  // availability" form — and since the existing availability page has no way to
  // create a calendar (only view/block already-existing slots), that resource
  // would be permanently stuck with zero bookable slots, no UI path to fix it.
  // A list, one entry per resource still needing its first calendar, closes it.
  const [pendingAvailability, setPendingAvailability] = useState<
    { id: string; name: string }[]
  >([]);

  const applyResourcesResult = useCallback((result: ResourceInfo[] | null, err: unknown) => {
    if (result !== null) {
      setResources(result);
      setLoadError(null);
    } else {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load your resources');
    }
  }, []);

  // Stable across renders unless the account's own real id list actually changes —
  // account.owned_resource_ids?? [] would otherwise be a fresh array every render,
  // which would re-trigger the effect below every render too.
  const ownedResourceIds = useMemo(
    () => account.owned_resource_ids ?? [],
    [account.owned_resource_ids]
  );

  // React's own documented data-fetching effect shape (matching
  // app/resources/[resourceId]/availability/page.tsx's identical fix, Phase 4.4) —
  // the state-setting calls live inside .then()/.catch(), not as a bare call to an
  // external state-setting function at the effect body's top level.
  useEffect(() => {
    let ignore = false;
    fetchOwnedResources(ownedResourceIds)
      .then((result) => {
        if (!ignore) applyResourcesResult(result, null);
      })
      .catch((err) => {
        if (!ignore) applyResourcesResult(null, err);
      });
    return () => {
      ignore = true;
    };
  }, [ownedResourceIds, applyResourcesResult]);

  const loadResources = useCallback(async () => {
    try {
      applyResourcesResult(await fetchOwnedResources(ownedResourceIds), null);
    } catch (err) {
      applyResourcesResult(null, err);
    }
  }, [ownedResourceIds, applyResourcesResult]);

  if (loadError) return <ErrorState description={loadError} onRetry={loadResources} />;
  if (resources === null) return <LoadingState label="Loading your resources…" />;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
          {account.business_name}
        </h1>
        <div className="flex items-center gap-4">
          <Link href="/orders" className="text-sm text-neutral-600 underline">
            Orders
          </Link>
          <Link href="/staff" className="text-sm text-neutral-600 underline">
            Manage staff
          </Link>
        </div>
      </div>

      {resources.length === 0 ? (
        <EmptyState
          title="No services yet"
          description="Add your first resource below to start taking bookings."
        />
      ) : (
        <ul className="flex flex-col divide-y divide-neutral-200 rounded-lg border border-neutral-200">
          {resources.map((r) => (
            <li key={r.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div>
                <p className="text-sm font-medium text-neutral-900">{r.name}</p>
                <p className="text-xs text-neutral-600">
                  {r.average_rating ? `★ ${r.average_rating} (${r.rating_count})` : 'No ratings yet'}
                </p>
              </div>
              <Link
                href={`/resources/${r.id}/availability`}
                className="text-sm text-neutral-600 underline"
              >
                Manage availability
              </Link>
            </li>
          ))}
        </ul>
      )}

      <CreateResourceForm
        domainCode={account.domain_code}
        onCreated={(resource) => {
          setPendingAvailability((prev) => [...prev, resource]);
          // Real bug found live during this phase's own E2E verification: calling
          // loadResources() here re-fetches using account.owned_resource_ids — a
          // snapshot captured once when the dashboard first loaded, before this
          // resource existed. It never includes the new id, so the list silently
          // stayed on "No services yet" forever after a real, successful creation.
          // Appending it directly to local state is simpler and actually correct;
          // a real reload naturally re-fetches the authoritative list from scratch.
          setResources((prev) => [
            ...(prev ?? []),
            {
              id: resource.id,
              name: resource.name,
              average_rating: null,
              rating_count: 0,
              assigned_staff_id: null,
            },
          ]);
        }}
      />

      {pendingAvailability.map((resource) => (
        <CreateAvailabilityForm key={resource.id} resource={resource} onCreated={() => {}} />
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [account, setAccount] = useState<BusinessAccount | null | 'loading'>('loading');

  useEffect(() => {
    let cancelled = false;
    me().then((result) => {
      if (cancelled) return;
      if (result === null) {
        router.push('/login');
        return;
      }
      // Staff accounts self-manage via their one assigned resource's own dashboard
      // (livetracker2.md §4.3) — this resource-list home is an owner concept
      // (§7.1's own scope); Phase 8 (staff-management UI) is where a real
      // staff-facing dashboard equivalent belongs, not here.
      if (result.role === 'STAFF') {
        const firstAssigned = result.assigned_resource_ids?.[0];
        router.push(firstAssigned ? `/resources/${firstAssigned}/availability` : '/');
        return;
      }
      setAccount(result);
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (account === 'loading' || account === null) return <LoadingState label="Checking session…" />;
  return <OwnerDashboard account={account} />;
}
