import { useState, useEffect, useRef, useMemo } from "react";
import type {
    DeliberationResult,
    DebateEntry,
    RoundSummary,
    JudgeEntry,
    RoutingInfo,
    InsufficientAgentsInfo,
    ToolCall,
} from "@/types";
import { agentStyle, agentInitials } from "@/types"

export type TimelineItem =
    | { type: "round_header"; round: number; label: string }
    | { type: "argument"; entry: DebateEntry }
    | { type: "summary"; entry: RoundSummary }
    | { type: "judge"; entry: JudgeEntry }
    | { type: "verdict"; text: string }
    | { type: "routing"; info: RoutingInfo }
    | { type: "insufficient_agents"; info: InsufficientAgentsInfo };

// Build a complete timeline from a finished deliberation result.
export function buildTimeline(data: DeliberationResult): TimelineItem[] {
    const items: TimelineItem[] = [];
    const summaryByRound = new Map(data.roundSummaries.map((s) => [s.round, s]));
    const judgeByRound = new Map(data.judgeHistory.map((j) => [j.round, j]));

    for (let r = 1; r<= data.rounds; r++) {
        items.push({
            type: "round_header",
            round: r,
            label: r === 1 ? "Opening Statements" : `Debate Round ${r}`,
        });
        for (const entry of data.debateHistory.filter((e) => e.round === r)) {
            items.push({ type: "argument", entry});
        }
        const summary = summaryByRound.get(r);
        if (summary) items.push({ type: "summary", entry: summary});
        const judge = judgeByRound.get(r);
        if (judge) items.push({ type: "judge", entry: judge});
    }
    if (data.finalVerdict) items.push({ type: "verdict", text: data.finalVerdict});
    return items
}

interface ArenaProps {
    timeline: TimelineItem[];
    isStreaming?: boolean;
    label?: string;
}

function TypingIndicator() {
    return (
        <div className="flex items-center gap-2 px-4 py-3 ml-11">
            <span className="text-xs text-slate-400 font-medium">Agent is analyzing...</span>
            <div className="flex gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-slate-400 typing-dot" />
                <div className="w-1.5 h-1.5 rounded-full bg-slate-400 typing-dot" />
                <div className="w-1.5 h-1.5 rounded-full bg-slate-400 typing-dot" />
            </div>
        </div>
    );
}



function AgentAvatar({ agent, allAgents }: { agent: string; allAgents: string[] }) {
    const style = agentStyle(agent, allAgents);
    return (
        <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-white text-[9px] font-blod shrink-0 shadow-sm"
            style={{ backgroundColor: style.fill }}
        >
            {agentInitials(agent)}
        </div>
    );
}

// Friendly metadata for each tool, so a raw function name like
// `get_team_form` reads as "Recent form" with a source-appropriate tone.
const TOOL_META: Record<string, { label: string; icon: string; source: "db" | "fans" }> = {
    get_player_stats:     { label: "Player stats",   icon: "📊", source: "db" },
    compare_players:      { label: "Player compare", icon: "📊", source: "db" },
    get_squad_stats:      { label: "Squad stats",    icon: "📊", source: "db" },
    get_team_fixtures:    { label: "Fixtures",       icon: "📅", source: "db" },
    get_team_form:        { label: "Recent form",    icon: "📈", source: "db" },
    list_entities:        { label: "Lookup",         icon: "🔎", source: "db" },
    search_fan_opinions:  { label: "Fan opinions",   icon: "💬", source: "fans" },
    get_recent_fan_clips: { label: "Fan clips",      icon: "💬", source: "fans" },
    list_fan_channels:    { label: "Fan channels",   icon: "💬", source: "fans" },
};

// The most salient argument value, shown inline so a chip reads
// "Recent form · Arsenal" without spelling out every parameter.
function argHint(args: Record<string, unknown>): string {
    for (const key of ["team", "player_name", "query", "club"]) {
        const v = args[key];
        if (v != null && v !== "") return String(v);
    }
    const first = Object.values(args)[0];
    return first == null ? "" : String(first);
}

// Pretty-print a tool result: JSON if it parses, otherwise the raw string.
function formatResult(result: string): string {
    try {
        return JSON.stringify(JSON.parse(result), null, 2);
    } catch {
        return result;
    }
}

// Discrete row of chips showing which data tools the agent called. A chip with
// a captured result is clickable: it expands to reveal the retrieved data —
// the evidence behind the argument.
function ToolChips({ calls }: { calls: ToolCall[] }) {
    const [openIndex, setOpenIndex] = useState<number | null>(null);
    if (!calls || calls.length === 0) return null;
    return (
        <div className="mt-2.5 pt-2 border-t border-slate-100">
            <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">
                    Data fetched
                </span>
                {calls.map((call, i) => {
                    const meta = TOOL_META[call.tool] ?? { label: call.tool, icon: "🔧", source: "db" as const };
                    const hint = argHint(call.args);
                    const tip = Object.entries(call.args)
                        .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
                        .join("\n");
                    const tone =
                        meta.source === "fans"
                            ? "bg-rose-50 border-rose-200 text-rose-600"
                            : "bg-slate-50 border-slate-200 text-slate-500";
                    const hasResult = Boolean(call.result);
                    const isOpen = openIndex === i;
                    return (
                        <button
                            key={i}
                            type="button"
                            title={tip}
                            disabled={!hasResult}
                            onClick={() => setOpenIndex(isOpen ? null : i)}
                            className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full border transition-colors ${tone} ${
                                hasResult ? "cursor-pointer hover:brightness-95" : "cursor-default"
                            } ${isOpen ? "ring-1 ring-slate-300" : ""}`}
                        >
                            <span aria-hidden>{meta.icon}</span>
                            <span>{meta.label}</span>
                            {hint && <span className="opacity-60 max-w-[120px] truncate">· {hint}</span>}
                            {hasResult && <span className="opacity-50">{isOpen ? "▾" : "▸"}</span>}
                        </button>
                    );
                })}
            </div>
            {openIndex !== null && calls[openIndex]?.result && (
                <div className="mt-2">
                    <div className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider mb-1">
                        Retrieved data · {TOOL_META[calls[openIndex].tool]?.label ?? calls[openIndex].tool}
                    </div>
                    <pre className="text-[10px] leading-relaxed text-slate-600 bg-slate-50 border border-slate-200 rounded-lg p-2.5 overflow-x-auto max-h-56 whitespace-pre-wrap">
                        {formatResult(calls[openIndex].result!)}
                    </pre>
                </div>
            )}
        </div>
    );
}

function ChatBubble({ entry, allAgents }: { entry: DebateEntry, allAgents: string[]}) {
    const [expanded, setExpanded] = useState(false);
    const agent = entry.agent;
    const style = agentStyle(agent, allAgents);
    const lines = entry.argument.split("\n").filter(Boolean);
    const preview = lines.slice(0, 2).join(" ");
    const isLong = lines.length > 2;

    return (
        <div className="flex gap-3 animate-fade-in-up">
            <AgentAvatar agent={agent} allAgents={allAgents} />
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                    <span className={`text-xs font-semibold uppercase tracking-wide ${style.text}`}>
                        {agent}
                    </span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${style.tag}`}>
                        R{entry.round}
                    </span>
                </div>
                <div className={`bg-white rounded-2x1 rounded-tl-sm shadow-sm border border-slate-200 px-4 py-3 border-1-3 ${style.border}`}>
                    <div className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
                        {expanded || !isLong ? entry.argument : preview + "..."}
                    </div>
                    {isLong && (
                        <button
                            onClick={() => setExpanded(!expanded)}
                            className="text-xs text-indigo-600 hover:text-indigo-800 mt-2 font-medium transition-colors"
                        >
                            {expanded ? "Show less" : "Read full analysis"}
                        </button>
                    )}
                    {entry.tool_calls && entry.tool_calls.length > 0 && (
                        <ToolChips calls={entry.tool_calls} />
                    )}
                </div>
            </div>
        </div>
    );
}

function SummaryCard({ entry }: { entry: RoundSummary }) {
    const [expanded, setExpanded] = useState(false);
    return (
        <div className="ml-11 animate-fade-in-up">
            <div className="bg-slate-50 rounded-x1 border border-slate-200 px-4 py-3 shadow-sm">
                <div className="flex items-center gap-2 mb-1.5">
                    <div className="w-5 h-5 rounded-full bg-amber-100 flex items-center justify-center">
                        <span className="text-[10px]">M</span>
                    </div>
                    <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                        Moderator Brief
                    </span>
                </div>
                <div className="text-sm text-slate-600 leading-relaxed whitespace-pre-wrap">
                    {expanded ? entry.summary : entry.summary.slice(0, 180) + "..."}
                </div>
                <button
                    onClick={() => setExpanded(!expanded)}
                    className="text-xs text-slate-500 hover:text-slate-8700 mt-1.5 font-medium transition-colors"
                >
                    {expanded ? "Collapse" : "Expand"}
                </button>
            </div>
        </div>
    );
}

function JudgeCard({ entry }: { entry: JudgeEntry }) {
    const [expanded, setExpanded] = useState(false);
    return (
        <div className="ml-11 animate-fade-in-up">
            <div className="bg-violet-50/60 rounded-xl border border-violet-200 px-4 py-3 shadow-sm">
                <div className="flex items-center gap-2 mb-1.5">
                    <div className="w-5 h-5 rounded-full bg-violet-200 flex items-center justify-center">
                        <span className="text-[10px] text-violet-700 font-bold">J</span>
                    </div>
                    <span className="text-xs font-semibold text-violet-700 uppercase tracking-wide">
                        Decision summary
                    </span>
                </div>
                {expanded && (
                    <>
                        <div className="text-sm text-slate-600 leading-relaxed whitespace-pre-wrap mt-2">
                            {entry.reasoning}
                        </div>
                        {entry.verdict && (
                            <div className="mt-3 pt-3 border-t border-violet-200">
                                <div className="text-[10px] font-semibold text-violet-600 uppercase tracking-wide mb-1">
                                    Verdict
                                </div>
                                <div className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
                                    {entry.verdict}
                                </div>
                            </div>
                        )}
                    </>
                )}
                <button
                    onClick={() => setExpanded(!expanded)}
                    className="text-xs text-violet-600 hover:text-violet-800 mt-1.5 font-medium transition-colors"
                >
                    {expanded ? "Collapse" : "View reasoning"}
                </button>
            </div>
        </div>
    );
}

export function Arena({ timeline, isStreaming = false, label }: ArenaProps) {
    const scrollRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (scrollRef.current){
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [timeline.length]);

    const allAgents = useMemo(() => {
        const seen = new Set<string>();
        const ordered: string[] = [];
        for (const item of timeline) {
            if (item.type === "argument" && !seen.has(item.entry.agent)) {
                seen.add(item.entry.agent);
                ordered.push(item.entry.agent);
            }
        }
        return ordered;
    }, [timeline]);

    return (
        <div className="flex flex-col h-full">
            {label && (
                <div className="pb-2 mb-2 border-b border-slate-200">
                    <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wide">
                        {label}
                    </span>
                </div>
            )}
            <div ref={scrollRef} className="flex flex-col gap-4 overflow-y-auto flex-1 pr-2 pb-6">
                {timeline.map((item, i) => {
                    if (item.type === "round_header") {
                        return (
                            <div key={i} className="sticky top-0 z-10 bg-gray-50/95 backdrop-blur-sm py-2 animate-fade-in-up">
                                <div className="flex items-center gap-3">
                                    <span className="text-[11px] font-semibold text-slate-400 uppercase tracking-widest">
                                        {item.label}
                                    </span>
                                    <div className="h-px flex-1 bg-slate-200" />
                                </div>
                            </div>
                        );
                    }
                    if (item.type === "argument") return <ChatBubble key={i} entry={item.entry} allAgents={allAgents}/>;
                    if (item.type === "summary") return <SummaryCard key={i} entry={item.entry} />;
                    if (item.type === "judge") return <JudgeCard key={i} entry={item.entry} />;
                    if (item.type === "routing") {
                        const info = item.info;
                        if (!info.enabled) return null;
                        return (
                            <div key={i} className="animate-fade-in-up">
                                <div className="bg-sky-50/70 rounded-xl border border-sky-200 px-4 py-3 shadow-sm">
                                    <div className="flex items-center gap-2 mb-1.5">
                                        <div className="w-5 h-5 rounded-full bg-sky-200 flex items-center justify-center">
                                            <span className="text-[10px] text-sky-700 font-bold">R</span>
                                        </div>
                                        <span className="text-xs font-semibold text-sky-700 uppercase tracking-wide">
                                            Smart Routing
                                        </span>
                                    </div>
                                    <div className="text-xs text-slate-600 leading-relaxed">
                                        <div className="mb-1">
                                            <span className="font-semibold">Selected:</span>{" "}
                                            {info.selected.length > 0
                                                ? info.selected.map((a) => a.label).join(", ")
                                                : "(none)"}
                                        </div>
                                        {info.skipped.length > 0 && (
                                            <div className="mb-1">
                                                <span className="font-semibold">Skipped:</span>{" "}
                                                {info.skipped.map((a) => a.label).join(", ")}
                                            </div>
                                        )}
                                        {info.reasoning && (
                                            <div className="text-slate-500 italic mt-1">{info.reasoning}</div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        );
                    }
                    if (item.type === "insufficient_agents") {
                        const info = item.info;
                        return (
                            <div key={i} className="animate-fade-in-up">
                                <div className="bg-amber-50 rounded-xl border-2 border-amber-300 px-4 py-3 shadow-sm">
                                    <div className="flex items-center gap-2 mb-1.5">
                                        <span className="text-xs font-bold text-amber-700 uppercase tracking-wide">
                                            Cannot Run Debate
                                        </span>
                                    </div>
                                    <div className="text-sm text-amber-800 leading-relaxed">
                                        {info.message}
                                    </div>
                                    {info.selected.length > 0 && (
                                        <div className="text-xs text-amber-700 mt-2">
                                            Only relevant agent: {info.selected.map((a) => a.label).join(", ")}
                                        </div>
                                    )}
                                    {info.reasoning && (
                                        <div className="text-xs text-amber-700/80 italic mt-1">
                                            {info.reasoning}
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    }
                    if (item.type === "verdict") {
                        return (
                            <div key={i} className="ml-11 animate-fade-in-up">
                                <div className="bg-indigo-50/70 rounded-xl border-2 border-indigo-200 px-5 py-4 shadow-sm">
                                    <div className="w-6 h-6 rounded-full bg-indigo-600 flex items-center justify-center">
                                        <span className="text-[10px] text-white font-bold">V</span>
                                    </div>
                                    <span className="text-xs font-bold text-indigo-700 uppercase tracking-wide">
                                        Final Verdict
                                    </span>
                                </div>
                                <div className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
                                    {item.text}
                                </div>
                            </div>
                        );
                    }
                    return null;
                })}

                {isStreaming && <TypingIndicator />}
            </div>
        </div>
    )
}