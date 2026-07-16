export interface TenantInfo {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string;
  is_owner: boolean;
  onboarded: boolean;
}

export interface ModuleInfo {
  id: string;
  label: string;
  description: string;
  core: boolean;
  default_on: boolean;
  owner_only: boolean;
  needs_oauth: boolean;
  secret_keys: string[];
  enabled: boolean;
  available: boolean;
  missing_secrets: string[];
}

export interface SecretInfo {
  key: string;
  label: string;
  module: string;
  required: boolean;
  help_url: string;
  placeholder: string;
  set: boolean;
}

export interface GeminiAccess {
  model: string;
  limited: boolean; // free-tier key: fallback model and/or daily quotas apply
  engine: "gemini" | "ollama"; // "ollama" = no Gemini model works for this key at all
  allow_premium: boolean; // opted in to models stronger than 2.5-flash (e.g. pro tier)
  can_set_premium: boolean; // false for the owner (always platform default)
}

export interface Me {
  tenant: TenantInfo;
  modules: ModuleInfo[];
  secrets: SecretInfo[];
  google_connected: boolean;
  gemini: GeminiAccess;
}

export interface SecretSaveResponse {
  ok: boolean;
  validation: string;
  model?: string;
  limited?: boolean;
}

export type ChatEvent =
  | { type: "ack"; chat_id: string }
  | { type: "thinking_token"; content: string }
  | { type: "token"; content: string }
  | { type: "tool_start"; name: string; input: unknown }
  | { type: "tool_end"; name: string }
  | { type: "done"; full_reply: string }
  | { type: "error"; code: "invalid_key" | "quota_exceeded" | "rate_limited" | "internal" }
  | { type: "notification"; message: string };

export interface Meal {
  id: number;
  meal_description: string;
  protein: number;
  carbs: number;
  calories: number;
  micros: Record<string, number>;
  source: string; // "text" | "image" | "water"
  created_at: string;
}

export interface NutritionToday {
  date: string;
  protein_target: number;
  meals: Meal[];
  totals: { protein: number; carbs: number; calories: number; micros: Record<string, number> };
  water_ml: number;
  water_target_ml: number;
}

export interface NutritionDay {
  date: string;
  protein: number;
  carbs: number;
  calories: number;
  meals: number;
  water_ml: number;
}

export interface Exercise {
  name: string;
  sets?: number;
  reps?: number;
  weight_kg?: number;
  rpe?: number;
}

export interface Workout {
  id: number;
  workout_type: string;
  title: string;
  duration_min: number;
  avg_rpe: number;
  total_volume: number;
  exercises: Exercise[];
  metrics: Record<string, unknown>;
  ai_summary?: string;
  ai_next_rec?: { summary?: string; focus?: string; targets?: string[] };
  source: string; // "text" | "image" | "structured" | "garmin"
  created_at: string;
}

export interface FitnessToday {
  date: string;
  workouts: Workout[];
  total_volume: number;
  total_duration_min: number;
}

export interface FitnessDay {
  date: string;
  total_volume: number;
  total_duration: number;
  avg_rpe: number;
  session_count: number;
  types: string[];
}

export interface BodyMetric {
  id?: number;
  weight_kg: number | null;
  lbm_kg: number | null;
  smm_kg: number | null;
  bf_pct: number | null;
  log_date: string;
  source: string;
  note?: string;
  extras?: Record<string, unknown>;
  ai_summary?: string;
}

export interface BodyMetricsResponse {
  latest: BodyMetric | null;
  history: BodyMetric[];
}

export interface DailyRec {
  readiness: "ready" | "light" | "rest";
  readiness_he: string;
  workout_type: string;
  title: string;
  duration_min: number;
  rationale: string;
  weekly_done: number;
  weekly_target: number;
  key_exercises: Exercise[];
  days_since_last: number;
  last_avg_rpe: number | null;
  garmin?: { sleep_score?: number; bb_high?: number; steps?: number } | null;
}

export interface ProgressionPoint {
  date: string;
  top_weight: number;
  volume: number;
}

export interface GarminStatus {
  connected: boolean;
  today: {
    sleep_score: number | null;
    steps: number | null;
    resting_hr: number | null;
    bb_latest: number | null;
  } | null;
}

export interface TodayInfo {
  weather: string;
  jobs_count: number;
}

export interface CalendarEvent {
  title: string;
  time: string;
  date: string;
  full_day: boolean;
}

export interface CalendarToday {
  events: CalendarEvent[];
  connected: boolean;
  error?: string;
}

export interface Job {
  id: number;
  action_type: string;
  payload: Record<string, unknown>;
  run_at: string;
  status: string;
  executed_at: string | null;
}

export interface ProactiveCard {
  id: number;
  card_type: string;
  title: string;
  detail: string;
  action_label: string;
  action_chat: string;
  expires_at: string | null;
  created_at: string;
}

export interface TuyaDevice {
  id: string;
  name: string;
  category: string;
  online: boolean;
}

export interface MemoryCategory {
  name: string;
  count: number;
}

export interface MemoryEntitySummary {
  entity: string;
  file: string;
  updated_at: number;
  preview: string;
}

export interface MemoryEntityDetail {
  entity: string;
  category: string;
  content: string;
  meta: Record<string, unknown>;
}

export interface MemoryRule {
  text: string;
  added: string;
  raw: string;
}

export interface AdminTenant {
  id: string;
  email: string;
  display_name: string;
  is_owner: boolean;
  status: string;
  onboarded: boolean;
}
