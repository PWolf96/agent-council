import { useMemo, useEffect, useState } from "react";
import { createRun, fetchModels } from "@/lib/api";
import type { Team, RunConfigPayload } from "@/types";

interface NewRunModalProps {
    teams: Team[];
    onClose: () => void;
    onCreated: () => void;
}

const STEPS = ["Prompt", "Team", "Agents", "Settings"];

export function NewRunModal({ teams, onClose, onCreated }: NewRunModalProps) {
    const [step, setStep] = useState(0);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [prompt, setPrompt] = useState("");
    const [teamId, setTeamId] = useState<string>("");
    const [smartRouting, setSmartRouting] = useState(false);
    const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());

    // Deliberation bounds: hard cap on adversarial-review passes and the
    // per-pass challenge budget (keep worst-case cost predictable).
    const [maxPasses, setMaxPasses] = useState(3);
    const [perPassBudget, setPerPassBudget] = useState(4);

    // Model selection. Every agent inherits `defaultModel` unless per-agent
    // models are toggled on, in which case `agentModels[key]` takes over.
    const [models, setModels] = useState<string[]>([]);
    const [defaultModel, setDefaultModel] = useState("");
    const [perAgentModels, setPerAgentModels] = useState(false);
    const [agentModels, setAgentModels] = useState<Record<string, string>>({});

    useEffect(() => {
        fetchModels()
            .then((cat) => {
                setModels(cat.models);
                setDefaultModel((prev) => prev || cat.default);
            })
            .catch(() => {
                // Fall back to a static list so the wizard still works offline.
                setModels(["gpt-4o-mini", "o4-mini"]);
                setDefaultModel((prev) => prev || "gpt-4o-mini");
            });
    }, []);

    const team = useMemo(() => teams.find((t) => t.id === teamId) ?? null, [teams, teamId]);
    const modelForAgent = (key: string) => agentModels[key] ?? defaultModel;

    function chooseTeam(id: string) {
        setTeamId(id);
        const t = teams.find((x) => x.id === id);
        setSelectedKeys(new Set(t ? t.agents.map((a) => a.key) : []));
    }

    function toggleAgent(key: string) {
        setSelectedKeys((prev) => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    }

    const stepValid = (() => {
        switch (step) {
            case 0:
                return prompt.trim().length > 0;
            case 1:
                return teamId !== "";
            case 2:
                return smartRouting || selectedKeys.size >= 2;
            case 3:
                return maxPasses >= 1 && perPassBudget >= 1;
            default:
                return false;
        }
    })();

    async function submit() {
        if (!team) return;
        setSubmitting(true);
        setError(null);
        const agent_models: Record<string, string> = {};
        if (perAgentModels) {
            for (const a of team.agents) agent_models[a.key] = modelForAgent(a.key);
        }
        const payload: RunConfigPayload = {
            prompt: prompt.trim(),
            team_id: teamId,
            smart_routing: smartRouting,
            agent_keys: smartRouting ? null : Array.from(selectedKeys),
            default_model: defaultModel,
            agent_models,
            max_passes: maxPasses,
            per_pass_budget: perPassBudget,
        };
        try {
            await createRun(payload);
            onCreated();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to start run");
            setSubmitting(false);
        }
    }

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm p-4">
            <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[88vh] flex flex-col overflow-hidden">
                {/* header + stepper */}
                <div className="px-6 pt-5 pb-4 border-b border-slate-100">
                    <div className="flex items-center justify-between">
                        <h2 className="text-sm font-bold text-slate-800">New deliberation</h2>
                        <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-sm">
                            ✕
                        </button>
                    </div>
                    <div className="flex items-center gap-1.5 mt-3">
                        {STEPS.map((label, i) => (
                            <div key={label} className="flex items-center gap-1.5 flex-1">
                                <div
                                    className={`flex-1 h-1 rounded-full ${
                                        i <= step ? "bg-indigo-500" : "bg-slate-150 bg-slate-100"
                                    }`}
                                />
                            </div>
                        ))}
                    </div>
                    <div className="text-[11px] text-slate-400 mt-1.5">
                        Step {step + 1} of {STEPS.length} · {STEPS[step]}
                    </div>
                </div>

                {/* body */}
                <div className="px-6 py-5 overflow-y-auto flex-1">
                    {step === 0 && (
                        <div>
                            <label className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">
                                Prompt
                            </label>
                            <textarea
                                value={prompt}
                                onChange={(e) => setPrompt(e.target.value)}
                                rows={5}
                                autoFocus
                                placeholder="e.g. What is the outlook for global markets over the next 6 months?"
                                className="mt-1.5 w-full text-sm px-3 py-2 rounded-lg border border-slate-200 focus:outline-none focus:border-indigo-300 focus:ring-1 focus:ring-indigo-200 resize-y"
                            />
                        </div>
                    )}

                    {step === 1 && (
                        <div className="space-y-2">
                            {teams.map((t) => (
                                <button
                                    key={t.id}
                                    onClick={() => chooseTeam(t.id)}
                                    className={`w-full text-left px-3 py-2.5 rounded-lg border transition-colors ${
                                        teamId === t.id
                                            ? "bg-indigo-50 border-indigo-300"
                                            : "bg-white border-slate-200 hover:border-slate-300"
                                    }`}
                                >
                                    <div className="text-sm font-semibold text-slate-700">{t.name}</div>
                                    <div className="text-[11px] text-slate-400">{t.agents.length} agents</div>
                                </button>
                            ))}
                        </div>
                    )}

                    {step === 2 && team && (
                        <div className="space-y-3">
                            <label className="flex items-start gap-2 cursor-pointer select-none rounded-lg border border-slate-200 p-3">
                                <input
                                    type="checkbox"
                                    checked={smartRouting}
                                    onChange={(e) => setSmartRouting(e.target.checked)}
                                    className="mt-0.5 h-4 w-4 rounded border-slate-300 text-indigo-600"
                                />
                                <span>
                                    <span className="block text-xs font-semibold text-slate-700">
                                        Smart agent routing
                                    </span>
                                    <span className="block text-[11px] text-slate-400 leading-snug">
                                        Let the router pick the relevant agents for this prompt.
                                    </span>
                                </span>
                            </label>

                            {!smartRouting && (
                                <div className="space-y-1.5">
                                    {team.agents.map((a) => (
                                        <label
                                            key={a.key}
                                            className="flex items-start gap-2 cursor-pointer rounded-lg border border-slate-200 p-2.5 hover:border-slate-300"
                                        >
                                            <input
                                                type="checkbox"
                                                checked={selectedKeys.has(a.key)}
                                                onChange={() => toggleAgent(a.key)}
                                                className="mt-0.5 h-4 w-4 rounded border-slate-300 text-indigo-600"
                                            />
                                            <span className="min-w-0">
                                                <span className="block text-xs font-medium text-slate-700">
                                                    {a.label}
                                                </span>
                                                {a.description && (
                                                    <span className="block text-[10px] text-slate-400 line-clamp-2">
                                                        {a.description}
                                                    </span>
                                                )}
                                            </span>
                                        </label>
                                    ))}
                                    {selectedKeys.size < 2 && (
                                        <p className="text-[11px] text-amber-600">
                                            Select at least 2 agents to debate.
                                        </p>
                                    )}
                                </div>
                            )}

                            <div className="pt-1 border-t border-slate-100 space-y-3">
                                <div>
                                    <label className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">
                                        Model {perAgentModels ? "(default)" : "(all agents)"}
                                    </label>
                                    <ModelSelect
                                        value={defaultModel}
                                        models={models}
                                        onChange={setDefaultModel}
                                    />
                                </div>

                                <label className="flex items-start gap-2 cursor-pointer select-none">
                                    <input
                                        type="checkbox"
                                        checked={perAgentModels}
                                        onChange={(e) => setPerAgentModels(e.target.checked)}
                                        className="mt-0.5 h-4 w-4 rounded border-slate-300 text-indigo-600"
                                    />
                                    <span>
                                        <span className="block text-xs font-semibold text-slate-700">
                                            Choose a model per agent
                                        </span>
                                        <span className="block text-[11px] text-slate-400 leading-snug">
                                            Otherwise every agent inherits the model above.
                                        </span>
                                    </span>
                                </label>

                                {perAgentModels && (
                                    <div className="space-y-2">
                                        {team.agents.map((a) => (
                                            <div
                                                key={a.key}
                                                className="flex items-center justify-between gap-3"
                                            >
                                                <span className="text-[11px] text-slate-600 font-medium min-w-0 truncate">
                                                    {a.label}
                                                </span>
                                                <div className="w-40 shrink-0">
                                                    <ModelSelect
                                                        value={modelForAgent(a.key)}
                                                        models={models}
                                                        onChange={(m) =>
                                                            setAgentModels((prev) => ({
                                                                ...prev,
                                                                [a.key]: m,
                                                            }))
                                                        }
                                                    />
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {step === 3 && (
                        <div className="space-y-5">
                            <p className="text-[11px] text-slate-400 leading-snug">
                                Deliberation runs as adversarial-review passes over the
                                evidence. These bounds keep worst-case cost predictable.
                            </p>
                            <div className="grid grid-cols-2 gap-4">
                                <NumberField
                                    label="Max passes"
                                    value={maxPasses}
                                    min={1}
                                    max={8}
                                    onChange={setMaxPasses}
                                />
                                <NumberField
                                    label="Per-pass budget"
                                    value={perPassBudget}
                                    min={1}
                                    max={12}
                                    onChange={setPerPassBudget}
                                />
                            </div>
                            <p className="text-[11px] text-slate-400 leading-snug">
                                <span className="font-medium text-slate-500">Max passes</span> caps
                                how many challenge sweeps run;{" "}
                                <span className="font-medium text-slate-500">per-pass budget</span>{" "}
                                caps the challenges allowed within each sweep.
                            </p>
                        </div>
                    )}

                    {error && <p className="text-[11px] text-red-500 mt-3">{error}</p>}
                </div>

                {/* footer */}
                <div className="px-6 py-4 border-t border-slate-100 flex items-center justify-between">
                    <button
                        onClick={() => (step === 0 ? onClose() : setStep((s) => s - 1))}
                        className="text-xs font-medium text-slate-500 hover:text-slate-700 px-3 py-2"
                    >
                        {step === 0 ? "Cancel" : "Back"}
                    </button>
                    {step < STEPS.length - 1 ? (
                        <button
                            disabled={!stepValid}
                            onClick={() => setStep((s) => s + 1)}
                            className="text-xs font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed transition-colors"
                        >
                            Next
                        </button>
                    ) : (
                        <button
                            disabled={!stepValid || submitting}
                            onClick={submit}
                            className="text-xs font-semibold px-4 py-2 rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed transition-colors"
                        >
                            {submitting ? "Starting…" : "Submit"}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}

function ModelSelect({
    value,
    models,
    onChange,
}: {
    value: string;
    models: string[];
    onChange: (v: string) => void;
}) {
    // Include the current value even if it's not in the catalog, so a stored
    // selection never silently disappears.
    const options = models.includes(value) || !value ? models : [value, ...models];
    return (
        <select
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="mt-1.5 w-full text-sm px-3 py-2 rounded-lg border border-slate-200 bg-white focus:outline-none focus:border-indigo-300 focus:ring-1 focus:ring-indigo-200"
        >
            {options.map((m) => (
                <option key={m} value={m}>
                    {m}
                </option>
            ))}
        </select>
    );
}

function NumberField({
    label,
    value,
    min,
    max,
    onChange,
}: {
    label: string;
    value: number;
    min: number;
    max: number;
    onChange: (v: number) => void;
}) {
    return (
        <div>
            <label className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold">
                {label}
            </label>
            <input
                type="number"
                value={value}
                min={min}
                max={max}
                onChange={(e) => onChange(Number(e.target.value))}
                className="mt-1.5 w-full text-sm px-3 py-2 rounded-lg border border-slate-200 focus:outline-none focus:border-indigo-300 focus:ring-1 focus:ring-indigo-200"
            />
        </div>
    );
}
