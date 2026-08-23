import { useMemo } from "react";
import type {
    ClaimStatus,
    DeliberationResult,
    RunConfigInfo,
} from "@/types";
import { categoryLabel } from "@/types";
import type { DrilldownKind } from "@/components/StatsDrilldown";

// v3 evidence dashboard: a read-only render of the decision, the claim ledger,
// the adversarial-review sweeps, open cruxes, and the evidence pool. Replaces
// the v2 weighted-score radar/trend charts (which had no v3 data source).
//
// The four tally tiles stay a fixed high-level overview; each is a button that
// opens a detail drilldown (handled by the host via ``onOpenDrilldown``).

interface StatsDashboardProps {
    result: DeliberationResult;
    config: RunConfigInfo;
    // ``focusClaimId`` lets a claim row deep-link into its own conversation thread.
    onOpenDrilldown?: (kind: DrilldownKind, focusClaimId?: string) => void;
}

const STATUS_STYLE: Record<ClaimStatus, string> = {
    asserted: "bg-slate-100 text-slate-500 border-slate-200",
    challenged: "bg-amber-50 text-amber-700 border-amber-200",
    revised: "bg-sky-50 text-sky-700 border-sky-200",
    corroborated: "bg-emerald-50 text-emerald-700 border-emerald-200",
    refuted: "bg-red-50 text-red-600 border-red-200",
    resolved: "bg-indigo-50 text-indigo-700 border-indigo-200",
};

function pct(value: number | null | undefined): string {
    return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function StatTile({
    label,
    value,
    hint,
    onClick,
}: {
    label: string;
    value: string;
    hint?: string;
    onClick?: () => void;
}) {
    const body = (
        <>
            <div className="flex items-center justify-between">
                <div className="text-[9px] uppercase tracking-wider font-semibold text-slate-400">
                    {label}
                </div>
                {onClick && <span className="text-[10px] text-slate-300 group-hover:text-indigo-400">›</span>}
            </div>
            <div className="text-sm font-semibold text-slate-700 mt-0.5">{value}</div>
            {hint && <div className="text-[10px] text-slate-400">{hint}</div>}
        </>
    );
    if (!onClick) {
        return <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">{body}</div>;
    }
    return (
        <button
            type="button"
            onClick={onClick}
            className="group text-left rounded-xl border border-slate-200 bg-white px-3 py-2.5 transition-colors hover:border-indigo-300 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-200"
            title={`View ${label.toLowerCase()} details`}
        >
            {body}
        </button>
    );
}

function CitationChips({ ids }: { ids: string[] }) {
    if (!ids || ids.length === 0) return <span className="text-slate-300">—</span>;
    return (
        <span className="inline-flex flex-wrap gap-1">
            {ids.map((id) => (
                <span
                    key={id}
                    className="text-[9px] font-mono px-1 py-0.5 rounded bg-slate-50 border border-slate-200 text-slate-500"
                >
                    {id}
                </span>
            ))}
        </span>
    );
}

export function StatsDashboard({ result, config, onOpenDrilldown }: StatsDashboardProps) {
    const { decision } = result;
    const supportingIds = useMemo(
        () => new Set(decision?.supporting_claims ?? []),
        [decision],
    );

    const dissent = decision?.unresolved_dissent ?? [];
    const cruxes = (result.cruxes ?? []).filter((c) => c.unresolved);

    // Sweep tallies across every adversarial-review pass.
    const sweepTotals = useMemo(() => {
        const acc = { filed: 0, admitted: 0, dropped: 0, revisions: 0 };
        for (const s of result.sweeps ?? []) {
            acc.filed += s.filed;
            acc.admitted += s.admitted;
            acc.dropped += s.dropped;
            acc.revisions += s.revisions;
        }
        return acc;
    }, [result.sweeps]);

    // Evidence-pool breakdown: total records, the productive (non-empty) ones,
    // and the quant forecasts.
    const evidenceStats = useMemo(() => {
        const records = result.evidence ?? [];
        const productive = records.filter((e) => !e.is_empty);
        const quant = records.filter((e) => e.provenance.startsWith("quant"));
        return { total: records.length, productive: productive.length, quant: quant.length };
    }, [result.evidence]);

    return (
        <div className="h-full overflow-y-auto pr-2 space-y-5 pb-6">
            {/* Decision */}
            <section className="rounded-2xl border border-slate-200 bg-white p-5">
                <div className="flex items-start justify-between gap-4">
                    <div className="text-[10px] uppercase tracking-wider font-semibold text-slate-400">
                        Decision
                    </div>
                    <span
                        className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                            decision?.confidence_kind === "calibratable"
                                ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                                : "bg-slate-100 text-slate-500 border-slate-200"
                        }`}
                        title={
                            decision?.confidence_kind === "calibratable"
                                ? "A forecast that resolves and can be scored"
                                : "A defensibility score, not P(true)"
                        }
                    >
                        {decision?.confidence_kind ?? "judgmental"}
                    </span>
                </div>
                <div className="flex items-baseline gap-2 mt-1">
                    <span className="text-3xl font-bold text-slate-800 font-mono">
                        {pct(decision?.confidence)}
                    </span>
                    <span className="text-[11px] text-slate-400">confidence</span>
                </div>
                {decision?.answer && (
                    <div className="mt-3 max-h-48 overflow-y-auto pr-1">
                        <p className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
                            {decision.answer}
                        </p>
                    </div>
                )}
            </section>

            {/* Run meta + tallies */}
            <section className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <StatTile
                    label="Passes"
                    value={`${result.sweeps?.length ?? 0}`}
                    hint={`cap ${config.maxPasses} · budget ${config.perPassBudget}`}
                    onClick={onOpenDrilldown && (() => onOpenDrilldown("conversation"))}
                />
                <StatTile
                    label="Challenges"
                    value={`${sweepTotals.admitted}/${sweepTotals.filed}`}
                    hint={`admitted · ${sweepTotals.dropped} dropped`}
                    onClick={onOpenDrilldown && (() => onOpenDrilldown("conversation"))}
                />
                <StatTile
                    label="Revisions"
                    value={`${sweepTotals.revisions}`}
                    hint="claims rewritten"
                    onClick={onOpenDrilldown && (() => onOpenDrilldown("conversation"))}
                />
                <StatTile
                    label="Evidence"
                    value={`${evidenceStats.productive}/${evidenceStats.total}`}
                    hint={`useful · ${evidenceStats.quant} quant`}
                    onClick={onOpenDrilldown && (() => onOpenDrilldown("evidence"))}
                />
            </section>

            {/* Surviving dissent */}
            <Section title={`Surviving dissent (${dissent.length})`}>
                {dissent.length === 0 ? (
                    <Empty>No surviving dissent.</Empty>
                ) : (
                    <ul className="space-y-2">
                        {dissent.map((d, i) => (
                            <li
                                key={`${d.claim_id}-${i}`}
                                className="rounded-lg border-l-2 border-amber-300 bg-amber-50/40 px-3 py-2"
                            >
                                <div className="text-[11px] font-semibold text-amber-700">
                                    {d.owner}
                                    {d.claim_id && d.claim_id !== "-" && (
                                        <span className="ml-1.5 font-mono text-[10px] text-amber-500">
                                            {d.claim_id}
                                        </span>
                                    )}
                                </div>
                                <p className="text-[12px] text-slate-700 leading-snug mt-0.5">
                                    {d.summary}
                                </p>
                            </li>
                        ))}
                    </ul>
                )}
            </Section>

            {/* Open cruxes */}
            {cruxes.length > 0 && (
                <Section title={`Open cruxes (${cruxes.length})`}>
                    <ul className="space-y-2">
                        {cruxes.map((c, i) => (
                            <li
                                key={c.crux_id || i}
                                className="rounded-lg border border-violet-200 bg-violet-50/50 px-3 py-2"
                            >
                                <p className="text-[12px] text-slate-700 leading-snug">
                                    {c.description || "(pivotal claim left unresolved)"}
                                </p>
                                {c.pivotal_claims.length > 0 && (
                                    <div className="mt-1.5">
                                        <CitationChips ids={c.pivotal_claims} />
                                    </div>
                                )}
                            </li>
                        ))}
                    </ul>
                </Section>
            )}

            {/* Claims — the unified ledger (supporting claims are starred). Each row
                opens that claim's challenge → response thread in the conversation. */}
            <Section title={`Claims (${result.claims?.length ?? 0})`}>
                {(result.claims?.length ?? 0) === 0 ? (
                    <Empty>No claims were authored.</Empty>
                ) : (
                    <>
                        {onOpenDrilldown && (
                            <p className="text-[10px] text-slate-400 mb-1.5">
                                ★ backs the decision · click a claim to follow its full thread
                            </p>
                        )}
                        <div className="overflow-x-auto rounded-lg border border-slate-200">
                            <table className="w-full text-[11px]">
                                <thead className="bg-slate-50 text-slate-400 uppercase tracking-wider">
                                    <tr>
                                        <th className="text-left font-semibold px-2.5 py-1.5">Claim</th>
                                        <th className="text-left font-semibold px-2.5 py-1.5">Owner</th>
                                        <th className="text-left font-semibold px-2.5 py-1.5">Status</th>
                                        <th className="text-right font-semibold px-2.5 py-1.5">Conf.</th>
                                        <th className="text-left font-semibold px-2.5 py-1.5">Cites</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {result.claims.map((c) => {
                                        const open = onOpenDrilldown
                                            ? () => onOpenDrilldown("conversation", c.claim_id)
                                            : undefined;
                                        return (
                                            <tr
                                                key={c.claim_id}
                                                onClick={open}
                                                onKeyDown={
                                                    open
                                                        ? (e) => {
                                                              if (e.key === "Enter" || e.key === " ") {
                                                                  e.preventDefault();
                                                                  open();
                                                              }
                                                          }
                                                        : undefined
                                                }
                                                tabIndex={open ? 0 : undefined}
                                                role={open ? "button" : undefined}
                                                title={open ? "Follow this claim's thread" : undefined}
                                                className={`border-t border-slate-100 align-top ${
                                                    open
                                                        ? "cursor-pointer hover:bg-indigo-50/50 focus:outline-none focus:bg-indigo-50"
                                                        : ""
                                                }`}
                                            >
                                                <td className="px-2.5 py-1.5">
                                                    <span className="font-mono text-slate-500">
                                                        {supportingIds.has(c.claim_id) && (
                                                            <span className="text-amber-500 mr-1">★</span>
                                                        )}
                                                        {c.claim_id}
                                                    </span>
                                                    {c.dimension && (
                                                        <div className="text-[10px] text-slate-400">
                                                            {categoryLabel(c.dimension)}
                                                        </div>
                                                    )}
                                                </td>
                                                <td className="px-2.5 py-1.5 text-slate-600">{c.owner}</td>
                                                <td className="px-2.5 py-1.5">
                                                    <span
                                                        className={`text-[9px] font-semibold px-1.5 py-0.5 rounded-full border ${
                                                            STATUS_STYLE[c.status] ?? STATUS_STYLE.asserted
                                                        }`}
                                                    >
                                                        {c.status}
                                                    </span>
                                                </td>
                                                <td className="px-2.5 py-1.5 text-right font-mono text-slate-500">
                                                    {pct(c.confidence)}
                                                </td>
                                                <td className="px-2.5 py-1.5">
                                                    <CitationChips ids={c.evidence_ids} />
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </Section>
        </div>
    );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <section>
            <div className="text-[10px] uppercase tracking-wider font-semibold text-slate-400 mb-2">
                {title}
            </div>
            {children}
        </section>
    );
}

function Empty({ children }: { children: React.ReactNode }) {
    return <p className="text-[11px] text-slate-400">{children}</p>;
}
