import { apiFetch } from './api-client';

/** "Detect my location" — wraps the browser's own Geolocation API as a Promise,
 * then resolves those GPS coordinates to a pincode server-side (geo_reverse_view
 * — keeps the third-party geocoding call out of the browser). Throws a plain,
 * user-facing message for every real failure mode (unsupported, denied, timed
 * out, or genuinely unresolved), never a raw browser/API error. */
export async function detectPincode(): Promise<string> {
  if (!('geolocation' in navigator)) {
    throw new Error("Your browser doesn't support location detection");
  }
  const position = await new Promise<GeolocationPosition>((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      timeout: 10000,
      maximumAge: 300000,
    });
  }).catch((err: GeolocationPositionError) => {
    throw new Error(
      err.code === err.PERMISSION_DENIED
        ? 'Location access was denied'
        : "Couldn't get your location"
    );
  });

  const resp = await apiFetch(
    `/api/v1/geo/reverse?lat=${position.coords.latitude}&lon=${position.coords.longitude}`
  );
  const body = await resp.json();
  if (!body.pincode) {
    throw new Error("Couldn't match your location to a pincode");
  }
  return body.pincode as string;
}
