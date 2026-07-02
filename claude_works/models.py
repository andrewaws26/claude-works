"""Typed domain models for the job-search MCP server.

Every value that crosses an MCP tool boundary is one of these dataclasses.
They are plain ``@dataclass`` types (standard library only) so the core package
imports with zero third-party dependencies, which keeps the unit tests fast and
lets the modules be reused outside the MCP runtime.

The five core types map onto the pipeline stages:

    SearchAngle -> Job -> Score -> Resume -> Application

A discovery sweep yields ``Job`` records; ``Score`` is the fit-rubric verdict for
a job; ``Resume`` is the artifact built for a job; ``Application`` is one row in
the ledger. ``SearchAngle`` is one of Andrew's reusable search lenses.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


def _slug(text: str) -> str:
    """Normalize a company/role name for de-duplication.

    Lowercases, strips punctuation and common corporate suffixes, and drops a
    trailing ATS counter (``addepar1`` -> ``addepar``). Mirrors the normalization
    used by the existing harvest scripts so the MCP and the cron loop agree on
    identity.
    """
    import re

    s = (text or "").lower().strip()
    s = re.sub(r"\b(inc|llc|corp|co|ltd|pbc|the)\b", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    s = re.sub(r"\d+$", "", s)
    return s


@dataclass(frozen=True)
class SearchAngle:
    """One reusable search lens from ``SEARCH_ANGLES.md``.

    Angles are lenses Andrew has explicitly defined (FDE, IoT, and so on). Each
    pairs a trigger phrase with a definition and the titles to target,
    so an agent can run "that kind of search" on request.
    """

    name: str
    trigger: str
    definition: str
    target_titles: tuple[str, ...] = ()
    is_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Job:
    """A single discovered role, normalized across every discovery source.

    ``role_key`` is the canonical per-role identity (ATS + org slug + job id) used
    for de-duplication. De-dup is always by ROLE, never by company: the same
    company with a different role is allowed, which is how Andrew got the Samsara
    interview.
    """

    title: str
    company: str
    url: str
    source: str = ""
    location: str = ""
    remote: bool = False
    comp: str = ""
    ats: str = ""

    @property
    def role_key(self) -> str:
        """Canonical per-role id: ``<ats>:<org>:<jobid>`` from the apply URL."""
        import re

        u = (self.url or "").split("?")[0].rstrip("/")
        for pat, tag in [
            (r"ashbyhq\.com/([^/]+)/([0-9a-f-]{8,})", "ashby"),
            (r"greenhouse\.io/([^/]+)/jobs/(\d+)", "gh"),
            (r"lever\.co/([^/]+)/([0-9a-f-]{8,})", "lever"),
        ]:
            m = re.search(pat, u, re.I)
            if m:
                return f"{tag}:{m.group(1).lower()}:{m.group(2).lower()}"
        return f"raw:{u.lower()}:"

    @property
    def company_slug(self) -> str:
        return _slug(self.company)

    @property
    def url_org_slug(self) -> str:
        """Org slug parsed from the apply URL.

        The de-dup fallback for ledger matching: discovery rows sometimes carry a
        missing or differently spelled company name, and the ATS URL org is the
        authoritative identity in those cases. Empty string when the URL is not a
        recognized ATS board.
        """
        import re
        from urllib.parse import unquote

        u = unquote(self.url or "")
        for pat in (
            r"ashbyhq\.com/([^/?#]+)",
            r"workable\.com/([^/?#]+)",
            r"greenhouse\.io/(?:embed/job_board\?for=)?([^/?#&]+)",
            r"lever\.co/([^/?#]+)",
        ):
            m = re.search(pat, u, re.I)
            if m:
                return _slug(m.group(1).replace("-", " "))
        return ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["role_key"] = self.role_key
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        return cls(
            title=d.get("title", ""),
            company=d.get("company", "") or d.get("org", ""),
            url=d.get("url", "") or d.get("apply_url", ""),
            source=d.get("source", "") or d.get("src", ""),
            location=d.get("location", "") or d.get("loc", ""),
            remote=bool(d.get("remote", False)),
            comp=d.get("comp", ""),
            ats=d.get("ats", ""),
        )


@dataclass
class Score:
    """The fit-rubric verdict for a job (see ``FIT_RUBRIC.md``).

    ``value`` is 0-10. ``pursue`` is the gate (>= the configured threshold and no
    hard cap). ``reasons`` explains the score in one line per signal so the
    decision is auditable in the ledger. ``hard_cap`` is set when a required gap or
    over-level/non-US/defense signal disqualifies the role outright.
    """

    value: float
    pursue: bool
    reasons: list[str] = field(default_factory=list)
    hard_cap: str | None = None
    angle: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Resume:
    """A built resume artifact and the verdicts of the 4-gate pipeline.

    The gates are: ``one_page`` (render -> 1 page), ``lint_ok`` (style/AI-tell
    linter), ``verify_ok`` (anti-fabrication blocklist), and the agent claim-trace
    check performed by the caller. ``passed`` is the AND of the automated gates.
    """

    name: str
    html_path: str = ""
    pdf_path: str = ""
    one_page: bool | None = None
    lint_ok: bool | None = None
    verify_ok: bool | None = None
    findings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True only when all three automated gates have run and passed."""
        return bool(self.one_page) and bool(self.lint_ok) and bool(self.verify_ok)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        return d


@dataclass
class Application:
    """One row in the application ledger (``applications.json``).

    Field names match the existing tracker schema exactly so records written by
    the MCP and by the cron loop are interchangeable.
    """

    company: str
    role: str
    status: str = "submitted"
    ats: str = ""
    date: str = ""
    apply_url: str = ""
    note: str = ""
    tier: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("tier") is None:
            d.pop("tier", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Application":
        return cls(
            company=d.get("company", ""),
            role=d.get("role", ""),
            status=d.get("status", ""),
            ats=d.get("ats", ""),
            date=d.get("date", ""),
            apply_url=d.get("apply_url", ""),
            note=d.get("note", ""),
            tier=d.get("tier"),
        )
