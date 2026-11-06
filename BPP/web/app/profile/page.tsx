'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { FormField } from '@/components/ui/FormField';
import { SearchableSelect } from '@/components/ui/SearchableSelect';
import { useToast } from '@/components/ui/Toast';
import { changePassword, updateProfile, type BusinessAccount } from '@/lib/auth-api';
import { fetchDivisions, fetchPincodesForDivision, type DivisionOption } from '@/lib/pincodes-api';
import { ApiError } from '@/lib/api-client';
import { capitalizeWords } from '@/lib/format';
import { ALL_INDIA_STATES } from '@/lib/constants';
import { useAccount } from '@/lib/account-context';

/** Business identity, public-facing contact info, and the self-service Live/Offline
 * toggle — owner-only, same owner-vs-staff scoping as /dashboard (a staff account has
 * no business identity of its own to edit). One edit action covers every info field at
 * once, rather than 5 separate cards each with their own edit/save — less repetitive
 * chrome, and editing several fields together needs one save, not several.
 *
 * Reads/writes the account through the shared AccountProvider (not its own separate
 * `me()` fetch) so a save here updates the header title — and anything else reading
 * the same context — in the same render, not just this page. */

// Mirrors BPP/backend/core/views.py's own REQUIRED_FOR_LIVE_PROFILE_FIELDS
// exactly — a business can't go "Live" with a half-filled listing. The backend
// is what actually enforces this (rejects is_accepting_bookings: true with a
// real error if anything's missing); this list only drives the Status toggle's
// own disabled state, so the UI doesn't invite an action the server will refuse.
const REQUIRED_BUSINESS_INFO_FIELDS: (keyof BusinessAccount)[] = [
  'business_name',
  'description',
  'public_phone',
  'public_email',
  'address',
  'state',
  'division',
  'pincode',
];

function isBusinessInfoComplete(account: BusinessAccount): boolean {
  return REQUIRED_BUSINESS_INFO_FIELDS.every((field) => (account[field] as string)?.trim());
}
export default function ProfilePage() {
  const router = useRouter();
  const { showToast } = useToast();
  const { account, loading, setAccount } = useAccount();

  useEffect(() => {
    if (loading) return;
    if (account === null) {
      router.push('/login');
      return;
    }
    if (account.role === 'STAFF') {
      const firstAssigned = account.assigned_resource_ids?.[0];
      router.push(firstAssigned ? `/resources/${firstAssigned}/availability` : '/');
    }
  }, [loading, account, router]);

  if (loading || account === null || account.role === 'STAFF') {
    return <LoadingState label="Loading your profile…" />;
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Profile</h1>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <BusinessInfoCard
          account={account}
          onSaved={(updated) => {
            setAccount(updated);
            showToast('Profile updated');
          }}
        />

        <div className="flex flex-col gap-6">
          <StatusCard
            isAcceptingBookings={account.is_accepting_bookings}
            isProfileComplete={isBusinessInfoComplete(account)}
            onSaved={(updated) => {
              setAccount(updated);
              showToast(updated.is_accepting_bookings ? "You're live" : "You're now offline");
            }}
          />

          <PasswordCard onSaved={() => showToast('Password updated successfully')} />
        </div>
      </div>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-medium text-neutral-500">{label}</dt>
      <dd className={`text-sm ${value ? 'text-neutral-900' : 'italic text-neutral-400'}`}>
        {value || 'Not set'}
      </dd>
    </div>
  );
}

function BusinessInfoCard({
  account,
  onSaved,
}: {
  account: BusinessAccount;
  onSaved: (account: BusinessAccount) => void;
}) {
  const [editing, setEditing] = useState(false);
  // `?? ''` throughout: guards against an input ever receiving `undefined` as its
  // value (React logs "changing an uncontrolled input to controlled" the moment a
  // controlled input's value goes from undefined to defined) — cheap defense against
  // any account payload that's missing one of these newer fields.
  const [businessName, setBusinessName] = useState(account.business_name ?? '');
  const [description, setDescription] = useState(account.description ?? '');
  const [publicPhone, setPublicPhone] = useState(account.public_phone ?? '');
  const [publicEmail, setPublicEmail] = useState(account.public_email ?? '');
  const [address, setAddress] = useState(account.address ?? '');
  // The cascading State -> Division -> Pincode picker: selecting a state loads
  // its real divisions (or reveals none exist yet — "Coming soon"); selecting a
  // division loads its real pincodes. Each select's own option list only exists
  // once its parent has been chosen, matching the required step-by-step flow.
  const [state, setState] = useState(account.state ?? '');
  const [division, setDivision] = useState(account.division ?? '');
  const [pincode, setPincode] = useState(account.pincode ?? '');
  const [divisionOptions, setDivisionOptions] = useState<DivisionOption[]>([]);
  const [pincodeOptions, setPincodeOptions] = useState<string[]>([]);
  const [divisionsLoading, setDivisionsLoading] = useState(false);
  const [pincodesLoading, setPincodesLoading] = useState(false);
  // Distinct from "no divisions/pincodes exist yet for this state" (a real,
  // successful empty result — shown as "Coming soon") — this is only set when
  // the request itself actually failed, so a real bug never gets silently
  // mistaken for missing data.
  const [divisionsError, setDivisionsError] = useState<string | null>(null);
  const [pincodesError, setPincodesError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // `keepDivision` restores an already-saved division's pincode list when
  // re-entering edit mode (see startEditing below) — empty for a fresh state
  // pick, where there's nothing to restore.
  async function loadDivisionsForState(stateName: string, keepDivision: string) {
    setDivisionsLoading(true);
    setDivisionsError(null);
    try {
      const divisions = await fetchDivisions(stateName);
      setDivisionOptions(divisions);
      const match = divisions.find((d) => d.name === keepDivision);
      if (!match) {
        setPincodeOptions([]);
        return;
      }
      setPincodesLoading(true);
      setPincodesError(null);
      try {
        setPincodeOptions(await fetchPincodesForDivision(match.id));
      } catch (err) {
        setPincodesError(err instanceof ApiError ? err.message : 'Could not load pincodes');
      } finally {
        setPincodesLoading(false);
      }
    } catch (err) {
      setDivisionOptions([]);
      setPincodeOptions([]);
      setDivisionsError(err instanceof ApiError ? err.message : 'Could not load divisions');
    } finally {
      setDivisionsLoading(false);
    }
  }

  function handleStateChange(newState: string) {
    setState(newState);
    setDivision('');
    setPincode('');
    setDivisionOptions([]);
    setPincodeOptions([]);
    setDivisionsError(null);
    setPincodesError(null);
    if (newState) loadDivisionsForState(newState, '');
  }

  async function handleDivisionChange(newDivisionName: string) {
    setDivision(newDivisionName);
    setPincode('');
    setPincodesError(null);
    const match = divisionOptions.find((d) => d.name === newDivisionName);
    if (!match) {
      setPincodeOptions([]);
      return;
    }
    setPincodesLoading(true);
    try {
      setPincodeOptions(await fetchPincodesForDivision(match.id));
    } catch (err) {
      setPincodeOptions([]);
      setPincodesError(err instanceof ApiError ? err.message : 'Could not load pincodes');
    } finally {
      setPincodesLoading(false);
    }
  }

  function startEditing() {
    setBusinessName(account.business_name ?? '');
    setDescription(account.description ?? '');
    setPublicPhone(account.public_phone ?? '');
    setPublicEmail(account.public_email ?? '');
    setAddress(account.address ?? '');
    setState(account.state ?? '');
    setDivision(account.division ?? '');
    setPincode(account.pincode ?? '');
    setError(null);
    setEditing(true);
    if (account.state) {
      loadDivisionsForState(account.state, account.division ?? '');
    } else {
      setDivisionOptions([]);
      setPincodeOptions([]);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const trimmedName = businessName.trim();
    if (!trimmedName) {
      setError('Business name is required');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const updated = await updateProfile({
        business_name: capitalizeWords(trimmedName),
        description: description.trim(),
        public_phone: publicPhone.trim(),
        public_email: publicEmail.trim(),
        address: address.trim(),
        state,
        division,
        pincode,
      });
      setEditing(false);
      onSaved(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update your profile');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-neutral-900">Business info</h2>
        {!editing && (
          <button
            type="button"
            onClick={startEditing}
            className="cursor-pointer rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
          >
            Edit
          </button>
        )}
      </div>

      {editing ? (
        <form onSubmit={handleSave} className="flex flex-col gap-4">
          <FormField
            label="Business name"
            value={businessName}
            onChange={(e) => setBusinessName(e.target.value)}
            error={error ?? undefined}
            autoFocus
            required
          />
          <div className="flex flex-col gap-1.5">
            <label htmlFor="profile-description" className="text-sm font-medium text-neutral-900">
              Description
            </label>
            <textarea
              id="profile-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Tell customers what your business offers"
              rows={3}
              className="w-full rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
          </div>
          <FormField
            label="Public phone number"
            type="tel"
            value={publicPhone}
            onChange={(e) => setPublicPhone(e.target.value)}
            placeholder="Visible to customers on your listing"
          />
          <FormField
            label="Public email"
            type="email"
            value={publicEmail}
            onChange={(e) => setPublicEmail(e.target.value)}
            placeholder="Visible to customers on your listing"
          />
          <div className="flex flex-col gap-1.5">
            <label htmlFor="profile-address" className="text-sm font-medium text-neutral-900">
              Address
            </label>
            <textarea
              id="profile-address"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              placeholder="Visible to customers on your listing"
              rows={2}
              className="w-full rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
          </div>
          <SearchableSelect
            label="State"
            value={state}
            onChange={handleStateChange}
            options={ALL_INDIA_STATES}
            placeholder="Select your state"
          />
          {state && (
            divisionsLoading ? (
              <p className="text-sm text-neutral-500">Loading divisions…</p>
            ) : divisionsError ? (
              <p role="alert" className="text-sm text-red-600">
                {divisionsError}
              </p>
            ) : divisionOptions.length === 0 ? (
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-neutral-900">Division</label>
                <p className="rounded-md border border-dashed border-neutral-300 px-3 py-2 text-sm text-neutral-500">
                  Coming soon — no division data for {state} yet.
                </p>
              </div>
            ) : (
              <SearchableSelect
                label="Division"
                value={division}
                onChange={handleDivisionChange}
                options={divisionOptions.map((d) => d.name)}
                placeholder="Select your division"
              />
            )
          )}
          {division && (
            pincodesLoading ? (
              <p className="text-sm text-neutral-500">Loading pincodes…</p>
            ) : pincodesError ? (
              <p role="alert" className="text-sm text-red-600">
                {pincodesError}
              </p>
            ) : (
              <SearchableSelect
                label="Pincode"
                value={pincode}
                onChange={setPincode}
                options={pincodeOptions}
                placeholder="Select your pincode"
              />
            )
          )}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={submitting || !businessName.trim()}
              className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              disabled={submitting}
              className="cursor-pointer rounded-md border border-neutral-300 px-4 py-1.5 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <dl className="flex flex-col gap-4">
          <InfoRow label="Business name" value={capitalizeWords(account.business_name)} />
          <InfoRow label="Description" value={account.description} />
          <InfoRow label="Public phone number" value={account.public_phone} />
          <InfoRow label="Public email" value={account.public_email} />
          <InfoRow label="Address" value={account.address} />
          <InfoRow label="State" value={account.state} />
          <InfoRow label="Division" value={account.division} />
          <InfoRow label="Pincode" value={account.pincode} />
        </dl>
      )}
    </div>
  );
}

function StatusCard({
  isAcceptingBookings,
  isProfileComplete,
  onSaved,
}: {
  isAcceptingBookings: boolean;
  isProfileComplete: boolean;
  onSaved: (account: BusinessAccount) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Only blocks turning ON — going offline is always allowed regardless of
  // profile completeness, this never traps an incomplete account live.
  const blockedFromGoingLive = !isAcceptingBookings && !isProfileComplete;

  async function handleToggle() {
    if (blockedFromGoingLive) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await updateProfile({ is_accepting_bookings: !isAcceptingBookings });
      onSaved(updated);
    } catch (err) {
      // Covers the server's own enforcement of the same rule — real defense in
      // depth if this ever gets called with a stale isProfileComplete value.
      setError(err instanceof ApiError ? err.message : 'Could not update your status');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-neutral-900">Status</h2>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={`h-2.5 w-2.5 rounded-full ${
              isAcceptingBookings ? 'bg-green-500' : 'bg-neutral-400'
            }`}
          />
          <p className="text-sm text-neutral-900">
            {isAcceptingBookings ? 'Live — visible to customers' : 'Offline — hidden from search'}
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={isAcceptingBookings}
          aria-label="Accepting bookings"
          onClick={handleToggle}
          disabled={submitting || blockedFromGoingLive}
          title={blockedFromGoingLive ? 'Complete every field in Business info to go live' : undefined}
          className={`inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full p-0.5 transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
            blockedFromGoingLive ? 'cursor-not-allowed' : 'cursor-pointer'
          } ${isAcceptingBookings ? 'bg-[var(--brand-600)]' : 'bg-neutral-300'}`}
        >
          <span
            aria-hidden="true"
            className={`h-5 w-5 rounded-full bg-white shadow transition-transform ${
              isAcceptingBookings ? 'translate-x-5' : 'translate-x-0'
            }`}
          />
        </button>
      </div>
      {blockedFromGoingLive && (
        <p className="text-xs text-neutral-500">
          Complete every field in Business info before going live.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}

function PasswordCard({ onSaved }: { onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setCurrentPassword('');
    setNewPassword('');
    setConfirmPassword('');
    setError(null);
  }

  function startEditing() {
    reset();
    setEditing(true);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    // Client-only checks first — no reason to round-trip to the server for a typo
    // in the confirm field, or for a field the user just left empty.
    if (!currentPassword || !newPassword || !confirmPassword) {
      setError('All three fields are required');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('New password and confirm password do not match');
      return;
    }
    if (newPassword === currentPassword) {
      setError('New password must be different from your current password');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await changePassword(currentPassword, newPassword);
      reset();
      setEditing(false);
      onSaved();
    } catch (err) {
      // Covers the server-side cases a client can't check itself: wrong current
      // password, and Django's own password-strength rules (too short, too common,
      // too similar to the account's own name/email, entirely numeric).
      setError(err instanceof ApiError ? err.message : 'Could not update your password');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-neutral-900">Password</h2>
        {!editing && (
          <button
            type="button"
            onClick={startEditing}
            className="cursor-pointer rounded-md border border-neutral-300 px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
          >
            Edit
          </button>
        )}
      </div>

      {editing ? (
        <form onSubmit={handleSave} className="flex flex-col gap-4">
          <FormField
            label="Current password"
            type="password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
            autoFocus
            required
          />
          <FormField
            label="New password"
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
            required
          />
          <FormField
            label="Confirm new password"
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
            required
          />
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={submitting || !currentPassword || !newPassword || !confirmPassword}
              className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? 'Updating…' : 'Update password'}
            </button>
            <button
              type="button"
              onClick={() => {
                reset();
                setEditing(false);
              }}
              disabled={submitting}
              className="cursor-pointer rounded-md border border-neutral-300 px-4 py-1.5 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <p className="text-sm text-neutral-600">••••••••</p>
      )}
    </div>
  );
}
