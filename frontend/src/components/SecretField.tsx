import { useState } from "react";
import type { SecretInfo, SecretSaveResponse } from "../lib/types";
import { api, ApiError } from "../lib/api";
import { Badge, Button, Input } from "./ui";

const ERROR_TEXT: Record<string, string> = {
  invalid_key: "This key was rejected by the provider — double-check and try again.",
  permission_denied: "This key doesn't have access — enable the \"Generative Language API\" for its project in Google Cloud Console, then try again.",
  no_supported_model: "This key can't run any supported Gemini model — check that the Generative Language API is enabled for it.",
  empty_value: "Value cannot be empty.",
};

export function SecretField({
  secret,
  onSaved,
}: {
  secret: SecretInfo;
  onSaved: () => void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [tierNote, setTierNote] = useState("");

  const save = async () => {
    if (!value.trim()) return;
    setBusy(true);
    setError("");
    setTierNote("");
    try {
      const res = await api.put<SecretSaveResponse>("/api/secrets", {
        key: secret.key,
        value: value.trim(),
      });
      setValue("");
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      if (res.limited && res.model) {
        setTierNote(
          `Free-tier key detected — your assistant will run on ${res.model}. ` +
          "Daily request limits apply and responses may be slower. " +
          "Enable billing on your Google project to unlock the stronger model.",
        );
      }
      onSaved();
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "save_failed";
      setError(ERROR_TEXT[detail] ?? `Could not save (${detail}).`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <label className="text-sm font-medium">{secret.label}</label>
        {secret.required && <Badge tone="warn">required</Badge>}
        {secret.set && <Badge tone="ok">configured</Badge>}
        {secret.help_url && (
          <a
            href={secret.help_url}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-indigo-400 hover:underline"
          >
            Where do I get this?
          </a>
        )}
      </div>
      <div className="flex gap-2">
        <Input
          type="password"
          autoComplete="off"
          placeholder={secret.set ? "••••••••  (saved — enter to replace)" : secret.placeholder || "Enter value"}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
        />
        <Button variant="secondary" disabled={busy || !value.trim()} onClick={save}>
          {busy ? "Checking…" : saved ? "Saved ✓" : "Save"}
        </Button>
      </div>
      {error && <p className="mt-1 text-xs text-red-400">{error}</p>}
      {tierNote && (
        <p className="mt-1 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          {tierNote}
        </p>
      )}
    </div>
  );
}
