import { useCallback, useState } from "react";
import { RunFeed } from "@/components/RunFeed";
import { DeliberationView } from "@/components/DeliberationView";
import { StatsView } from "@/components/StatsView";
import { FollowUpChat } from "@/components/FollowUpChat";

type View =
    | { name: "feed" }
    | { name: "deliberation"; runId: string }
    | { name: "stats"; runId: string }
    | { name: "chat"; debateId: string; topic: string; conversationId?: string };

export default function App() {
    const [view, setView] = useState<View>({ name: "feed" });

    const openChat = useCallback(
        (debateId: string, topic: string, conversationId?: string) =>
            setView({ name: "chat", debateId, topic, conversationId }),
        [],
    );

    const goFeed = useCallback(() => setView({ name: "feed" }), []);

    const title =
        view.name === "feed"
            ? "Live"
            : view.name === "deliberation"
              ? "Deliberation"
              : view.name === "stats"
                ? "Stats"
                : "Follow-up";

    return (
        <div className="h-screen bg-gray-50 text-slate-800 flex flex-col overflow-hidden">
            <header className="shrink-0 border-b border-slate-200 bg-white px-6 py-3 shadow-sm">
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-3">
                        <div className="w-7 h-7 rounded-lg bg-indigo-600 flex items-center justify-center shadow-sm">
                            <span className="text-white text-xs font-bold">D</span>
                        </div>
                        <div>
                            <h1 className="text-sm font-bold tracking-tight text-slate-800">
                                Deliberation Platform
                            </h1>
                            <p className="text-[10px] text-slate-400">Config-driven multi-agent analysis</p>
                        </div>
                    </div>

                    {view.name !== "feed" && (
                        <button
                            onClick={goFeed}
                            className="ml-2 text-[11px] font-medium px-3 py-1 rounded-md bg-slate-100 text-slate-600 hover:text-slate-800 transition-colors"
                        >
                            ← Back to live
                        </button>
                    )}

                    <span className="ml-auto text-[11px] text-slate-400">{title}</span>
                </div>
            </header>

            <div className="flex-1 overflow-hidden">
                {view.name === "feed" && (
                    <RunFeed
                        onOpenDeliberation={(id) => setView({ name: "deliberation", runId: id })}
                        onOpenStats={(id) => setView({ name: "stats", runId: id })}
                    />
                )}
                {view.name === "deliberation" && (
                    <DeliberationView runId={view.runId} onOpenChat={openChat} />
                )}
                {view.name === "stats" && <StatsView runId={view.runId} />}
                {view.name === "chat" && (
                    <div className="h-full p-6 overflow-hidden">
                        <FollowUpChat
                            key={view.debateId}
                            debateId={view.debateId}
                            debateTopic={view.topic}
                            conversationId={view.conversationId ?? null}
                            onBack={goFeed}
                        />
                    </div>
                )}
            </div>
        </div>
    );
}
