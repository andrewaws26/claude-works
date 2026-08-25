"""Live discovery against the public ATS posting APIs (Ashby, Greenhouse, Lever).

This is the one discovery source that ships fully working: the posting APIs are
public, per-org, and need no key, so a fresh install can find real roles with
nothing but a seed list of org slugs. The default seeds below are a starter set
of companies with public boards; a ``seed_boards`` object in ``policy.json``
replaces them wholesale:

    "seed_boards": {
      "ashby": ["openai", "ramp"],
      "greenhouse": ["anthropic", "stripe"],
      "lever": ["plaid"]
    }

Field notes baked in from production use (see ``submission.ATS_GOTCHAS``): the
Ashby posting API rejects default library user agents, so every request sends a
browser User-Agent; a 404 on a board means that org disabled the public API,
not that its roles closed, so boards that fail are skipped, never fatal; and
the Ashby ``isRemote`` flag over-promises, so it is carried as-is here and the
JD body is still checked downstream.

``fetch_job_description`` closes the loop for scoring: given any recognized ATS
job URL it returns the posting's title, location, and plain-text description,
so ``score_job`` can score on the JD instead of the title alone. Standard
library only; every network call is injectable for tests.
"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from collections.abc import Callable, Mapping
from html.parser import HTMLParser
from typing import Any

from .config import POLICY
from .models import Job

# The posting APIs reject urllib's default UA (403 on every Ashby board).
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Starter seeds: companies with (at time of writing) public posting APIs and a
# steady stream of AI/software roles. A policy.json "seed_boards" object
# replaces this wholesale; boards that 404 or time out are skipped gracefully.
DEFAULT_SEED_BOARDS: dict[str, tuple[str, ...]] = {
    "ashby": ("openai", "ramp", "linear", "replit", "elevenlabs", "cursor", "sierra"),
    "greenhouse": ("anthropic", "stripe", "databricks", "figma", "scaleai"),
    "lever": ("mistral", "zoox", "octoenergy"),
}

Fetcher = Callable[[str], Any]


def seed_boards(policy: dict[str, Any] | None = None) -> dict[str, tuple[str, ...]]:
    """The org-slug seed list per ATS, from policy.json when defined."""
    pol = POLICY if policy is None else policy
    raw = pol.get("seed_boards")
    if not isinstance(raw, dict):
        return DEFAULT_SEED_BOARDS
    return {str(k).lower(): tuple(str(o).lower() for o in v) for k, v in raw.items()}


def _get_json(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts
        return json.loads(resp.read().decode("utf-8", errors="replace"))


class _TextExtractor(HTMLParser):
    """Flatten posting HTML to readable plain text (block tags become newlines)."""

    _BLOCK = {"p", "div", "li", "br", "ul", "ol", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(fragment: str) -> str:
    """Plain text from a posting-API HTML description (entities unescaped)."""
    p = _TextExtractor()
    p.feed(html.unescape(fragment or ""))
    text = "".join(p.parts)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


# --------------------------------------------------------------------------- #
# Per-ATS board sweeps
# --------------------------------------------------------------------------- #

def ashby_is_remote(posting: Mapping[str, Any]) -> bool:
    """True only when an Ashby posting is genuinely remote.

    Ashby exposes two location fields and they disagree constantly. ``isRemote``
    is set to true for HYBRID postings as well, so trusting it queues
    city-anchored roles as remote and the whole screen is wasted downstream.
    ``workplaceType`` is the authoritative one: "Remote", "Hybrid", or "OnSite".
    Prefer it whenever the board returns it, and fall back to ``isRemote`` only
    for the older payloads that omit it.
    """
    workplace = str(posting.get("workplaceType") or "").strip().lower()
    if workplace:
        return workplace == "remote"
    return bool(posting.get("isRemote", False))


def ashby_board_jobs(org: str, fetch: Fetcher = _get_json) -> list[Job]:
    doc = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    jobs: list[Job] = []
    for j in doc.get("jobs", []):
        url = j.get("applyUrl") or j.get("jobUrl") or ""
        if not (j.get("title") and url):
            continue
        jobs.append(Job(
            title=str(j["title"]).strip(),
            company=str(doc.get("name") or org),
            url=str(url),
            source=f"boards/ashby/{org}",
            location=str(j.get("location") or ""),
            remote=ashby_is_remote(j),
            comp=str((j.get("compensation") or {}).get("compensationTierSummary") or ""),
            ats="Ashby",
        ))
    return jobs


def greenhouse_board_jobs(org: str, fetch: Fetcher = _get_json) -> list[Job]:
    doc = fetch(f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs")
    jobs: list[Job] = []
    for j in doc.get("jobs", []):
        url = j.get("absolute_url") or ""
        if not (j.get("title") and url):
            continue
        loc = str(((j.get("location") or {}).get("name")) or "")
        jobs.append(Job(
            title=str(j["title"]).strip(),
            company=org,
            url=str(url),
            source=f"boards/greenhouse/{org}",
            location=loc,
            remote="remote" in loc.lower(),
            ats="Greenhouse",
        ))
    return jobs


def lever_board_jobs(org: str, fetch: Fetcher = _get_json) -> list[Job]:
    doc = fetch(f"https://api.lever.co/v0/postings/{org}?mode=json")
    jobs: list[Job] = []
    for j in doc if isinstance(doc, list) else []:
        url = j.get("hostedUrl") or ""
        if not (j.get("text") and url):
            continue
        cats = j.get("categories") or {}
        loc = str(cats.get("location") or "")
        jobs.append(Job(
            title=str(j["text"]).strip(),
            company=org,
            url=str(url),
            source=f"boards/lever/{org}",
            location=loc,
            remote=(j.get("workplaceType") == "remote") or "remote" in loc.lower(),
            ats="Lever",
        ))
    return jobs


# An ATS "company" board that is really a recruiting marketplace: hundreds of
# postings that belong to OTHER employers, reposted under the marketplace's own
# org slug. Applying through one means the real employer is hidden until after a
# recruiter screen, which is a standing skip. The tell is scale plus spread, not
# any single posting: a genuine company of the size implied by 200+ open reqs
# does not also spread them across dozens of unrelated cities.
AGGREGATOR_MIN_POSTINGS = 200
AGGREGATOR_MIN_LOCATIONS = 20


def looks_like_aggregator_board(jobs: list[Job],
                                min_postings: int = AGGREGATOR_MIN_POSTINGS,
                                min_locations: int = AGGREGATOR_MIN_LOCATIONS) -> bool:
    """True when a board's shape says marketplace rather than employer.

    Checked on the board as a whole, before any role is scored, so a marketplace
    costs one sweep instead of one wasted screen per posting it contributes.
    Both conditions must hold: a large employer really can post 200 reqs, and a
    small distributed startup really can span 20 cities, but the combination of
    both is the marketplace signature.
    """
    if len(jobs) < min_postings:
        return False
    locations = {j.location.strip().lower() for j in jobs if j.location.strip()}
    return len(locations) >= min_locations


_BOARD_FNS: dict[str, Callable[[str, Fetcher], list[Job]]] = {
    "ashby": ashby_board_jobs,
    "greenhouse": greenhouse_board_jobs,
    "lever": lever_board_jobs,
}


def boards_sweep(seeds: dict[str, tuple[str, ...]] | None = None,
                 fetch: Fetcher = _get_json) -> tuple[list[Job], list[str]]:
    """Sweep every seeded board; return (jobs, skipped-board notes).

    A board that errors (disabled API, timeout, bad org slug) is skipped and
    reported in the notes rather than failing the sweep: one dead seed must
    never cost the whole discovery pass. The notes go back to the caller so a
    silent cap never masquerades as full coverage.
    """
    jobs: list[Job] = []
    skipped: list[str] = []
    for ats, orgs in (seeds or seed_boards()).items():
        fn = _BOARD_FNS.get(ats)
        if fn is None:
            skipped.append(f"{ats}: unsupported ATS in seed_boards")
            continue
        for org in orgs:
            try:
                jobs.extend(fn(org, fetch))
            except Exception as e:  # noqa: BLE001 - per-board isolation is the point
                skipped.append(f"{ats}/{org}: {e}")
    return jobs, skipped


# --------------------------------------------------------------------------- #
# JD retrieval for scoring
# --------------------------------------------------------------------------- #

def fetch_job_description(url: str, fetch: Fetcher = _get_json) -> dict[str, Any]:
    """The posting's title, location, and plain-text JD for a recognized ATS URL.

    Uses the same public APIs as the board sweep (Ashby board endpoint filtered
    by job id, Greenhouse per-job endpoint, Lever per-posting endpoint). Returns
    ``{title, company, location, remote, text}`` on success or ``{error}`` when
    the URL is unrecognized or the API is unavailable; it never fabricates a
    description.
    """
    u = (url or "").split("?")[0].rstrip("/")

    m = re.search(r"ashbyhq\.com/([^/]+)/([0-9a-f-]{8,})", u, re.I)
    if m:
        org, jid = m.group(1), m.group(2).lower()
        try:
            doc = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true")
        except Exception as e:  # noqa: BLE001
            return {"error": f"ashby board API unavailable for {org} (org may have disabled it): {e}"}
        for j in doc.get("jobs", []):
            if str(j.get("id", "")).lower() == jid:
                return {
                    "title": j.get("title", ""), "company": doc.get("name") or org,
                    "location": j.get("location", ""), "remote": bool(j.get("isRemote", False)),
                    "text": html_to_text(j.get("descriptionHtml") or ""),
                }
        return {"error": f"job {jid} not on the {org} board (a missing id on a 200 board means it closed)"}

    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/]+)/jobs/(\d+)", u, re.I)
    if m:
        org, jid = m.group(1), m.group(2)
        try:
            j = fetch(f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{jid}")
        except Exception as e:  # noqa: BLE001
            return {"error": f"greenhouse job API unavailable for {org}/{jid}: {e}"}
        loc = str(((j.get("location") or {}).get("name")) or "")
        return {
            "title": j.get("title", ""), "company": org, "location": loc,
            "remote": "remote" in loc.lower(), "text": html_to_text(j.get("content") or ""),
        }

    m = re.search(r"lever\.co/([^/]+)/([0-9a-f-]{8,})", u, re.I)
    if m:
        org, jid = m.group(1), m.group(2)
        try:
            j = fetch(f"https://api.lever.co/v0/postings/{org}/{jid}")
        except Exception as e:  # noqa: BLE001
            return {"error": f"lever posting API unavailable for {org}/{jid}: {e}"}
        lists_text = "\n".join(
            f"{sec.get('text', '')}\n{html_to_text(sec.get('content') or '')}"
            for sec in (j.get("lists") or [])
        )
        cats = j.get("categories") or {}
        return {
            "title": j.get("text", ""), "company": org,
            "location": str(cats.get("location") or ""),
            "remote": j.get("workplaceType") == "remote",
            "text": (str(j.get("descriptionPlain") or "") + "\n" + lists_text).strip(),
        }

    return {"error": "unrecognized ATS URL; supported: Ashby, Greenhouse, Lever job URLs"}
