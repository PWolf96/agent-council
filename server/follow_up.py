import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END, MessagesState

from server.core.agents.teams import find_agent_by_key
from server.core.orchestration.memory import shared_memory
from server.core.orchestration.observability import flush as langfuse_flush
from server.core.orchestration.observability import langchain_config
from server.core.prompts import get_agent_prompt, get_compiled

SERVER_DIR = Path(__file__).resolve().parent
CONVERSATIONS_DIR = SERVER_DIR / "run_info" / "follow_up_conversations"
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

def _agent_keys_from_record(record: dict) -> dict[str, str]:
    labels: list[str] = []
    seen: set[str] = set()
    for entry in record.get("result", {}).get("debateHistory", []):
        label = entry.get("agent", "")
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return {label: label.lower().replace(" ", "_") for label in labels}

_FIRST_MESSAGE_TEMPLATE = """\
DEBATE CONTEXT:
Topic: {topic}

Final Verdict: {verdict}

Agent Positions:
{positions}

FOLLOW-UP QUESTION:
{question}
"""

_agents_cache: dict[str, object] = {}
_model: ChatOpenAI | None = None

def _get_model() -> ChatOpenAI:
    global _model
    if _model is None:
        _model = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.3,
            streaming=True
        )
    return _model

def _get_agent(display_name: str, prompt_key: str):
    if display_name in _agents_cache:
        return _agents_cache[display_name]
    
    model = _get_model()
    agent_info = find_agent_by_key(prompt_key)
    if agent_info is None:
        raise ValueError(f"Unknown agent key for follow-up: {prompt_key!r}")
    prompt = get_agent_prompt(agent_info.prompt_path)
    sys_msg = SystemMessage(
        content=prompt.text,
        additional_kwargs={"cache_control": {"type": "ephemeral"}},
    )

    def _make_chat_node(system_message, chat_model, prompt_client):
        # Route through a template so the managed persona prompt links to the
        # generation in Langfuse (the chat node runs inside the follow-up graph,
        # so the metadata registers and the LLM call links).
        template = ChatPromptTemplate.from_messages(
            [system_message, MessagesPlaceholder("messages")]
        )
        if prompt_client is not None:
            template.metadata = {"langfuse_prompt": prompt_client}
        chain = template | chat_model

        def chat(state: MessagesState):
            result = chain.invoke({"messages": state["messages"]})
            return {"messages": [result]}
        return chat

    graph = StateGraph(MessagesState)
    graph.add_node("chat", _make_chat_node(sys_msg, model, prompt.client))
    graph.add_edge(START, "chat")
    graph.add_edge("chat", END)

    compiled = graph.compile(checkpointer=shared_memory)
    _agents_cache[display_name] = compiled
    return compiled

def _build_debate_context(record: dict) -> dict:
    result = record["result"]
    topic = record["topic"]
    verdict = result.get("finalVerdict", "")

    last_positions: dict[str, str] = {}
    for entry in result["debateHistory"]:
        last_positions[entry["agent"]] = entry["argument"]

    positions_text = ""
    for agent, argument in last_positions.items():
        positions_text += f"\n{agent}:\n{argument}\n"
    
    return {
        "topic": topic,
        "verdict": verdict,
        "positions": positions_text
    }

def _format_first_message(debate_context: dict, question: str) -> str:
    # User-facing content (not the system prompt), so it's managed/versioned in
    # Langfuse but not trace-linked — the persona prompt is the linked one.
    return get_compiled(
        "follow-up/first-message",
        fallback=_FIRST_MESSAGE_TEMPLATE,
        topic=debate_context["topic"],
        verdict=debate_context["verdict"],
        positions=debate_context["positions"],
        question=question,
    ).text

def _seed_from_json(
        agent,
        config: dict,
        conv_messages: list[dict],
        agent_name: str,
        debate_context: dict,
) -> None:
    lc_messages: list = []
    first_user = True

    for msg in conv_messages:
        if msg["role"] == "user":
            content = msg["content"]
            if first_user:
                content = _format_first_message(debate_context, content)
                first_user = False
            lc_messages.append(HumanMessage(content=content))
        elif msg["role"] == "agent" and msg["agent"] == agent_name:
            lc_messages.append(AIMessage(content=msg["content"]))

    if lc_messages:
        agent.update_state(config, {"messages": lc_messages})

def _invoke_agent(
    agent_name: str,
    prompt_key: str,
    conv_id: str,
    question: str,
    prior_messages: list[dict],
    debate_context: dict,
    debate_id: str,
) -> str:
    agent = _get_agent(agent_name, prompt_key)
    # Trace each follow-up reply under the originating debate's session so the
    # conversation groups with the run in Langfuse's Sessions view. Built via
    # langchain_config (not a context manager) because this runs in a worker
    # thread that wouldn't inherit an ambient trace context.
    config = langchain_config(
        session_id=debate_id,
        tags=["follow-up"],
        run_name=f"follow-up-{agent_name}",
        base={"configurable": {"thread_id": f"followup_{conv_id}_{agent_name}"}},
    )

    state = agent.get_state(config)
    has_memory = bool(state.values and state.values.get("messages"))

    if not has_memory and prior_messages:
        _seed_from_json(agent, config, prior_messages, agent_name, debate_context)
        message = question
    elif not has_memory:
        message = _format_first_message(debate_context, question)
    else:
        message = question

    result = agent.invoke(
        {"messages": [HumanMessage(content=message)]},
        config=config
    )
    return result["messages"][-1].content


def create_conversation(debate_id: str) -> dict:
    conv_id = str(uuid.uuid4())
    conversation = {
        "id": conv_id,
        "debateId": debate_id,
        "createdAt": time.time(),
        "updatedAt": time.time(),
        "messages": []
    }
    filepath = CONVERSATIONS_DIR / f"{conv_id}.json"
    filepath.write_text(json.dumps(conversation, indent=2), encoding="utf-8")
    return conversation

def load_conversation(conv_id: str) -> dict | None:
    filepath = CONVERSATIONS_DIR / f"{conv_id}.json"
    if not filepath.exists():
        return None
    return json.loads(filepath.read_text(encoding="utf-8"))

def save_conversation(conversation: dict) -> None:
    conversation["updatedAt"] = time.time()
    filepath = CONVERSATIONS_DIR / f"{conversation['id']}.json"
    filepath.write_text(json.dumps(conversation, indent=2), encoding="utf-8")

def list_conversations(debate_id: str) -> list[dict]:
    convos = []
    for filepath in CONVERSATIONS_DIR.glob("*.json"):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            if data.get("debateId") == debate_id:
                convos.append({
                    "id": data["id"],
                    "debateId": data["debateId"],
                    "createdAt": data["createdAt"],
                    "updatedAt": data["updatedAt"],
                    "messagesCount": len(data["messages"]),
                    "preview": _conversation_preview(data["messages"])
                })
        except (json.JSONDecodeError, KeyError):
            continue
    convos.sort(key=lambda c: c["updatedAt"], reverse=True)
    return convos


def delete_conversation(conv_id: str) -> bool:
    filepath = CONVERSATIONS_DIR / f"{conv_id}.json"
    if not filepath.exists():
        return False
    filepath.unlink()
    return True

def _conversation_preview(messages: list[dict]) -> str:
    for msg in messages:
        if msg["role"] == "user":
            return msg["content"][:120]
    return ""

def stream_follow_up(
        debate_record: dict,
        conversation: dict,
        question: str,
):
    agent_keys = _agent_keys_from_record(debate_record)
    context = _build_debate_context(debate_record)
    debate_id = debate_record["id"]

    user_msg = {
        "role": "user",
        "content": question,
        "timestamp": time.time()
    }
    conversation["messages"].append(user_msg)
    yield {"event": "user_message", "data": user_msg}

    prior_messages = conversation["messages"][:-1]
    agent_responses: list[dict] = []

    with ThreadPoolExecutor(max_workers=max(len(agent_keys), 1)) as pool:
        futures = {
            pool.submit(
                _invoke_agent,
                agent_name,
                prompt_key,
                conversation["id"],
                question,
                prior_messages,
                context,
                debate_id,
            ): agent_name
            for agent_name, prompt_key in agent_keys.items()
        }

        for future in as_completed(futures):
            agent_name = futures[future]
            response_text = future.result()

            agent_msg = {
                "role": "agent",
                "agent": agent_name,
                "content": response_text,
                "timestamp": time.time()
            }
            agent_responses.append(agent_msg)

            yield {"event": "agent_response", "data": agent_msg}
    
    conversation["messages"].extend(agent_responses)
    save_conversation(conversation)

    # Worker threads buffered their observations; push them before the request
    # ends so follow-up traces don't linger until the next flush interval.
    langfuse_flush()

    yield {"event": "complete", "data": {}}