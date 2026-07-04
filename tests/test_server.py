"""The server wiring: every documented tool is registered with the MCP runtime."""

from __future__ import annotations

import asyncio

EXPECTED_TOOLS = {
    "discover_jobs",
    "curate_queue",
    "score_job",
    "get_search_angle",
    "list_search_angles",
    "list_claim_fragments",
    "build_resume",
    "render_resume",
    "verify_resume",
    "submit_application",
    "record_application",
    "list_queue",
    "list_applications",
    "ledger_summary",
}


def test_all_documented_tools_are_registered():
    from claude_works import server

    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, EXPECTED_TOOLS - names


def test_every_tool_has_a_contract_docstring():
    from claude_works import server

    tools = asyncio.run(server.mcp.list_tools())
    for t in tools:
        assert t.description and len(t.description) > 40, t.name
