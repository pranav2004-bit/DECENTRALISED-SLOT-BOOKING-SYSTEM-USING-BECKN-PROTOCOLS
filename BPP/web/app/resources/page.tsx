'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { EmptyState } from '@/components/ui/EmptyState';
import { FormField } from '@/components/ui/FormField';
import { Modal } from '@/components/ui/Modal';
import { Tooltip } from '@/components/ui/Tooltip';
import { useToast } from '@/components/ui/Toast';
import { me, type BusinessAccount } from '@/lib/auth-api';
import { getSlots, type ResourceInfo, type SlotInfo } from '@/lib/availability-api';
import {
  createResource,
  createAvailability,
  deleteAvailability,
  fetchOwnedResources,
  updateResource,
  setResourceStatus,
  deleteResource,
} from '@/lib/resources-api';
import { ApiError } from '@/lib/api-client';
import { RESOURCE_TYPES_BY_DOMAIN } from '@/lib/constants';
import { capitalizeWords } from '@/lib/format';
import { BRAND } from '@/lib/brand';

/**
 * livetracker3.md §7.1: the resource-list home a logged-in owner manages their
 * bookable resources from. Two real, different UIs live in this one file: Healthcare
 * gets the purpose-built "Doctors" table (name/specialisation/gender/availability/
 * actions, per this brand's own domain_data fields) at the user's explicit request;
 * every other brand keeps the original generic resource list unchanged — a chair or
 * a bay has no "specialisation"/"years of experience" concept, so those fields
 * only apply, and only render, for Healthcare.
 */

const IS_HEALTHCARE = BRAND.id === 'medical';
const IS_BEAUTY = BRAND.id === 'beauty';
const IS_AUTOMOTIVE = BRAND.id === 'automotive';

// ============================================================================
// Generic (Beauty/Automotive) view — unchanged from before the Doctors rework.
// ============================================================================

function CreateResourceForm({
  domainCode,
  onCreated,
}: {
  domainCode: string;
  onCreated: (resource: {
    id: string;
    name: string;
    price_currency: string;
    price_value: string;
    created_at: string;
  }) => void;
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
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-white p-4">
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
        className="cursor-pointer self-start rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
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
      <div className="flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-4">
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
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-white p-4">
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
        className="cursor-pointer self-start rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
      >
        {submitting ? 'Generating…' : 'Generate availability'}
      </button>
    </form>
  );
}

function GenericResourcesView({ account }: { account: BusinessAccount }) {
  const [resources, setResources] = useState<ResourceInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
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

  const ownedResourceIds = useMemo(
    () => account.owned_resource_ids ?? [],
    [account.owned_resource_ids]
  );

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
          {capitalizeWords(account.business_name)}
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
        <ul className="flex flex-col divide-y divide-neutral-200 rounded-lg border border-neutral-200 bg-white">
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
          setResources((prev) => [
            ...(prev ?? []),
            {
              id: resource.id,
              name: resource.name,
              average_rating: null,
              rating_count: 0,
              assigned_staff_id: null,
              domain_data: {},
              is_active: true,
              price_currency: resource.price_currency,
              price_value: resource.price_value,
              created_at: resource.created_at,
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

// ============================================================================
// Healthcare — the "Doctors" table.
// ============================================================================

const GENDER_OPTIONS = [
  { value: 'male', label: 'Male' },
  { value: 'female', label: 'Female' },
  { value: 'other', label: 'Other' },
];

// A single doctor can only see one patient per appointment slot — unlike a
// salon chair or a garage bay, "capacity per slot" isn't a real choice
// here, so it's fixed rather than exposed as a form field.
const DOCTOR_SLOT_CAPACITY = 1;

// Mirrors BPP/backend/core/views.py's own MAX_SLOT_COUNT exactly — the
// client-side input's own ceiling, so a value the backend would reject
// outright isn't even typeable first (same "mirrored, not shared across the
// language boundary" convention as SPECIALISATIONS/BEAUTY_SERVICES above).
const MAX_SLOT_COUNT = 500;

// Matches AvailabilityCalendar.days' own weekday tokens (shared/inventory_core/models.py's
// _WEEKDAY_TOKENS) exactly — sent to the backend as-is, never translated.
const WEEKDAY_TOKENS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'] as const;
const WEEKDAY_LABELS: Record<(typeof WEEKDAY_TOKENS)[number], string> = {
  MON: 'Mon',
  TUE: 'Tue',
  WED: 'Wed',
  THU: 'Thu',
  FRI: 'Fri',
  SAT: 'Sat',
  SUN: 'Sun',
};
// JS Date.getDay() is Sun-first (0=Sun..6=Sat) — this re-indexes into the same
// MON..SUN tokens the backend uses, not a second parallel day-naming scheme.
const JS_DAY_INDEX_TO_TOKEN = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'] as const;

// Mirrored in BPP/backend/core/healthcare_adapter.py's own SPECIALISATIONS
// constant — a fixed list, not free text, kept in sync across the language
// boundary the same way GENDER_OPTIONS/domain codes already are (ADR-0004).
const SPECIALISATIONS = [
  'General Medicine / General Physician',
  'Cardiology',
  'Orthopedics',
  'Gynecology & Obstetrics',
  'Pediatrics',
  'Oncology',
  'Neurology & Neurosurgery',
  'Gastroenterology',
  'Nephrology',
  'Urology',
  'Pulmonology',
  'Dermatology',
  'Ophthalmology',
  'ENT (Otolaryngology)',
  'Endocrinology',
  'Rheumatology',
  'Hematology',
  'Psychiatry',
  'General Surgery',
  'Emergency Medicine',
];

// Mirrored in BPP/backend/core/beauty_adapter.py's own SPECIALISATIONS
// constant — same fixed-list convention as Healthcare's SPECIALISATIONS above.
const BEAUTY_SERVICES = [
  'Hair Styling',
  'Hair Cutting',
  'Hair Coloring',
  'Hair Spa & Treatment',
  'Bridal Makeup',
  'Party Makeup',
  'Facial & Skin Care',
  'Manicure',
  'Pedicure',
  'Waxing',
  'Threading',
  'Mehendi (Henna)',
  'Massage & Spa',
  'Nail Art',
];

interface Doctor {
  id: string;
  name: string;
  is_active: boolean;
  // Whether this doctor currently has at least one still-open (AVAILABLE)
  // slot right now — drives the Availability button's green/red color in
  // the table. Refreshed on doctor list load and whenever the availability
  // modal closes, not live-ticking otherwise.
  hasLiveAvailability: boolean;
  domain_data: {
    doctor_id?: string;
    // Beauty's equivalent of doctor_id (StaffTableView/AddStylistForm below)
    // — same type reused for both rather than a separate near-identical
    // interface, since the field shapes already line up one-for-one.
    staff_id?: string;
    // Automotive's equivalent — a bay has no person-only fields below
    // (specialisation/education/years_of_experience/gender), just a name,
    // price and this id.
    bay_id?: string;
    specialisation?: string;
    education?: string;
    years_of_experience?: number;
    gender?: string;
  };
  price_currency: string;
  price_value: string;
  /** "Date of joining" — the slot-history browser doesn't go back further than this. */
  created_at: string;
}

function AddDoctorForm({
  onCreated,
  onCancel,
}: {
  onCreated: (doctor: Doctor) => void;
  onCancel: () => void;
}) {
  const { showToast } = useToast();
  const [name, setName] = useState('');
  const [specialisation, setSpecialisation] = useState(SPECIALISATIONS[0]);
  const [education, setEducation] = useState('');
  const [yearsOfExperience, setYearsOfExperience] = useState('');
  // Defaults to Female (not Male) at the user's explicit request — still a fully
  // editable dropdown, this only changes which option is pre-selected.
  const [gender, setGender] = useState('female');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = name.trim() && specialisation && education.trim() && yearsOfExperience !== '';

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const doctor = await createResource(name.trim(), 'doctor', {
        specialisation,
        education: education.trim(),
        years_of_experience: Number(yearsOfExperience),
        gender,
      });
      onCreated({
        id: doctor.id,
        name: doctor.name,
        is_active: doctor.is_active,
        hasLiveAvailability: false,
        domain_data: doctor.domain_data,
        price_currency: doctor.price_currency,
        price_value: doctor.price_value,
        created_at: doctor.created_at,
      });
      showToast('Doctor added successfully');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add this doctor');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <FormField label="Name" value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="doctor-specialisation" className="text-sm font-medium text-neutral-900">
          Specialisation
        </label>
        <select
          id="doctor-specialisation"
          value={specialisation}
          onChange={(e) => setSpecialisation(e.target.value)}
          className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
        >
          {SPECIALISATIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>
      <FormField
        label="Education"
        value={education}
        onChange={(e) => setEducation(e.target.value)}
        placeholder="e.g. MBBS, MD"
        required
      />
      <FormField
        label="Years of experience"
        type="number"
        min={0}
        max={80}
        value={yearsOfExperience}
        onChange={(e) => setYearsOfExperience(e.target.value)}
        required
      />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="doctor-gender" className="text-sm font-medium text-neutral-900">
          Gender
        </label>
        <select
          id="doctor-gender"
          value={gender}
          onChange={(e) => setGender(e.target.value)}
          className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
        >
          {GENDER_OPTIONS.map((option) => (
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
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting || !canSubmit}
          className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm text-white hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Adding…' : 'Add doctor'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function formatSlotStart(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

/** Formats a plain "HH:MM" time-of-day string (not an ISO instant — see
 * formatSlotStart above for that) as 12-hour clock time, e.g. "17:00" ->
 * "5:00 PM". Used by AutoCare's token/queue mode to show the daily window
 * (start-end) as a single label, instead of per-time-anchor slot chips. */
function formatClockTime(time: string): string {
  const [h, m] = time.split(':').map(Number);
  const period = h >= 12 ? 'PM' : 'AM';
  const hour12 = h % 12 === 0 ? 12 : h % 12;
  return `${hour12}:${String(m).padStart(2, '0')} ${period}`;
}

// A real AVAILABLE slot whose end_time has already passed was never booked and
// is no longer offerable — it's stale history, not something a patient could
// still book, so the owner-facing view shouldn't color it the same live teal
// as a genuinely open future slot. BOOKED/HELD/CANCELLED already carry their
// own accurate meaning regardless of time, so only AVAILABLE is reinterpreted.
type DisplayStatus = SlotInfo['status'] | 'EXPIRED';
function displaySlotStatus(slot: SlotInfo): DisplayStatus {
  if (slot.status === 'AVAILABLE' && new Date(slot.end_time).getTime() < Date.now()) {
    return 'EXPIRED';
  }
  return slot.status;
}

function slotStatusLabel(status: DisplayStatus): string {
  return status.charAt(0) + status.slice(1).toLowerCase();
}

function slotStatusClasses(status: DisplayStatus): string {
  switch (status) {
    case 'AVAILABLE':
      return 'border-[var(--brand-600)] bg-white text-[var(--brand-700)]';
    case 'HELD':
      return 'border-amber-400 bg-amber-50 text-amber-700';
    case 'BOOKED':
      return 'border-blue-400 bg-blue-50 text-blue-700';
    case 'CANCELLED':
      return 'border-neutral-300 bg-neutral-100 text-neutral-400 line-through';
    case 'EXPIRED':
      return 'border-neutral-300 bg-neutral-200 text-neutral-600';
  }
}

function slotDurationLabel(slots: SlotInfo[]): string | null {
  if (slots.length === 0) return null;
  const minutes = Math.round(
    (new Date(slots[0].end_time).getTime() - new Date(slots[0].start_time).getTime()) / 60000
  );
  return `${minutes} min slots`;
}

// Mon–Sun bounds of the week containing "now" — the single definition of
// "current week" shared by the Recent view's default scoping and the Doctors
// table's green/red Availability indicator, so the two never disagree about
// what counts as "this week."
function getCurrentWeekRange(): { weekStart: Date; weekEnd: Date } {
  const now = new Date();
  const weekStart = new Date(now);
  weekStart.setDate(now.getDate() - ((now.getDay() + 6) % 7)); // most recent Monday
  weekStart.setHours(0, 0, 0, 0);
  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekStart.getDate() + 6);
  weekEnd.setHours(23, 59, 59, 999);
  return { weekStart, weekEnd };
}

// "Has existing availability" for the Doctors table's green/red indicator: a
// real AVAILABLE slot, not yet expired, falling within the current Mon–Sun
// week. Matches the Recent view's own default scope exactly, so the table
// dot and the modal it opens never contradict each other — e.g. a doctor
// whose only slots are last week's (now expired) shows red here, same as the
// modal showing "No slots scheduled for this week."
function isSlotLiveThisWeek(slot: SlotInfo): boolean {
  if (slot.status !== 'AVAILABLE') return false;
  if (new Date(slot.end_time).getTime() < Date.now()) return false;
  const { weekStart, weekEnd } = getCurrentWeekRange();
  const start = new Date(slot.start_time);
  return start >= weekStart && start <= weekEnd;
}

// Groups a flat slot list into one entry per calendar day, ordered the same
// way the backend already returns them (by start_time) — reused by both the
// current-week view and the history browser's final "slots for this date" step.
function groupSlotsByDate(slots: SlotInfo[]): { date: Date; slots: SlotInfo[] }[] {
  const groups = new Map<string, SlotInfo[]>();
  for (const slot of slots) {
    const dateKey = new Date(slot.start_time).toDateString();
    if (!groups.has(dateKey)) groups.set(dateKey, []);
    groups.get(dateKey)!.push(slot);
  }
  return Array.from(groups.entries()).map(([dateKey, daySlots]) => ({
    date: new Date(dateKey),
    slots: daySlots,
  }));
}

// The compact calendar-slide-style day view (date heading + duration caption +
// slot grid + status legend) — shared by the current-week default view and the
// history browser's final drill-down step, so the two always render a day's
// slots the same way.
function SlotDayGrid({
  group,
  legendSlots,
}: {
  group: { date: Date; slots: SlotInfo[] };
  legendSlots: SlotInfo[];
}) {
  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-bold text-neutral-900">
          {group.date.toLocaleDateString(undefined, {
            weekday: 'short',
            day: 'numeric',
            month: 'short',
            year: 'numeric',
          })}
        </p>
        {IS_AUTOMOTIVE && group.slots.length > 0 ? (
          // Token/queue mode: every slot this day shares the identical window
          // (slotDurationLabel's "N min slots" would be true but meaningless
          // here — the window is the whole day's span, not a per-slot length).
          <p className="text-xs text-neutral-500">
            {formatSlotStart(group.slots[0].start_time)} – {formatSlotStart(group.slots[0].end_time)}
          </p>
        ) : (
          slotDurationLabel(group.slots) && (
            <p className="text-xs text-neutral-500">{slotDurationLabel(group.slots)}</p>
          )
        )}
      </div>
      <div className="grid grid-cols-4 gap-1.5">
        {group.slots.map((slot, index) => (
          <span
            key={slot.id}
            className={`rounded-lg border px-1.5 py-1.5 text-center text-xs font-semibold ${slotStatusClasses(displaySlotStatus(slot))}`}
          >
            {IS_AUTOMOTIVE ? `#${index + 1}` : formatSlotStart(slot.start_time)}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs text-neutral-500">
        {(['AVAILABLE', 'EXPIRED', 'HELD', 'BOOKED', 'CANCELLED'] as const)
          .filter((status) => legendSlots.some((s) => displaySlotStatus(s) === status))
          .map((status) => (
            <span key={status} className="flex items-center gap-1">
              <span
                className={`h-2.5 w-2.5 rounded-sm border ${slotStatusClasses(status)}`}
                aria-hidden="true"
              />
              {slotStatusLabel(status)}
            </span>
          ))}
      </div>
    </>
  );
}

function AvailabilityModal({
  doctor,
  onClose,
  customerNoun = 'patient',
}: {
  doctor: Doctor;
  onClose: () => void;
  /** Reused as-is for Beauty's stylists (StylistsView below) — only this one
   * word in the preview copy differs between the two. */
  customerNoun?: string;
}) {
  const { showToast } = useToast();
  // Date and time kept as separate inputs (not one combined datetime-local
  // widget) — merged into a single ISO-ish string below only where the rest
  // of this component needs one value (the preview calc, the real submit).
  const [rangeStartDate, setRangeStartDate] = useState('');
  const [rangeStartTime, setRangeStartTime] = useState('');
  const [rangeEndDate, setRangeEndDate] = useState('');
  const [rangeEndTime, setRangeEndTime] = useState('');
  const rangeStart = rangeStartDate && rangeStartTime ? `${rangeStartDate}T${rangeStartTime}` : '';
  const rangeEnd = rangeEndDate && rangeEndTime ? `${rangeEndDate}T${rangeEndTime}` : '';
  // From can't be set in the past — a doctor's own current date/time is the
  // floor. Computed fresh on each render (no ticking clock needed for a
  // short-lived form) rather than once at mount, so it stays correct if the
  // modal is left open across midnight.
  const now = new Date();
  const todayDateString = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const nowTimeString = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  const minStartTime = rangeStartDate === todayDateString ? nowTimeString : undefined;
  // To can't be before From either — its floor is whatever From is currently
  // set to (which itself is already floored at now/today above), falling
  // back to today/now when From hasn't been picked yet.
  const minEndDate = rangeStartDate || todayDateString;
  const minEndTime = rangeEndDate === (rangeStartDate || todayDateString)
    ? rangeStartTime || nowTimeString
    : undefined;
  const [slotDurationMinutes, setSlotDurationMinutes] = useState(30);
  // AutoCare's token/queue mode only (IS_AUTOMOTIVE) — how many identical-time,
  // individually-bookable numbered slots the daily window generates, replacing
  // "Slot duration" (which doesn't apply here: every slot shares the whole
  // window, nothing is sliced by duration). Kept as a string, same pattern as
  // AddStylistForm's yearsOfExperience, so an empty field doesn't coerce to a
  // misleading 0.
  const [maxCapacity, setMaxCapacity] = useState('20');
  // Which weekdays this recurring window applies to — all 7 selected by default
  // (matches the previous, implicit every-day behavior). Sent to the backend as
  // AvailabilityCalendar.days; blank/all-selected means "every day" there too.
  const [selectedDays, setSelectedDays] = useState<string[]>([...WEEKDAY_TOKENS]);
  function toggleDay(token: string) {
    setSelectedDays((prev) =>
      prev.includes(token) ? prev.filter((d) => d !== token) : [...prev, token]
    );
  }
  // The consultation fee charged to the patient for each booked slot — lives on
  // the Resource itself (one fee per doctor, not per-slot), so submitting this
  // form also saves it via updateResource before generating availability.
  const [feePerSlot, setFeePerSlot] = useState(doctor.price_value);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  // Recent — current live slots is the default landing view (what the owner
  // almost always wants: "is this doctor already set up"); the From/To/days
  // form is reached deliberately via "Set up availability" rather than being
  // shown up front every time the modal opens. "history" is a year → month →
  // date → slots drill-down over the doctor's full slot record, reached via
  // a "View history" link from the recent view.
  const [view, setView] = useState<'recent' | 'configure' | 'history'>('recent');
  const [recentSlots, setRecentSlots] = useState<SlotInfo[] | null>(null);
  const [recentLoading, setRecentLoading] = useState(false);
  const [recentError, setRecentError] = useState<string | null>(null);
  const [deletingAvailability, setDeletingAvailability] = useState(false);
  // Which date's slots the calendar-style Recent view shows — a single day at a
  // time (date strip + grid), matching the CustomerSearchDemo slide's layout,
  // not every generated day stacked one under another.
  const [selectedRecentDateKey, setSelectedRecentDateKey] = useState<string | null>(null);
  const [historyStep, setHistoryStep] = useState<'years' | 'months' | 'dates' | 'slots'>('years');
  const [historyYear, setHistoryYear] = useState<number | null>(null);
  const [historyMonth, setHistoryMonth] = useState<number | null>(null);
  const [historyDateKey, setHistoryDateKey] = useState<string | null>(null);

  function openHistory() {
    setHistoryStep('years');
    setHistoryYear(null);
    setHistoryMonth(null);
    setHistoryDateKey(null);
    setView('history');
  }

  async function loadRecentSlots() {
    setRecentLoading(true);
    setRecentError(null);
    try {
      const { slots } = await getSlots(doctor.id);
      setRecentSlots(slots);
    } catch (err) {
      setRecentError(err instanceof ApiError ? err.message : 'Could not load current slots');
    } finally {
      setRecentLoading(false);
    }
  }

  useEffect(() => {
    loadRecentSlots();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleDeleteAvailability() {
    if (!window.confirm(`Delete all open (not yet booked) slots for ${doctor.name}?`)) return;
    setDeletingAvailability(true);
    try {
      const result = await deleteAvailability(doctor.id);
      showToast(
        result.kept_count > 0
          ? `Removed ${result.deleted_count} open slot${result.deleted_count === 1 ? '' : 's'} — ${result.kept_count} kept (already booked)`
          : `Removed ${result.deleted_count} open slot${result.deleted_count === 1 ? '' : 's'}`
      );
      await loadRecentSlots();
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : 'Could not delete availability', 'error');
    } finally {
      setDeletingAvailability(false);
    }
  }

  // No manual slot count — From's time and To's time are each day's session
  // window, and however many `durationMinutes`-sized slots fit back-to-back
  // between them is generated automatically (a trailing slot that would run
  // past the end time is dropped, not truncated), e.g. 19:00 → 20:00 at 30
  // min → 19:00–19:30, 19:30–20:00.
  function expandTimes(startTime: string, endTime: string, durationMinutes: number): string[] {
    // A zero/negative/non-finite duration would never advance the loop below
    // (t += durationMinutes stalls or goes backwards) — an infinite loop that
    // freezes the tab. This is reachable in practice: clearing the "Slot
    // duration" field to retype a new value briefly sets it to 0
    // (Number('') === 0) before the next keystroke lands.
    if (!Number.isFinite(durationMinutes) || durationMinutes <= 0) return [];
    if (!/^\d{1,2}:\d{2}$/.test(startTime) || !/^\d{1,2}:\d{2}$/.test(endTime)) return [];
    const [sh, sm] = startTime.split(':').map(Number);
    const [eh, em] = endTime.split(':').map(Number);
    const startMinutes = sh * 60 + sm;
    const endMinutes = eh * 60 + em;
    const expanded: string[] = [];
    for (let t = startMinutes; t + durationMinutes <= endMinutes; t += durationMinutes) {
      const hh = Math.floor(t / 60) % 24;
      const mm = t % 60;
      expanded.push(`${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`);
    }
    return expanded;
  }

  // AutoCare's token/queue mode only — the whole daily window's own length
  // (end - start) in minutes, which becomes AvailabilityCalendar.slot_duration
  // for that mode (every one of the slot_count identical slots spans the full
  // window, there's no per-time-anchor slicing to compute here).
  function windowMinutes(startTime: string, endTime: string): number {
    if (!/^\d{1,2}:\d{2}$/.test(startTime) || !/^\d{1,2}:\d{2}$/.test(endTime)) return 0;
    const [sh, sm] = startTime.split(':').map(Number);
    const [eh, em] = endTime.split(':').map(Number);
    return eh * 60 + em - (sh * 60 + sm);
  }

  // Preview — a local simulation of the slots this form will generate (daily
  // recurrence, same as the real submit below), computed from the unsaved
  // form values so the owner can sanity-check it before actually calling the
  // backend. Capped at 14 days so a large date range still renders instantly
  // instead of listing hundreds of days.
  const PREVIEW_MAX_DAYS = 14;
  const preview = useMemo(() => {
    if (!rangeStart || !rangeEnd) return null;
    const start = new Date(rangeStart);
    const end = new Date(rangeEnd);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) return null;

    let expandedTimes: string[];
    let tokenCount: number | undefined;
    if (IS_AUTOMOTIVE) {
      // Token/queue mode: one shared window per day, not sliced by duration —
      // `times` carries just the window's own start (matching
      // AvailabilityCalendar's "slot_count requires exactly one anchor" rule),
      // `tokenCount` is how many identical, independently-bookable numbered
      // slots that one window generates.
      const count = Number(maxCapacity);
      if (!/^\d{1,2}:\d{2}$/.test(rangeStartTime) || !/^\d{1,2}:\d{2}$/.test(rangeEndTime)) {
        return null;
      }
      if (!Number.isFinite(count) || count <= 0) return null;
      expandedTimes = [rangeStartTime];
      tokenCount = count;
    } else {
      expandedTimes = expandTimes(rangeStartTime, rangeEndTime, slotDurationMinutes);
      if (expandedTimes.length === 0) return null;
    }

    const days: Date[] = [];
    let totalDays = 0;
    const cursor = new Date(start);
    while (cursor <= end) {
      if (selectedDays.includes(JS_DAY_INDEX_TO_TOKEN[cursor.getDay()])) {
        totalDays += 1;
        if (days.length < PREVIEW_MAX_DAYS) days.push(new Date(cursor));
      }
      cursor.setDate(cursor.getDate() + 1);
    }
    return { days, times: expandedTimes, totalDays, tokenCount };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    rangeStart,
    rangeEnd,
    rangeStartTime,
    rangeEndTime,
    slotDurationMinutes,
    maxCapacity,
    selectedDays,
  ]);

  // "Recent" groups the real, already-generated Slot rows (from getSlots, the
  // same source the live availability dashboard uses) by calendar day — the
  // backend already returns them ordered by start_time, so grouping preserves
  // that order without a separate sort.
  const recentByDate = useMemo(
    () => (recentSlots ? groupSlotsByDate(recentSlots) : null),
    [recentSlots]
  );

  // The default/landing view is scoped to the current Mon–Sun week only — not
  // the doctor's whole slot history — per the owner's own framing: "only show
  // the current week slots... if not yet created, show no slots scheduled."
  const { weekStart, weekEnd } = getCurrentWeekRange();
  const currentWeekGroups =
    recentByDate?.filter((g) => g.date >= weekStart && g.date <= weekEnd) ?? null;

  // Falls back to the first day of the current week whenever the
  // previously-selected one no longer exists (e.g. right after a delete, or
  // on first load).
  const activeWeekGroup =
    (selectedRecentDateKey
      ? currentWeekGroups?.find((g) => g.date.toDateString() === selectedRecentDateKey)
      : null) ?? currentWeekGroups?.[0] ?? null;

  // History browser: bounded below by the doctor's own "date of joining"
  // (Resource.created_at) — never earlier, since no slot can predate it.
  const joiningDate = new Date(doctor.created_at);
  const joiningYear = joiningDate.getFullYear();
  const currentYear = now.getFullYear();
  const latestDataYear = recentSlots?.length
    ? Math.max(...recentSlots.map((s) => new Date(s.start_time).getFullYear()))
    : currentYear;
  const historyYears: number[] = [];
  for (let y = Math.max(currentYear, latestDataYear); y >= joiningYear; y--) historyYears.push(y);

  function monthsForHistoryYear(year: number): number[] {
    const startMonth = year === joiningYear ? joiningDate.getMonth() : 0;
    const months: number[] = [];
    for (let m = 11; m >= startMonth; m--) months.push(m);
    return months;
  }

  const datesForHistoryMonth =
    historyYear !== null && historyMonth !== null
      ? (recentByDate ?? []).filter(
          (g) => g.date.getFullYear() === historyYear && g.date.getMonth() === historyMonth
        )
      : [];

  const historyDateGroup =
    historyDateKey !== null
      ? datesForHistoryMonth.find((g) => g.date.toDateString() === historyDateKey) ?? null
      : null;

  function formatSlotWindow(time: string): string {
    const [h, m] = time.split(':').map(Number);
    const format = (totalMinutes: number) => {
      const hh = Math.floor(totalMinutes / 60) % 24;
      const mm = totalMinutes % 60;
      const period = hh >= 12 ? 'PM' : 'AM';
      const hour12 = hh % 12 === 0 ? 12 : hh % 12;
      return `${hour12}:${String(mm).padStart(2, '0')} ${period}`;
    };
    const startMinutes = h * 60 + m;
    return `${format(startMinutes)} – ${format(startMinutes + slotDurationMinutes)}`;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!rangeStart || !rangeEnd || selectedDays.length === 0) return;
    const maxCapacityNumber = Number(maxCapacity);
    if (IS_AUTOMOTIVE && (!maxCapacity || !Number.isFinite(maxCapacityNumber) || maxCapacityNumber <= 0)) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      if (feePerSlot !== doctor.price_value) {
        await updateResource(doctor.id, { price_value: Number(feePerSlot) });
      }
      await createAvailability(doctor.id, {
        // From/To now carry an explicit date AND time (datetime-local), so the
        // owner controls the exact instant directly — e.g. a single-day window
        // just needs a different time on the same date, satisfying the
        // backend's own range_end > range_start validation without any
        // implicit start/end-of-day anchoring.
        rangeStart: new Date(rangeStart).toISOString(),
        rangeEnd: new Date(rangeEnd).toISOString(),
        // Token/queue mode (AutoCare only): a single anchor — the window's own
        // start — matching AvailabilityCalendar's "slot_count requires exactly
        // one time-of-day anchor" rule; every other brand keeps today's
        // multi-anchor, duration-sliced expansion unchanged.
        times: IS_AUTOMOTIVE ? [rangeStartTime] : expandTimes(rangeStartTime, rangeEndTime, slotDurationMinutes),
        frequencyDays: 1,
        // All 7 selected is sent as '' (every day) — identical to the old,
        // implicit behavior — rather than spelling out all 7 tokens.
        days: selectedDays.length === WEEKDAY_TOKENS.length ? '' : selectedDays.join(','),
        // Token/queue mode's "slot_duration" is the whole daily window's own
        // length (end - start), not a single slot's length — every one of the
        // slot_count identical slots spans the full window. windowMinutes(...)
        // below (same HH:MM parsing expandTimes already uses) computes that.
        slotDurationMinutes: IS_AUTOMOTIVE
          ? windowMinutes(rangeStartTime, rangeEndTime)
          : slotDurationMinutes,
        slotCapacity: DOCTOR_SLOT_CAPACITY,
        ...(IS_AUTOMOTIVE ? { slotCount: maxCapacityNumber } : {}),
      });
      showToast(`Availability set for ${doctor.name}`);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not generate availability');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      title={view === 'recent' ? `Availability for ${doctor.name}` : `Set up availability for ${doctor.name}`}
      onClose={onClose}
    >
      {view === 'recent' ? (
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex flex-col gap-0.5">
              <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
                Recent — current live slots
              </p>
              <button
                type="button"
                onClick={openHistory}
                className="w-fit cursor-pointer rounded-md border border-[var(--brand-600)] px-3 py-1 text-xs font-medium text-[var(--brand-700)] hover:bg-[var(--brand-50)]"
              >
                View history
              </button>
            </div>
            {recentSlots !== null && recentSlots.some((s) => displaySlotStatus(s) === 'AVAILABLE') && (
              <button
                type="button"
                onClick={handleDeleteAvailability}
                disabled={deletingAvailability}
                className="cursor-pointer rounded-md border border-red-300 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {deletingAvailability ? 'Deleting…' : 'Delete availability'}
              </button>
            )}
          </div>
          {recentLoading ? (
            <p className="text-sm text-neutral-600">Loading current slots…</p>
          ) : recentError ? (
            <p role="alert" className="text-sm text-red-600">
              {recentError}
            </p>
          ) : !recentByDate || recentByDate.length === 0 ? (
            <p className="text-sm text-neutral-600">No availability generated for this doctor yet.</p>
          ) : !currentWeekGroups || currentWeekGroups.length === 0 ? (
            <p className="text-sm text-neutral-600">No slots scheduled for this week.</p>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="-mx-5 flex divide-x divide-neutral-200 overflow-x-auto border-b border-neutral-200">
                {currentWeekGroups.map(({ date, slots }) => {
                  const dateKey = date.toDateString();
                  const active = activeWeekGroup?.date.toDateString() === dateKey;
                  return (
                    <button
                      key={dateKey}
                      type="button"
                      onClick={() => setSelectedRecentDateKey(dateKey)}
                      className={
                        active
                          ? 'flex min-w-[52px] flex-none cursor-pointer flex-col items-center bg-[var(--brand-100)] py-2 text-[var(--brand-700)]'
                          : 'flex min-w-[52px] flex-none cursor-pointer flex-col items-center py-2 text-neutral-600 hover:bg-neutral-50'
                      }
                    >
                      <span className="text-xs font-medium tracking-wide">
                        {date.toLocaleDateString(undefined, { weekday: 'short' }).toUpperCase()}
                      </span>
                      <span className="text-xs font-bold">{date.getDate()}</span>
                    </button>
                  );
                })}
              </div>

              {activeWeekGroup && <SlotDayGrid group={activeWeekGroup} legendSlots={recentSlots ?? []} />}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setView('configure')}
              className="flex cursor-pointer items-center gap-1.5 rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--brand-700)]"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
              Set up availability
            </button>
          </div>
        </div>
      ) : view === 'history' ? (
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => {
                if (historyStep === 'years') {
                  setView('recent');
                } else if (historyStep === 'months') {
                  setHistoryStep('years');
                  setHistoryYear(null);
                } else if (historyStep === 'dates') {
                  setHistoryStep('months');
                  setHistoryMonth(null);
                } else {
                  setHistoryStep('dates');
                  setHistoryDateKey(null);
                }
              }}
              className="flex cursor-pointer items-center gap-1 rounded-md py-1 pr-2 text-sm font-medium text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M15 18l-6-6 6-6" />
              </svg>
              Back
            </button>
            <span className="h-4 w-px bg-neutral-300" aria-hidden="true" />
            <p className="text-sm font-semibold text-neutral-900">
              {historyStep === 'years' && 'Select a year'}
              {historyStep === 'months' && historyYear}
              {historyStep === 'dates' && historyYear !== null && historyMonth !== null &&
                `${MONTH_NAMES[historyMonth]} ${historyYear}`}
            </p>
          </div>

          {historyStep === 'years' && (
            <div className="flex flex-wrap gap-2">
              {historyYears.map((year) => (
                <button
                  key={year}
                  type="button"
                  onClick={() => {
                    setHistoryYear(year);
                    setHistoryStep('months');
                  }}
                  className="cursor-pointer rounded-full bg-neutral-100 px-5 py-2 text-sm font-semibold text-neutral-800 hover:bg-[var(--brand-100)] hover:text-[var(--brand-700)]"
                >
                  {year}
                </button>
              ))}
            </div>
          )}

          {historyStep === 'months' && historyYear !== null && (
            <div className="flex flex-wrap gap-2">
              {monthsForHistoryYear(historyYear).map((month) => (
                <button
                  key={month}
                  type="button"
                  onClick={() => {
                    setHistoryMonth(month);
                    setHistoryStep('dates');
                  }}
                  className="cursor-pointer rounded-full bg-neutral-100 px-4 py-2 text-sm font-semibold text-neutral-800 hover:bg-[var(--brand-100)] hover:text-[var(--brand-700)]"
                >
                  {MONTH_NAMES[month].slice(0, 3)}
                </button>
              ))}
            </div>
          )}

          {historyStep === 'dates' &&
            (datesForHistoryMonth.length === 0 ? (
              <p className="text-sm text-neutral-600">
                No slots recorded in {historyMonth !== null ? MONTH_NAMES[historyMonth] : ''}{' '}
                {historyYear}.
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {datesForHistoryMonth
                  .slice()
                  .sort((a, b) => a.date.getTime() - b.date.getTime())
                  .map((group) => {
                    const dateKey = group.date.toDateString();
                    return (
                      <button
                        key={dateKey}
                        type="button"
                        onClick={() => {
                          setHistoryDateKey(dateKey);
                          setHistoryStep('slots');
                        }}
                        className="flex h-11 w-11 cursor-pointer items-center justify-center rounded-full bg-neutral-100 text-sm font-semibold text-neutral-800 hover:bg-[var(--brand-100)] hover:text-[var(--brand-700)]"
                      >
                        {group.date.getDate()}
                      </button>
                    );
                  })}
              </div>
            ))}

          {historyStep === 'slots' && historyDateGroup && (
            <SlotDayGrid group={historyDateGroup} legendSlots={historyDateGroup.slots} />
          )}
        </div>
      ) : (
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-900">Dates</label>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="date"
              value={rangeStartDate}
              onChange={(e) => setRangeStartDate(e.target.value)}
              aria-label="Start date"
              min={todayDateString}
              required
              className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
            <input
              type="date"
              value={rangeEndDate}
              onChange={(e) => setRangeEndDate(e.target.value)}
              aria-label="End date"
              min={minEndDate}
              required
              className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-900">
            Daily time window
          </label>
          <p className="-mt-1 text-xs text-neutral-500">
            Applied every day in the date range above.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="time"
              value={rangeStartTime}
              onChange={(e) => setRangeStartTime(e.target.value)}
              aria-label="Start time"
              min={minStartTime}
              required
              className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
            <input
              type="time"
              value={rangeEndTime}
              onChange={(e) => setRangeEndTime(e.target.value)}
              aria-label="End time"
              min={minEndTime}
              required
              className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-sm font-medium text-neutral-900">Repeats on</label>
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAY_TOKENS.map((token) => {
              const active = selectedDays.includes(token);
              return (
                <button
                  key={token}
                  type="button"
                  onClick={() => toggleDay(token)}
                  aria-pressed={active}
                  className={
                    active
                      ? 'cursor-pointer rounded-full bg-[var(--brand-600)] px-3 py-1.5 text-xs font-semibold text-white'
                      : 'cursor-pointer rounded-full border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50'
                  }
                >
                  {WEEKDAY_LABELS[token]}
                </button>
              );
            })}
          </div>
          {selectedDays.length === 0 && (
            <p className="text-xs text-red-600">Select at least one day.</p>
          )}
        </div>
        {IS_AUTOMOTIVE ? (
          <FormField
            label="Max capacity"
            type="number"
            min={1}
            max={MAX_SLOT_COUNT}
            value={maxCapacity}
            onChange={(e) => setMaxCapacity(e.target.value)}
            required
          />
        ) : (
          <FormField
            label="Slot duration (minutes)"
            type="number"
            min={1}
            value={slotDurationMinutes}
            onChange={(e) => setSlotDurationMinutes(Number(e.target.value))}
            required
          />
        )}
        <FormField
          label={`Fee per slot (${doctor.price_currency || 'INR'})`}
          type="number"
          min={0}
          step="0.01"
          value={feePerSlot}
          onChange={(e) => setFeePerSlot(e.target.value)}
          required
        />
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            type="submit"
            disabled={
              submitting ||
              selectedDays.length === 0 ||
              (IS_AUTOMOTIVE && (!maxCapacity || Number(maxCapacity) <= 0))
            }
            className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm text-white hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? 'Generating…' : 'Generate availability'}
          </button>
          <button
            type="button"
            onClick={() => setShowPreview((prev) => !prev)}
            className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
          >
            {showPreview ? 'Hide preview' : 'Preview'}
          </button>
          <button
            type="button"
            onClick={() => setView('recent')}
            className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
          >
            Back
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
        </div>

        {showPreview && (
          <div className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-neutral-50 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
              Preview — what a {customerNoun} sees
            </p>
            {!preview ? (
              <p className="text-sm text-neutral-600">
                Fill in From and To above to preview the available slots.
              </p>
            ) : (
              <div className="flex flex-col gap-4">
                {preview.days.map((date) => (
                  <div key={date.toISOString()} className="flex flex-col gap-2">
                    <p className="text-sm font-medium text-neutral-900">
                      {date.toLocaleDateString(undefined, {
                        weekday: 'short',
                        day: 'numeric',
                        month: 'short',
                        year: 'numeric',
                      })}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {IS_AUTOMOTIVE ? (
                        // One shared window, slot_count identical numbered
                        // slots — not a chip per time anchor, there's only
                        // ever the one anchor in token/queue mode.
                        <span className="rounded-full border border-[var(--brand-600)] bg-white px-3 py-1 text-xs font-medium text-[var(--brand-700)]">
                          {formatClockTime(rangeStartTime)} – {formatClockTime(rangeEndTime)} ·{' '}
                          {preview.tokenCount} slot{preview.tokenCount === 1 ? '' : 's'}
                        </span>
                      ) : (
                        preview.times.map((t) => (
                          <span
                            key={t}
                            className="rounded-full border border-[var(--brand-600)] bg-white px-3 py-1 text-xs font-medium text-[var(--brand-700)]"
                          >
                            {formatSlotWindow(t)}
                          </span>
                        ))
                      )}
                    </div>
                  </div>
                ))}
                {preview.totalDays > preview.days.length && (
                  <p className="text-xs text-neutral-500">
                    Showing the first {preview.days.length} of {preview.totalDays} days.
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </form>
      )}
    </Modal>
  );
}

function EditDoctorModal({
  doctor,
  onClose,
  onSaved,
}: {
  doctor: Doctor;
  onClose: () => void;
  onSaved: (doctor: Doctor) => void;
}) {
  const { showToast } = useToast();
  const [name, setName] = useState(doctor.name);
  const [specialisation, setSpecialisation] = useState(
    doctor.domain_data.specialisation ?? SPECIALISATIONS[0]
  );
  const [education, setEducation] = useState(doctor.domain_data.education ?? '');
  const [yearsOfExperience, setYearsOfExperience] = useState(
    String(doctor.domain_data.years_of_experience ?? '')
  );
  const [gender, setGender] = useState(doctor.domain_data.gender ?? 'male');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = name.trim() && specialisation && education.trim() && yearsOfExperience !== '';

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await updateResource(doctor.id, {
        name: capitalizeWords(name.trim()),
        domain_data: {
          specialisation,
          education: education.trim(),
          years_of_experience: Number(yearsOfExperience),
          gender,
        },
      });
      showToast('Doctor updated successfully');
      onSaved({
        ...doctor,
        id: updated.id,
        name: updated.name,
        is_active: updated.is_active,
        domain_data: updated.domain_data as Doctor['domain_data'],
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update this doctor');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Edit ${doctor.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <FormField label="Name" value={name} onChange={(e) => setName(e.target.value)} required />
        <div className="flex flex-col gap-1.5">
          <label htmlFor="edit-doctor-specialisation" className="text-sm font-medium text-neutral-900">
            Specialisation
          </label>
          <select
            id="edit-doctor-specialisation"
            value={specialisation}
            onChange={(e) => setSpecialisation(e.target.value)}
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
          >
            {SPECIALISATIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <FormField label="Education" value={education} onChange={(e) => setEducation(e.target.value)} required />
        <FormField
          label="Years of experience"
          type="number"
          min={0}
          max={80}
          value={yearsOfExperience}
          onChange={(e) => setYearsOfExperience(e.target.value)}
          required
        />
        <div className="flex flex-col gap-1.5">
          <label htmlFor="edit-doctor-gender" className="text-sm font-medium text-neutral-900">
            Gender
          </label>
          <select
            id="edit-doctor-gender"
            value={gender}
            onChange={(e) => setGender(e.target.value)}
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
          >
            {GENDER_OPTIONS.map((option) => (
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
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={submitting || !canSubmit}
            className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm text-white hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DoctorStatusToggle({
  doctor,
  onUpdated,
}: {
  doctor: Doctor;
  onUpdated: (doctor: Doctor) => void;
}) {
  const { showToast } = useToast();
  const [submitting, setSubmitting] = useState(false);

  async function handleToggle() {
    setSubmitting(true);
    try {
      const result = await setResourceStatus(doctor.id, !doctor.is_active);
      onUpdated({ ...doctor, is_active: result.is_active });
      showToast(
        result.is_active ? `${doctor.name} is now active` : `${doctor.name} is now inactive`
      );
    } catch (err) {
      showToast(
        err instanceof ApiError ? err.message : 'Could not update this doctor’s status',
        'error'
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Tooltip label={doctor.is_active ? 'Deactivate' : 'Activate'}>
      <button
        type="button"
        role="switch"
        aria-checked={doctor.is_active}
        aria-label={`${doctor.name} active status`}
        onClick={handleToggle}
        disabled={submitting}
        className={`inline-flex h-5 w-9 flex-shrink-0 cursor-pointer items-center rounded-full p-0.5 transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
          doctor.is_active ? 'bg-green-500' : 'bg-neutral-300'
        }`}
      >
        <span
          aria-hidden="true"
          className={`h-4 w-4 rounded-full bg-white shadow transition-transform ${
            doctor.is_active ? 'translate-x-4' : 'translate-x-0'
          }`}
        />
      </button>
    </Tooltip>
  );
}

function DoctorRow({
  doctor,
  onOpenAvailability,
  onOpenEdit,
  onUpdated,
  onDeleted,
}: {
  doctor: Doctor;
  onOpenAvailability: (doctor: Doctor) => void;
  onOpenEdit: (doctor: Doctor) => void;
  onUpdated: (doctor: Doctor) => void;
  onDeleted: (doctorId: string) => void;
}) {
  const { showToast } = useToast();
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    if (!window.confirm(`Delete ${doctor.name}? This can't be undone.`)) return;
    setDeleting(true);
    try {
      await deleteResource(doctor.id);
      onDeleted(doctor.id);
      showToast(`${doctor.name} deleted`);
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : 'Could not delete this doctor', 'error');
      setDeleting(false);
    }
  }

  return (
    <tr className={`border-t border-neutral-200 ${doctor.is_active ? '' : 'opacity-60'}`}>
      <td className="px-4 py-3 text-sm font-mono text-neutral-600">
        {doctor.domain_data.doctor_id || '—'}
      </td>
      <td className="px-4 py-3 text-sm font-medium text-neutral-900">{doctor.name}</td>
      <td className="px-4 py-3 text-sm text-neutral-700">
        {doctor.domain_data.specialisation || '—'}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-700">
        {doctor.domain_data.education || '—'}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-700">
        {doctor.domain_data.years_of_experience !== undefined
          ? `${doctor.domain_data.years_of_experience} yrs`
          : '—'}
      </td>
      <td className="px-4 py-3 text-sm capitalize text-neutral-700">
        {doctor.domain_data.gender || '—'}
      </td>
      <td className="px-4 py-3">
        <Tooltip label={doctor.hasLiveAvailability ? 'Has open slots' : 'No open slots'}>
          <button
            type="button"
            onClick={() => onOpenAvailability(doctor)}
            className={`cursor-pointer rounded-md border px-3 py-1.5 text-sm font-medium text-white ${
              doctor.hasLiveAvailability
                ? 'border-green-600 bg-green-600 hover:bg-green-700'
                : 'border-red-600 bg-red-600 hover:bg-red-700'
            }`}
          >
            Availability
          </button>
        </Tooltip>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          <Tooltip label="Edit">
            <button
              type="button"
              onClick={() => onOpenEdit(doctor)}
              aria-label={`Edit ${doctor.name}`}
              className="cursor-pointer text-neutral-500 hover:text-neutral-900"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
          </Tooltip>
          <DoctorStatusToggle doctor={doctor} onUpdated={onUpdated} />
          <Tooltip label="Delete">
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              aria-label={`Delete ${doctor.name}`}
              className="cursor-pointer text-red-500 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6h16Z" />
              </svg>
            </button>
          </Tooltip>
        </div>
      </td>
    </tr>
  );
}

function DoctorsView({ account }: { account: BusinessAccount }) {
  const [doctors, setDoctors] = useState<Doctor[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [availabilityDoctor, setAvailabilityDoctor] = useState<Doctor | null>(null);
  const [editDoctor, setEditDoctor] = useState<Doctor | null>(null);
  const [addDoctorOpen, setAddDoctorOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');
  const [genderFilter, setGenderFilter] = useState('all');
  const [specialisationFilter, setSpecialisationFilter] = useState('all');
  const [doctorIdSearch, setDoctorIdSearch] = useState('');

  const ownedResourceIds = useMemo(
    () => account.owned_resource_ids ?? [],
    [account.owned_resource_ids]
  );

  const loadDoctors = useCallback(async () => {
    try {
      // getSlots directly (not fetchOwnedResources, which discards the slots
      // it already fetches) — needed to know each doctor's current live
      // availability for the Availability button's green/red color.
      const results = await Promise.all(ownedResourceIds.map((id) => getSlots(id)));
      const mapped = results.map(({ resource, slots }) => ({
        id: resource.id,
        name: resource.name,
        is_active: resource.is_active,
        hasLiveAvailability: slots.some(isSlotLiveThisWeek),
        domain_data: resource.domain_data as Doctor['domain_data'],
        price_currency: resource.price_currency,
        price_value: resource.price_value,
        created_at: resource.created_at,
      }));
      // Recently created doctors on top — doctor_id is assigned in strictly
      // increasing order at creation (resource_create_view), so sorting by it
      // descending is the same as sorting by creation time descending.
      mapped.sort((a, b) =>
        (b.domain_data.doctor_id ?? '').localeCompare(a.domain_data.doctor_id ?? '', undefined, {
          numeric: true,
        })
      );
      setDoctors(mapped);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load your doctors');
    }
  }, [ownedResourceIds]);

  useEffect(() => {
    let ignore = false;
    loadDoctors().then(() => {
      if (ignore) return;
    });
    return () => {
      ignore = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownedResourceIds]);

  function handleUpdated(updated: Doctor) {
    setDoctors((prev) => (prev ?? []).map((d) => (d.id === updated.id ? updated : d)));
  }

  if (loadError) return <ErrorState description={loadError} onRetry={loadDoctors} />;
  if (doctors === null) return <LoadingState label="Loading your doctors…" />;

  const filteredDoctors = doctors.filter((d) => {
    if (statusFilter === 'active' && !d.is_active) return false;
    if (statusFilter === 'inactive' && d.is_active) return false;
    if (genderFilter !== 'all' && d.domain_data.gender !== genderFilter) return false;
    if (specialisationFilter !== 'all' && d.domain_data.specialisation !== specialisationFilter) {
      return false;
    }
    if (
      doctorIdSearch.trim() &&
      !(d.domain_data.doctor_id ?? '').toLowerCase().includes(doctorIdSearch.trim().toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  const addDoctorButton = (
    <Tooltip label="Add">
      <button
        type="button"
        onClick={() => setAddDoctorOpen(true)}
        className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--brand-700)]"
      >
        Add doctor
      </button>
    </Tooltip>
  );

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      {/* Business name shows once, in the header — not repeated here. Only shown
          once a first doctor already exists — with zero doctors the same button
          appears centered, inside the empty state below, as the page's one clear
          call to action instead of competing with it. */}
      {doctors.length > 0 && <div className="flex justify-end">{addDoctorButton}</div>}

      {doctors.length > 0 && (
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-doctor-id" className="text-xs font-medium text-neutral-600">
              Doctor ID
            </label>
            <div className="relative">
              <input
                id="filter-doctor-id"
                type="text"
                value={doctorIdSearch}
                onChange={(e) => setDoctorIdSearch(e.target.value)}
                placeholder="e.g. DOC-0001"
                className="rounded-md border border-neutral-300 bg-white px-3 py-1.5 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
              />
              {doctorIdSearch && (
                <button
                  type="button"
                  onClick={() => setDoctorIdSearch('')}
                  aria-label="Clear doctor ID search"
                  className="absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer text-neutral-400 hover:text-neutral-700"
                >
                  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-status" className="text-xs font-medium text-neutral-600">
              Status
            </label>
            <select
              id="filter-status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
              className="w-36 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            >
              <option value="all">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-gender" className="text-xs font-medium text-neutral-600">
              Gender
            </label>
            <select
              id="filter-gender"
              value={genderFilter}
              onChange={(e) => setGenderFilter(e.target.value)}
              className="w-36 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            >
              <option value="all">All</option>
              {GENDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-specialisation" className="text-xs font-medium text-neutral-600">
              Specialisation
            </label>
            <select
              id="filter-specialisation"
              value={specialisationFilter}
              onChange={(e) => setSpecialisationFilter(e.target.value)}
              className="w-64 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            >
              <option value="all">All</option>
              {SPECIALISATIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {doctors.length === 0 ? (
        <EmptyState
          title="No doctors yet"
          description="Add your first doctor to start taking bookings."
          action={addDoctorButton}
        />
      ) : filteredDoctors.length === 0 ? (
        <EmptyState
          title="No doctors match these filters"
          description="Try changing or clearing a filter above."
          action={
            <button
              type="button"
              onClick={() => {
                setDoctorIdSearch('');
                setStatusFilter('all');
                setGenderFilter('all');
                setSpecialisationFilter('all');
              }}
              className="cursor-pointer rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            >
              Clear filters
            </button>
          }
        />
      ) : (
        <div className="w-full overflow-x-auto overflow-y-hidden rounded-lg border border-neutral-200 bg-white">
          <table className="w-full min-w-[640px] border-collapse">
            <thead>
              <tr className="bg-neutral-50 text-left text-xs font-bold uppercase tracking-wide text-neutral-500">
                <th className="px-4 py-3">Doctor ID</th>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Specialisation</th>
                <th className="px-4 py-3">Education</th>
                <th className="px-4 py-3">Experience</th>
                <th className="px-4 py-3">Gender</th>
                <th className="px-4 py-3">Availability</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredDoctors.map((doctor) => (
                <DoctorRow
                  key={doctor.id}
                  doctor={doctor}
                  onOpenAvailability={setAvailabilityDoctor}
                  onOpenEdit={setEditDoctor}
                  onUpdated={handleUpdated}
                  onDeleted={(id) => setDoctors((prev) => (prev ?? []).filter((d) => d.id !== id))}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {addDoctorOpen && (
        <Modal title="Add a doctor" onClose={() => setAddDoctorOpen(false)}>
          <AddDoctorForm
            onCreated={(doctor) => {
              setDoctors((prev) => [doctor, ...(prev ?? [])]);
              setAddDoctorOpen(false);
            }}
            onCancel={() => setAddDoctorOpen(false)}
          />
        </Modal>
      )}

      {availabilityDoctor && (
        <AvailabilityModal
          doctor={availabilityDoctor}
          onClose={() => {
            setAvailabilityDoctor(null);
            // Refresh so the Availability button's green/red status reflects
            // whatever was just generated or deleted, not a stale snapshot.
            loadDoctors();
          }}
        />
      )}
      {editDoctor && (
        <EditDoctorModal
          doctor={editDoctor}
          onClose={() => setEditDoctor(null)}
          onSaved={(updated) => {
            handleUpdated(updated);
            setEditDoctor(null);
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// Beauty — the "Stylists" table. Mirrors the Doctors table above structurally
// (same AvailabilityModal/DoctorStatusToggle/SlotDayGrid/slot helpers, reused
// as-is) but with its own copy and fields: a service specialisation from
// BEAUTY_SERVICES instead of a medical one, no "education" (no real salon-
// credential equivalent to a medical degree), id key "staff_id" instead of
// "doctor_id". Kept as its own set of components rather than parameterizing
// AddDoctorForm/EditDoctorModal/DoctorRow/DoctorsView in place — those stay
// completely untouched so CareNest's own behavior can't regress from this.
// ============================================================================

function AddStylistForm({
  onCreated,
  onCancel,
}: {
  onCreated: (stylist: Doctor) => void;
  onCancel: () => void;
}) {
  const { showToast } = useToast();
  const [name, setName] = useState('');
  const [specialisation, setSpecialisation] = useState(BEAUTY_SERVICES[0]);
  const [yearsOfExperience, setYearsOfExperience] = useState('');
  const [gender, setGender] = useState('female');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = name.trim() && specialisation && yearsOfExperience !== '';

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const stylist = await createResource(name.trim(), 'stylist', {
        specialisation,
        years_of_experience: Number(yearsOfExperience),
        gender,
      });
      onCreated({
        id: stylist.id,
        name: stylist.name,
        is_active: stylist.is_active,
        hasLiveAvailability: false,
        domain_data: stylist.domain_data,
        price_currency: stylist.price_currency,
        price_value: stylist.price_value,
        created_at: stylist.created_at,
      });
      showToast('Stylist added successfully');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add this stylist');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <FormField label="Name" value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="stylist-specialisation" className="text-sm font-medium text-neutral-900">
          Service
        </label>
        <select
          id="stylist-specialisation"
          value={specialisation}
          onChange={(e) => setSpecialisation(e.target.value)}
          className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
        >
          {BEAUTY_SERVICES.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>
      <FormField
        label="Years of experience"
        type="number"
        min={0}
        max={80}
        value={yearsOfExperience}
        onChange={(e) => setYearsOfExperience(e.target.value)}
        required
      />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="stylist-gender" className="text-sm font-medium text-neutral-900">
          Gender
        </label>
        <select
          id="stylist-gender"
          value={gender}
          onChange={(e) => setGender(e.target.value)}
          className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
        >
          {GENDER_OPTIONS.map((option) => (
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
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting || !canSubmit}
          className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm text-white hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Adding…' : 'Add stylist'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function EditStylistModal({
  stylist,
  onClose,
  onSaved,
}: {
  stylist: Doctor;
  onClose: () => void;
  onSaved: (stylist: Doctor) => void;
}) {
  const { showToast } = useToast();
  const [name, setName] = useState(stylist.name);
  const [specialisation, setSpecialisation] = useState(
    stylist.domain_data.specialisation ?? BEAUTY_SERVICES[0]
  );
  const [yearsOfExperience, setYearsOfExperience] = useState(
    String(stylist.domain_data.years_of_experience ?? '')
  );
  const [gender, setGender] = useState(stylist.domain_data.gender ?? 'female');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = name.trim() && specialisation && yearsOfExperience !== '';

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await updateResource(stylist.id, {
        name: capitalizeWords(name.trim()),
        domain_data: {
          specialisation,
          years_of_experience: Number(yearsOfExperience),
          gender,
        },
      });
      showToast('Stylist updated successfully');
      onSaved({
        ...stylist,
        id: updated.id,
        name: updated.name,
        is_active: updated.is_active,
        domain_data: updated.domain_data as Doctor['domain_data'],
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update this stylist');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Edit ${stylist.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <FormField label="Name" value={name} onChange={(e) => setName(e.target.value)} required />
        <div className="flex flex-col gap-1.5">
          <label htmlFor="edit-stylist-specialisation" className="text-sm font-medium text-neutral-900">
            Service
          </label>
          <select
            id="edit-stylist-specialisation"
            value={specialisation}
            onChange={(e) => setSpecialisation(e.target.value)}
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
          >
            {BEAUTY_SERVICES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
        <FormField
          label="Years of experience"
          type="number"
          min={0}
          max={80}
          value={yearsOfExperience}
          onChange={(e) => setYearsOfExperience(e.target.value)}
          required
        />
        <div className="flex flex-col gap-1.5">
          <label htmlFor="edit-stylist-gender" className="text-sm font-medium text-neutral-900">
            Gender
          </label>
          <select
            id="edit-stylist-gender"
            value={gender}
            onChange={(e) => setGender(e.target.value)}
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
          >
            {GENDER_OPTIONS.map((option) => (
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
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={submitting || !canSubmit}
            className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm text-white hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

function StylistRow({
  stylist,
  onOpenAvailability,
  onOpenEdit,
  onUpdated,
  onDeleted,
}: {
  stylist: Doctor;
  onOpenAvailability: (stylist: Doctor) => void;
  onOpenEdit: (stylist: Doctor) => void;
  onUpdated: (stylist: Doctor) => void;
  onDeleted: (stylistId: string) => void;
}) {
  const { showToast } = useToast();
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    if (!window.confirm(`Delete ${stylist.name}? This can't be undone.`)) return;
    setDeleting(true);
    try {
      await deleteResource(stylist.id);
      onDeleted(stylist.id);
      showToast(`${stylist.name} deleted`);
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : 'Could not delete this stylist', 'error');
      setDeleting(false);
    }
  }

  return (
    <tr className={`border-t border-neutral-200 ${stylist.is_active ? '' : 'opacity-60'}`}>
      <td className="px-4 py-3 text-sm font-mono text-neutral-600">
        {stylist.domain_data.staff_id || '—'}
      </td>
      <td className="px-4 py-3 text-sm font-medium text-neutral-900">{stylist.name}</td>
      <td className="px-4 py-3 text-sm text-neutral-700">
        {stylist.domain_data.specialisation || '—'}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-700">
        {stylist.domain_data.years_of_experience !== undefined
          ? `${stylist.domain_data.years_of_experience} yrs`
          : '—'}
      </td>
      <td className="px-4 py-3 text-sm capitalize text-neutral-700">
        {stylist.domain_data.gender || '—'}
      </td>
      <td className="px-4 py-3">
        <Tooltip label={stylist.hasLiveAvailability ? 'Has open slots' : 'No open slots'}>
          <button
            type="button"
            onClick={() => onOpenAvailability(stylist)}
            className={`cursor-pointer rounded-md border px-3 py-1.5 text-sm font-medium text-white ${
              stylist.hasLiveAvailability
                ? 'border-green-600 bg-green-600 hover:bg-green-700'
                : 'border-red-600 bg-red-600 hover:bg-red-700'
            }`}
          >
            Availability
          </button>
        </Tooltip>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          <Tooltip label="Edit">
            <button
              type="button"
              onClick={() => onOpenEdit(stylist)}
              aria-label={`Edit ${stylist.name}`}
              className="cursor-pointer text-neutral-500 hover:text-neutral-900"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
          </Tooltip>
          <DoctorStatusToggle doctor={stylist} onUpdated={onUpdated} />
          <Tooltip label="Delete">
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              aria-label={`Delete ${stylist.name}`}
              className="cursor-pointer text-red-500 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6h16Z" />
              </svg>
            </button>
          </Tooltip>
        </div>
      </td>
    </tr>
  );
}

function StylistsView({ account }: { account: BusinessAccount }) {
  const [stylists, setStylists] = useState<Doctor[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [availabilityStylist, setAvailabilityStylist] = useState<Doctor | null>(null);
  const [editStylist, setEditStylist] = useState<Doctor | null>(null);
  const [addStylistOpen, setAddStylistOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');
  const [genderFilter, setGenderFilter] = useState('all');
  const [specialisationFilter, setSpecialisationFilter] = useState('all');
  const [staffIdSearch, setStaffIdSearch] = useState('');

  const ownedResourceIds = useMemo(
    () => account.owned_resource_ids ?? [],
    [account.owned_resource_ids]
  );

  const loadStylists = useCallback(async () => {
    try {
      const results = await Promise.all(ownedResourceIds.map((id) => getSlots(id)));
      const mapped = results.map(({ resource, slots }) => ({
        id: resource.id,
        name: resource.name,
        is_active: resource.is_active,
        hasLiveAvailability: slots.some(isSlotLiveThisWeek),
        domain_data: resource.domain_data as Doctor['domain_data'],
        price_currency: resource.price_currency,
        price_value: resource.price_value,
        created_at: resource.created_at,
      }));
      // Recently created stylists on top — same reasoning as DoctorsView's
      // identical sort (staff_id is assigned in strictly increasing order at
      // creation). A plain "chair" resource has no staff_id at all — those
      // sort last, which is fine, this view only ever creates stylists.
      mapped.sort((a, b) =>
        (b.domain_data.staff_id ?? '').localeCompare(a.domain_data.staff_id ?? '', undefined, {
          numeric: true,
        })
      );
      setStylists(mapped);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load your stylists');
    }
  }, [ownedResourceIds]);

  useEffect(() => {
    let ignore = false;
    loadStylists().then(() => {
      if (ignore) return;
    });
    return () => {
      ignore = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownedResourceIds]);

  function handleUpdated(updated: Doctor) {
    setStylists((prev) => (prev ?? []).map((d) => (d.id === updated.id ? updated : d)));
  }

  if (loadError) return <ErrorState description={loadError} onRetry={loadStylists} />;
  if (stylists === null) return <LoadingState label="Loading your stylists…" />;

  const filteredStylists = stylists.filter((s) => {
    if (statusFilter === 'active' && !s.is_active) return false;
    if (statusFilter === 'inactive' && s.is_active) return false;
    if (genderFilter !== 'all' && s.domain_data.gender !== genderFilter) return false;
    if (specialisationFilter !== 'all' && s.domain_data.specialisation !== specialisationFilter) {
      return false;
    }
    if (
      staffIdSearch.trim() &&
      !(s.domain_data.staff_id ?? '').toLowerCase().includes(staffIdSearch.trim().toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  const addStylistButton = (
    <Tooltip label="Add">
      <button
        type="button"
        onClick={() => setAddStylistOpen(true)}
        className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--brand-700)]"
      >
        Add stylist
      </button>
    </Tooltip>
  );

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      {stylists.length > 0 && <div className="flex justify-end">{addStylistButton}</div>}

      {stylists.length > 0 && (
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-staff-id" className="text-xs font-medium text-neutral-600">
              Stylist ID
            </label>
            <div className="relative">
              <input
                id="filter-staff-id"
                type="text"
                value={staffIdSearch}
                onChange={(e) => setStaffIdSearch(e.target.value)}
                placeholder="e.g. STY-0001"
                className="rounded-md border border-neutral-300 bg-white px-3 py-1.5 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
              />
              {staffIdSearch && (
                <button
                  type="button"
                  onClick={() => setStaffIdSearch('')}
                  aria-label="Clear stylist ID search"
                  className="absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer text-neutral-400 hover:text-neutral-700"
                >
                  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-stylist-status" className="text-xs font-medium text-neutral-600">
              Status
            </label>
            <select
              id="filter-stylist-status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
              className="w-36 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            >
              <option value="all">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-stylist-gender" className="text-xs font-medium text-neutral-600">
              Gender
            </label>
            <select
              id="filter-stylist-gender"
              value={genderFilter}
              onChange={(e) => setGenderFilter(e.target.value)}
              className="w-36 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            >
              <option value="all">All</option>
              {GENDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-stylist-specialisation" className="text-xs font-medium text-neutral-600">
              Service
            </label>
            <select
              id="filter-stylist-specialisation"
              value={specialisationFilter}
              onChange={(e) => setSpecialisationFilter(e.target.value)}
              className="w-64 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            >
              <option value="all">All</option>
              {BEAUTY_SERVICES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {stylists.length === 0 ? (
        <EmptyState
          title="No stylists yet"
          description="Add your first stylist to start taking bookings."
          action={addStylistButton}
        />
      ) : filteredStylists.length === 0 ? (
        <EmptyState
          title="No stylists match these filters"
          description="Try changing or clearing a filter above."
          action={
            <button
              type="button"
              onClick={() => {
                setStaffIdSearch('');
                setStatusFilter('all');
                setGenderFilter('all');
                setSpecialisationFilter('all');
              }}
              className="cursor-pointer rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            >
              Clear filters
            </button>
          }
        />
      ) : (
        <div className="w-full overflow-x-auto overflow-y-hidden rounded-lg border border-neutral-200 bg-white">
          <table className="w-full min-w-[640px] border-collapse">
            <thead>
              <tr className="bg-neutral-50 text-left text-xs font-bold uppercase tracking-wide text-neutral-500">
                <th className="px-4 py-3">Stylist ID</th>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Service</th>
                <th className="px-4 py-3">Experience</th>
                <th className="px-4 py-3">Gender</th>
                <th className="px-4 py-3">Availability</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredStylists.map((stylist) => (
                <StylistRow
                  key={stylist.id}
                  stylist={stylist}
                  onOpenAvailability={setAvailabilityStylist}
                  onOpenEdit={setEditStylist}
                  onUpdated={handleUpdated}
                  onDeleted={(id) => setStylists((prev) => (prev ?? []).filter((d) => d.id !== id))}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {addStylistOpen && (
        <Modal title="Add a stylist" onClose={() => setAddStylistOpen(false)}>
          <AddStylistForm
            onCreated={(stylist) => {
              setStylists((prev) => [stylist, ...(prev ?? [])]);
              setAddStylistOpen(false);
            }}
            onCancel={() => setAddStylistOpen(false)}
          />
        </Modal>
      )}

      {availabilityStylist && (
        <AvailabilityModal
          doctor={availabilityStylist}
          customerNoun="customer"
          onClose={() => {
            setAvailabilityStylist(null);
            loadStylists();
          }}
        />
      )}
      {editStylist && (
        <EditStylistModal
          stylist={editStylist}
          onClose={() => setEditStylist(null)}
          onSaved={(updated) => {
            handleUpdated(updated);
            setEditStylist(null);
          }}
        />
      )}
    </div>
  );
}

// ============================================================================
// Automotive — the "Bays" table. Mirrors the Doctors/Stylists tables above
// structurally (same AvailabilityModal/DoctorStatusToggle/SlotDayGrid/slot
// helpers, reused as-is) but with no person-only fields at all — a bay is a
// physical thing, not staff, so it carries just a name, price and its own
// server-generated "BAY-0001"-style id. A mechanic is staff assigned to a bay
// (the existing Staff page), not a second resource type here.
// ============================================================================

function AddBayForm({
  onCreated,
  onCancel,
}: {
  onCreated: (bay: Doctor) => void;
  onCancel: () => void;
}) {
  const { showToast } = useToast();
  const [name, setName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = Boolean(name.trim());

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const bay = await createResource(name.trim(), 'bay');
      onCreated({
        id: bay.id,
        name: bay.name,
        is_active: bay.is_active,
        hasLiveAvailability: false,
        domain_data: bay.domain_data,
        price_currency: bay.price_currency,
        price_value: bay.price_value,
        created_at: bay.created_at,
      });
      showToast('Bay added successfully');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add this bay');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <FormField label="Name" value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
      {error && (
        <p role="alert" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={submitting || !canSubmit}
          className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm text-white hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? 'Adding…' : 'Add bay'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={submitting}
          className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function EditBayModal({
  bay,
  onClose,
  onSaved,
}: {
  bay: Doctor;
  onClose: () => void;
  onSaved: (bay: Doctor) => void;
}) {
  const { showToast } = useToast();
  const [name, setName] = useState(bay.name);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = Boolean(name.trim());

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const updated = await updateResource(bay.id, { name: capitalizeWords(name.trim()) });
      showToast('Bay updated successfully');
      onSaved({
        ...bay,
        id: updated.id,
        name: updated.name,
        is_active: updated.is_active,
        domain_data: updated.domain_data as Doctor['domain_data'],
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update this bay');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title={`Edit ${bay.name}`} onClose={onClose}>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <FormField label="Name" value={name} onChange={(e) => setName(e.target.value)} required />
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={submitting || !canSubmit}
            className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm text-white hover:bg-[var(--brand-700)] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="cursor-pointer rounded-md border border-neutral-300 px-4 py-2 text-sm font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

function BayRow({
  bay,
  onOpenAvailability,
  onOpenEdit,
  onUpdated,
  onDeleted,
}: {
  bay: Doctor;
  onOpenAvailability: (bay: Doctor) => void;
  onOpenEdit: (bay: Doctor) => void;
  onUpdated: (bay: Doctor) => void;
  onDeleted: (bayId: string) => void;
}) {
  const { showToast } = useToast();
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    if (!window.confirm(`Delete ${bay.name}? This can't be undone.`)) return;
    setDeleting(true);
    try {
      await deleteResource(bay.id);
      onDeleted(bay.id);
      showToast(`${bay.name} deleted`);
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : 'Could not delete this bay', 'error');
      setDeleting(false);
    }
  }

  return (
    <tr className={`border-t border-neutral-200 ${bay.is_active ? '' : 'opacity-60'}`}>
      <td className="px-4 py-3 text-sm font-mono text-neutral-600">{bay.domain_data.bay_id || '—'}</td>
      <td className="px-4 py-3 text-sm font-medium text-neutral-900">{bay.name}</td>
      <td className="px-4 py-3">
        <Tooltip label={bay.hasLiveAvailability ? 'Has open slots' : 'No open slots'}>
          <button
            type="button"
            onClick={() => onOpenAvailability(bay)}
            className={`cursor-pointer rounded-md border px-3 py-1.5 text-sm font-medium text-white ${
              bay.hasLiveAvailability
                ? 'border-green-600 bg-green-600 hover:bg-green-700'
                : 'border-red-600 bg-red-600 hover:bg-red-700'
            }`}
          >
            Availability
          </button>
        </Tooltip>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-3">
          <Tooltip label="Edit">
            <button
              type="button"
              onClick={() => onOpenEdit(bay)}
              aria-label={`Edit ${bay.name}`}
              className="cursor-pointer text-neutral-500 hover:text-neutral-900"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
          </Tooltip>
          <DoctorStatusToggle doctor={bay} onUpdated={onUpdated} />
          <Tooltip label="Delete">
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              aria-label={`Delete ${bay.name}`}
              className="cursor-pointer text-red-500 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6h16Z" />
              </svg>
            </button>
          </Tooltip>
        </div>
      </td>
    </tr>
  );
}

function BaysView({ account }: { account: BusinessAccount }) {
  const [bays, setBays] = useState<Doctor[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [availabilityBay, setAvailabilityBay] = useState<Doctor | null>(null);
  const [editBay, setEditBay] = useState<Doctor | null>(null);
  const [addBayOpen, setAddBayOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');
  const [bayIdSearch, setBayIdSearch] = useState('');

  const ownedResourceIds = useMemo(
    () => account.owned_resource_ids ?? [],
    [account.owned_resource_ids]
  );

  const loadBays = useCallback(async () => {
    try {
      const results = await Promise.all(ownedResourceIds.map((id) => getSlots(id)));
      const mapped = results.map(({ resource, slots }) => ({
        id: resource.id,
        name: resource.name,
        is_active: resource.is_active,
        hasLiveAvailability: slots.some(isSlotLiveThisWeek),
        domain_data: resource.domain_data as Doctor['domain_data'],
        price_currency: resource.price_currency,
        price_value: resource.price_value,
        created_at: resource.created_at,
      }));
      // Recently created bays on top — same reasoning as DoctorsView's/
      // StylistsView's identical sort (bay_id is assigned in strictly
      // increasing order at creation).
      mapped.sort((a, b) =>
        (b.domain_data.bay_id ?? '').localeCompare(a.domain_data.bay_id ?? '', undefined, {
          numeric: true,
        })
      );
      setBays(mapped);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load your bays');
    }
  }, [ownedResourceIds]);

  useEffect(() => {
    let ignore = false;
    loadBays().then(() => {
      if (ignore) return;
    });
    return () => {
      ignore = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ownedResourceIds]);

  function handleUpdated(updated: Doctor) {
    setBays((prev) => (prev ?? []).map((d) => (d.id === updated.id ? updated : d)));
  }

  if (loadError) return <ErrorState description={loadError} onRetry={loadBays} />;
  if (bays === null) return <LoadingState label="Loading your bays…" />;

  const filteredBays = bays.filter((b) => {
    if (statusFilter === 'active' && !b.is_active) return false;
    if (statusFilter === 'inactive' && b.is_active) return false;
    if (
      bayIdSearch.trim() &&
      !(b.domain_data.bay_id ?? '').toLowerCase().includes(bayIdSearch.trim().toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  const addBayButton = (
    <Tooltip label="Add">
      <button
        type="button"
        onClick={() => setAddBayOpen(true)}
        className="cursor-pointer rounded-md bg-[var(--brand-600)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--brand-700)]"
      >
        Add bay
      </button>
    </Tooltip>
  );

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-4 py-8 sm:px-6 lg:px-8">
      {bays.length > 0 && <div className="flex justify-end">{addBayButton}</div>}

      {bays.length > 0 && (
        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-bay-id" className="text-xs font-medium text-neutral-600">
              Bay ID
            </label>
            <div className="relative">
              <input
                id="filter-bay-id"
                type="text"
                value={bayIdSearch}
                onChange={(e) => setBayIdSearch(e.target.value)}
                placeholder="e.g. BAY-0001"
                className="rounded-md border border-neutral-300 bg-white px-3 py-1.5 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
              />
              {bayIdSearch && (
                <button
                  type="button"
                  onClick={() => setBayIdSearch('')}
                  aria-label="Clear bay ID search"
                  className="absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer text-neutral-400 hover:text-neutral-700"
                >
                  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="filter-bay-status" className="text-xs font-medium text-neutral-600">
              Status
            </label>
            <select
              id="filter-bay-status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
              className="w-36 rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            >
              <option value="all">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>
        </div>
      )}

      {bays.length === 0 ? (
        <EmptyState
          title="No bays yet"
          description="Add your first bay to start taking bookings."
          action={addBayButton}
        />
      ) : filteredBays.length === 0 ? (
        <EmptyState
          title="No bays match these filters"
          description="Try changing or clearing a filter above."
          action={
            <button
              type="button"
              onClick={() => {
                setBayIdSearch('');
                setStatusFilter('all');
              }}
              className="cursor-pointer rounded-md border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            >
              Clear filters
            </button>
          }
        />
      ) : (
        <div className="w-full overflow-x-auto overflow-y-hidden rounded-lg border border-neutral-200 bg-white">
          <table className="w-full min-w-[480px] border-collapse">
            <thead>
              <tr className="bg-neutral-50 text-left text-xs font-bold uppercase tracking-wide text-neutral-500">
                <th className="px-4 py-3">Bay ID</th>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Availability</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredBays.map((bay) => (
                <BayRow
                  key={bay.id}
                  bay={bay}
                  onOpenAvailability={setAvailabilityBay}
                  onOpenEdit={setEditBay}
                  onUpdated={handleUpdated}
                  onDeleted={(id) => setBays((prev) => (prev ?? []).filter((d) => d.id !== id))}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {addBayOpen && (
        <Modal title="Add a bay" onClose={() => setAddBayOpen(false)}>
          <AddBayForm
            onCreated={(bay) => {
              setBays((prev) => [bay, ...(prev ?? [])]);
              setAddBayOpen(false);
            }}
            onCancel={() => setAddBayOpen(false)}
          />
        </Modal>
      )}

      {availabilityBay && (
        <AvailabilityModal
          doctor={availabilityBay}
          customerNoun="customer"
          onClose={() => {
            setAvailabilityBay(null);
            loadBays();
          }}
        />
      )}
      {editBay && (
        <EditBayModal
          bay={editBay}
          onClose={() => setEditBay(null)}
          onSaved={(updated) => {
            handleUpdated(updated);
            setEditBay(null);
          }}
        />
      )}
    </div>
  );
}

// ============================================================================

export default function ResourcesPage() {
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
  if (IS_HEALTHCARE) return <DoctorsView account={account} />;
  if (IS_BEAUTY) return <StylistsView account={account} />;
  if (IS_AUTOMOTIVE) return <BaysView account={account} />;
  return <GenericResourcesView account={account} />;
}
