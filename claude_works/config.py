"""Configuration: paths, the comp floor, the rails, and where credentials live.

No secrets are stored here. The repo root is discovered relative to this file,
every tracked document is referenced by name, and application credentials are
read from the environment only when a submission tool actually needs them. If a
credential is requested and the environment variable is unset, the caller gets a
clear error rather than a silent fallback.

The rails encoded here are the honest-by-default policy from ``AGENTS.md`` and
``AUTHORIZATIONS.md``: defense/surveillance exclusions, the comp floor, and the
list of active interview tracks that must never be re-applied to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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
    overemployed: Path = REPO_ROOT / "OVEREMPLOYED_GUIDE.md"
    outputs: Path = REPO_ROOT / "outputs"
    resumes: Path = RESUMES_DIR
    genlib: Path = RESUMES_DIR / "_genlib.py"
    render_sh: Path = RESUMES_DIR / "_render.sh"
    verify_resume: Path = REPO_ROOT / "verify_resume.py"
    lint_resume: Path = REPO_ROOT / "lint_resume.py"


PATHS = Paths()


@dataclass(frozen=True)
class Rails:
    """The honesty + safety policy enforced across the tools.

    Mirrors ``AGENTS.md`` / ``AUTHORIZATIONS.md`` / ``FIT_RUBRIC.md``. These are the
    lines the system will not cross: it never fabricates, never solves captchas,
    never auto-sends outbound, and never applies to excluded domains or active
    interview tracks.
    """

    # Base comp floor (USD/yr). Configurable; refinement default set by Andrew.
    comp_floor: int = int(os.environ.get("JOBSEARCH_COMP_FLOOR", "120000"))

    # Pursue jobs scoring at or above this on the 0-10 rubric.
    pursue_threshold: float = float(os.environ.get("JOBSEARCH_PURSUE_THRESHOLD", "7.0"))

    # Hard-required skills Andrew lacks -> cap the score (disqualify as best-fit).
    hard_gap_skills: tuple[str, ...] = (
        "kubernetes", "k8s", "kafka", "spark", "airflow",
        "fine-tune", "fine tune", "model training", "rlhf",
        "cuda", "vllm", "three.js", "webgl", "rust-primary",
        "web3", "smart contract", "salesforce admin",
    )

    # Over-level signals -> disqualify (Andrew targets mid / IC / first-hire).
    overlevel_terms: tuple[str, ...] = (
        "staff", "principal", "lead", "director", "head of", "vp",
        "vice president", "distinguished", "chief", "fellow", "manager",
    )

    # Domain exclusions -> never apply.
    excluded_domains: tuple[str, ...] = (
        "defense", "military", "surveillance", "weapon", "nuclear",
        "palantir", "anduril", "clearance", "biometric", "warfighter",
    )

    # Companies / tracks that must never be re-applied to (active interviews + caps).
    excluded_companies: tuple[str, ...] = (
        "rippling", "samsara", "mercor", "onedigital", "elevenlabs",
        "scale ai", "axon", "humana",
    )

    def env_var_for(self, field_name: str) -> str:
        """The environment variable a secret field is read from (never stored)."""
        return {
            "email": "JOBSEARCH_APPLY_EMAIL",
            "username": "JOBSEARCH_APPLY_USERNAME",
            "password": "JOBSEARCH_APPLY_PASSWORD",
        }.get(field_name, "")


RAILS = Rails()


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
