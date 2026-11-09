'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { EmptyState } from '@/components/ui/EmptyState';
import { me, type BusinessAccount } from '@/lib/auth-api';
import { fetchDashboardStats, type DashboardStats, type DoctorPerformanceRow } from '@/lib/dashboard-api';
import { formatAmount } from '@/lib/format';
import { BRAND } from '@/lib/brand';

/**
 * Dashboard home (2026-11-06) — the business owner's/staff's real at-a-glance
 * view: roster + today's availability, today's/this month's revenue, booking
 * volume, and a 7-day trend. Every figure comes from the real
 * `dashboard_stats_view` (BPP/backend/core/views.py), scoped server-side to
 * exactly the same resource set the Bookings page and the live WebSocket
 * already use — never recomputed client-side from a second, potentially
 * drifting source.
 */

/** Universal dashboard convention (Stripe, Razorpay, Google Analytics): a raw
 * number next to the period it's being compared against. `null` renders
 * nothing — no fabricated "0%" when there's genuinely no prior baseline.
 * A real SVG arrow, not a Unicode ▲/▼ glyph — a Unicode triangle's size and
 * baseline shift between fonts/platforms in a way a drawn path never does,
 * the same reason every real dashboard (Stripe, Linear) icons this instead
 * of relying on a character. */
function TrendArrow({ direction }: { direction: 'up' | 'down' }) {
  return (
    <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" fill="currentColor" aria-hidden="true">
      <path d={direction === 'up' ? 'M6 2l5 7H1z' : 'M6 10 1 3h10z'} />
    </svg>
  );
}

function ChangeBadge({ pct }: { pct: number | null }) {
  if (pct === null) return null;
  const isUp = pct > 0;
  const isFlat = pct === 0;
  return (
    <span
      className={
        isFlat
          ? 'inline-flex items-center gap-0.5 rounded-full bg-neutral-100 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-neutral-500'
          : isUp
            ? 'inline-flex items-center gap-0.5 rounded-full bg-green-100 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-green-700'
            : 'inline-flex items-center gap-0.5 rounded-full bg-red-100 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-red-700'
      }
    >
      {!isFlat && <TrendArrow direction={isUp ? 'up' : 'down'} />}
      {Math.abs(pct)}%
    </span>
  );
}

function StatCard({
  label,
  value,
  sublabel,
  changePct,
  accent = false,
}: {
  label: string;
  value: string;
  sublabel?: string;
  changePct?: number | null;
  accent?: boolean;
}) {
  // A bare "-" is "no data yet", not a real value — rendering it at the same
  // bold, full-strength weight as a genuine ₹500 or 10 makes an empty state
  // read as a glitch instead of a deliberate absence. Every real dashboard
  // (Stripe, Linear) recedes this case visually instead of letting it
  // compete with actual numbers.
  const isEmpty = value === '-';
  return (
    <div
      className={
        accent
          ? 'flex flex-col gap-1 rounded-xl border border-[var(--brand-600)]/30 bg-[var(--brand-50)] px-5 py-4'
          : 'flex flex-col gap-1 rounded-xl border border-neutral-200 bg-white px-5 py-4'
      }
    >
      <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">{label}</span>
      <span className="flex items-center gap-2">
        <span
          className={
            isEmpty
              ? 'text-2xl font-semibold tabular-nums text-neutral-300'
              : accent
                ? 'text-2xl font-bold tabular-nums text-[var(--brand-700)]'
                : 'text-2xl font-bold tabular-nums text-neutral-900'
          }
        >
          {value}
        </span>
        {changePct !== undefined && <ChangeBadge pct={changePct} />}
      </span>
      {sublabel && <span className="text-xs text-neutral-500">{sublabel}</span>}
    </div>
  );
}

// AutoCare's bays have no specialisation at all (same reasoning as the Bays
// management table already dropping Service/Experience/Gender) — the
// performance table's own Specialisation column is dropped for this brand
// only, CareNest/StyleNest keep it since their resources genuinely have one.
const IS_AUTOMOTIVE = BRAND.id === 'automotive';

const WEEKDAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function dayLabel(isoDate: string): string {
  const date = new Date(`${isoDate}T00:00:00`);
  return `${WEEKDAY_SHORT[date.getDay()]} ${date.getDate()}`;
}

const CHART_WIDTH = 600;
const CHART_HEIGHT = 160;
const CHART_TOP_PADDING = 16; // room for the highest point's own dot, not clipped

/** Stripe/Razorpay-dashboard-style area chart — the industry-standard shape
 * for a money trend (a bar chart reads as "N separate events"; revenue is a
 * continuous flow, which a filled line communicates at a glance). */
/** Both charts plot one point/bar per day in its own equal-width slot
 * (`CHART_WIDTH / trend.length`), centered within it — this row mirrors that
 * exact math with `flex` + explicit percentage widths instead of
 * `justify-between` (which distributes *items*, not their centers, and so
 * drifts out of alignment with the plotted points/bars above it, worse the
 * more a label's own rendered width differs from its neighbors'). */
function DayLabelRow({ trend }: { trend: DashboardStats['trend'] }) {
  return (
    <div className="mt-2 flex text-[11px] font-medium text-neutral-500">
      {trend.map((day) => (
        <span key={day.date} className="text-center" style={{ width: `${100 / trend.length}%` }}>
          {dayLabel(day.date)}
        </span>
      ))}
    </div>
  );
}

function RevenueChart({ trend, currency }: { trend: DashboardStats['trend']; currency: string }) {
  const values = trend.map((d) => Number(d.revenue));
  const max = Math.max(1, ...values);
  const total = values.reduce((a, b) => a + b, 0);
  const allZero = total === 0;

  const barSlot = CHART_WIDTH / trend.length;
  const points = trend.map((day, i) => {
    const x = barSlot * i + barSlot / 2;
    const y = CHART_TOP_PADDING + (1 - Number(day.revenue) / max) * (CHART_HEIGHT - CHART_TOP_PADDING);
    return { x, y, day };
  });
  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ');
  const areaPath = `${linePath} L${points[points.length - 1].x},${CHART_HEIGHT} L${points[0].x},${CHART_HEIGHT} Z`;

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="mb-1 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Revenue - last 7 days</h2>
        <span className="text-xs font-medium tabular-nums text-neutral-500">
          Total {formatAmount(total, currency)}
        </span>
      </div>
      {allZero ? (
        <p className="py-11 text-center text-sm text-neutral-500">No revenue in the last 7 days.</p>
      ) : (
        <>
          <svg
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            preserveAspectRatio="none"
            className="w-full"
            style={{ height: CHART_HEIGHT }}
            role="img"
            aria-label={`Revenue over the last 7 days, totalling ${formatAmount(total, currency)}`}
          >
            <defs>
              <linearGradient id="revenue-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--brand-600)" stopOpacity="0.25" />
                <stop offset="100%" stopColor="var(--brand-600)" stopOpacity="0" />
              </linearGradient>
            </defs>
            <line x1={0} y1={CHART_HEIGHT - 1} x2={CHART_WIDTH} y2={CHART_HEIGHT - 1} stroke="#a3a3a3" strokeWidth={1.5} />
            <path d={areaPath} fill="url(#revenue-fill)" />
            <path
              d={linePath}
              fill="none"
              stroke="var(--brand-600)"
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {points.map((p, i) => {
              // Today gets the same emphasis BookingsChart gives its own
              // "today" bar (full opacity, more visual weight) — the two
              // charts are meant to read as one system, not two
              // independently-tuned widgets that happen to share a color.
              const isToday = i === points.length - 1;
              return (
                <circle
                  key={p.day.date}
                  cx={p.x}
                  cy={p.y}
                  r={isToday ? 5 : 3}
                  fill="var(--brand-600)"
                  opacity={isToday ? 1 : 0.7}
                  stroke={isToday ? 'white' : 'none'}
                  strokeWidth={isToday ? 2 : 0}
                >
                  <title>{`${formatAmount(p.day.revenue, currency)} - ${dayLabel(p.day.date)}`}</title>
                </circle>
              );
            })}
          </svg>
          <DayLabelRow trend={trend} />
        </>
      )}
    </div>
  );
}

/** Same drawing surface/conventions as RevenueChart (fixed viewBox, baseline,
 * day-label row below) — the two charts read as one visual system, not two
 * independently-styled widgets glued together. A 0-value day draws no bar at
 * all (just its "0" sitting on the baseline) rather than a barely-visible
 * sliver — a real, previously-shipped bug this replaces (a near-invisible
 * fixed-minimum-height bar reads as a rendering glitch, not a zero). */
function BookingsChart({ trend }: { trend: DashboardStats['trend'] }) {
  const maxBookings = Math.max(1, ...trend.map((d) => d.bookings));
  const total = trend.reduce((sum, d) => sum + d.bookings, 0);
  const barSlot = CHART_WIDTH / trend.length;
  const barWidth = Math.min(36, barSlot * 0.5);
  const drawableHeight = CHART_HEIGHT - CHART_TOP_PADDING;

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-5">
      <div className="mb-1 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-neutral-900">Bookings - last 7 days</h2>
        <span className="text-xs font-medium tabular-nums text-neutral-500">Total {total}</span>
      </div>
      {total === 0 ? (
        <p className="py-11 text-center text-sm text-neutral-500">No bookings in the last 7 days.</p>
      ) : (
        <>
          <svg
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            className="w-full"
            style={{ height: CHART_HEIGHT }}
            role="img"
            aria-label={`Bookings over the last 7 days, totalling ${total}`}
          >
            <line x1={0} y1={CHART_HEIGHT - 1} x2={CHART_WIDTH} y2={CHART_HEIGHT - 1} stroke="#a3a3a3" strokeWidth={1.5} />
            {trend.map((day, i) => {
              const centerX = barSlot * i + barSlot / 2;
              const barHeight = day.bookings === 0 ? 0 : Math.max(6, (day.bookings / maxBookings) * drawableHeight);
              const isToday = i === trend.length - 1;
              return (
                <g key={day.date}>
                  {barHeight > 0 && (
                    <rect
                      x={centerX - barWidth / 2}
                      y={CHART_HEIGHT - 1 - barHeight}
                      width={barWidth}
                      height={barHeight}
                      rx={3}
                      fill="var(--brand-600)"
                      opacity={isToday ? 1 : 0.6}
                    >
                      <title>{`${day.bookings} booking(s) - ${dayLabel(day.date)}`}</title>
                    </rect>
                  )}
                  <text
                    x={centerX}
                    y={CHART_HEIGHT - 1 - barHeight - 8}
                    textAnchor="middle"
                    fontSize={11}
                    fontWeight={600}
                    fill="#404040"
                    className="tabular-nums"
                  >
                    {day.bookings}
                  </text>
                </g>
              );
            })}
          </svg>
          <DayLabelRow trend={trend} />
        </>
      )}
    </div>
  );
}

/** Full roster, ranked — not just the single "Top doctor" stat card. The
 * card has a fixed max-height and scrolls internally once the roster grows
 * past what fits, so a 10+ doctor business doesn't stretch the whole
 * dashboard layout to fit every row; the backend already sorts by revenue
 * descending, so the doctors worth seeing first are visible without
 * scrolling at all. */
/** Fixed to roughly 3 data rows' worth of height (header + 3 * row height) —
 * a deliberate, tuned value, not a measurement of anything else on the
 * page: 3 rows is enough to show the top performers at a glance, and the
 * roster scrolls internally past that regardless of how tall any other
 * panel on the page happens to be. */
function DoctorPerformance({
  doctors = [],
  currency,
}: {
  doctors?: DoctorPerformanceRow[];
  currency: string;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-xs font-bold uppercase tracking-wide text-neutral-500">
        {BRAND.resourceNounSingular} performance (this month)
      </h2>
      <div className="flex max-h-[181px] flex-col overflow-hidden rounded-xl border border-neutral-200 bg-white">
        {doctors.length === 0 ? (
          <p className="flex flex-1 items-center justify-center px-5 py-8 text-center text-sm text-neutral-500">
            No {BRAND.resourceNounPlural.toLowerCase()} yet.
          </p>
        ) : (
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="w-full min-w-[480px] border-collapse text-sm">
              <thead>
                <tr className="sticky top-0 bg-neutral-50 text-left text-xs font-semibold uppercase tracking-wide text-neutral-500">
                  <th className="px-5 py-2">{BRAND.resourceNounSingular}</th>
                  {!IS_AUTOMOTIVE && <th className="px-3 py-2">Specialisation</th>}
                  {/* Numeric columns right-align, text columns left-align —
                      the same convention every real data table (Stripe's own
                      transaction list included) uses, so a column of amounts
                      stacks for scanning instead of ragging left like prose. */}
                  <th className="px-3 py-2 text-right">Fee</th>
                  <th className="px-5 py-2 text-right">Bookings</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-200">
                {doctors.map((doctor) => (
                  <tr key={doctor.name} className="hover:bg-neutral-50">
                    <td className="px-5 py-3 font-semibold text-neutral-900">{doctor.name}</td>
                    {!IS_AUTOMOTIVE && (
                      <td className="px-3 py-3 text-neutral-600">{doctor.specialisation || '-'}</td>
                    )}
                    <td className="px-3 py-3 text-right tabular-nums text-neutral-900">
                      {formatAmount(doctor.fee, currency)}
                    </td>
                    <td className="px-5 py-3 text-right tabular-nums text-neutral-900">{doctor.bookings}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

function DashboardContent({ account }: { account: BusinessAccount }) {
  const [stats, setStats] = useState<DashboardStats | 'loading' | 'error'>('loading');
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchDashboardStats()
      .then((result) => {
        if (!cancelled) setStats(result);
      })
      .catch(() => {
        if (!cancelled) setStats('error');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setStats(await fetchDashboardStats());
    } catch {
      // Keep whatever was already on screen — a failed manual refresh
      // shouldn't blank out real data the owner was just looking at.
    } finally {
      setRefreshing(false);
    }
  }, []);

  // A browser Back/Forward navigation can restore this whole page from the
  // bfcache — React state and all — without re-running the mount effect
  // above, so without this the owner would see numbers frozen from whenever
  // they first loaded the page, not "every time accurate data must be
  // present ... whenever users visit". `event.persisted` is exactly the
  // signal that distinguishes a bfcache restore from a genuine fresh load
  // (which the mount effect above already covers).
  useEffect(() => {
    function onPageShow(event: PageTransitionEvent) {
      if (event.persisted) handleRefresh();
    }
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, [handleRefresh]);

  if (stats === 'loading') return <LoadingState label="Loading dashboard…" />;
  if (stats === 'error') {
    return (
      <EmptyState
        title="Couldn't load the dashboard"
        description="Something went wrong fetching your stats. Try refreshing the page."
      />
    );
  }

  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Welcome back</h1>
          <p className="text-sm text-neutral-500">{today}</p>
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={refreshing}
          aria-label="Refresh dashboard"
          className="flex cursor-pointer items-center gap-1.5 rounded-md border border-[var(--brand-600)] px-3 py-1.5 text-sm font-medium text-[var(--brand-700)] hover:bg-[var(--brand-50)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          <svg
            viewBox="0 0 24 24"
            className={refreshing ? 'h-4 w-4 animate-spin' : 'h-4 w-4'}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="flex flex-col gap-3">
          <h2 className="text-xs font-bold uppercase tracking-wide text-neutral-500">
            {BRAND.resourceNounPlural}
          </h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <StatCard label={`Total ${BRAND.resourceNounPlural.toLowerCase()}`} value={String(stats.total_doctors)} />
            <StatCard label="Available today" value={String(stats.doctors_available_today)} />
            <StatCard label="Not available today" value={String(stats.doctors_unavailable_today)} />
            <StatCard
              label="Overall rating"
              value={stats.overall_rating ? `${stats.overall_rating} ★` : '-'}
            />
          </div>
        </section>

        <section className="flex flex-col gap-3">
          <h2 className="text-xs font-bold uppercase tracking-wide text-neutral-500">Revenue &amp; bookings</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <StatCard
              label="Today's earnings"
              value={formatAmount(stats.earnings_today, stats.currency)}
              changePct={stats.earnings_today_change_pct}
              accent
            />
            <StatCard
              label="This month's earnings"
              value={formatAmount(stats.earnings_this_month, stats.currency)}
              changePct={stats.earnings_this_month_change_pct}
              accent
            />
            <StatCard
              label="Bookings today"
              value={String(stats.bookings_today)}
              changePct={stats.bookings_today_change_pct}
            />
            <StatCard label="Total bookings" value={String(stats.total_bookings)} />
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="flex flex-col gap-3">
          <h2 className="text-xs font-bold uppercase tracking-wide text-neutral-500">Performance</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <StatCard
              label="Avg. booking value"
              value={stats.avg_booking_value ? formatAmount(stats.avg_booking_value, stats.currency) : '-'}
            />
            <StatCard label="Pending payments" value={String(stats.pending_payment_count)} />
            <StatCard
              label="Cancellation rate"
              value={stats.cancellation_rate ? `${stats.cancellation_rate}%` : '-'}
            />
            <StatCard
              label={`Top ${BRAND.resourceNounSingular.toLowerCase()} (this month)`}
              value={
                stats.top_doctor
                  ? `${stats.top_doctor.name} - ${formatAmount(stats.top_doctor.revenue, stats.currency)}`
                  : '-'
              }
            />
          </div>
        </section>

        <DoctorPerformance doctors={stats.doctor_performance} currency={stats.currency} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <RevenueChart trend={stats.trend} currency={stats.currency} />
        <BookingsChart trend={stats.trend} />
      </div>
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
      setAccount(result);
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (account === 'loading' || account === null) return <LoadingState label="Checking session…" />;
  return <DashboardContent account={account} />;
}
