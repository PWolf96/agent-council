import { useMemo } from "react";
import { OneSidedDiff } from "@/components/TextDiff";
import type { RunRecord } from "@/types";

function collectArguments(record: RunRecord): Map<string, string> {
    const map = new Map<string, string>();
    for (const entry of record.result?.debateHistory ?? []) {
        map.set(`R${entry.round} · ${entry.agent}`, entry.argument);
    }
    return map;
}

function DiffRow({ title, a, b }: { title: string; a: string; b: string }) {
    return (
        <div className="grid grid-cols-2 gap-4">
            <div className="bg-white rounded-xl border border-emerald-200 p-4 shadow-sm">
                <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-2">
                    {title}
                </div>
                <OneSidedDiff self={a} other={b} tone="current" />
            </div>
            <div className="bg-white rounded-xl border border-red-200 p-4 shadow-sm">
                <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-2">
                    {title}
                </div>
                <OneSidedDiff self={b} other={a} tone="compared" />
            </div>
        </div>
    );
}

// Side-by-side comparison with per-side highlighting: left = current run (its
// unique text in green), right = compared run (its unique text in red). Rows are
// aligned by round+agent, with the final verdict on top.
export function TranscriptDiff({ left, right }: { left: RunRecord; right: RunRecord }) {
    const leftArgs = useMemo(() => collectArguments(left), [left]);
    const rightArgs = useMemo(() => collectArguments(right), [right]);

    const keys = useMemo(
        () => Array.from(new Set([...leftArgs.keys(), ...rightArgs.keys()])).sort(),
        [leftArgs, rightArgs],
    );

    return (
        <div className="h-full overflow-y-auto pr-2 space-y-4">
            <div className="grid grid-cols-2 gap-4 sticky top-0 bg-gray-50 py-1 z-10 text-[11px] font-semibold">
                <span className="text-emerald-700">A · current — differences in green</span>
                <span className="text-red-700">B · compared — differences in red</span>
            </div>

            <DiffRow
                title="Final verdict"
                a={left.result?.finalVerdict ?? ""}
                b={right.result?.finalVerdict ?? ""}
            />

            {keys.map((key) => (
                <DiffRow key={key} title={key} a={leftArgs.get(key) ?? ""} b={rightArgs.get(key) ?? ""} />
            ))}
        </div>
    );
}
