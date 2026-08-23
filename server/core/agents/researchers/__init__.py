"""Researcher team (L2): planner-selected, MCP-driven evidence gatherers.

Public surface:

- :mod:`registry` — the researcher catalog (``get_researchers``,
  ``score_researcher``) the Orchestrator and Planner select from.
- :class:`base.ResearcherAgent` — an LLM that decides + executes MCP calls for
  its domain, writing gather-only evidence into the Evidence Pool.
"""
