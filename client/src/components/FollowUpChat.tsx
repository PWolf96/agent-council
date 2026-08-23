import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import type { ChatMessage } from "@/types";
import { agentStyle, agentInitials } from "@/types";
import {
    createFollowUpConversation,
    fetchFollowUpConversations,
    streamFollowUpMessage,
} from "@/lib/api";
import type { FollowUpStreamEvent } from "@/lib/api";

interface FollowUpChatProps {
    debateId: string;
    debateTopic: string;
    conversationId?: string | null;
    onBack: () => void;
}

function AgentAvatar({ agent, allAgents }: { agent: string; allAgents: string[] }) {
    const style = agentStyle(agent, allAgents)
    return (
        <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-white text-[9px] font-bold shrink-0 shadow-sm"
            style={{ backgroundColor: style.fill }}
        >
            {agentInitials(agent)}
        </div>
    );
}

function UserBubble({ message }: { message: ChatMessage }) {
    return (
        <div className="flex gap-3 justify-end animate-fade-in-up">
            <div className="max-w-[70%]">
                <div className="bg-indigo-600 text-white rounded-2x1 rounded-tr-sm px-4 py-3 shadow-sm">
                    <div className="text-sm leading-relaxed whitespace-pre-wrap">
                        {message.content}
                    </div>
                </div>
            </div>
            <div className="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-700 text-[9px] font-bold shrink-0 shadow-sm">
                You
            </div>
        </div>
    );
}

function AgentBubble({ message, allAgents }: { message: ChatMessage; allAgents: string[] }) {
    const agent = message.agent ?? "Unknown ";
    const style = agentStyle(agent, allAgents)
    const [expanded, setExpanded] = useState(false);
    const lines = message.content.split("\n").filter(Boolean);
    const preview = lines.slice(0, 2).join(" ");
    const isLong = lines.length > 2;

    return (
        <div className="flex gap-3 animate-fade-in-up">
            <AgentAvatar agent={agent} allAgents={allAgents}/>
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                    <span
                        className={`text-xs font-semibold uppercase tracking-wide ${style.text}`}
                    >
                        {agent}
                    </span>
                    <span
                        className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${style.tag}`}
                    >
                        Follow-up
                    </span>
                </div>
                <div
                    className={`bg-white rounded-2x1 rounded-tl-sm shadow-sm border border-slate-200 px-4 py-3 border-1-3 ${style.border}`}
                >
                    <div className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
                        {expanded || !isLong ? message.content : preview + "..."}
                    </div>
                    {isLong && (
                        <button
                            onClick={() => setExpanded(!expanded)}
                            className="text-xs text-indigo-600 hover:text-indigo-800 mt-2 font-medium transition-colors"
                        >
                            {expanded ? "Show less" : "Read full response"}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
}

function TypingIndicator() {
    return (
        <div className="flex items-center gap-2 px-4 py-3 ml-11">
            <span className="text-xs text-slate-400 font-medium">Agents are responding...</span>
            <div className="flex gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-slate-400 typing-dot" />
                <div className="w-1.5 h-1.5 rounded-full bg-slate-400 typing-dot" />
                <div className="w-1.5 h-1.5 rounded-full bg-slate-400 typing-dot" />
            </div>
        </div>
    );
}

export function FollowUpChat({
    debateId,
    debateTopic,
    conversationId,
    onBack,
}: FollowUpChatProps) {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [input, setInput] = useState("");
    const [isStreaming, setIsStreaming] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [convId, setConvId] = useState<string | null>(conversationId ?? null);
    const [loading, setLoading] = useState(!!conversationId);
    const scrollRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);

    const allAgents = useMemo(() => {
        const seen = new Set<string>();
        const ordered: string[] = [];
        for (const msg of messages) {
            if (msg.agent && !seen.has(msg.agent)) {
                seen.add(msg.agent);
                ordered.push(msg.agent);
            }
        }
        return ordered;
    }, [messages])


    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
    }, [messages.length]);

    useEffect(() => {
        if (!conversationId) return;
        setLoading(true);
        fetchFollowUpConversations(debateId, conversationId)
            .then((conv) => {
                setMessages(conv.messages);
                setConvId(conv.id);
            })
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false));
    }, [debateId, conversationId]);

    const handleSend = useCallback(async () => {
        const question = input.trim();
        if (!question || isStreaming) return;

        setInput("");
        setError(null);
        setIsStreaming(true);

        try {
            let activeConvId = convId;
            if (!activeConvId) {
                const conv = await createFollowUpConversation(debateId);
                activeConvId = conv.id;
                setConvId(conv.id);
            }

            await streamFollowUpMessage(
                debateId,
                activeConvId,
                question,
                (event: FollowUpStreamEvent) => {
                    switch (event.event) {
                        case "user_message":
                            setMessages((prev) => [...prev, event.data]);
                            break
                        case "agent_response":
                            setMessages((prev) => [...prev, event.data]);
                            break;
                        case "error":
                            setError(event.data.message)
                            break;
                    }
                },
            );
        } catch (err) {
            setError(err instanceof Error ? err.message : "Unknown error");
        } finally {
            setIsStreaming(false);
            inputRef.current?.focus();
        }
    }, [input, isStreaming, convId, debateId]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    if (loading) {
        return (
            <div className="h-full flex items-center justify-center">
                <span className="text-sm text-slate-400">Loading conversation...</span>
            </div>
        );
    }

    return (
        <div className="flex flex-col h-full">
            <div className="shrink-0 pb-3 mb-3 border-b border-slate-200">
                <button
                    onClick={onBack}
                    className="text-xs text-indigo-600 hover:text-indigo-800 font-medium mb-2 flex items-center gap-1"
                >
                    <span>&larr;</span> Back
                </button>
                <div className="flex items-center gap-3">
                    <div className="w-7 h-7 rounded-lg bg-purple-600 flex items-center justify-center shadow-sm">
                        <span className="text-white text-xs font-bold">C</span>
                    </div>
                    <div>
                        <h2 className="text-sm font-bold text-slate-700">Follow-up Chat</h2>
                        <p className="text-[10px] text-slate-400 truncate max-w-[500px]" title={debateTopic}>
                            {debateTopic}
                        </p>
                    </div>
                    {isStreaming && (
                        <span className="ml-auto text-[10px] text-purple-600 font-semibold animate-pulse">
                            Agents responding...
                        </span>
                    )}
                </div>
            </div>

            <div ref={scrollRef} className="flex-1 overflow-y-auto pr-2 pb-4">
                <div className="flex flex-col gap-4">
                    {messages.length === 0 && !isStreaming && (
                        <div className="text-center py-12">
                            <div className="text-slate-400 text-sm mb-1">
                                Ask a follow-up questiona about this debate
                            </div>
                            <div className="text-slate-400 text-[11px]">
                                The team agents will respond from their area of expertise
                            </div>
                        </div>
                    )}
                    {messages.map((msg, i) =>
                        msg.role === "user" ? (
                            <UserBubble key={i} message={msg} />
                        ) : (
                            <AgentBubble key={i} message={msg} allAgents={allAgents}/>
                        ),
                    )}
                    {isStreaming && <TypingIndicator />}
                </div>
            </div>

            {error && (
                <div className="shrink-0 px-3 py-2 mb-2 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600">
                    {error}
                </div>
            )}

            <div className="shrink-0 border-t border-slate-200 pt-3">
                <div className="flex gap-2">
                    <textarea
                        ref={inputRef}
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Ask a follow-up question..."
                        disabled={isStreaming}
                        rows={2}
                        className="flex-1 resize-none rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                    <button
                        onClick={handleSend}
                        disabled={!input.trim() || isStreaming}
                        className="shrink-0 px-4 py-2.5 rounded-xl bg-indigo-600 text-white text-sm font-semibold shadow-sm hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        Send
                    </button>
                </div>
                <div className="text-[10px] text-slate-400 mt-1.5 ml-1">
                    Press Enter to send, Shif+Enter for a new line
                </div>
            </div>
        </div>
    );
}