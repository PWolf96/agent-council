import { useEffect, useState } from "react";
import { StatsDashboard } from "@/components/StatsDashboard";
import { CompareSidebar } from "@/components/CompareSidebar";
import { StatsDrilldown, type DrilldownKind } from "@/components/StatsDrilldown";
import { fetchRun } from "@/lib/api";
import type { DeliberationResult, RunRecord } from "@/types";

// What the open drilldown slide-over needs: which detail view, the run's result
// to read from, and a label so it's clear which side (in compare mode) it's for.
interface DrilldownState {
    kind: DrilldownKind;
    result: DeliberationResult;
    label?: string;
    focusClaimId?: string;
}

function StatsPanel({
    rec,
    label,
    tint,
    onOpenDrilldown,
}: {
    rec: RunRecord;
    label?: string;
    tint?: string;
    onOpenDrilldown?: (kind: DrilldownKind, focusClaimId?: string) => void;
}) {
    return (
        <div className={`flex-1 flex flex-col overflow-hidden rounded-lg ${tint ?? ""}`}>
            {label && (
                <div className="text-[11px] font-semibold text-slate-500 mb-2 line-clamp-1 px-1">
                    {label}
                </div>
            )}
            <div className="flex-1 overflow-hidden">
                {rec.result ? (
                    <StatsDashboard
                        result={rec.result}
                        config={rec.config}
                        onOpenDrilldown={onOpenDrilldown}
                    />
                ) : (
                    <p className="text-sm text-slate-400">No results.</p>
                )}
            </div>
        </div>
    );
}

export function StatsView({ runId }: { runId: string }) {
    const [record, setRecord] = useState<RunRecord | null>(null);
    const [error, setError] = useState<string | null>(null);

    const [compareOpen, setCompareOpen] = useState(false);
    const [compareId, setCompareId] = useState<string | null>(null);
    const [compareRecord, setCompareRecord] = useState<RunRecord | null>(null);

    const [drilldown, setDrilldown] = useState<DrilldownState | null>(null);

    useEffect(() => {
        setRecord(null);
        setDrilldown(null);
        fetchRun(runId)
            .then(setRecord)
            .catch((e) => setError(e instanceof Error ? e.message : "Failed to load run"));
    }, [runId]);

    useEffect(() => {
        if (!compareId) {
            setCompareRecord(null);
            return;
        }
        setCompareRecord(null);
        fetchRun(compareId).then(setCompareRecord).catch(() => setCompareRecord(null));
    }, [compareId]);

    if (error) return <div className="p-6 text-sm text-red-500">{error}</div>;
    if (!record) return <div className="p-6 text-sm text-slate-400">Loading…</div>;

    const comparing = compareRecord !== null;

    return (
        <div className="h-full flex flex-col w-full px-6 py-4">
            <div className="mb-3 flex items-center gap-3">
                <div className="min-w-0">
                    <h2 className="text-sm font-bold text-slate-800 line-clamp-1">{record.prompt}</h2>
                    <p className="text-[11px] text-slate-400">{record.teamName} · Stats</p>
                </div>
                <div className="ml-auto flex items-center gap-2">
                    {comparing && (
                        <button
                            onClick={() => setCompareId(null)}
                            className="text-[11px] font-semibold px-3 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600 transition-colors"
                        >
                            Exit compare
                        </button>
                    )}
                    <button
                        onClick={() => setCompareOpen((o) => !o)}
                        className={`text-[11px] font-semibold px-3 py-1.5 rounded-lg border transition-colors ${
                            compareOpen
                                ? "bg-indigo-600 text-white border-indigo-600"
                                : "bg-white border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
                        }`}
                    >
                        Compare
                    </button>
                </div>
            </div>

            <div className="flex-1 flex overflow-hidden gap-4">
                <div className="flex-1 flex gap-4 overflow-hidden">
                    {comparing ? (
                        <>
                            <StatsPanel
                                rec={record}
                                label={`A · ${record.prompt}`}
                                onOpenDrilldown={(kind, focusClaimId) =>
                                    setDrilldown({
                                        kind,
                                        result: record.result!,
                                        label: `A · ${record.prompt}`,
                                        focusClaimId,
                                    })
                                }
                            />
                            <StatsPanel
                                rec={compareRecord!}
                                label={`B · ${compareRecord!.prompt}`}
                                tint="bg-amber-50/30"
                                onOpenDrilldown={(kind, focusClaimId) =>
                                    setDrilldown({
                                        kind,
                                        result: compareRecord!.result!,
                                        label: `B · ${compareRecord!.prompt}`,
                                        focusClaimId,
                                    })
                                }
                            />
                        </>
                    ) : (
                        <div className="max-w-2xl mx-auto w-full overflow-hidden">
                            <StatsPanel
                                rec={record}
                                onOpenDrilldown={(kind, focusClaimId) =>
                                    record.result &&
                                    setDrilldown({ kind, result: record.result, focusClaimId })
                                }
                            />
                        </div>
                    )}
                </div>

                {compareOpen && (
                    <CompareSidebar
                        currentId={record.id}
                        selectedId={compareId}
                        onSelect={(id) => {
                            setCompareId(id);
                            setCompareOpen(false);
                        }}
                        onClose={() => setCompareOpen(false)}
                    />
                )}
            </div>

            {drilldown && (
                <StatsDrilldown
                    kind={drilldown.kind}
                    result={drilldown.result}
                    label={drilldown.label}
                    focusClaimId={drilldown.focusClaimId}
                    onClose={() => setDrilldown(null)}
                />
            )}
        </div>
    );
}
