import { apiFetch } from './api-client';

/** Profile page's State -> Division -> Pincode cascade — steps 2 and 3. */

export interface DivisionOption {
  id: number;
  name: string;
}

/** Real divisions under the given state's Circle, or [] when that state has no
 * shared.postal_directory data yet (not an error — most states don't yet). */
export async function fetchDivisions(state: string): Promise<DivisionOption[]> {
  const resp = await apiFetch(`/api/v1/postal-directory/divisions?state=${encodeURIComponent(state)}`);
  const body = await resp.json();
  return body.divisions;
}

export async function fetchPincodesForDivision(divisionId: number): Promise<string[]> {
  const resp = await apiFetch(`/api/v1/postal-directory/pincodes?division_id=${divisionId}`);
  const body = await resp.json();
  return body.pincodes;
}
