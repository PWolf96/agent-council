import { useEffect, useMemo, useRef } from "react";
import type {
    Claim,
    DeliberationResult,
    EvidenceRecord,
    StrengthTier,
    SweepLog,
    SweepOutcome,
} from "@/types";
import { agentInitials, agentStyle } from "@/types";

// Two drilldowns now:
//  - "conversation": the development of the argument — every claim threaded with
//    the challenges filed against it and the owner's responses/revisions, in
//    chat form, so the reader can follow how the final argument was reached.
//  - "evidence": the raw evidence pool.
// (Passes / Challenges / Revisions / Claims all open the conversation; only the
// Evidence tile opens the evidence view.)
export type DrilldownKind = "conversation" | "evidence";

const KIND_TITLE: Record<DrilldownKind, string> = {
    conversation: "Argument development",
    evidence: "Evidence pool",
};

interface StatsDrilldownProps {
    kind: DrilldownKind;
    result: DeliberationResult;
    label?: string;
    // When opened from a specific claim row, scroll to + highlight that thread.
    focusClaimId?: string;
    onClose: () => void;
}

function pct(value: number | null | undefined): string {
    return value == null ? "—" : `${Math.round(value * 100)}%`;
}

const SEVERITY_STYLE: Record<string, string> = {
    minor: "bg-slate-100 text-slate-500 border-slate-200",
    major: "bg-amber-50 text-amber-700 border-amber-200",
    critical: "bg-red-50 text-red-600 border-red-200",
};

const STATUS_STYLE: Record<string, string> = {
    asserted: "bg-slate-100 text-slate-500 border-slate-200",
    challenged: "bg-amber-50 text-amber-700 border-amber-200",
    revised: "bg-sky-50 text-sky-700 border-sky-200",
    corroborated: "bg-emerald-50 text-emerald-700 border-emerald-200",
    refuted: "bg-red-50 text-red-600 border-red-200",
    resolved: "bg-indigo-50 text-indigo-700 border-indigo-200",
    empty: "bg-slate-100 text-slate-400 border-slate-200",
};

const TIER_STYLE: Record<StrengthTier, string> = {
    weak: "bg-slate-100 text-slate-500 border-slate-200",
    moderate: "bg-sky-50 text-sky-700 border-sky-200",
    strong: "bg-emerald-50 text-emerald-700 border-emerald-200",
    authoritative: "bg-indigo-50 text-indigo-700 border-indigo-200",
};

function Badge({ text, styleMap }: { text: string; styleMap: Record<string, string> }) {
    const cls = styleMap[text] ?? "bg-slate-100 text-slate-500 border-slate-200";
    return (
        <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded-full border ${cls}`}>
            {text}
        </span>
    );
}

// A pre → post confidence move, coloured by direction.
function ConfidenceDelta({ pre, post }: { pre: number; post: number }) {
    const up = post > pre;
    const flat = post === pre;
    const color = flat ? "text-slate-400" : up ? "text-emerald-600" : "text-red-500";
    return (
        <span className={`text-[10px] font-mono ${color}`}>
            {pct(pre)} → {pct(post)} {flat ? "" : up ? "▲" : "▼"}
        </span>
    );
}

export function StatsDrilldown({ kind, result, label, focusClaimId, onClose }: StatsDrilldownProps) {
    // Close on Escape — matches the slide-over / modal convention.
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [onClose]);

    return (
        <div className="fixed inset-0 z-40">
            <div className="absolute inset-0 bg-slate-900/20" onClick={onClose} aria-hidden />
            <aside
                role="dialog"
                aria-label={KIND_TITLE[kind]}
                className="absolute right-0 top-0 h-full w-[460px] max-w-[94vw] bg-white border-l border-slate-200 shadow-xl flex flex-col"
            >
                <header className="flex items-start justify-between gap-3 px-5 py-4 border-b border-slate-100">
                    <div className="min-w-0">
                        <div className="text-[10px] uppercase tracking-wider font-semibold text-slate-400">
                            Drilldown
                        </div>
                        <h3 className="text-sm font-bold text-slate-800">{KIND_TITLE[kind]}</h3>
                        {label && (
                            <p className="text-[10px] text-slate-400 line-clamp-1 mt-0.5">{label}</p>
                        )}
                    </div>
                    <button
                        onClick={onClose}
                        className="text-slate-400 hover:text-slate-600 text-sm leading-none px-1"
                        aria-label="Close drilldown"
                    >
                        ✕
                    </button>
                </header>

                <div className="flex-1 overflow-y-auto px-5 py-4">
                    {kind === "conversation" ? (
                        <ConversationBody result={result} focusClaimId={focusClaimId} />
                    ) : (
                        <EvidenceBody evidence={result.evidence ?? []} />
                    )}
                </div>
            </aside>
        </div>
    );
}

function Empty({ children }: { children: React.ReactNode }) {
    return <p className="text-[12px] text-slate-400 py-6 text-center">{children}</p>;
}

// ---- conversation (claims threaded with challenges + responses) -------------

function outcomesByClaim(sweeps: SweepLog[]): Map<string, Array<SweepOutcome & { sweep: number }>> {
    const map = new Map<string, Array<SweepOutcome & { sweep: number }>>();
    for (const s of sweeps) {
        for (const o of s.outcomes) {
            const list = map.get(o.target_claim) ?? [];
            list.push({ ...o, sweep: s.sweep });
            map.set(o.target_claim, list);
        }
    }
    for (const list of map.values()) list.sort((a, b) => a.sweep - b.sweep);
    return map;
}

const RESPONSE_VERB: Record<string, string> = {
    cite: "defended it with further evidence",
    concede: "conceded the point",
    revise: "revised the claim",
};

function ChatBubble({
    side,
    name,
    color,
    initials,
    children,
    meta,
}: {
    side: "left" | "right";
    name: string;
    color: string; // hex, applied inline (palette colours aren't static classes)
    initials: string;
    children: React.ReactNode;
    meta?: React.ReactNode;
}) {
    const right = side === "right";
    return (
        <div className={`flex gap-2 ${right ? "flex-row-reverse" : ""}`}>
            <div
                style={{ backgroundColor: color }}
                className="shrink-0 w-6 h-6 rounded-full grid place-items-center text-[9px] font-bold text-white"
                title={name}
            >
                {initials}
            </div>
            <div className={`min-w-0 max-w-[82%] ${right ? "items-end" : "items-start"} flex flex-col`}>
                <div className="flex items-center gap-1.5">
                    <span className="text-[10px] font-semibold text-slate-500">{name}</span>
                    {meta}
                </div>
                <div
                    className={`mt-0.5 rounded-2xl px-3 py-1.5 text-[12px] leading-snug ${
                        right
                            ? "bg-amber-50 border border-amber-200 text-slate-700 rounded-tr-sm"
                            : "bg-slate-50 border border-slate-200 text-slate-700 rounded-tl-sm"
                    }`}
                >
                    {children}
                </div>
            </div>
        </div>
    );
}

function ClaimThread({
    claim,
    outcomes,
    supporting,
    palette,
    highlight,
    threadRef,
}: {
    claim: Claim;
    outcomes: Array<SweepOutcome & { sweep: number }>;
    supporting: boolean;
    palette: string[];
    highlight: boolean;
    threadRef?: (el: HTMLDivElement | null) => void;
}) {
    const ownerColor = agentStyle(claim.owner, palette).fill;
    return (
        <section
            ref={threadRef}
            className={`rounded-xl border bg-white p-3 transition-shadow ${
                highlight ? "border-indigo-400 ring-2 ring-indigo-200" : "border-slate-200"
            }`}
        >
            <div className="flex items-center justify-between gap-2 mb-2">
                <div className="flex items-center gap-1.5 min-w-0">
                    {supporting && (
                        <span title="Backs the decision" className="text-amber-500 text-[11px]">
                            ★
                        </span>
                    )}
                    <span className="text-[10px] font-mono text-slate-400">{claim.claim_id}</span>
                    {claim.dimension && (
                        <span className="text-[10px] text-slate-400 truncate">· {claim.dimension}</span>
                    )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                    <Badge text={claim.status} styleMap={STATUS_STYLE} />
                    <span className="text-[10px] font-mono text-slate-500">{pct(claim.confidence)}</span>
                </div>
            </div>

            <div className="space-y-2.5">
                {/* The owner's assertion opens the thread. */}
                <ChatBubble
                    side="left"
                    name={claim.owner}
                    color={ownerColor}
                    initials={agentInitials(claim.owner)}
                    meta={<span className="text-[9px] text-slate-400">asserted</span>}
                >
                    {claim.assertion}
                </ChatBubble>

                {/* Each challenge, then the owner's response to it. */}
                {outcomes.map((o) => (
                    <div key={o.challenge_id} className="space-y-2.5">
                        <ChatBubble
                            side="right"
                            name={o.challenger}
                            color="#d97706"
                            initials={agentInitials(o.challenger)}
                            meta={
                                <span className="flex items-center gap-1">
                                    <Badge text={o.kind} styleMap={{}} />
                                    <Badge text={o.severity} styleMap={SEVERITY_STYLE} />
                                    <span className="text-[9px] text-slate-300">pass {o.sweep}</span>
                                </span>
                            }
                        >
                            Filed a {o.severity} {o.kind.replace(/_/g, " ")} objection.
                        </ChatBubble>
                        <ChatBubble
                            side="left"
                            name={claim.owner}
                            color={ownerColor}
                            initials={agentInitials(claim.owner)}
                            meta={<ConfidenceDelta pre={o.pre_confidence} post={o.post_confidence} />}
                        >
                            {claim.owner.split(" ")[0]} {RESPONSE_VERB[o.response] ?? o.response}.
                        </ChatBubble>
                    </div>
                ))}

                {outcomes.length === 0 && (
                    <p className="text-[10px] text-slate-400 pl-8">Stood unchallenged.</p>
                )}
            </div>
        </section>
    );
}

function ConversationBody({
    result,
    focusClaimId,
}: {
    result: DeliberationResult;
    focusClaimId?: string;
}) {
    const claims = useMemo(() => result.claims ?? [], [result.claims]);
    const supportingIds = useMemo(
        () => new Set(result.decision?.supporting_claims ?? []),
        [result.decision],
    );
    const byClaim = useMemo(() => outcomesByClaim(result.sweeps ?? []), [result.sweeps]);

    // Palette keys: every participant (claim owners + challengers) gets a stable
    // colour, reusing the team-wide agent styling.
    const palette = useMemo(() => {
        const set = new Set<string>();
        for (const c of claims) set.add(c.owner);
        for (const list of byClaim.values()) for (const o of list) set.add(o.challenger);
        return [...set];
    }, [claims, byClaim]);

    // Supporting claims first (they form the final argument), then the rest.
    const ordered = useMemo(() => {
        return [...claims].sort((a, b) => {
            const sa = supportingIds.has(a.claim_id) ? 0 : 1;
            const sb = supportingIds.has(b.claim_id) ? 0 : 1;
            if (sa !== sb) return sa - sb;
            return a.claim_id.localeCompare(b.claim_id);
        });
    }, [claims, supportingIds]);

    const focusRef = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        if (focusClaimId && focusRef.current) {
            focusRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }, [focusClaimId]);

    if (claims.length === 0) return <Empty>No claims were authored.</Empty>;

    return (
        <div className="space-y-3">
            <p className="text-[11px] text-slate-400">
                Each claim is threaded with the challenges against it and the owner's responses, in order —
                follow it top-to-bottom to see how the argument was built. ★ marks claims that back the
                final decision.
            </p>
            {ordered.map((c) => (
                <ClaimThread
                    key={c.claim_id}
                    claim={c}
                    outcomes={byClaim.get(c.claim_id) ?? []}
                    supporting={supportingIds.has(c.claim_id)}
                    palette={palette}
                    highlight={c.claim_id === focusClaimId}
                    threadRef={c.claim_id === focusClaimId ? (el) => (focusRef.current = el) : undefined}
                />
            ))}
        </div>
    );
}

// ---- evidence --------------------------------------------------------------

function EvidenceBody({ evidence }: { evidence: EvidenceRecord[] }) {
    // Productive records first; empty/errored ones are kept (auditable) but sink.
    const sorted = useMemo(
        () => [...evidence].sort((a, b) => Number(a.is_empty) - Number(b.is_empty)),
        [evidence],
    );
    if (sorted.length === 0) return <Empty>No evidence was gathered.</Empty>;
    const productive = sorted.filter((e) => !e.is_empty).length;
    return (
        <div className="space-y-3">
            <p className="text-[11px] text-slate-400">
                {productive} productive of {sorted.length} record{sorted.length === 1 ? "" : "s"}.
            </p>
            {sorted.map((e) => (
                <div
                    key={e.id}
                    className={`rounded-lg border px-3 py-2.5 ${
                        e.is_empty ? "border-slate-200 bg-slate-50/60 opacity-70" : "border-slate-200 bg-white"
                    }`}
                >
                    <div className="flex items-center justify-between gap-2">
                        <span className="text-[10px] font-mono text-slate-500">{e.id}</span>
                        <div className="flex items-center gap-1">
                            <Badge text={e.strength_tier} styleMap={TIER_STYLE} />
                            {e.is_empty && <Badge text="empty" styleMap={STATUS_STYLE} />}
                        </div>
                    </div>
                    <div className="mt-1 text-[12px] text-slate-700 font-medium break-all">{e.source_tool}</div>
                    <div className="text-[10px] text-slate-400">{e.provenance}</div>
                </div>
            ))}
        </div>
    );
}
