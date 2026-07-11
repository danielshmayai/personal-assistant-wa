import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSession } from "../lib/session";
import { api, ApiError } from "../lib/api";
import { Badge, Button, Card, Input, Switch, cn } from "../components/ui";

const OPTIONAL_KEYS = [
  { key: "TAVILY_API_KEY", label: "Tavily Search API Key", hint: "Better web search (free tier available)", placeholder: "tvly-..." },
  { key: "TUYA_ACCESS_ID", label: "Tuya Access ID", hint: "Smart-home control", placeholder: "" },
  { key: "TUYA_ACCESS_KEY", label: "Tuya Access Key", hint: "", placeholder: "" },
  { key: "GOOGLE_TTS_API_KEY", label: "Google Cloud TTS Key", hint: "Natural voice replies", placeholder: "" },
];

export default function Onboarding() {
  const { me, refresh } = useSession();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [geminiKey, setGeminiKey] = useState("");
  const [optional, setOptional] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Set<string>>(() => new Set(
    me?.modules.filter((m) => (m.default_on || m.core) && m.available).map((m) => m.id) ?? []
  ));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const selectableModules = useMemo(
    () => (me?.modules ?? []).filter((m) => m.available),
    [me],
  );

  const finish = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post("/api/onboarding", {
        gemini_api_key: geminiKey.trim(),
        secrets: Object.fromEntries(
          Object.entries(optional).filter(([, v]) => v.trim()),
        ),
        modules: [...selected],
      });
      await refresh();
      navigate("/");
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "failed";
      setError(
        detail === "invalid_key"
          ? "That Gemini key was rejected — double-check it and try again."
          : `Could not finish setup (${detail}).`,
      );
      if (detail === "invalid_key") setStep(0);
    } finally {
      setBusy(false);
    }
  };

  const steps = ["Gemini API Key", "Optional Integrations", "Choose Modules"];

  return (
    <div className="mx-auto flex min-h-full max-w-2xl flex-col justify-center p-4">
      <div className="mb-6 text-center">
        <div className="text-4xl">⚡</div>
        <h1 className="mt-2 text-2xl font-semibold">
          Welcome{me?.tenant.display_name ? `, ${me.tenant.display_name.split(" ")[0]}` : ""}
        </h1>
        <p className="mt-1 text-sm text-slate-400">Three quick steps and your assistant is live.</p>
      </div>

      <div className="mb-6 flex items-center justify-center gap-2">
        {steps.map((label, i) => (
          <div key={label} className="flex items-center gap-2">
            <div
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold",
                i < step ? "bg-emerald-600 text-white" : i === step ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-400",
              )}
            >
              {i < step ? "✓" : i + 1}
            </div>
            <span className={cn("hidden text-xs sm:block", i === step ? "text-slate-200" : "text-slate-500")}>
              {label}
            </span>
            {i < steps.length - 1 && <div className="h-px w-6 bg-slate-700" />}
          </div>
        ))}
      </div>

      <Card className="p-6">
        {step === 0 && (
          <div>
            <h2 className="font-medium">Your Gemini API key <Badge tone="warn">required</Badge></h2>
            <p className="mt-1 text-sm text-slate-400">
              The assistant runs on Google Gemini using <b>your</b> key, so your usage is yours alone.
              Get a free key at{" "}
              <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" className="text-indigo-400 hover:underline">
                aistudio.google.com/apikey
              </a>.
            </p>
            <Input
              className="mt-4"
              type="password"
              placeholder="AIza..."
              value={geminiKey}
              onChange={(e) => setGeminiKey(e.target.value)}
            />
            {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
            <div className="mt-5 flex justify-end">
              <Button disabled={!geminiKey.trim()} onClick={() => setStep(1)}>Continue</Button>
            </div>
          </div>
        )}

        {step === 1 && (
          <div>
            <h2 className="font-medium">Optional integrations</h2>
            <p className="mt-1 text-sm text-slate-400">
              Add these now or later in Settings — everything is stored encrypted.
            </p>
            <div className="mt-4 space-y-3">
              {OPTIONAL_KEYS.map((k) => (
                <div key={k.key}>
                  <label className="text-sm">{k.label}{k.hint && <span className="text-slate-500"> — {k.hint}</span>}</label>
                  <Input
                    className="mt-1"
                    type="password"
                    placeholder={k.placeholder || "Leave empty to skip"}
                    value={optional[k.key] ?? ""}
                    onChange={(e) => setOptional((prev) => ({ ...prev, [k.key]: e.target.value }))}
                  />
                </div>
              ))}
            </div>
            <div className="mt-5 flex justify-between">
              <Button variant="ghost" onClick={() => setStep(0)}>Back</Button>
              <Button onClick={() => setStep(2)}>Continue</Button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div>
            <h2 className="font-medium">Choose your modules</h2>
            <p className="mt-1 text-sm text-slate-400">You can change these anytime in Settings.</p>
            <div className="mt-4 space-y-2">
              {selectableModules.map((m) => (
                <div key={m.id} className="flex items-center justify-between rounded-lg border border-slate-800 px-3 py-2">
                  <div>
                    <span className="text-sm font-medium">{m.label}</span>
                    {m.core && <Badge tone="info">core</Badge>}
                    <p className="text-xs text-slate-500">{m.description}</p>
                  </div>
                  <Switch
                    checked={m.core || selected.has(m.id)}
                    disabled={m.core}
                    onChange={(next) =>
                      setSelected((prev) => {
                        const s = new Set(prev);
                        next ? s.add(m.id) : s.delete(m.id);
                        return s;
                      })
                    }
                  />
                </div>
              ))}
            </div>
            {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
            <div className="mt-5 flex justify-between">
              <Button variant="ghost" onClick={() => setStep(1)}>Back</Button>
              <Button disabled={busy} onClick={finish}>{busy ? "Setting up…" : "Finish setup"}</Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
