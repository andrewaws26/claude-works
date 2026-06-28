"""Discovery and scoring: find roles across sources, score them against the rubric.

Discovery wraps the repo's existing standard-library harvest scripts
(``board_harvest.py`` ATS miner, ``newsource_harvest.py`` Getro + Claude-ecosystem
sweep, ``hiringcafe.py`` aggregator) rather than re-implementing them, so the MCP
and the cron loop mine the same sources with the same filters. The scripts are
imported by file path because they live at the repo root, not inside a package.

Scoring implements ``FIT_RUBRIC.md``: core-stack overlap, rare-edge match, level
fit, clean channel and remote, with hard caps for required-skill gaps, over-level
titles, non-US-only, and excluded domains. The search angles come from
``SEARCH_ANGLES.md`` and act as lenses that bias scoring toward a lane.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Callable

from .config import PATHS, RAILS
from .models import Job, Score, SearchAngle


# --------------------------------------------------------------------------- #
# Search angles (parsed from SEARCH_ANGLES.md)
# --------------------------------------------------------------------------- #

# Keyword bias per angle name -> titles/terms that should score higher in that lens.
_ANGLE_BIAS: dict[str, tuple[str, ...]] = {
    "fde": ("forward deployed", "applied ai", "solutions engineer", "ai engineer", "demo", "founding engineer"),
    "overemployed": ("async", "remote", "contract", "backend", "internal tooling", "eval", "pipeline"),
    "junior applied ai": ("associate", "junior", "ai engineer i", "applied ai"),
    "iot": ("iot", "telematics", "edge", "connected", "industrial", "robotics", "fleet"),
    "industrial": ("industrial", "enterprise", "vertical", "manufacturing"),
    "fallback-first": ("engineer i", "engineer ii", "implementation", "support engineer", "customer engineer"),
    "analog-domain": ("healthcare", "legal", "document", "clinical", "compliance", "telematics"),
}


def _parse_search_angles(path: Path | None = None) -> list[SearchAngle]:
    """Parse ``SEARCH_ANGLES.md`` into ``SearchAngle`` records.

    The doc uses numbered ``## N. Name`` sections with ``- **Trigger:**`` and
    ``- **Definition:**`` bullets. The parser is forgiving: any section it cannot
    fully parse still yields a name so the catalog stays complete.
    """
    p = path or PATHS.search_angles
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    angles: list[SearchAngle] = []
    # Split on "## N. Title" headers, keeping the body.
    sections = re.split(r"\n##\s+\d+\.\s+", text)
    for sec in sections[1:]:
        lines = sec.splitlines()
        raw_name = lines[0].strip()
        # name = first chunk before a "(" qualifier, e.g. "FDE / Converting-Profile  (PRIMARY ...)"
        name = re.split(r"\s{2,}\(|\s+\(", raw_name)[0].strip()
        is_default = "default" in raw_name.lower() or "primary" in raw_name.lower()
        trigger = _grab(sec, r"\*\*Trigger:\*\*\s*(.+)")
        definition = _grab(sec, r"\*\*Definition:\*\*\s*(.+)")
        targets = _grab(sec, r"\*\*Target(?:\s+titles)?:\*\*\s*(.+)")
        target_titles = tuple(t.strip() for t in re.split(r"[,;]", targets) if t.strip())[:12]
        angles.append(
            SearchAngle(
                name=name,
                trigger=trigger,
                definition=definition,
                target_titles=target_titles,
                is_default=is_default,
            )
        )
    return angles


def _grab(block: str, pattern: str) -> str:
    m = re.search(pattern, block, re.I)
    return m.group(1).strip() if m else ""


def list_search_angles(path: Path | None = None) -> list[SearchAngle]:
    """Return every search angle defined in ``SEARCH_ANGLES.md``."""
    return _parse_search_angles(path)


def get_search_angle(name: str, path: Path | None = None) -> SearchAngle | None:
    """Look up one angle by a fuzzy name/trigger match (case-insensitive substring)."""
    q = (name or "").lower().strip()
    angles = _parse_search_angles(path)
    if not q:
        for a in angles:
            if a.is_default:
                return a
    for a in angles:
        if q and (q in a.name.lower() or q in a.trigger.lower()):
            return a
    return None


def _angle_bias_terms(angle: str | None) -> tuple[str, ...]:
    if not angle:
        return ()
    key = angle.lower()
    for k, terms in _ANGLE_BIAS.items():
        if k in key or key in k:
            return terms
    return ()


# --------------------------------------------------------------------------- #
# Scoring (FIT_RUBRIC.md)
# --------------------------------------------------------------------------- #

# Core-stack signals (the actual day-job Andrew does daily). Up to +4.
_CORE = ("anthropic", "claude", "mcp", "agent", "agentic", "llm", "eval",
         "playwright", "openai", "rag", "prompt", "guardrail", "applied ai")
# Rare-edge signals (few candidates have these; Andrew genuinely does). Up to +3.
_EDGE = ("edge", "can bus", "j1939", "robotics", "iot", "industrial", "telematics",
         "hipaa", "healthcare", "clinical", "document", "extraction", "forward deployed",
         "first engineer", "founding engineer", "devrel", "developer advocate", "mandarin")
# Level-fit positives (+2) and a clean-channel bonus (+1).
_LEVEL_OK = ("mid", "ic", "first hire", "first technical", "2-5", "2 to 5")


def score_job(job: Job, angle: str | None = None, jd_text: str = "") -> Score:
    """Score a job 0-10 against ``FIT_RUBRIC.md`` and return the pursue verdict.

    Scoring blends the job title with any provided JD text (titles under- and
    over-sell, so passing the JD sharpens the result). An angle biases the score
    toward its lane. Hard caps (required-skill gap, over-level title, non-US-only,
    excluded domain, excluded company) set ``hard_cap`` and force ``pursue=False``.
    """
    blob = f"{job.title} {job.location} {jd_text}".lower()
    reasons: list[str] = []

    # --- hard caps first (any one disqualifies) ---
    cap = _hard_cap(job, blob)
    if cap:
        return Score(value=min(5.0, 5.0), pursue=False, reasons=[f"hard cap: {cap}"], hard_cap=cap, angle=angle or "")

    value = 0.0

    core_hits = sorted({t for t in _CORE if t in blob})
    if core_hits:
        value += min(4.0, 1.0 + 0.75 * len(core_hits))
        reasons.append(f"core-stack overlap: {', '.join(core_hits[:5])} (+{min(4.0, 1.0 + 0.75 * len(core_hits)):.1f})")

    edge_hits = sorted({t for t in _EDGE if t in blob})
    if edge_hits:
        value += min(3.0, 1.0 * len(edge_hits))
        reasons.append(f"rare-edge match: {', '.join(edge_hits[:5])} (+{min(3.0, 1.0 * len(edge_hits)):.1f})")

    if any(t in blob for t in _LEVEL_OK) and not any(t in job.title.lower() for t in RAILS.overlevel_terms):
        value += 2.0
        reasons.append("level fit: mid / IC / first-hire (+2.0)")
    elif not any(t in job.title.lower() for t in RAILS.overlevel_terms):
        value += 1.0
        reasons.append("level neutral: no over-level title (+1.0)")

    if job.ats.lower() in ("ashby", "greenhouse") or "ashby" in job.url or "greenhouse" in job.url:
        value += 1.0
        reasons.append("clean channel: Ashby/Greenhouse autonomous-submit (+1.0)")

    if job.remote or "remote" in blob:
        value += 0.5
        reasons.append("remote (+0.5)")

    # angle lens bias (does not exceed the cap; nudges within the lane)
    bias = _angle_bias_terms(angle)
    if bias and any(b in blob for b in bias):
        value += 0.5
        reasons.append(f"angle '{angle}' lane match (+0.5)")

    value = round(min(10.0, value), 2)
    pursue = value >= RAILS.pursue_threshold
    if not reasons:
        reasons.append("no scoring signals matched (title-only)")
    return Score(value=value, pursue=pursue, reasons=reasons, hard_cap=None, angle=angle or "")


def _hard_cap(job: Job, blob: str) -> str | None:
    """Return the disqualifying reason if any hard cap applies, else None."""
    title_low = job.title.lower()
    for term in RAILS.overlevel_terms:
        if re.search(rf"\b{re.escape(term)}\b", title_low):
            return f"over-level title ('{term}')"
    for gap in RAILS.hard_gap_skills:
        if re.search(rf"\b{re.escape(gap)}\b.*\b(required|must have|expert)\b", blob) or \
           re.search(rf"\b(required|must have|expert)\b.*\b{re.escape(gap)}\b", blob):
            return f"hard-required skill gap ('{gap}')"
    for dom in RAILS.excluded_domains:
        if re.search(rf"\b{re.escape(dom)}\b", blob):
            return f"excluded domain ('{dom}')"
    for co in RAILS.excluded_companies:
        if co in job.company_slug or co.replace(" ", "") in job.company_slug:
            return f"excluded company / active track ('{co}')"
    return None


# --------------------------------------------------------------------------- #
# Source wrappers (import the existing repo scripts by path)
# --------------------------------------------------------------------------- #

def _load_repo_module(filename: str):
    """Import a repo-root script as a module (they are scripts, not a package)."""
    path = PATHS.root / filename
    if not path.exists():
        raise FileNotFoundError(f"discovery source not found: {path}")
    spec = importlib.util.spec_from_file_location(f"_jobsearch_src_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# source name -> (filename, callable returning list of raw dicts/tuples)
_SOURCES: dict[str, tuple[str, Callable[[Any], list]]] = {
    "newsource": ("newsource_harvest.py", lambda m: m.getro_sweep() + m.anthropic_sweep()),
    "getro": ("newsource_harvest.py", lambda m: m.getro_sweep()),
    "anthropic": ("newsource_harvest.py", lambda m: m.anthropic_sweep()),
}


def available_sources() -> list[str]:
    """Names accepted by ``discover_jobs``'s ``source`` argument."""
    return sorted(_SOURCES) + ["board_harvest"]


def discover_jobs(
    angle: str | None = None,
    source: str = "newsource",
    limit: int = 25,
    network_ok: bool = True,
) -> list[Job]:
    """Find roles from a discovery source, normalized to ``Job`` and ranked by fit.

    ``source`` selects which live sweep to run: ``newsource`` (Getro VC networks +
    Anthropic-customer ATS boards, the highest-yield combo), ``getro``,
    ``anthropic``, or ``board_harvest`` (the curated Ashby/Greenhouse seed miner).
    ``angle`` biases ranking toward a lane from ``SEARCH_ANGLES.md``. Set
    ``network_ok=False`` to return an empty list without making any HTTP calls
    (used by tests). Results are de-duped by role within the call and ranked by the
    fit score; the ledger de-dup is applied separately by the tracker.
    """
    if not network_ok:
        return []
    src = source.lower().strip()
    if src == "board_harvest":
        return _discover_board_harvest(angle, limit)
    if src not in _SOURCES:
        raise ValueError(f"unknown source {source!r}; choose from {available_sources()}")

    filename, getter = _SOURCES[src]
    mod = _load_repo_module(filename)
    raw = getter(mod)
    jobs = [Job.from_dict(r if isinstance(r, dict) else {}) for r in raw]
    return _rank(jobs, angle, limit)


def _discover_board_harvest(angle: str | None, limit: int) -> list[Job]:
    """Run the board_harvest Ashby/GH/Lever miners over the seed orgs.

    board_harvest's ``main()`` mutates the queue file; to keep discovery read-only
    we call its per-board functions directly over the seed lists instead.
    """
    mod = _load_repo_module("board_harvest.py")
    jobs: list[Job] = []
    for org in getattr(mod, "SEED_ASHBY", []):
        for title, loc, url, rem in mod.ashby_board(org):
            if title and url and mod.LANE.search(title) and not mod.SENIOR.search(title):
                jobs.append(Job(title=title.strip(), company=org, url=url, source="board_harvest/Ashby",
                                location=str(loc), remote=bool(rem), ats="Ashby"))
    for org in getattr(mod, "SEED_GH", []):
        for title, loc, url, rem in mod.gh_board(org):
            if title and url and mod.LANE.search(title) and not mod.SENIOR.search(title):
                jobs.append(Job(title=title.strip(), company=org, url=url, source="board_harvest/Greenhouse",
                                location=str(loc), remote=bool(rem), ats="Greenhouse"))
    return _rank(jobs, angle, limit)


def _rank(jobs: list[Job], angle: str | None, limit: int) -> list[Job]:
    """De-dup by role, score, and return the top ``limit`` by fit then remote."""
    seen: set[str] = set()
    scored: list[tuple[float, Job]] = []
    for j in jobs:
        rk = j.role_key
        if rk in seen:
            continue
        seen.add(rk)
        s = score_job(j, angle=angle)
        scored.append((s.value, j))
    scored.sort(key=lambda t: (-t[0], 0 if t[1].remote else 1))
    return [j for _, j in scored[:limit]]
