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
import { fetchOwnedResources } from '@/lib/resources-api';
import {
  listStaff,
  createStaff,
  assignStaffToResource,
  unassignStaffFromResource,
  setStaffActive,
  resetStaffPassword,
  type StaffAccount,
} from '@/lib/staff-api';
import { ApiError } from '@/lib/api-client';

/**
 * livetracker3.md §8.1: a real staff-management screen — an owner creates a staff
 * account and assigns it to one of its own resources, reusing the same owned-resource
 * list `app/dashboard/page.tsx` (§7.1) already builds (`fetchOwnedResources`) rather
 * than a second, parallel resource picker.
 */

function CreateStaffForm({ onCreated }: { onCreated: (staff: StaffAccount) => void }) {
  const [businessName, setBusinessName] = useState('');
  const [contact, setContact] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const staff = await createStaff(businessName.trim(), contact.trim(), password);
      setBusinessName('');
      setContact('');
      setPassword('');
      onCreated(staff);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create this staff account');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border border-neutral-200 p-4">
      <h2 className="text-sm font-semibold text-neutral-900">Add a staff account</h2>
      <FormField
        label="Staff name"
        value={businessName}
        onChange={(e) => setBusinessName(e.target.value)}
        required
      />
      <FormField
        label="Email"
        type="email"
        value={contact}
        onChange={(e) => setContact(e.target.value)}
        required
      />
      <FormField
        label="Password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
      />
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={submitting || !businessName.trim() || !contact.trim() || !password}
        className="self-start rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
      >
        {submitting ? 'Adding…' : 'Add staff'}
      </button>
    </form>
  );
}

function AssignStaffRow({
  resource,
  staff,
  onAssigned,
  onUnassigned,
}: {
  resource: ResourceInfo;
  staff: StaffAccount[];
  onAssigned: (resourceId: string, staffId: string) => void;
  onUnassigned: (resourceId: string) => void;
}) {
  const assigned = staff.find((s) => s.id === resource.assigned_staff_id);
  // Real gap found on this phase's own audit, after closing it: `SECURITY.md`'s own
  // documented IDOR boundary assumes a staff account has exactly *one* assigned
  // Resource (`_resource_accessible_to()`'s comment: "the one Resource its owner
  // explicitly assigned to it") — nothing server-side enforces that, and this
  // screen was the first place a real human could accidentally violate it (assign
  // an already-assigned staff account to a second resource too). Since login only
  // ever redirects to `assigned_resource_ids[0]`, a second assignment would be
  // silently unreachable via login, not just redundant. Excluding staff already
  // assigned elsewhere from a resource's own picker keeps the UI honest about the
  // system's real one-resource-per-staff design without changing backend semantics.
  const assignableStaff = staff.filter(
    (s) =>
      s.is_active &&
      (s.assigned_resource_ids.length === 0 || s.assigned_resource_ids.includes(resource.id))
  );
  // Real bug found live during this section's own verification: a plain
  // `useState(staff[0]?.id ?? '')` only reads its initial value once, at mount —
  // adding the first-ever staff account after this row already rendered (staff
  // going from empty to non-empty) never updated it, so the newly available
  // option existed in the <select> but was never the value `handleAssign`
  // actually submitted. Deriving the effective selection during render instead
  // (falling back to the first staff id whenever the explicit pick is stale or
  // unset) needs no effect at all, per React's own "you might not need an
  // effect" guidance — `explicitSelection` only records the user's own pick.
  const [explicitSelection, setExplicitSelection] = useState<string | null>(null);
  const selected =
    explicitSelection && assignableStaff.some((s) => s.id === explicitSelection)
      ? explicitSelection
      : (assignableStaff[0]?.id ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [unassigning, setUnassigning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAssign() {
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      await assignStaffToResource(resource.id, selected);
      onAssigned(resource.id, selected);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not assign this staff account');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleUnassign() {
    setUnassigning(true);
    setError(null);
    try {
      await unassignStaffFromResource(resource.id);
      onUnassigned(resource.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not unassign this staff account');
    } finally {
      setUnassigning(false);
    }
  }

  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-neutral-900">{resource.name}</p>
        <div className="flex items-center gap-2">
          <p className="text-xs text-neutral-600">
            {assigned ? `Assigned to ${assigned.business_name}` : 'Unassigned'}
          </p>
          {assigned && (
            <button
              type="button"
              onClick={handleUnassign}
              disabled={unassigning}
              className="text-xs text-neutral-600 underline disabled:opacity-50"
            >
              {unassigning ? 'Unassigning…' : 'Unassign'}
            </button>
          )}
        </div>
      </div>
      {assignableStaff.length > 0 && (
        <div className="flex items-center gap-2">
          <select
            aria-label={`Assign staff to ${resource.name}`}
            value={selected}
            onChange={(e) => setExplicitSelection(e.target.value)}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
          >
            {assignableStaff.map((s) => (
              <option key={s.id} value={s.id}>
                {s.business_name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleAssign}
            disabled={submitting || !selected}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50"
          >
            {submitting ? 'Assigning…' : 'Assign'}
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </li>
  );
}

function StaffRow({
  staffMember,
  resourceName,
  onStatusChanged,
}: {
  staffMember: StaffAccount;
  resourceName: string | undefined;
  onStatusChanged: (staffId: string, isActive: boolean) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [resettingPassword, setResettingPassword] = useState(false);
  const [newPassword, setNewPassword] = useState('');
  const [resetSubmitting, setResetSubmitting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetDone, setResetDone] = useState(false);

  async function handleToggle() {
    setSubmitting(true);
    setError(null);
    try {
      const nextActive = !staffMember.is_active;
      await setStaffActive(staffMember.id, nextActive);
      onStatusChanged(staffMember.id, nextActive);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update this staff account');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResetPassword(e: React.FormEvent) {
    e.preventDefault();
    setResetSubmitting(true);
    setResetError(null);
    try {
      await resetStaffPassword(staffMember.id, newPassword);
      setNewPassword('');
      setResetDone(true);
    } catch (err) {
      setResetError(err instanceof ApiError ? err.message : 'Could not reset this password');
    } finally {
      setResetSubmitting(false);
    }
  }

  return (
    <li className="flex flex-col gap-1 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-neutral-900">
            {staffMember.business_name}
            {!staffMember.is_active && (
              <span className="ml-2 rounded bg-neutral-200 px-1.5 py-0.5 text-xs text-neutral-600">
                Inactive
              </span>
            )}
          </p>
          <p className="text-xs text-neutral-600">{staffMember.contact}</p>
        </div>
        <div className="flex items-center gap-2">
          <p className="text-xs text-neutral-600">
            {staffMember.is_active && staffMember.assigned_resource_ids.length > 0
              ? `Assigned to ${resourceName ?? 'a resource'}`
              : 'Unassigned'}
          </p>
          <button
            type="button"
            onClick={() => {
              setResettingPassword((prev) => !prev);
              setResetError(null);
              setResetDone(false);
            }}
            className="text-xs text-neutral-600 underline"
          >
            Reset password
          </button>
          <button
            type="button"
            onClick={handleToggle}
            disabled={submitting}
            className="text-xs text-neutral-600 underline disabled:opacity-50"
          >
            {submitting
              ? staffMember.is_active
                ? 'Deactivating…'
                : 'Reactivating…'
              : staffMember.is_active
                ? 'Deactivate'
                : 'Reactivate'}
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      {resettingPassword && (
        <form
          onSubmit={handleResetPassword}
          className="mt-2 flex items-end gap-2 border-t border-neutral-100 pt-2"
        >
          <div className="flex-1">
            <FormField
              label={`New password for ${staffMember.business_name}`}
              type="password"
              value={newPassword}
              onChange={(e) => {
                setNewPassword(e.target.value);
                setResetDone(false);
              }}
              required
            />
          </div>
          <button
            type="submit"
            disabled={resetSubmitting || !newPassword}
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm disabled:opacity-50"
          >
            {resetSubmitting ? 'Setting…' : 'Set password'}
          </button>
          {resetDone && <p className="text-sm text-green-700">Password updated.</p>}
          {resetError && (
            <p role="alert" className="text-sm text-red-600">
              {resetError}
            </p>
          )}
        </form>
      )}
    </li>
  );
}

function StaffManagement({ account }: { account: BusinessAccount }) {
  const [resources, setResources] = useState<ResourceInfo[] | null>(null);
  const [staff, setStaff] = useState<StaffAccount[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const ownedResourceIds = useMemo(
    () => account.owned_resource_ids ?? [],
    [account.owned_resource_ids]
  );

  const applyResult = useCallback(
    (result: [ResourceInfo[], StaffAccount[]] | null, err: unknown) => {
      if (result !== null) {
        setResources(result[0]);
        setStaff(result[1]);
        setLoadError(null);
      } else {
        setLoadError(err instanceof ApiError ? err.message : 'Could not load staff or resources');
      }
    },
    []
  );

  const load = useCallback(async () => {
    try {
      const result = await Promise.all([fetchOwnedResources(ownedResourceIds), listStaff()]);
      applyResult(result, null);
    } catch (err) {
      applyResult(null, err);
    }
  }, [ownedResourceIds, applyResult]);

  useEffect(() => {
    let ignore = false;
    Promise.all([fetchOwnedResources(ownedResourceIds), listStaff()])
      .then((result) => {
        if (!ignore) applyResult(result, null);
      })
      .catch((err) => {
        if (!ignore) applyResult(null, err);
      });
    return () => {
      ignore = true;
    };
  }, [ownedResourceIds, applyResult]);

  if (loadError) return <ErrorState description={loadError} onRetry={load} />;
  if (resources === null || staff === null) return <LoadingState label="Loading staff…" />;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Staff</h1>
        <Link href="/dashboard" className="text-sm text-neutral-600 underline">
          Back to dashboard
        </Link>
      </div>

      {staff.length === 0 ? (
        <EmptyState
          title="No staff yet"
          description="Add a staff account below to give someone their own login."
        />
      ) : (
        <ul className="flex flex-col divide-y divide-neutral-200 rounded-lg border border-neutral-200">
          {staff.map((s) => (
            <StaffRow
              key={s.id}
              staffMember={s}
              resourceName={resources.find((r) => r.id === s.assigned_resource_ids[0])?.name}
              onStatusChanged={(staffId, isActive) => {
                setStaff((prev) =>
                  (prev ?? []).map((existing) =>
                    existing.id === staffId
                      ? {
                          ...existing,
                          is_active: isActive,
                          // Deactivating clears the assignment server-side too — same
                          // real gap the earlier unassign work closed one level up.
                          assigned_resource_ids: isActive ? existing.assigned_resource_ids : [],
                        }
                      : existing
                  )
                );
                if (!isActive) {
                  // `s` here is this specific row's own staff account, from the
                  // enclosing `staff.map((s) => ...)` — its `assigned_resource_ids`
                  // as of this render is exactly what just got cleared server-side.
                  setResources((prev) =>
                    (prev ?? []).map((res) =>
                      s.assigned_resource_ids.includes(res.id)
                        ? { ...res, assigned_staff_id: null }
                        : res
                    )
                  );
                }
              }}
            />
          ))}
        </ul>
      )}

      <CreateStaffForm onCreated={(newStaff) => setStaff((prev) => [...(prev ?? []), newStaff])} />

      {resources.length > 0 && (
        <div className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold text-neutral-900">Assign staff to a resource</h2>
          <ul className="flex flex-col divide-y divide-neutral-200 rounded-lg border border-neutral-200">
            {resources.map((r) => (
              <AssignStaffRow
                key={r.id}
                resource={r}
                staff={staff}
                onAssigned={(resourceId, staffId) => {
                  setResources((prev) =>
                    (prev ?? []).map((res) =>
                      res.id === resourceId ? { ...res, assigned_staff_id: staffId } : res
                    )
                  );
                  setStaff((prev) =>
                    (prev ?? []).map((s) => ({
                      ...s,
                      // Re-assigning a resource that's already assigned to this same
                      // staff account (a redundant Assign click, no-op server-side)
                      // must not duplicate resourceId in local state — filter first,
                      // then append, rather than a blind spread-append.
                      assigned_resource_ids:
                        s.id === staffId
                          ? [...s.assigned_resource_ids.filter((id) => id !== resourceId), resourceId]
                          : s.assigned_resource_ids.filter((id) => id !== resourceId),
                    }))
                  );
                }}
                onUnassigned={(resourceId) => {
                  setResources((prev) =>
                    (prev ?? []).map((res) =>
                      res.id === resourceId ? { ...res, assigned_staff_id: null } : res
                    )
                  );
                  setStaff((prev) =>
                    (prev ?? []).map((s) => ({
                      ...s,
                      assigned_resource_ids: s.assigned_resource_ids.filter(
                        (id) => id !== resourceId
                      ),
                    }))
                  );
                }}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function StaffPage() {
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
      // Only an owner has staff of its own to manage (mirrors `staff_view`'s own
      // owner-only 403 posture) — a staff account here has nothing to do, so send
      // it to the same place `app/dashboard/page.tsx` already sends it.
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
  return <StaffManagement account={account} />;
}
