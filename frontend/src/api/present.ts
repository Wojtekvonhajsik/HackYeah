import type { DataStatus, SourceType, Summary, Verdict } from './types';

// Każdy stan ma symbol + tekst, żeby znaczenie nie zależało od koloru.
export const VERDICT: Record<Verdict, { label: string; symbol: string }> = {
  blocker: { label: 'Bariera nie do pokonania', symbol: '✕' },
  difficult: { label: 'Utrudnienie', symbol: '!' },
  uncertain: { label: 'Niepewne', symbol: '?' },
  unknown: { label: 'Brak danych', symbol: '–' },
  ok: { label: 'W porządku', symbol: '✓' },
  amenity: { label: 'Udogodnienie', symbol: '+' },
};

// "no_known_barriers" celowo NIE brzmi "dostępne": brak znanych barier to nie dowód ich braku.
export const SUMMARY: Record<Summary, string> = {
  barriers: 'Są bariery',
  difficulties: 'Są utrudnienia',
  incomplete_data: 'Niepełne dane',
  no_known_barriers: 'Brak znanych barier (to nie znaczy: na pewno dostępne)',
};

export const STATUS: Record<DataStatus, string> = {
  confirmed: 'Potwierdzone',
  unverified: 'Niezweryfikowane',
  outdated: 'Nieaktualne',
  conflicting: 'Sprzeczne dane',
};

export const SOURCE: Record<SourceType, string> = {
  official: 'Dane oficjalne',
  owner: 'Właściciel miejsca',
  verified_user: 'Zweryfikowany użytkownik',
  osm: 'OpenStreetMap',
  user_report: 'Zgłoszenie użytkownika',
  ai_detection: 'Wykrycie AI (hipoteza)',
};

// ZAŁOŻENIE: trust jest w skali 0..1 (schemat nie podaje zakresu - do potwierdzenia z backendem).
// Pokazujemy poziom opisowy, nie procent. Progi są robocze i trzymane w jednym miejscu.
export const TRUST_LEVELS = { high: 0.75, medium: 0.4 };

export function trustLevel(trust: number): 'wysoka' | 'średnia' | 'niska' {
  if (trust >= TRUST_LEVELS.high) return 'wysoka';
  if (trust >= TRUST_LEVELS.medium) return 'średnia';
  return 'niska';
}

/** Źródło, które trzeba oznaczać jako niepotwierdzoną hipotezę. */
export const isHypothesis = (source: SourceType): boolean => source === 'ai_detection';
