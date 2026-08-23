import { useEffect, useState } from "react";
import { fetchRuns } from "@/lib/api";
import type { RunSummary } from "@/types";

interface CompareSidebarProps {
    currentId: string;
    selectedId: string | null;
    onSelect: (id: string) => void;
    onClose: () => void;
}

// Right-hand drawer for picking another completed run to compare against.
export function CompareSidebar({ currentId, selectedId, onSelect, onClose }: CompareSidebarProps) {
    const [runs, setRuns] = useState<RunSummary[]>([]);

    useEffect(() => {
        fetchRuns().then(setRuns).catch(() => setRuns([]));
    }, []);

    const options = runs.filter((r) => r.id !== currentId && r.status === "completed");

    return (
        <aside className="w-72 shrink-0 border-l border-slate-200 bg-white/70 p-4 overflow-y-auto">
            <div className="flex items-center justify-between mb-3">
                <span className="text-[11px] uppercase tracking-wide font-semibold text-slate-500">
                    Compare with
                </span>
                <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xs">
                    ✕
                </button>
            </div>

            {options.length === 0 && (
                <p className="text-[11px] text-slate-400">No other completed runs to compare.</p>
            )}

            <div className="space-y-1.5">
                {options.map((r) => (
                    <button
                        key={r.id}
                        onClick={() => onSelect(r.id)}
                        className={`w-full text-left px-2.5 py-2 rounded-lg border transition-colors ${
                            selectedId === r.id
                                ? "bg-amber-50 border-amber-300"
                                : "bg-white border-slate-200 hover:border-slate-300"
                        }`}
                    >
                        <div className="text-[11px] font-medium text-slate-700 line-clamp-2">
                            {r.prompt}
                        </div>
                        <div className="text-[9px] text-slate-400 mt-0.5">
                            {r.teamName} ·{" "}
                            {r.decided ? `decided ${Math.round((r.confidence ?? 0) * 100)}%` : "no decision"} ·{" "}
                            {r.timestamp ? new Date(r.timestamp * 1000).toLocaleDateString() : ""}
                        </div>
                    </button>
                ))}
            </div>
        </aside>
    );
}
