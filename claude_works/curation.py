"""Queue curation: triage discovered roles into a fit-ranked active set and a
parked set, so the autonomous loop never wastes a firing walking past poor fits.

Discovery yields many ``Job`` records, but only a fraction are genuine fits. Left
unsorted, the loop picks whatever role is next in line, which is often a Design
Engineer, a Consultant, an over-level title, or a non-US posting. Curation triages
the whole queue once: every job is either KEPT with a fit score (so the loop applies
to the strongest open match first) or PARKED with an auditable reason. Parked roles
are never discarded, only set aside, so a human can review or restore them.

Curation reuses the same ``RAILS`` the scorer enforces, so triage and per-job scoring
disqualify the same roles. Standard library only, so it imports with zero third-party
dependencies and the unit tests stay fast.

    curate(jobs, applied_slugs) -> CurationResult(active=[(Job, fit)], parked=[(Job, reason)])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import RAILS
from .models import Job

# Role-title lanes the candidate converts in, with fit points (strongest first).
# These bias the active queue so the loop applies to the best-matching role first.
LANE_POINTS: dict[str, int] = {
    "forward deployed": 6,
    "applied ai": 5,
    "ai engineer": 5,
    "ai developer": 5,
    "agent": 5,
    "solutions engineer": 5,
    "automation engineer": 5,
    "automation": 4,
    "developer experience": 4,
    "developer advocate": 4,
    "integration engineer": 4,
    "implementation": 4,
    "full stack": 3,
    "software engineer": 3,
    "backend": 3,
    "platform engineer": 3,
    "support engineer": 3,
}

# Off-lane titles to park (design / sales / consulting / research / non-software).
OFF_LANE: tuple[str, ...] = (
    "design engineer", "designer", "ux ", "ui/ux", "consultant", "value engineer",
    "pre-sales", "presales", "sales engineer", "recruiter", "sourcer", " sales",
    "account executive", "account manager", "marketing", "copywriter",
    "research scientist", "researcher", "data scientist", "hardware engineer",
    "mechanical", "electrical engineer", "firmware", "embedded ", "product manager",
    "program manager", "project manager", "strategist", "controller", "accountant",
    "technician",
)

# Extra over-level / wrong-level signals beyond RAILS.overlevel_terms.
EXTRA_LEVEL: tuple[str, ...] = ("founding", "founder", "intern", "apprentice")

# Advanced-degree knockout: "Scientist" titles (Research/Applied/ML/Data Scientist)
# and JDs that require a PhD or Master's are a hard credential gap for a candidate
# without an advanced degree, regardless of how well the lane otherwise scores.
ADVANCED_DEGREE: tuple[str, ...] = (
    "phd", "ph.d", "doctorate", "doctoral", "master's degree", "masters degree",
    "ms or phd", "graduate degree", "advanced degree", "requires a phd",
)

# US-location signals. When a location is present but shows none of these, the
# posting is treated as non-US-only and parked.
US_SIGNALS: tuple[str, ...] = (
    "united states", "usa", "u.s", "u.s.a", "remote", " us", "us-", "us ", ", us",
    "california", "new york", "texas", "washington", "massachusetts", "colorado",
    "illinois", "georgia", "florida", "san francisco", "seattle", "boston",
    "austin", "denver", "chicago", "los angeles", "atlanta", "remote, us", "us remote",
)

# The reasons curate can assign (stable vocabulary for summaries and tests).
PARK_REASONS: tuple[str, ...] = (
    "already-applied", "excluded-company", "excluded-domain", "over-level",
    "advanced-degree", "off-lane", "non-us-only", "hard-skill-gap",
)


@dataclass
class CurationResult:
    """The triage outcome for one queue.

    ``active`` is the fit-ranked list of ``(job, fit)`` to pursue, highest fit first.
    ``parked`` pairs each set-aside ``(job, reason)``. ``counts`` is the reason
    histogram for a one-line summary. Nothing is discarded.
    """

    active: list[tuple[Job, int]] = field(default_factory=list)
    parked: list[tuple[Job, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": [{**j.to_dict(), "fit": f} for j, f in self.active],
            "parked": [{**j.to_dict(), "park_reason": r} for j, r in self.parked],
            "counts": dict(self.counts),
        }


def _blob(job: Job) -> str:
    return " ".join([job.title, job.company, job.location, job.comp]).lower()


def park_reason(job: Job, applied_slugs: set[str]) -> str | None:
    """Return why a job should be parked, or ``None`` to keep it.

    Checks run cheapest-and-most-decisive first and reuse ``RAILS`` so curation and
    the scorer agree on what disqualifies a role.
    """
    title = job.title.lower()
    blob = _blob(job)
    if job.company_slug and job.company_slug in applied_slugs:
        return "already-applied"
    if any(co in blob for co in RAILS.excluded_companies):
        return "excluded-company"
    if any(dom in blob for dom in RAILS.excluded_domains):
        return "excluded-domain"
    if any(t in title for t in RAILS.overlevel_terms) or any(t in title for t in EXTRA_LEVEL):
        return "over-level"
    if "scientist" in title or any(d in blob for d in ADVANCED_DEGREE):
        return "advanced-degree"
    if any(t in title for t in OFF_LANE):
        return "off-lane"
    if job.location and not any(s in job.location.lower() for s in US_SIGNALS):
        return "non-us-only"
    if any(s in blob for s in RAILS.hard_gap_skills):
        return "hard-skill-gap"
    return None


def fit_score(job: Job) -> int:
    """A small integer fit score so the active queue ranks best-match first."""
    title = job.title.lower()
    blob = _blob(job)
    score = 0
    for kw, pts in LANE_POINTS.items():
        if kw in title:
            score = max(score, pts)
    if job.remote or "remote" in blob:
        score += 2
    if "python" in blob:
        score += 1
    if "typescript" in blob or "react" in blob:
        score += 1
    if "llm" in blob or "rag" in blob or "generative" in blob:
        score += 1
    if "senior" in title:
        score += 1
    if "junior" in title or "associate" in title:
        score -= 1
    return score


def curate(jobs: Iterable[Job], applied_slugs: Iterable[str] | None = None) -> CurationResult:
    """Partition ``jobs`` into a fit-ranked active set and a reasoned parked set.

    ``applied_slugs`` are normalized company slugs already in the ledger; matching
    jobs are parked as ``already-applied``. The active list is sorted by fit
    descending so the caller can pop the strongest open match in O(1).
    """
    applied = set(applied_slugs or ())
    result = CurationResult()
    for job in jobs:
        reason = park_reason(job, applied)
        if reason:
            result.parked.append((job, reason))
            result.counts[reason] = result.counts.get(reason, 0) + 1
        else:
            result.active.append((job, fit_score(job)))
    result.active.sort(key=lambda jf: -jf[1])
    return result
