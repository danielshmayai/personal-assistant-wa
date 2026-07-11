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

export interface Me {
  tenant: TenantInfo;
  modules: ModuleInfo[];
  secrets: SecretInfo[];
  google_connected: boolean;
}

export type ChatEvent =
  | { type: "ack"; chat_id: string }
  | { type: "thinking_token"; content: string }
  | { type: "token"; content: string }
  | { type: "tool_start"; name: string; input: unknown }
  | { type: "tool_end"; name: string }
  | { type: "done"; full_reply: string }
  | { type: "error"; code: "invalid_key" | "quota_exceeded" | "rate_limited" | "internal" };

export interface AdminTenant {
  id: string;
  email: string;
  display_name: string;
  is_owner: boolean;
  status: string;
  onboarded: boolean;
}
