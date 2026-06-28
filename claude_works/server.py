"""FastMCP server wiring the job-search tools together (stdio transport).

Run as a Claude Code / Claude Desktop MCP server:

    python -m claude_works

Each tool below is a thin, typed adapter over a core module. The docstring on each
tool is its user-facing contract: it is what Claude reads to decide when and how to
call the tool. Tools return JSON-serializable dicts/lists so structured results
flow straight back into the model. Discovery tools that hit the network accept a
``source`` and ``limit``; everything else operates on the local trackers and the
resume pipeline.

Honesty is enforced in the modules, not papered over here: ``score_job`` can
return a hard cap, ``build_resume`` reports gate findings instead of claiming
success, and ``submit_application`` returns a fill-and-park PLAN rather than
faking a browser submit.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import discovery, resume as resume_mod, submission, tracker
from .models import Application, Job

mcp = FastMCP("claude-works")


# --------------------------------------------------------------------------- #
# Discovery + scoring
# --------------------------------------------------------------------------- #

@mcp.tool()
def discover_jobs(angle: str = "", source: str = "newsource", limit: int = 25) -> list[dict[str, Any]]:
    """Find fresh roles from a discovery source, ranked by fit, de-duped by role.

    Args:
        angle: a search lens from SEARCH_ANGLES.md (e.g. "FDE", "Overemployed",
            "IoT") to bias ranking toward that lane. Empty = the default FDE lane.
        source: which live sweep to run. One of "newsource" (Getro VC networks +
            Anthropic-customer ATS boards, highest yield), "getro", "anthropic", or
            "board_harvest" (curated Ashby/Greenhouse seed miner).
        limit: max roles to return.

    Returns a list of job dicts (title, company, url, source, location, remote,
    ats, role_key). Network calls are made by the underlying repo scripts.
    """
    jobs = discovery.discover_jobs(angle=angle or None, source=source, limit=limit, network_ok=True)
    return [j.to_dict() for j in tracker.dedupe_jobs(jobs)]


@mcp.tool()
def score_job(title: str, company: str = "", url: str = "", location: str = "",
              jd_text: str = "", angle: str = "") -> dict[str, Any]:
    """Score one role 0-10 against FIT_RUBRIC.md and return the pursue verdict.

    Pass the JD text when available; titles under- and over-sell, so scoring on the
    JD is sharper. Returns value (0-10), pursue (bool, >= threshold and no hard
    cap), reasons (one line per signal), and hard_cap (set when a required-skill
    gap, over-level title, non-US-only, or excluded domain/company disqualifies it).
    """
    job = Job(title=title, company=company, url=url, location=location)
    return discovery.score_job(job, angle=angle or None, jd_text=jd_text).to_dict()


@mcp.tool()
def get_search_angle(name: str = "") -> dict[str, Any] | None:
    """Look up one search angle (lens) by name or trigger from SEARCH_ANGLES.md.

    Empty name returns the default (FDE / converting-profile) lane. Returns the
    angle's name, trigger phrase, definition, and target titles, or null if no
    angle matches.
    """
    a = discovery.get_search_angle(name)
    return a.to_dict() if a else None


@mcp.tool()
def list_search_angles() -> list[dict[str, Any]]:
    """List every search angle defined in SEARCH_ANGLES.md (name, trigger, definition)."""
    return [a.to_dict() for a in discovery.list_search_angles()]


# --------------------------------------------------------------------------- #
# Resume build + verify (the 4-gate)
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_claim_fragments() -> dict[str, list[str]]:
    """List the verified resume building blocks from _genlib.py (roles, bullets, projects).

    Every fragment traces to CLAIMS_BANK.md. Build resumes only from these names (or
    text that traces to the claims bank); this is what keeps the output honest.
    """
    return resume_mod.list_claim_fragments()


@mcp.tool()
def build_resume(name: str, tagline: str, summary: str,
                 experience: list[list[Any]], projects: list[str],
                 skills: list[list[str]]) -> dict[str, Any]:
    """Build a tailored one-page resume from verified fragments and run the static gates.

    Args:
        name: output file stem (becomes "<name>.html" in the resumes dir).
        tagline: the header tagline (mid-level; no over-level words).
        summary: the summary paragraph.
        experience: list of [role_key, [bullet, ...]] where role_key is one of
            bnb / twinspires / upwork / humana / tesla / dojo / lifespring, and each
            bullet is a fragment NAME (e.g. "BNB_AI") or text tracing to CLAIMS_BANK.
        projects: list of project fragment names (e.g. "P_CASEK") or verified HTML.
        skills: list of [label, text] rows for the Skills block.

    Returns the resume with lint_ok / verify_ok and any findings. The 1-page render
    gate runs separately via render_resume (it needs Chrome).
    """
    exp = [(row[0], list(row[1])) for row in experience]
    sk = [(row[0], row[1]) for row in skills]
    res = resume_mod.build_resume(name, tagline, summary, exp, projects, sk)
    return res.to_dict()


@mcp.tool()
def render_resume(name: str) -> dict[str, Any]:
    """Render <name>.html to PDF via _render.sh and report whether it is one page.

    Returns the resume with one_page set and the pdf_path. Requires Google Chrome
    and qpdf. This is the first of the 4 gates.
    """
    return resume_mod.render_resume(name).to_dict()


@mcp.tool()
def verify_resume(path: str) -> dict[str, Any]:
    """Run the two static gates (lint + anti-fabrication verify) on a resume HTML.

    Use this to check any resume on disk. Returns lint_ok, verify_ok, passed (their
    AND), and findings: every blocklist hit (C/C++, fabricated employer, model
    over-claim, ...) and style flag (banned words, em dashes, rule-of-three).
    """
    return resume_mod.verify_resume_file(path).to_dict()


# --------------------------------------------------------------------------- #
# Submission (fill-and-park plan)
# --------------------------------------------------------------------------- #

@mcp.tool()
def submit_application(title: str, company: str, url: str, location: str = "",
                       ats: str = "", resume_path: str = "") -> dict[str, Any]:
    """Build the fill-and-park submission plan for a role (no browser is driven here).

    Returns a plan an agent executes with the Playwright MCP: the ATS, the action
    ("auto_submit" for Ashby/Greenhouse, "fill_and_park" for Lever/Workday/captcha
    walls, or "blocked" for a rail violation), the standard field values and honest
    screening answers, the single human_step left when parked, and ATS-specific
    gotcha notes. Identity PII and credentials are read from the environment, never
    stored. Report the real outcome afterward with record_application.
    """
    job = Job(title=title, company=company, url=url, location=location, ats=ats)
    return submission.plan_submission(job, resume_path=resume_path).to_dict()


# --------------------------------------------------------------------------- #
# Tracker (the ledger + the queue)
# --------------------------------------------------------------------------- #

@mcp.tool()
def record_application(company: str, role: str, status: str = "submitted",
                       ats: str = "", apply_url: str = "", note: str = "",
                       tier: str = "") -> dict[str, Any]:
    """Append one row to the application ledger (applications.json), de-duped by role.

    Status mirrors the existing vocabulary ("submitted", "submitted-verified",
    "deferred-captcha", "skipped-overlevel", ...). Date defaults to today. Returns
    whether it was recorded (false if this company+role is already logged) and the
    new total.
    """
    app = Application(company=company, role=role, status=status, ats=ats,
                      apply_url=apply_url, note=note, tier=tier or None)
    return tracker.record_application(app)


@mcp.tool()
def list_queue(status: str = "todo", limit: int = 50) -> list[dict[str, Any]]:
    """List roles in the discovery queue (top300_jobs.json) by queue status.

    Status is the queue's own field ("todo" by default). Returns up to ``limit``
    job dicts. Use this to apply from the existing queue before running new
    discovery (the queue-first gate).
    """
    return [j.to_dict() for j in tracker.queue_jobs(status=status)[:limit]]


@mcp.tool()
def list_applications(status: str = "") -> list[dict[str, Any]]:
    """List ledger rows, optionally filtered by status. Empty status = all rows."""
    return [a.to_dict() for a in tracker.list_applications(status=status or None)]


@mcp.tool()
def ledger_summary() -> dict[str, int]:
    """Return a count of ledger rows by status (a one-glance system summary)."""
    return tracker.status_counts()


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
