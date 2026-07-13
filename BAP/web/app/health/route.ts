import { NextResponse } from 'next/server';

/**
 * Liveness only, per OBSERVABILITY.md — frontend apps expose /health without a
 * backend dependency check (a slow backend shouldn't make the frontend report
 * unhealthy, per the same graceful-degradation reasoning as the backend's own
 * /health vs /ready split).
 */
export async function GET() {
  return NextResponse.json({ status: 'ok', service: 'bap-web' });
}
