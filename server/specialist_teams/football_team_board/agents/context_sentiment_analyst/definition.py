from pathlib import Path

from server.core.agents.base import build_claim_writer

PROMPT_PATH = Path(__file__).parent / "prompt.md"


def create_context_sentiment_analyst_agent(model_name: str | None = None):
    # Merged physical + psychological + social + fan-sentiment: the human/context side.
    return build_claim_writer(
        prompt_path=PROMPT_PATH,
        dimension="context",
        key="context_sentiment_analyst",
        label="Context Sentiment Analyst",
        model_name=model_name,
    )
