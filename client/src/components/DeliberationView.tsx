import { useEffect, useState } from "react";
import { Arena, buildTimeline } from "@/components/Arena";
import type { TimelineItem } from "@/components/Arena";
import { CompareSidebar } from "@/components/CompareSidebar";
import { TranscriptDiff } from "@/components/TranscriptDiff";
import { fetchRun } from "@/lib/api";
import type { RunRecord } from "@/types";

interface DeliberationViewProps {
    runId: string;
    onOpenChat: (debateId: string, topic: string) => void;
}

function toItems(rec: RunRecord): TimelineItem[] {
    const items: TimelineItem[] = [];
    if (rec.routing?.enabled) items.push({ type: "routing", info: rec.routing });
    if (rec.insufficientAgents)
        items.push({ type: "insufficient_agents", info: rec.insufficientAgents });
    if (rec.result) items.push(...buildTimeline(rec.result));
    return items;
}

function btn(active = false) {
    return `text-[11px] font-semibold px-3 py-1.5 rounded-lg border transition-colors ${
        active
            ? "bg-indigo-600 text-white border-indigo-600"
            : "bg-white border-slate-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600"
    }`;
}

export function DeliberationView({ runId, onOpenChat }: DeliberationViewProps) {
    const [record, setRecord] = useState<RunRecord | null>(null);
    const [error, setError] = useState<string | null>(null);

    const [compareOpen, setCompareOpen] = useState(false);
    const [compareId, setCompareId] = useState<string | null>(null);
    const [compareRecord, setCompareRecord] = useState<RunRecord | null>(null);
    const [showDiff, setShowDiff] = useState(false);

    useEffect(() => {
        setRecord(null);
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
                    <p className="text-[11px] text-slate-400">
                        {record.teamName} ·{" "}
                        {record.result?.decided
                            ? `Decided · ${Math.round((record.result.decision?.confidence ?? 0) * 100)}% confidence`
                            : "No decision"}
                    </p>
                </div>

                <div className="ml-auto flex items-center gap-2">
                    {comparing && (
                        <button onClick={() => setShowDiff((d) => !d)} className={btn(showDiff)}>
                            {showDiff ? "Hide differences" : "Show differences"}
                        </button>
                    )}
                    {comparing && (
                        <button
                            onClick={() => {
                                setCompareId(null);
                                setShowDiff(false);
                            }}
                            className={btn()}
                        >
                            Exit compare
                        </button>
                    )}
                    <button onClick={() => setCompareOpen((o) => !o)} className={btn(compareOpen)}>
                        Compare
                    </button>
                    <button
                        onClick={() => onOpenChat(record.id, record.prompt)}
                        className="text-[11px] font-semibold px-3 py-1.5 rounded-lg bg-purple-600 text-white hover:bg-purple-700 transition-colors"
                    >
                        Follow-up
                    </button>
                </div>
            </div>

            <div className="flex-1 flex overflow-hidden gap-4">
                <div className="flex-1 overflow-hidden">
                    {comparing ? (
                        showDiff ? (
                            <TranscriptDiff left={record} right={compareRecord!} />
                        ) : (
                            <div className="h-full flex gap-4 overflow-hidden">
                                <div className="flex-1 overflow-hidden bg-gray-50 rounded-lg p-3 border border-slate-200">
                                    <Arena timeline={toItems(record)} label={`A · ${record.prompt}`} />
                                </div>
                                <div className="flex-1 overflow-hidden bg-amber-50/30 rounded-lg p-3 border border-amber-200">
                                    <Arena
                                        timeline={toItems(compareRecord!)}
                                        label={`B · ${compareRecord!.prompt}`}
                                    />
                                </div>
                            </div>
                        )
                    ) : (
                        <div className="h-full max-w-3xl mx-auto overflow-hidden">
                            <Arena timeline={toItems(record)} />
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
        </div>
    );
}
