# agent-framework

A **multi-agent deliberation platform**. You give it a prompt; a team of LLM
agents — each a domain specialist — debates it across several rounds. A judge
scores every agent on weighted categories, consensus is computed
deterministically, and the whole deliberation is persisted and rendered live in
a web UI.

The engine is **config-driven** and **workflow-agnostic**: a single `RunConfig`
object (built by the UI wizard) describes everything about a run, and teams are
plain folders on disk — adding a new team or agent is a matter of dropping in a
file, no core changes required.

---

## Table of contents

- [How it works](#how-it-works)
- [Tech stack](#tech-stack)
- [Running locally](#running-locally)
- [Repository layout](#repository-layout)
- [Architecture & request flow](#architecture--request-flow)
- [The deliberation algorithm](#the-deliberation-algorithm)
- [Configuration (`RunConfig`)](#configuration-runconfig)
- [Extending the platform](#extending-the-platform)
- [API reference](#api-reference)

---

## How it works

1. In the UI you write a prompt, pick a **team**, choose which **agents**
   participate (or let **smart routing** decide), and tune the **weights**,
   **consensus threshold**, and **round limits**.
2. The wizard serializes this into a `RunConfig` and `POST`s it to the backend.
3. The backend runs the deliberation **asynchronously** on a background thread
   pool and persists the result. The frontend **polls** every 3s for status.
4. When a run completes, its card unlocks the **Deliberation** view (the full
   debate transcript) and the **Stats** view (judge scores, weighted
   breakdowns). You can then open a **Follow-up** chat to interrogate the
   result.

The agents can be backed by real data: the example football team queries a
PostgreSQL stats warehouse and a Qdrant store of fan-channel transcripts via
LangChain tools.

---

## Tech stack

| Layer        | Technology                                                            |
| ------------ | -------------------------------------------------------------------- |
| Backend      | Python 3.14, FastAPI, uvicorn                                        |
| Agents       | LangChain + LangGraph, OpenAI (`gpt-4o-mini` by default)             |
| Data tools   | PostgreSQL (`psycopg2`), Qdrant (`qdrant-client`), OpenAI embeddings |
| Persistence  | JSON files on disk (swappable `RunStore`)                            |
| Frontend     | React 19, Vite, TypeScript, TailwindCSS v4, Radix UI, Recharts       |

PostgreSQL and Qdrant are treated as **external resources** — the platform
connects to them but does not manage them.

---

## Running locally

There are two ways to run the stack: with **Docker Compose** (recommended — one
command) or **natively** with `uv` and `npm`. Either way, Postgres and Qdrant
are external; bring them up separately and point the app at them via `.env`.

### Configure environment

Create a `.env` file in the repository root. Everything except the API key has a
sensible local default.

```
OPENAI_API_KEY=sk-...

# PostgreSQL (structured football statistics)
DB_HOST=127.0.0.1
DB_PORT=5432
DB_DATABASE=garviznormalize
DB_USER=garviz_user
DB_PASSWORD=SuperSecret123

# Qdrant (fan-channel transcript embeddings)
QDRANT_URL=http://localhost:6333
EMBEDDING_MODEL=text-embedding-3-small
QDRANT_DEFAULT_COLLECTION=yt_transcripts
```

> The `DB_HOST`/`QDRANT_URL` values are written from the host's point of view.
> Under Docker Compose the server container can't reach `localhost`, so the
> compose file overrides those two to `host.docker.internal` — no edit needed.

### Option A — Docker Compose (recommended)

**Prerequisites:** Docker Desktop (or Docker Engine + Compose v2), plus your
external Postgres + Qdrant running on the host.

```bash
docker compose up --build
```

| Service  | URL                   | Notes                                  |
| -------- | --------------------- | -------------------------------------- |
| `client` | http://localhost:5173 | Vite dev server with hot-module reload |
| `server` | http://localhost:8080 | FastAPI via `uvicorn --reload`         |

Both `./server` and `./client` are bind-mounted, so source edits reload live.
Stop with `Ctrl-C`, or `docker compose down` to remove the containers. The dev
images live in `.ci/Dockerfile.{server,client}.dev`.

### Option B — Run natively

**Prerequisites:** Python 3.14+ and [uv](https://docs.astral.sh/uv/),
Node.js 20+ and npm, plus external Postgres + Qdrant.

```bash
# Terminal 1 — server (http://localhost:8080)
uv sync
uv run python -m server.api.app

# Terminal 2 — client (http://localhost:5173)
cd client
npm install
npm run dev
```

Vite proxies `/api/*` to the backend (see `client/vite.config.ts`). Open
http://localhost:5173 in your browser.

---

## Repository layout

```
agent-framework/
├── server/                     # FastAPI backend
│   ├── api/                    # Thin HTTP layer
│   │   ├── app.py              #   App factory + entrypoint (uvicorn :8080)
│   │   └── routes/             #   runs, teams, follow_up routers
│   ├── core/                   # Workflow-agnostic engine (no team specifics)
│   │   ├── config/             #   RunConfig schema + weight validation
│   │   ├── agents/             #   Agent runtime
│   │   │   ├── base.py         #     build_agent(): ReAct or simple agent
│   │   │   ├── teams.py        #     Team/agent discovery from disk
│   │   │   └── general/        #     Cross-team agents: router, judge, summarizer
│   │   ├── orchestration/      #   The deliberation pipeline (see below)
│   │   └── persistence/        #   RunStore protocol + JSON-file store
│   ├── tools/                  # LangChain tools agents can call
│   │   ├── postgres_tools.py   #   Football stats (read-only Postgres)
│   │   ├── qdrant_tools.py     #   Fan-transcript semantic search
│   │   ├── db/                 #   Connection config + helpers
│   │   └── registry.py         #   Name -> tool, plus named toolsets
│   ├── workflows/              # Per-team isolation (the "what", not the "how")
│   │   └── <team_id>/
│   │       ├── team.json       #   Display name + agent descriptions
│   │       ├── agents/<key>/   #   definition.py (factory) + prompt.md
│   │       └── workflow.py     #   (optional) override the default workflow
│   ├── runs/                   # Persisted run records (one JSON per run)
│   └── main.py, react_demo.py  # CLI demos; follow_up.py = follow-up engine
├── client/                     # Vite/React frontend
│   └── src/
│       ├── App.tsx             #   View switcher (feed / deliberation / stats / chat)
│       ├── components/         #   RunFeed, DeliberationView, StatsView, ...
│       ├── hooks/useRunFeed.ts #   3s polling hook
│       ├── lib/api.ts          #   Typed fetch wrappers
│       └── types/              #   Shared TypeScript types
├── .ci/                        # Dev + prod Dockerfiles, nginx config
├── docker-compose.yml          # Local dev stack (client + server)
└── .k8s/                       # Kubernetes manifests (deploy)
```

### The two halves of the backend

The backend is deliberately split so that **generic engine** and
**team-specific content** never bleed into each other:

- **`server/core/`** knows *how* to run a deliberation but nothing about
  football or markets. It discovers teams, runs the graph, judges, persists.
- **`server/workflows/<team>/`** knows *what* a team is — its agents, their
  prompts, their tools, and (optionally) a custom workflow. Pure content +
  configuration.

---

## Architecture & request flow

```
┌─────────────┐   POST /api/runs (RunConfig)    ┌──────────────────────────┐
│   Browser   │ ──────────────────────────────► │  api/routes/runs.py      │
│  (React)    │                                  └────────────┬─────────────┘
│             │   GET /api/runs   (poll 3s)                   │ create_run
│             │ ◄──────────────────────────────              ▼
└─────────────┘                                  ┌──────────────────────────┐
                                                 │  RunManager              │
                            persist record       │  • validate team         │
                       ┌───────────────────────► │  • record = "queued"     │
                       │                          │  • executor.submit(...)  │
                ┌──────┴───────┐                  └────────────┬─────────────┘
                │  RunStore    │                               │ background thread
                │ (JSON files) │ ◄──── persist final payload   ▼
                └──────────────┘                  ┌──────────────────────────┐
                                                  │  resolve_workflow(team)  │
                                                  │  └ DeliberationWorkflow  │
                                                  │     └ stream_deliberation│
                                                  │        └ LangGraph       │
                                                  └────────────┬─────────────┘
                                                               │ events
                                                               ▼
                                                  build_run_payload(events)
```

Step by step:

1. **`RunConfig` arrives** at `POST /api/runs`. Pydantic validates it (bad
   configs → `422`; unknown team → `400`).
2. **`RunManager.create_run`** mints a run id, writes a `queued` record to the
   `RunStore`, and submits the work to the **`RunExecutor`** (a bounded
   `ThreadPoolExecutor`). It returns immediately.
3. **In the background**, `RunManager._execute` flips the record to `running`,
   then calls **`resolve_workflow(team_id)`** — which returns a per-team
   `workflow.py` override if present, otherwise the default
   **`DeliberationWorkflow`**.
4. The workflow calls **`stream_deliberation`**, which (optionally) runs the
   **router** to pick relevant agents, builds the **LangGraph**, and yields a
   stream of events (`routing`, `argument`, `summary`, `judge`, `verdict`, …).
5. **`build_run_payload`** reduces those events into the final transcript shape
   and the record is updated to `completed` (or `failed`, with the error).
6. The frontend **polls `GET /api/runs`** (the `useRunFeed` hook) for the card
   feed and fetches `GET /api/runs/{id}` for the full transcript once done.

**Two swap seams** keep this open for scale without touching orchestration:

- **`RunExecutor`** — swap the in-process thread pool for a durable queue
  (Celery/ARQ/Redis) by implementing one method.
- **`RunStore`** — swap JSON files for Postgres/Redis by implementing the
  abstract store.

---

## The deliberation algorithm

The debate is a **LangGraph state machine** (`core/orchestration/graph.py`)
over a shared `DeliberationState`. Agents run **in parallel** each round via
LangGraph `Send`.

```
                 ┌──────────────────────────────────────────────┐
                 │                                               │ (no consensus
                 ▼                                               │  & rounds left)
START ─► [all agents, round 1] ─► increment_round ─► consensus ──┤
        (opening statements,                          (judge)    │
         fan-out in parallel)                            │       ▼
                                                         │   summarize
                                          consensus OR   │       │
                                          max_rounds ──► END     ▼
                                                          increment_round_debate
                                                                 │
                                          [all agents, debate] ◄─┘
                                          (respond to summary,
                                           fan-out in parallel)
```

- **Round 1 — opening statements.** Every agent answers the prompt from its
  specialty (`FIRST_ROUND_PROMPT`), in parallel. ReAct agents may call tools
  (Postgres/Qdrant) first; the tools they used are captured for the UI.
- **Consensus check (judge).** Skipped until `min_rounds`. The judge scores each
  agent on each category (1–10) with structured output. Scores are clamped,
  then combined **deterministically**:
  - per-agent score = Σ(category_score × `category_weights`)
  - overall score = Σ(per-agent score × `agent_weights`)
  - **consensus reached** if `overall ≥ consensus_threshold`.
  - Consensus is **forced** once `max_rounds` is hit, so a run always
    terminates.
- **Summarize.** If no consensus and rounds remain, a moderator summarizes the
  round; that summary feeds the next round.
- **Debate rounds.** Agents respond to the summary, challenge each other, and
  defend their position (`DEBATE_PROMPT`). Loop back to the judge.

The result of every run includes the full `debateHistory`, `roundSummaries`,
`judgeHistory` (with score breakdowns), `consensusReached`, and `finalVerdict`.

**General-purpose agents** (in `core/agents/general/`) are shared across teams:

| Agent        | Role                                                              |
| ------------ | ---------------------------------------------------------------- |
| `router`     | Picks the relevant subset of agents when smart routing is on     |
| `judge`      | Scores agents per category; produces reasoning + verdict         |
| `summarizer` | Condenses each round into the summary the next round debates over |

---

## Configuration (`RunConfig`)

Defined in `server/core/config/schema.py`; this is the single source of truth
for a run.

| Field                 | Type                 | Meaning                                                                 |
| --------------------- | -------------------- | ---------------------------------------------------------------------- |
| `prompt`              | `str`                | The statement to deliberate (required, non-empty).                     |
| `team_id`             | `str`                | Which team debates (must exist under `server/workflows/`).             |
| `smart_routing`       | `bool`               | If true, the router selects the participating agents from the prompt.  |
| `agent_keys`          | `list[str] \| None`  | Explicit agent subset; `None`/`[]` = all. Ignored when smart routing.  |
| `agent_weights`       | `dict[str, float]`   | Per-agent weights; must sum to 1.0.                                    |
| `category_weights`    | `dict[str, float]`   | Per-category weights (e.g. evidence, reasoning); must sum to 1.0.      |
| `consensus_threshold` | `float` (1–10)       | Overall weighted score needed to declare consensus.                    |
| `min_rounds`          | `int` (≥1)           | Judge won't end the debate before this many rounds.                    |
| `max_rounds`          | `int` (≥`min_rounds`)| Hard cap; consensus is forced here.                                    |

---

## Extending the platform

Everything below is additive — no core edits.

**Add a team.** Create `server/workflows/<team_id>/team.json`:

```json
{
  "name": "My Team",
  "agent_descriptions": { "my_agent": "What this agent specializes in." }
}
```

**Add an agent.** Create `server/workflows/<team_id>/agents/<key>/`:

- `prompt.md` — the agent's system prompt / persona.
- `definition.py` — a factory named `create_<key>_agent`:

  ```python
  from pathlib import Path
  from server.core.agents.base import build_agent
  from server.tools import get_toolset

  PROMPT_PATH = Path(__file__).parent / "prompt.md"

  def create_my_agent():
      # tools=[] gives a plain LLM agent; pass tools for a ReAct agent.
      return build_agent(prompt_path=PROMPT_PATH, tools=get_toolset("stats"))
  ```

  Teams and agents are **auto-discovered** at runtime — no registration needed.

**Add a custom workflow.** Drop `server/workflows/<team_id>/workflow.py`
exposing `get_workflow()` or `WORKFLOW`; `resolve_workflow` will use it instead
of the default deliberation.

**Add a tool.** Implement a LangChain `@tool`, register it in
`server/tools/registry.py` (and optionally a named toolset), then reference it
from an agent's `definition.py`.

**Swap the executor / store.** Implement `RunExecutor`
(`core/orchestration/executor.py`) or `RunStore` (`core/persistence/base.py`)
and wire it into the `RunManager` / `get_store()` factory.

---

## API reference

All endpoints are served under `http://localhost:8080`.

| Method   | Path                                                  | Purpose                                        |
| -------- | ----------------------------------------------------- | ---------------------------------------------- |
| `GET`    | `/api/teams`                                          | Team catalog (teams → agents + descriptions).  |
| `POST`   | `/api/runs`                                           | Create a run from a `RunConfig`. Returns id.   |
| `GET`    | `/api/runs`                                           | Run summaries, newest first (the 3s poll).     |
| `GET`    | `/api/runs/{id}`                                      | Full run record + transcript.                  |
| `GET`    | `/api/runs/{id}/status`                               | Lightweight status / consensus flag.           |
| `DELETE` | `/api/runs/{id}`                                      | Delete a run.                                  |
| `POST`   | `/api/follow-up/{id}/conversations`                  | Start a follow-up conversation on a run.       |
| `GET`    | `/api/follow-up/{id}/conversations`                  | List conversations for a run.                  |
| `GET`    | `/api/follow-up/{id}/conversations/{conv}`           | Fetch one conversation.                        |
| `DELETE` | `/api/follow-up/{id}/conversations/{conv}`           | Delete a conversation.                         |
| `POST`   | `/api/follow-up/{id}/conversations/{conv}/message`   | Ask a question — streamed back over **SSE**.   |

Runs are poll-based; only the follow-up chat streams (Server-Sent Events).
