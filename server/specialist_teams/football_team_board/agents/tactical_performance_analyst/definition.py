from pathlib import Path

from server.core.agents.base import build_claim_writer

PROMPT_PATH = Path(__file__).parent / "prompt.md"


def create_tactical_performance_analyst_agent(model_name: str | None = None):
    # Merged in-possession + out-of-possession + transition: on-pitch performance.
    return build_claim_writer(
        prompt_path=PROMPT_PATH,
        dimension="tactical",
        key="tactical_performance_analyst",
        label="Tactical Performance Analyst",
        model_name=model_name,
    )
