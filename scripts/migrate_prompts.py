"""One-time (idempotent) upload of every repo prompt into Langfuse.

Run from the repo root with the ``LANGFUSE_*`` env vars set (``.env`` is loaded
automatically):

    uv run python scripts/migrate_prompts.py          # create/update as needed
    uv run python scripts/migrate_prompts.py --dry-run # show what would happen

The repo stays the source of truth for the *initial* content: personas come from
the ``prompt.md`` files, templated prompts from the constants that already define
them. Single-brace ``{var}`` placeholders are converted to Langfuse's
``{{var}}`` syntax here. All prompts are uploaded as ``type="text"`` with the
``production`` label (they are already live). Re-running only creates a new
version when the content actually changed, so it's safe to run repeatedly.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable when run as a plain script (python scripts/...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

# Import after load_dotenv so the source constants resolve normally.
from langfuse import get_client  # noqa: E402

from server.core.agents.base import load_prompt  # noqa: E402
from server.core.agents.teams import get_all_teams  # noqa: E402
from server.core.prompts import name_from_path  # noqa: E402
from server.follow_up import _FIRST_MESSAGE_TEMPLATE  # noqa: E402

_SERVER = Path(__file__).resolve().parent.parent / "server"
_GENERAL = _SERVER / "core" / "agents" / "general"


def _to_langfuse_vars(template: str, variables: list[str]) -> str:
    """Convert ``{var}`` -> ``{{var}}`` for the named variables only.

    Targeted (not regex) so literal braces in a prompt are left untouched.
    """
    out = template
    for v in variables:
        out = out.replace("{" + v + "}", "{{" + v + "}}")
    return out


def _build_registry() -> list[dict]:
    """Every prompt to upload: name, text (Langfuse syntax), source label."""
    entries: list[dict] = []

    # --- general personas (no variables) ---
    # The planner, challenger, and synthesizer are loaded by these names via
    # ``get_text(...)``; their prompt.md files are the local fallback + the seed.
    for name in ("planner", "challenger", "synthesizer"):
        entries.append({
            "name": name,
            "text": load_prompt(_GENERAL / name / "prompt.md"),
            "source": f"general/{name}/prompt.md",
        })

    # --- workflow specialist personas (no variables) ---
    for team in get_all_teams().values():
        for agent in team.agents:
            entries.append({
                "name": name_from_path(agent.prompt_path),
                "text": load_prompt(agent.prompt_path),
                "source": str(agent.prompt_path.relative_to(_SERVER.parent)),
            })

    # --- templated prompts ({var} -> {{var}}) ---
    entries.append({
        "name": "follow-up/first-message",
        "text": _to_langfuse_vars(
            _FIRST_MESSAGE_TEMPLATE, ["topic", "verdict", "positions", "question"]
        ),
        "source": "follow_up.py:_FIRST_MESSAGE_TEMPLATE",
    })

    return entries


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    client = get_client()
    if not client.auth_check():
        print("Langfuse auth failed — check LANGFUSE_* env vars.", file=sys.stderr)
        return 1

    registry = _build_registry()
    created = updated = unchanged = 0

    for entry in registry:
        name, text = entry["name"], entry["text"]
        try:
            existing = client.get_prompt(name, label="production", cache_ttl_seconds=0)
        except Exception:  # noqa: BLE001 - not found yet
            existing = None

        if existing is not None and existing.prompt == text:
            unchanged += 1
            print(f"  = {name:42s} unchanged (v{existing.version}) [{entry['source']}]")
            continue

        action = "update" if existing is not None else "create"
        if action == "update":
            updated += 1
        else:
            created += 1

        if dry_run:
            print(f"  ~ {name:42s} would {action} [{entry['source']}]")
            continue

        client.create_prompt(name=name, prompt=text, type="text", labels=["production"])
        print(f"  + {name:42s} {action}d [{entry['source']}]")

    client.flush()
    print(
        f"\n{len(registry)} prompts: {created} created, {updated} updated, "
        f"{unchanged} unchanged" + (" (dry run)" if dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
