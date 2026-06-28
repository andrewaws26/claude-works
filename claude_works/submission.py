"""Submission abstraction: build a fill-and-park plan for an application.

Honest scope note. The actual form-driving in this system is performed by the
**Playwright MCP** under an interactive agent (it handles trusted clicks, the
Ashby labeled-radio focus+Space gotcha, Greenhouse EEO numeric ids, and so on).
That browser driver is a separate MCP the agent calls; it is not embedded here,
and this module does not click anything on its own. What this module owns is the
deterministic, testable part of submission:

  * classify a job's ATS from its URL,
  * decide whether the role is auto-submittable or must be parked at a captcha,
  * assemble the exact standard field values and honest screening answers from
    ``AUTHORIZATIONS.md`` (credentials pulled from the environment, never stored),
  * produce a ``SubmissionPlan`` the agent executes step by step with Playwright.

This keeps the policy (what to fill, what never to cross) in typed, reviewable
code, and leaves only the mechanical clicking to the live browser tool. Nothing
here fabricates a success: ``submit_application`` returns a PLAN with a clear
``action`` of ``auto_submit`` or ``fill_and_park``, and the caller reports the
real outcome back through ``record_application``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .config import RAILS, get_credential
from .models import Job


# Standard, honest answers to common screening questions (AUTHORIZATIONS.md).
# These are policy, not secrets. Identity/contact PII and credentials are pulled
# from the environment at call time, never hard-coded here.
STANDARD_ANSWERS: dict[str, str] = {
    "authorized_to_work_us": "Yes",
    "require_sponsorship": "No",
    "located_in_us": "Yes",
    "willing_to_relocate": "Yes",
    "over_18": "Yes",
    "non_compete": "No",
    "gender": "Decline to self-identify",
    "race_ethnicity": "Decline to self-identify",
    "veteran_status": "Decline to self-identify",
    "disability_status": "I do not want to answer",
    "how_did_you_hear": "LinkedIn",
    "website_portfolio": "github.com/andrewaws26",
    "linkedin": "linkedin.com/in/andrewdsieg",
    "github": "github.com/andrewaws26",
}

# ATSes whose forms this system can fill and submit without a human step.
AUTO_SUBMIT_ATS = {"ashby", "greenhouse"}
# ATSes / signals that force a fill-and-park (captcha or irreducible human step).
PARK_ATS = {"lever", "workday"}


@dataclass
class SubmissionPlan:
    """The deterministic plan a Playwright agent executes for one application.

    ``action`` is either ``auto_submit`` (fill everything and submit) or
    ``fill_and_park`` (fill everything that does not need Andrew, then stop at the
    captcha / human step and log it). ``fields`` is the standard data to enter;
    ``screening_answers`` are the honest answers; ``human_step`` describes the one
    thing left for Andrew when parked. ``resume_path`` is the PDF to upload.
    """

    job: dict[str, Any]
    ats: str
    action: str
    resume_path: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    screening_answers: dict[str, str] = field(default_factory=dict)
    human_step: str | None = None
    rail_block: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_ats(job: Job) -> str:
    """Return the ATS name inferred from the apply URL (``ashby``/``greenhouse``/...)."""
    u = (job.url or "").lower()
    if "ashbyhq.com" in u:
        return "ashby"
    if "greenhouse.io" in u:
        return "greenhouse"
    if "lever.co" in u:
        return "lever"
    if "myworkdayjobs.com" in u or "workday" in u:
        return "workday"
    return job.ats.lower() or "unknown"


def _rail_block(job: Job) -> str | None:
    """Return a rail-violation reason if this job must not be applied to, else None."""
    slug = job.company_slug
    for co in RAILS.excluded_companies:
        if co.replace(" ", "") in slug:
            return f"excluded company / active track: {co}"
    blob = f"{job.title} {job.company} {job.location}".lower()
    for dom in RAILS.excluded_domains:
        if re.search(rf"\b{re.escape(dom)}\b", blob):
            return f"excluded domain: {dom}"
    return None


def _identity_fields(include_credentials: bool) -> dict[str, str]:
    """Assemble the identity/contact fields, pulling PII + creds from the env.

    Name is the one non-secret constant. Email/phone/address and the portal
    username/password come from ``JOBSEARCH_*`` env vars when present; if they are
    unset the field is simply omitted (the agent fills it from local memory), which
    is why nothing sensitive lives in this file.
    """
    import os

    fields = {"name": "Andrew Sieg"}
    for key, env in (
        ("email", "JOBSEARCH_APPLY_EMAIL"),
        ("phone", "JOBSEARCH_APPLY_PHONE"),
        ("location", "JOBSEARCH_APPLY_LOCATION"),
    ):
        v = os.environ.get(env)
        if v:
            fields[key] = v
    fields.update({k: STANDARD_ANSWERS[k] for k in ("website_portfolio", "linkedin", "github")})
    if include_credentials:
        try:
            fields["portal_username"] = get_credential("username")
            fields["portal_password"] = get_credential("password")
        except RuntimeError:
            pass  # creds not in env -> agent supplies from local memory
    return fields


def plan_submission(job: Job, resume_path: str = "", include_credentials: bool = False) -> SubmissionPlan:
    """Build the fill-and-park plan for a job without driving any browser.

    Decides ``auto_submit`` vs ``fill_and_park`` from the ATS, blocks rail
    violations up front (returns a plan with ``action='blocked'`` and a
    ``rail_block`` reason), and assembles the standard field values plus honest
    screening answers. The returned plan is what an agent hands to the Playwright
    MCP step by step.
    """
    ats = classify_ats(job)
    block = _rail_block(job)
    if block:
        return SubmissionPlan(
            job=job.to_dict(), ats=ats, action="blocked", rail_block=block,
            notes=[f"rail violation, do not apply: {block}"],
        )

    if ats in AUTO_SUBMIT_ATS:
        action, human = "auto_submit", None
    elif ats in PARK_ATS:
        action = "fill_and_park"
        human = ("captcha / hCaptcha (Lever)" if ats == "lever"
                 else "Workday date-spinbutton or account verification")
    else:
        action = "fill_and_park"
        human = "unknown ATS: fill everything fillable, park at any captcha/account wall"

    plan = SubmissionPlan(
        job=job.to_dict(),
        ats=ats,
        action=action,
        resume_path=resume_path,
        fields=_identity_fields(include_credentials),
        screening_answers={k: v for k, v in STANDARD_ANSWERS.items()
                           if k not in ("website_portfolio", "linkedin", "github")},
        human_step=human,
    )
    if ats == "ashby":
        plan.notes.append("Ashby labeled-radio gotcha: use locator.focus() then keyboard.press('Space').")
    if ats == "greenhouse":
        plan.notes.append("Greenhouse EEO numeric ids need [id=\"1101\"] attribute selectors; exact-label auth/sponsorship.")
    if action == "fill_and_park":
        plan.notes.append("Park one role at a time; log to NEEDS_YOUR_ATTENTION.md with the resume staged.")
    return plan
