import * as SecureStore from 'expo-secure-store';
import * as Crypto from 'expo-crypto';
import type {
  Assessment,
  ImageRef,
  Observation,
  Place,
  PresetCatalog,
  ProfileRequest,
  VoteResponse,
  VoteValue,
} from './types';

// Telefon nie widzi "localhost" komputera. Ustaw w .env:
// EXPO_PUBLIC_API_URL=http://192.168.x.x:8000   (emulator Androida: http://10.0.2.2:8000)
// Backend uruchom z: uvicorn bezbarier.api.main:app --host 0.0.0.0
const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://10.0.2.2:8000';

// Pierwsze wywołanie assessment pobiera dane z Overpass i bywa wolne.
const TIMEOUT_MS = { default: 15_000, assessment: 40_000 };

export class ApiError extends Error {
  constructor(
    public status: number, // 0 = brak połączenia / timeout
    public detail: string,
  ) {
    super(detail);
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = TIMEOUT_MS.default,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
      signal: controller.signal,
    });
    if (!res.ok) {
      let detail = `Błąd ${res.status}`;
      try {
        const body = await res.json();
        // 4xx/503 z backendu: {detail: string}; 422: {detail: [{msg, ...}]}
        if (typeof body.detail === 'string') detail = body.detail;
        else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join('; ');
      } catch {
        /* odpowiedź bez JSON-a */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    const aborted = e instanceof Error && e.name === 'AbortError';
    throw new ApiError(
      0,
      aborted ? 'Serwer nie odpowiedział na czas. Spróbuj ponownie.' : 'Brak połączenia z serwerem.',
    );
  } finally {
    clearTimeout(timer);
  }
}

// --- Anonimowy identyfikator głosującego (lokalnie, bez konta) --------------

const VOTER_KEY = 'voter_id';
let cachedVoterId: string | null = null;

export async function getVoterId(): Promise<string> {
  if (cachedVoterId) return cachedVoterId;
  let id = await SecureStore.getItemAsync(VOTER_KEY);
  if (!id) {
    id = Crypto.randomUUID();
    await SecureStore.setItemAsync(VOTER_KEY, id);
  }
  cachedVoterId = id;
  return id;
}

// --- Endpointy --------------------------------------------------------------

export const getPresets = () => request<PresetCatalog>('/presets');

export const listPlaces = () => request<Place[]>('/places');

/** 503 = wyszukiwarka OSM niedostępna (komunikat po polsku w ApiError.detail). */
export const searchPlaces = (q: string) =>
  request<Place[]>(`/places/search?q=${encodeURIComponent(q)}`);

export const getAssessment = (placeId: string, profile: ProfileRequest) =>
  request<Assessment>(
    `/places/${encodeURIComponent(placeId)}/assessment`,
    { method: 'POST', body: JSON.stringify(profile) },
    TIMEOUT_MS.assessment,
  );

/**
 * Magazyn backendu jest w pamięci: po jego restarcie 404 na znanym miejscu.
 * Wtedy ponownie rejestrujemy miejsce przez wyszukiwanie i próbujemy raz jeszcze.
 */
export async function assessPlace(place: Place, profile: ProfileRequest): Promise<Assessment> {
  try {
    return await getAssessment(place.id, profile);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      const results = await searchPlaces(place.name);
      if (results.some((p) => p.id === place.id)) return getAssessment(place.id, profile);
    }
    throw e;
  }
}

export async function vote(
  observationId: string,
  value: VoteValue,
  correctionAttrs?: Record<string, unknown>,
): Promise<VoteResponse> {
  return request<VoteResponse>(`/observations/${encodeURIComponent(observationId)}/votes`, {
    method: 'POST',
    body: JSON.stringify({
      voter_id: await getVoterId(),
      value,
      correction_attrs: value === 'deny' ? (correctionAttrs ?? null) : null,
    }),
  });
}

export const analyzeImage = (image: ImageRef, placeId?: string) =>
  request<Observation[]>(
    '/observations/analyze',
    { method: 'POST', body: JSON.stringify({ image, place_id: placeId ?? null }) },
    TIMEOUT_MS.assessment,
  );
