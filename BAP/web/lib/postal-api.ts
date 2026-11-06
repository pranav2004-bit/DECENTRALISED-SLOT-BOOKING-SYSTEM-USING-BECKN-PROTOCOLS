import { apiFetch, ApiError } from './api-client';

/** Search page's State -> Division location picker, including "Detect my location". */

export interface DivisionOption {
  id: number;
  name: string;
}

export interface PincodeLookupResult {
  division: DivisionOption;
  state: string;
}

/** Real divisions under the given state's Circle, or [] when that state has no
 * shared.postal_directory data yet (not an error — most states don't yet). */
export async function fetchDivisions(state: string): Promise<DivisionOption[]> {
  const resp = await apiFetch(`/api/v1/postal-directory/divisions?state=${encodeURIComponent(state)}`);
  const body = await resp.json();
  return body.divisions;
}

/** "Detect my location" step 2: the Division + State for a real pincode (already
 * resolved from GPS coordinates by detectPincode). null for a pincode outside
 * this project's seeded coverage — not an error, just nothing to auto-fill. */
export async function lookupByPincode(pincode: string): Promise<PincodeLookupResult | null> {
  try {
    const resp = await apiFetch(`/api/v1/postal-directory/lookup?pincode=${encodeURIComponent(pincode)}`);
    return await resp.json();
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
