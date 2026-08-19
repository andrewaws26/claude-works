"""Configuration: paths, the comp floor, the rails, and where credentials live.

No secrets are stored here. The repo root is discovered relative to this file,
every tracked document is referenced by name, and application credentials are
read from the environment only when a submission tool actually needs them. If a
credential is requested and the environment variable is unset, the caller gets a
clear error rather than a silent fallback.

The rails encoded here are the honest-by-default policy from ``AGENTS.md`` and
``AUTHORIZATIONS.md``: defense/surveillance exclusions, the comp floor, and the
list of active interview tracks that must never be re-applied to.

The defaults are one candidate's policy. To run this for a different candidate,
drop a ``policy.json`` next to the trackers (in ``JOBSEARCH_DATA_DIR``) and any
key it defines replaces the corresponding default wholesale: the rails lists
here, the scoring vocabularies in ``discovery.py``, and the lane tables in
``curation.py``. See ``examples/policy.sample.json`` for every supported key. A policy
file that exists but does not parse fails loudly at import, because silently
falling back to someone else's exclusion list is exactly the kind of quiet
wrongness this codebase is built to refuse.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Data root = the directory holding the tracker, queue, and standing-policy
# documents. Defaults to the directory that contains this package, and is
# overridable via JOBSEARCH_DATA_DIR so the package imports and runs standalone
# even when those files are absent. Every reader degrades gracefully to an empty
# default when a referenced file is missing (see tracker and discovery).
REPO_ROOT = Path(os.environ.get("JOBSEARCH_DATA_DIR", str(Path(__file__).resolve().parent.parent)))

# Resume generator + render pipeline live outside the repo (private home dir).
# Overridable via env for testability and for a different machine.
RESUMES_DIR = Path(os.environ.get("JOBSEARCH_RESUMES_DIR", str(Path.home() / "Documents" / "Resumes")))


@dataclass(frozen=True)
class Paths:
    """Absolute paths to the documents the loop reads and writes.

    These are the canonical trackers and standing-policy docs. The MCP never
    moves or renames them; it reads and (for the tracker) appends in place so the
    cron loop and the MCP share one source of truth.
    """

    root: Path = REPO_ROOT
    applications: Path = REPO_ROOT / "applications.json"
    queue: Path = REPO_ROOT / "top300_jobs.json"
    needs_attention: Path = REPO_ROOT / "NEEDS_YOUR_ATTENTION.md"
    search_angles: Path = REPO_ROOT / "SEARCH_ANGLES.md"
    claims_bank: Path = REPO_ROOT / "CLAIMS_BANK.md"
    fit_rubric: Path = REPO_ROOT / "FIT_RUBRIC.md"
    authorizations: Path = REPO_ROOT / "AUTHORIZATIONS.md"
    outputs: Path = REPO_ROOT / "outputs"
    resumes: Path = RESUMES_DIR
    genlib: Path = RESUMES_DIR / "_genlib.py"
    render_sh: Path = RESUMES_DIR / "_render.sh"
    verify_resume: Path = REPO_ROOT / "verify_resume.py"
    lint_resume: Path = REPO_ROOT / "lint_resume.py"


PATHS = Paths()


def load_policy(path: Path | None = None) -> dict[str, Any]:
    """Load the optional per-candidate ``policy.json`` from the data dir.

    Returns ``{}`` when the file is absent (the built-in defaults apply). A file
    that exists but cannot be parsed, or that is not a JSON object, raises
    instead of degrading: a candidate who wrote a policy must never silently run
    on the defaults.
    """
    p = path or (REPO_ROOT / "policy.json")
    if not p.exists():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"policy file {p} is not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise RuntimeError(f"policy file {p} must be a JSON object, got {type(doc).__name__}")
    return doc


POLICY = load_policy()


def policy_tuple(key: str, default: tuple[str, ...], policy: dict[str, Any] | None = None) -> tuple[str, ...]:
    """A lowercased term tuple from the policy file, or ``default`` when unset.

    A key that IS present replaces the default wholesale (including an empty
    list, which turns that check off deliberately).
    """
    pol = POLICY if policy is None else policy
    if key not in pol:
        return default
    return tuple(str(t).lower() for t in pol[key])


@dataclass(frozen=True)
class Rails:
    """The honesty + safety policy enforced across the tools.

    Mirrors ``AGENTS.md`` / ``AUTHORIZATIONS.md`` / ``FIT_RUBRIC.md``. These are the
    lines the system will not cross: it never fabricates, never solves captchas,
    never auto-sends outbound, and never applies to excluded domains or active
    interview tracks.

    Construct via ``Rails.from_env()`` to pick up the ``JOBSEARCH_*`` overrides
    and the ``policy.json`` per-candidate lists at call time (the module-level
    ``RAILS`` is built that way at import). Plain construction takes explicit
    values, which is what tests use. The defaults below are one candidate's
    policy; a different candidate overrides them in ``policy.json``, never by
    editing this file.
    """

    # Base comp floor (USD/yr).
    comp_floor: int = 120000

    # Pursue jobs scoring at or above this on the 0-10 rubric.
    pursue_threshold: float = 7.0

    # Hard-required skills the candidate lacks -> cap the score (disqualify as best-fit).
    hard_gap_skills: tuple[str, ...] = (
        "kubernetes", "k8s", "kafka", "spark", "airflow",
        "fine-tune", "fine tune", "model training", "rlhf",
        "cuda", "vllm", "three.js", "webgl", "rust-primary",
        "web3", "smart contract", "salesforce admin",
    )

    # Over-level signals -> disqualify (the default profile targets mid / IC / first-hire).
    overlevel_terms: tuple[str, ...] = (
        "staff", "principal", "lead", "director", "head of", "vp",
        "vice president", "distinguished", "chief", "fellow", "manager",
    )

    # Domain exclusions -> never apply.
    excluded_domains: tuple[str, ...] = (
        "defense", "military", "surveillance", "weapon", "nuclear",
        "palantir", "anduril", "clearance", "biometric", "warfighter",
        "nation state", "nation-state",
    )

    # Companies / tracks that must never be re-applied to (active interviews + caps),
    # plus specific defense contractors whose postings read clean on title and stack
    # alone (a generic-sounding "AI Infrastructure Engineer" req) but whose business
    # is excluded_domains-flagged on inspection - added once discovered so future
    # runs skip them without re-researching the company each time.
    excluded_companies: tuple[str, ...] = (
        "rippling", "samsara", "mercor", "onedigital", "elevenlabs",
        "scale ai", "axon", "humana", "havocai",
    )

    def env_var_for(self, field_name: str) -> str:
        """The environment variable a secret field is read from (never stored)."""
        return {
            "email": "JOBSEARCH_APPLY_EMAIL",
            "username": "JOBSEARCH_APPLY_USERNAME",
            "password": "JOBSEARCH_APPLY_PASSWORD",
        }.get(field_name, "")

    @classmethod
    def from_env(cls, policy: dict[str, Any] | None = None) -> Rails:
        """Build a ``Rails`` from the environment plus the per-candidate policy.

        Precedence for the numbers: env var, then ``policy.json``, then the
        default. The four term lists come from ``policy.json`` when present,
        replacing the defaults wholesale. Reading happens here, at
        instantiation, not at class-definition time, so a test (or a
        re-launched server) that changes either source and calls ``from_env()``
        sees its values.
        """
        pol = POLICY if policy is None else policy
        return cls(
            comp_floor=int(os.environ.get("JOBSEARCH_COMP_FLOOR", str(pol.get("comp_floor", 120000)))),
            pursue_threshold=float(
                os.environ.get("JOBSEARCH_PURSUE_THRESHOLD", str(pol.get("pursue_threshold", 7.0)))
            ),
            hard_gap_skills=policy_tuple("hard_gap_skills", cls.hard_gap_skills, pol),
            overlevel_terms=policy_tuple("overlevel_terms", cls.overlevel_terms, pol),
            excluded_domains=policy_tuple("excluded_domains", cls.excluded_domains, pol),
            excluded_companies=policy_tuple("excluded_companies", cls.excluded_companies, pol),
        )


RAILS = Rails.from_env()

# Bare "clearance" in excluded_domains is a defense signal UNLESS negated ("no
# clearance", "clearance not required") - some postings advertise the ABSENCE of a
# clearance requirement, and a naive substring match false-positives on that
# negation. Shared by every excluded_domains check so the fix lives in one place.
_CLEARANCE_NEGATED = re.compile(
    r"\bno\b\W+(?:\w+\W+){0,2}clearance|clearance\W+(?:\w+\W+){0,2}not required|without\W+(?:\w+\W+){0,2}clearance"
)


def matched_excluded_domain(blob: str, domains: tuple[str, ...] | None = None) -> str | None:
    """First excluded-domain term found in ``blob`` (word-boundary match), or ``None``."""
    doms = RAILS.excluded_domains if domains is None else domains
    for dom in doms:
        if dom == "clearance":
            if "clearance" in blob and not _CLEARANCE_NEGATED.search(blob):
                return dom
            continue
        if re.search(rf"\b{re.escape(dom)}\b", blob):
            return dom
    return None


def get_credential(field_name: str) -> str:
    """Read an application credential from the environment.

    Credentials are NEVER stored in the repo. Set ``JOBSEARCH_APPLY_EMAIL``,
    ``JOBSEARCH_APPLY_USERNAME``, and ``JOBSEARCH_APPLY_PASSWORD`` in the shell
    that launches the server. Raises ``RuntimeError`` if the variable is unset so a
    submission fails loudly instead of silently mis-filling a form.
    """
    var = RAILS.env_var_for(field_name)
    if not var:
        raise RuntimeError(f"unknown credential field: {field_name!r}")
    value = os.environ.get(var)
    if not value:
        raise RuntimeError(
            f"credential {field_name!r} not set; export {var} in the environment "
            "(credentials are never committed to the repo)"
        )
    return value
