import type { RunSummary } from "@/types";

const STATUS_STYLES: Record<string, string> = {
    queued: "bg-slate-100 text-slate-500 border-slate-200",
    running: "bg-indigo-50 text-indigo-600 border-indigo-200",
    completed: "bg-emerald-50 text-emerald-700 border-emerald-200",
    failed: "bg-red-50 text-red-600 border-red-200",
};

function pct(value: number | null | undefined): string {
    return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function StatusBadge({ status }: { status: RunSummary["status"] }) {
    const style = STATUS_STYLES[status] ?? STATUS_STYLES.queued;
    return (
        <span
            className={`inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full border ${style}`}
        >
            {status === "running" && (
                <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-pulse" />
            )}
            {status}
        </span>
    );
}

interface RunCardProps {
    run: RunSummary;
    onOpenDeliberation: (id: string) => void;
    onOpenStats: (id: string) => void;
    onDelete: (id: string) => void;
}

export function RunCard({ run, onOpenDeliberation, onOpenStats, onDelete }: RunCardProps) {
    const done = run.status === "completed";
    const when = run.timestamp ? new Date(run.timestamp * 1000).toLocaleString() : "";

    return (
        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-5 flex flex-col gap-4">
            <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                    <p className="text-sm font-semibold text-slate-800 leading-snug line-clamp-2">
                        {run.prompt || "(no prompt)"}
                    </p>
                    <p className="text-[11px] text-slate-400 mt-1">{when}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                    <StatusBadge status={run.status} />
                    <button
                        onClick={() => onDelete(run.id)}
                        title="Delete run"
                        className="text-slate-300 hover:text-red-500 text-xs transition-colors"
                    >
                        ✕
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-[11px]">
                <div>
                    <div className="text-slate-400 uppercase tracking-wide font-semibold mb-1">Team</div>
                    <div className="text-slate-700 font-medium">{run.teamName || run.teamId}</div>
                </div>
                <div>
                    <div className="text-slate-400 uppercase tracking-wide font-semibold mb-1">
                        Confidence
                    </div>
                    <div className="text-slate-700 font-mono">
                        {done ? pct(run.confidence) : "—"}
                    </div>
                </div>

                <div className="col-span-2">
                    <div className="text-slate-400 uppercase tracking-wide font-semibold mb-1">Agents</div>
                    {run.smartRouting ? (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-sky-50 border border-sky-200 text-sky-600 font-medium">
                            Smart routing
                        </span>
                    ) : (
                        <div className="text-slate-600">
                            {run.agents.map((a) => a.label).join(", ") || "—"}
                        </div>
                    )}
                </div>
            </div>

            <div className="flex items-center justify-between border-t border-slate-100 pt-3">
                <div>
                    {run.status === "failed" ? (
                        <span className="text-[11px] text-red-500" title={run.error ?? ""}>
                            Failed{run.error ? `: ${run.error.slice(0, 60)}` : ""}
                        </span>
                    ) : done ? (
                        <span
                            className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${
                                run.decided
                                    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                                    : "bg-amber-50 text-amber-700 border-amber-200"
                            }`}
                        >
                            {run.decided ? `Decided · ${pct(run.confidence)}` : "No decision"}
                        </span>
                    ) : (
                        <span className="text-[11px] text-slate-400">In progress…</span>
                    )}
                </div>

                <div className="flex items-center gap-2">
                    <button
                        disabled={!done}
                        onClick={() => onOpenDeliberation(run.id)}
                        className="text-[11px] font-semibold px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed transition-colors"
                    >
                        Deliberation
                    </button>
                    <button
                        disabled={!done}
                        onClick={() => onOpenStats(run.id)}
                        className="text-[11px] font-semibold px-3 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                        Stats
                    </button>
                </div>
            </div>
        </div>
    );
}
