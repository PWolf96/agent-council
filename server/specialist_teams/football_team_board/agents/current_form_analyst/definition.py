from pathlib import Path

from server.core.agents.base import build_claim_writer

PROMPT_PATH = Path(__file__).parent / "prompt.md"


def create_current_form_analyst_agent(model_name: str | None = None):
    # Recent results, momentum, and scoring/conceding trends.
    return build_claim_writer(
        prompt_path=PROMPT_PATH,
        dimension="form",
        key="current_form_analyst",
        label="Current Form Analyst",
        model_name=model_name,
    )
