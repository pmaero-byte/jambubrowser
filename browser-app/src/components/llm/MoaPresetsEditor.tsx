import { useEffect, useState } from "react";
import { Loader2, AlertCircle, Save, RotateCcw, CheckCircle2 } from "lucide-react";
import {
  fetchMoaPresets,
  saveMoaPresets,
  clearMoaPresets,
  type MoaPreset,
  type MoaPresetsResponse,
} from "../../utils/api";

interface MoaPresetsEditorProps {
  availableProviders: string[];
  onSaved?: () => void;
}

function isPreset(value: unknown): value is MoaPreset {
  return !!value && typeof value === "object" && "aggregator" in (value as object);
}

export function MoaPresetsEditor({ availableProviders, onSaved }: MoaPresetsEditorProps) {
  const [data, setData] = useState<MoaPresetsResponse | null>(null);
  const [draft, setDraft] = useState<Record<string, MoaPreset>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchMoaPresets();
      setData(resp);
      setDraft(resp.presets);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const updatePreset = (name: string, patch: Partial<MoaPreset>) => {
    setDraft((d) => ({
      ...d,
      [name]: { ...(d[name] || { aggregator: { provider: "mock" } }), ...patch },
    }));
  };

  const updateAggregator = (name: string, provider: string) => {
    setDraft((d) => ({
      ...d,
      [name]: { ...(d[name] || { aggregator: { provider } }), aggregator: { provider } },
    }));
  };

  const updateReferenceProvider = (presetName: string, index: number, provider: string) => {
    setDraft((d) => {
      const p = d[presetName];
      if (!p) return d;
      const refs = [...(p.reference_models || [])];
      refs[index] = { ...(refs[index] || { provider }), provider };
      return { ...d, [presetName]: { ...p, reference_models: refs } };
    });
  };

  const addReference = (presetName: string) => {
    setDraft((d) => {
      const p = d[presetName];
      if (!p) return d;
      return {
        ...d,
        [presetName]: { ...p, reference_models: [...(p.reference_models || []), { provider: "mock" }] },
      };
    });
  };

  const removeReference = (presetName: string, index: number) => {
    setDraft((d) => {
      const p = d[presetName];
      if (!p) return d;
      return {
        ...d,
        [presetName]: {
          ...p,
          reference_models: (p.reference_models || []).filter((_, i) => i !== index),
        },
      };
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const valid: Record<string, MoaPreset> = {};
      for (const [name, body] of Object.entries(draft)) {
        if (isPreset(body)) valid[name] = body;
      }
      const resp = await saveMoaPresets(valid);
      setSavedAt(Date.now());
      onSaved?.();
      void resp;
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    setSaving(true);
    setError(null);
    try {
      await clearMoaPresets();
      await load();
      setSavedAt(Date.now());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading MoA presets…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 p-3 text-sm text-rose-400">
        <AlertCircle className="h-4 w-4 shrink-0" />
        <span className="truncate" title={error}>{error}</span>
      </div>
    );
  }

  const presetNames = Object.keys(draft);

  return (
    <div className="flex h-full flex-col gap-3 overflow-hidden p-3" aria-label="MoA preset editor">
      <header className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-foreground">MoA Presets</h3>
          <p className="text-xs text-muted-foreground">
            {data?.has_override
              ? "Runtime override active — changes here override env + defaults."
              : "Using env or built-in defaults — save to install a runtime override."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {savedAt && (
            <span className="flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-3 w-3" /> saved
            </span>
          )}
          <button
            type="button"
            onClick={handleReset}
            disabled={saving || !data?.has_override}
            className="flex items-center gap-1 rounded border border-border bg-card px-2 py-1 text-xs text-foreground transition hover:bg-muted disabled:opacity-50"
          >
            <RotateCcw className="h-3 w-3" /> Reset
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1 rounded bg-primary px-2 py-1 text-xs font-medium text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
          >
            <Save className="h-3 w-3" /> {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto">
        {presetNames.length === 0 && (
          <p className="text-sm text-muted-foreground">No presets available.</p>
        )}
        {presetNames.map((name) => {
          const p = draft[name];
          if (!p) return null;
          return (
            <section
              key={name}
              className="rounded-lg border border-border bg-card/40 p-3"
              aria-label={`Preset ${name}`}
            >
              <div className="mb-2 flex items-center justify-between">
                <h4 className="text-sm font-semibold text-foreground">{name}</h4>
                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={p.enabled !== false}
                    onChange={(e) => updatePreset(name, { enabled: e.target.checked })}
                  />
                  enabled
                </label>
              </div>

              <div className="mb-2 space-y-1">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Reference models ({p.reference_models?.length ?? 0})
                </p>
                {(p.reference_models || []).map((r, i) => (
                  <div key={i} className="flex items-center gap-1">
                    <select
                      value={r.provider}
                      onChange={(e) => updateReferenceProvider(name, i, e.target.value)}
                      className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs text-foreground"
                    >
                      {availableProviders.map((prov) => (
                        <option key={prov} value={prov}>
                          {prov}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => removeReference(name, i)}
                      className="rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-rose-400"
                      aria-label={`Remove reference ${i + 1}`}
                    >
                      ×
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => addReference(name)}
                  className="rounded border border-dashed border-border px-2 py-0.5 text-[11px] text-muted-foreground transition hover:border-foreground hover:text-foreground"
                >
                  + add reference
                </button>
              </div>

              <div className="mb-2">
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Aggregator
                </p>
                <select
                  value={p.aggregator.provider}
                  onChange={(e) => updateAggregator(name, e.target.value)}
                  className="w-full rounded border border-border bg-background px-2 py-1 text-xs text-foreground"
                >
                  {availableProviders.map((prov) => (
                    <option key={prov} value={prov}>
                      {prov}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-3 gap-2 text-xs">
                <label className="flex flex-col gap-0.5">
                  <span className="text-muted-foreground">ref temp</span>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="2"
                    value={p.reference_temperature ?? 0.6}
                    onChange={(e) => updatePreset(name, { reference_temperature: Number(e.target.value) })}
                    className="rounded border border-border bg-background px-2 py-1 text-foreground"
                  />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-muted-foreground">agg temp</span>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="2"
                    value={p.aggregator_temperature ?? 0.4}
                    onChange={(e) => updatePreset(name, { aggregator_temperature: Number(e.target.value) })}
                    className="rounded border border-border bg-background px-2 py-1 text-foreground"
                  />
                </label>
                <label className="flex flex-col gap-0.5">
                  <span className="text-muted-foreground">max tokens</span>
                  <input
                    type="number"
                    step="256"
                    min="64"
                    value={p.max_tokens ?? 4096}
                    onChange={(e) => updatePreset(name, { max_tokens: Number(e.target.value) })}
                    className="rounded border border-border bg-background px-2 py-1 text-foreground"
                  />
                </label>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
