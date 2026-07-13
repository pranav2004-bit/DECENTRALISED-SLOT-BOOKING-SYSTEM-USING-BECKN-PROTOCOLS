import { NextResponse } from 'next/server';

/**
 * Liveness only, per OBSERVABILITY.md — frontend apps expose /health without a
 * backend dependency check.
 */
export async function GET() {
  return NextResponse.json({ status: 'ok', service: 'bpp-web' });
}
