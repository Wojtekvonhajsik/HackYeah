// Typy zgodne z openapi.json backendu (stan: wersja 0.1.0).
// Daty to ISO "YYYY-MM-DD".

export type ISODate = string;

export interface GeoPoint {
  lat: number;
  lon: number;
}

export type FeatureType =
  | 'kerb'
  | 'steps'
  | 'ramp'
  | 'entrance'
  | 'surface'
  | 'path_width'
  | 'incline'
  | 'obstacle'
  | 'crossing'
  | 'amenity';

export type SourceType =
  | 'official'
  | 'owner'
  | 'verified_user'
  | 'osm'
  | 'user_report'
  | 'ai_detection';

export type Verdict = 'blocker' | 'uncertain' | 'difficult' | 'unknown' | 'ok' | 'amenity';
export type DataStatus = 'confirmed' | 'unverified' | 'outdated' | 'conflicting';
export type Summary = 'barriers' | 'difficulties' | 'incomplete_data' | 'no_known_barriers';
export type VoteValue = 'confirm' | 'deny';

export interface Source {
  type: SourceType;
  name: string;
  url?: string | null;
  license?: string | null;
  observed_at: ISODate;
  retrieved_at?: ISODate | null;
  sample?: boolean;
}

export interface Evidence {
  observation_id: string;
  source: Source;
  verdict: Verdict;
  reasons: string[];
  trust: number;
  status: DataStatus;
  confirmations: number;
  denials: number;
  last_confirmed_at: ISODate | null;
}

export interface FeatureAssessment {
  feature_id: string;
  type: FeatureType;
  label: string;
  location: GeoPoint;
  verdict: Verdict;
  reasons: string[];
  status: DataStatus;
  trust: number;
  conflict: boolean;
  evidence: Evidence[];
}

export interface Assessment {
  summary: Summary;
  summary_text: string;
  features: FeatureAssessment[];
  missing: string[];
  counts: Partial<Record<Verdict, number>>;
  contains_sample_data: boolean;
  contains_unverified: boolean;
  warnings?: string[];
}

// --- Profil ---------------------------------------------------------------

export interface MaxThreshold {
  soft: number;
  hard: number;
}
export interface MinThreshold {
  soft: number;
  hard: number;
}

export interface MobilityNeeds {
  max_edge_height_cm: MaxThreshold;
  max_step_count: MaxThreshold;
  min_width_cm: MinThreshold;
  max_incline_pct: MaxThreshold;
  difficult_surfaces?: string[];
  blocking_surfaces?: string[];
  needs_handrail?: boolean;
}

export interface VisionNeeds {
  needs_tactile_paving?: boolean;
  needs_sound_signals?: boolean;
  needs_step_contrast?: boolean;
  overhang_min_cm?: number;
  overhang_max_cm?: number;
}

export interface Needs {
  preset?: string | null;
  mobility?: MobilityNeeds | null;
  vision?: VisionNeeds | null;
  wants?: string[];
}

export interface PresetInfo {
  label: string;
  description: string;
  needs: Needs;
}

/** Odpowiedź GET /presets: klucz = identyfikator presetu. */
export type PresetCatalog = Record<string, PresetInfo>;

// --- Miejsca, obserwacje, zdjęcia -----------------------------------------

export interface Place {
  id: string;
  name: string;
  location: GeoPoint;
  address?: string | null;
  osm_type?: string | null;
  osm_id?: number | null;
  sample?: boolean;
  data_loaded?: boolean;
}

export interface Observation {
  id: string;
  type: FeatureType;
  attrs?: Record<string, unknown>;
  location: GeoPoint;
  source: Source;
  confidence?: number; // 0..1
  place_id?: string | null;
  confirmations?: number;
  denials?: number;
  last_confirmed_at?: ISODate | null;
}

export interface ImageRef {
  id: string;
  provider: string;
  url?: string | null;
  location: GeoPoint;
  heading?: number | null;
  captured_at: ISODate;
  license?: string | null;
  attribution_url?: string | null;
}

// --- Żądania ----------------------------------------------------------------

export interface ProfileRequest {
  preset?: string | null;
  overrides?: Record<string, unknown> | null;
  needs?: Needs | null;
  today?: ISODate | null;
}

export interface VoteRequest {
  voter_id: string;
  value: VoteValue;
  correction_attrs?: Record<string, unknown> | null;
}

export interface VoteResponse {
  observation: Observation;
  correction?: Observation | null;
}

export interface AnalyzeRequest {
  image: ImageRef;
  place_id?: string | null;
}
