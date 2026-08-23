export interface TeamAgent {
    key: string;
    label: string;
    description?: string;
    // Present on run-summary agents (the model each specialist runs on).
    model?: string;
}

export interface Team {
    id: string;
    name: string;
    agents: TeamAgent[];
}

// The v3 run config as stored on the record (camelCase, server-shaped).
// Weighted consensus is gone; a run is bounded by adversarial-review passes.
export interface RunConfigInfo {
    defaultModel: string;
    agentModels: Record<string, string>;
    maxPasses: number;
    perPassBudget: number;
    smartRouting: boolean;
}

export type RunStatus = "queued" | "running" | "completed" | "failed";

// snake_case payload the wizard POSTs to /api/runs ("the config file").
// Mirrors server/core/config/schema.py::RunConfig.
export interface RunConfigPayload {
    prompt: string;
    team_id: string;
    smart_routing: boolean;
    agent_keys: string[] | null;
    default_model: string;
    agent_models: Record<string, string>;
    max_passes: number;
    per_pass_budget: number;
}

export interface ModelCatalog {
    models: string[];
    default: string;
}

export interface RunSummary {
    id: string;
    timestamp: number;
    prompt: string;
    teamId: string;
    teamName: string;
    smartRouting: boolean;
    agents: TeamAgent[];
    status: RunStatus;
    // v3: the run produced a calibrated decision (replaces consensusReached),
    // with its confidence in [0, 1] (null until the run completes).
    decided: boolean;
    confidence: number | null;
    error?: string | null;
}

export interface RunRecord {
    id: string;
    status: RunStatus;
    error?: string | null;
    created_at: number;
    completed_at: number | null;
    prompt: string;
    topic: string;
    teamId: string;
    teamName: string;
    smartRouting: boolean;
    agentKeys: string[] | null;
    agents: TeamAgent[];
    config: RunConfigInfo;
    routing: RoutingInfo | null;
    insufficientAgents: InsufficientAgentsInfo | null;
    result: DeliberationResult | null;
}

export interface RoutingAgent {
    key: string;
    label: string;
}

export interface RoutingInfo {
    enabled: boolean;
    selected: RoutingAgent[];
    skipped: RoutingAgent[];
    reasoning: string;
}

export interface InsufficientAgentsInfo {
    message: string;
    selected: RoutingAgent[];
    reasoning: string;
}

export function categoryLabel(key: string): string {
    return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export interface ToolCall {
    tool: string;
    args: Record<string, unknown>;
    // The data the tool returned — the evidence behind the agent's argument.
    // Optional for backward-compat with runs saved before results were captured.
    result?: string;
}

export interface DebateEntry {
    round: number;
    agent: string;
    argument: string;
    tool_calls?: ToolCall[];
}

// v3 emits a single synthetic summary entry (the decision narration), so the
// per-round per-category scores are gone.
export interface JudgeEntry {
    round: number;
    reasoning: string;
    verdict: string;
}

export interface RoundSummary {
    round: number;
    summary: string;
}

// ---- v3 evidence-grounded record (mirrors server/core/evidence/models.py) ---

export type ClaimStatus =
    | "asserted"
    | "challenged"
    | "revised"
    | "corroborated"
    | "refuted"
    | "resolved";

export type StrengthTier = "weak" | "moderate" | "strong" | "authoritative";
export type ConfidenceKind = "calibratable" | "judgmental";

export interface Claim {
    claim_id: string;
    owner: string;
    dimension?: string;
    status: ClaimStatus;
    confidence: number;
    assertion: string;
    evidence_ids: string[];
}

export interface Dissent {
    claim_id: string;
    owner: string;
    summary: string;
}

export interface Crux {
    crux_id: string;
    description: string;
    pivotal_claims: string[];
    unresolved: boolean;
}

export interface SweepOutcome {
    challenge_id: string;
    target_claim: string;
    challenger: string;
    kind: string;
    severity: string;
    response: string;
    pre_confidence: number;
    post_confidence: number;
    status: string;
}

export interface SweepLog {
    sweep: number;
    filed: number;
    admitted: number;
    dropped: number;
    revisions: number;
    outcomes: SweepOutcome[];
}

export interface ContradictionOutcome {
    claim_a: string;
    claim_b: string;
    resolution: "reconciled" | "dominant" | "unresolved";
    winner?: string | null;
    reason: string;
}

export interface EvidenceRecord {
    id: string;
    source_tool: string;
    strength_tier: StrengthTier;
    provenance: string;
    is_empty: boolean;
}

export interface Decision {
    answer: string;
    confidence: number;
    confidence_kind: ConfidenceKind;
    supporting_claims: string[];
    unresolved_dissent: Dissent[];
    open_cruxes: Crux[];
    citations: string[];
}

export interface DeliberationResult {
    // --- stable timeline view (Arena / follow-up / transcript diff) ---
    rounds: number;
    debateHistory: DebateEntry[];
    roundSummaries: RoundSummary[];
    judgeHistory: JudgeEntry[];
    decided: boolean;
    finalVerdict: string;
    // --- full v3 record (Stats dashboard / audit) ---
    decision: Decision;
    claims: Claim[];
    sweeps: SweepLog[];
    cruxes: Crux[];
    contradictions: ContradictionOutcome[];
    evidence: EvidenceRecord[];
}

const COLOR_PALETTE = [
    { fill: "#0d9488", border: "border-l-teal-500", text: "text-teal-700", tag: "bg-teal-50 text-teal-700 border-teal-200" },
    { fill: "#d97706", border: "border-l-amber-500", text: "text-amber-700", tag: "bg-teal-50 text-amber-700 border-amber-200" },
    { fill: "#4f46e5", border: "border-l-indigo-500", text: "text-indigo-700", tag: "bg-teal-50 text-indigo-700 border-indigo-200" },
    { fill: "#16a34a", border: "border-l-green-500", text: "text-green-700", tag: "bg-green-50 text-green-700 border-green-200" },
    { fill: "#dc2626", border: "border-l-red-500", text: "text-red-700", tag: "bg-red-50 text-red-700 border-red-200" },
    { fill: "#7c3aed", border: "border-l-violet-500", text: "text-violet-700", tag: "bg-violet-50 text-violet-700 border-violet-200" },
    { fill: "#0284c7", border: "border-l-sky-500", text: "text-sky-700", tag: "bg-sky-50 text-sky-700 border-sky-200" },
    { fill: "#be185d", border: "border-l-pink-500", text: "text-pink-700", tag: "bg-pink-50 text-pink-700 border-pink-200" },
]

export type AgentStyle = (typeof COLOR_PALETTE)[0];

export function agentStyle(label: string, allAgents: string[]): AgentStyle {
    const idx = allAgents.indexOf(label);
    return COLOR_PALETTE[(idx === -1 ? 0 : idx) % COLOR_PALETTE.length];
}

export function agentInitials(label: string): string {
    const words = label.split(/\s+/);
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    return label.slice(0, 2).toUpperCase();
}

export interface ChatMessage {
    role: "user" | "agent";
    agent?: string;
    content: string;
    timestamp: number;
}

export interface FollowUpConversation {
    id: string;
    debateId: string;
    createdAt: number;
    updatedAt: number;
    messages: ChatMessage[]
}

export interface FollowUpListItem {
    id: string;
    debateId: string;
    createdAt: number;
    updatedAt: number;
    messageCount: number;
    preview: string;
}