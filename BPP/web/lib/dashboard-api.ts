import { apiFetch } from './api-client';

/** The real Dashboard home (BPP/backend/core/views.py's `dashboard_stats_view`) —
 * scoped server-side to exactly the same resource set `orders_list_view` uses. */
export interface DashboardTrendDay {
  date: string;
  bookings: number;
  revenue: string;
}

export interface TopDoctor {
  name: string;
  revenue: string;
  bookings: number;
}

/** Every owned doctor, not just the ones with bookings — a doctor with zero
 * bookings this month is a real roster gap this list should surface, not
 * silently omit. Pre-sorted by `revenue` descending. */
export interface DoctorPerformanceRow {
  name: string;
  /** Empty string for a non-healthcare brand instance (Beauty/Automotive
   * resources never set this) or a doctor whose profile is incomplete. */
  specialisation: string;
  fee: string;
  /** This month's total — also drives the sort order (by `revenue`, not
   * this field, but both come from the same month-scoped query). */
  bookings: number;
  revenue: string;
}

export interface DashboardStats {
  total_doctors: number;
  doctors_available_today: number;
  doctors_unavailable_today: number;
  /** Review-weighted average across every owned doctor's own rating, to one
   * decimal place — `null` when nobody has been rated yet. */
  overall_rating: string | null;
  rating_count: number;
  earnings_today: string;
  /** % change vs. yesterday — `null` when yesterday had no revenue to
   * compare against (nothing honest to divide by). */
  earnings_today_change_pct: number | null;
  earnings_this_month: string;
  /** % change vs. the same number of days into last month (an
   * apples-to-apples "month to date" comparison, not a partial month
   * against a complete one). */
  earnings_this_month_change_pct: number | null;
  total_bookings: number;
  bookings_today: number;
  bookings_today_change_pct: number | null;
  /** Confirmed bookings nobody has actually paid for yet — a worklist, not
   * date-scoped. */
  pending_payment_count: number;
  /** This month's average revenue per paid booking — `null` with no paid
   * bookings yet this month. */
  avg_booking_value: string | null;
  /** % of this month's non-HELD bookings that were cancelled or no-show —
   * `null` with no bookings yet this month. */
  cancellation_rate: string | null;
  top_doctor: TopDoctor | null;
  doctor_performance: DoctorPerformanceRow[];
  currency: string;
  trend: DashboardTrendDay[];
}

/** "Today"/"this month" are computed server-side in the caller's own local
 * timezone — `getTimezoneOffset()` is the exact value the backend expects
 * (IST: -330), reused as-is rather than re-deriving it there. */
export async function fetchDashboardStats(): Promise<DashboardStats> {
  const tzOffsetMinutes = new Date().getTimezoneOffset();
  const resp = await apiFetch(`/api/v1/dashboard/stats?tz_offset_minutes=${tzOffsetMinutes}`);
  return resp.json();
}
