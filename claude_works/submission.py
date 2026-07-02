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
# (Workable: recaptcha is usually disabled. Hirebridge: completable after a
# re-type email gate, no emailed code.)
AUTO_SUBMIT_ATS = {"ashby", "greenhouse", "workable", "hirebridge"}
# ATSes / signals that force a fill-and-park (captcha or irreducible human step).
PARK_ATS = {"lever", "workday"}

# Hard-won, per-ATS form-handling tactics, accreted as the system learns a better
# way (the public mirror of the private ATS_PLAYBOOK.md). This is the "memory" of
# how each ATS behaves. APPEND here whenever a new gotcha is discovered.
ATS_GOTCHAS: dict[str, list[str]] = {
    "ashby": [
        "Ashby labeled-radio gotcha: use locator.focus() then keyboard.press('Space').",
        "Resume is the LAST input[type=file] (id _systemfield_resume); the first file input is autofill-from-resume.",
        "Location is a typeahead: type the city, then click the [role=option] matching 'City, State, Country'.",
        "Yes/No questions render as <button> with an _act class when selected; clicking an already-selected one TOGGLES IT OFF, so check state instead of re-clicking.",
        "Set those Yes/No buttons with a REAL pointer click, never a scripted element.click(): a scripted click sets the _act visual but not the React form value, so the field reads as a missing required field on submit; recover a mismatched one by clicking the opposite answer then the intended one.",
        "A remote flag can still hide 'N days/week in office' in the body; read the description before treating as remote.",
        "Trust the application-page header 'Location Type' field (Remote / Hybrid / Onsite) over the posting-api isRemote flag, which is unreliable: a posting can report isRemote=true while the page header reads Location Type=Hybrid for a specific city, so only a header of 'Remote' clears a remote-first filter.",
        "Headless JD screening: the per-job posting-api endpoint returns Unauthorized, but the board endpoint posting-api/job-board/<org> returns the whole board as {jobs:[...]}; filter by job id (a missing id means the posting is closed). Each job carries title, location, isRemote, secondaryLocations, compensation, applyUrl, and descriptionHtml, and secondaryLocations[].address country fields give a fast location screen before opening a browser.",
        "Some boards run server-side bot detection that rejects a fully-valid headless submit with an alert 'We couldn't submit your application. Your application submission was flagged as possible spam. Please submit your application again.' The form clears and re-submitting from the same automated browser gets flagged again, because it is fingerprinting the automation, not validating the data. Treat it as a robot wall, not a captcha to defeat: do not re-submit in a loop (repeat attempts look like the spam being blocked), park the application with the resume and answers prepared, and have a human submit once from an ordinary browser. This wall shows up across role types, including plain backend engineering postings, so do not assume a non-customer-facing role is exempt; the alert wording varies slightly (for example ending 'If you believe this was a mistake, please submit your application again.').",
        "On newer boards the selected Yes/No state is the class _active_* rather than _act, and an accessibility snapshot may only mark the last group active, so verify each group's selected class by reading the DOM rather than trusting the snapshot.",
    ],
    "greenhouse": [
        "Greenhouse EEO numeric ids need [id=\"1101\"] attribute selectors; match auth/sponsorship by exact label.",
        "Auto-submittable: upload the resume file input, then the standard fields, then submit.",
        "Screening + EEO dropdowns are React-Select comboboxes: get the combobox by name, click it, type the option, press Enter (type-and-Enter filters then selects).",
        "The phone Country React-Select is REQUIRED and the usual silent submit-blocker; if submit fails with an aria-invalid 'country', set it to United States and resubmit.",
        "After the resume uploads, Greenhouse REMOVES the file input and shows the filename near Resume/CV; do not treat the missing input as a failed upload or try to re-upload.",
        "An invisible reCAPTCHA badge does not block a legit submit; success is a redirect to a /confirmation 'Thank you for applying' page.",
        "Custom dropdowns are not always Yes/No: a consent question's only option may be literally 'I consent', so read the actual option text scoped to that field's own control rather than assuming, and note the always-present phone-country listbox pollutes a global option query.",
        "Some boards now gate the final submit behind an emailed 8-character human-verification code: after an otherwise-valid submit the form reveals an 'enter the code to confirm you're a human' field and disables Submit. This is an email-ownership check, not a captcha, so the default path is to read the code from the applicant's own inbox (scoped read-only IMAP, revocable app password), enter it, and finish the submit; only fill-and-park if no code is retrievable. It is intermittent and appears only after field validation passes, so a code prompt means the form was otherwise complete and correct, not a build failure.",
        "The emailed-code field can render as 8 separate single-char boxes (only the first is named): click the first box and type the full code so the widget auto-distributes one char per box. Submit stays disabled until all boxes fill, then re-enables; the code is case-sensitive and alphanumeric, so type it verbatim.",
        "Location and geo-autocomplete fields need real keystrokes: a single fill() sets the value but fires no lookup, so no option list appears; clear any prior fill() text first (focus, select-all, backspace) or slow typing APPENDS and corrupts the value.",
        "The Location (City) geocode options render as [class*=option] (e.g. 'City, State, Country'), NOT [role=option]; the [role=option] matches are the always-present phone-country listbox, so type the city slowly, wait about 3 seconds for the lookup, then click the option by EXACT text (getByText('City, State, Country', exact)).",
        "On company-branded career pages the whole form is inside an embedded iframe: locate the frame that holds the file inputs / comboboxes and operate within it, not the top page.",
        "A required cover letter with no attached file: click its 'enter manually' toggle to reveal a textarea, then type a genuine tailored letter into the VISIBLE textarea, excluding the hidden g-recaptcha-response textarea.",
    ],
    "lever": [
        "hCaptcha-walled: fill everything, then PARK at the captcha for the human.",
        "Resume: setInputFiles on the hidden input#resume-upload-input (do not click through the captcha overlay).",
        "Radios: set by clicking the input matched on its label text; the generic fill-form helper malforms non-boolean setChecked values.",
        "Required consent checkbox sits under the hCaptcha widget: once the challenge renders, the captcha iframe subtree intercepts pointer events and a normal click on the checkbox times out. Set it programmatically (checked=true, then dispatch input+change+click) before parking so the form is fully ready for the human.",
        "Lever auto-parses the uploaded resume and may auto-fill current location and current company from it; leave those unless wrong.",
    ],
    "workable": [
        "recaptcha is usually disabled, so usually auto-submittable; if an hCaptcha appears, park instead.",
        "Masked DATE inputs (MM/DD/YYYY) need sequential typing (pressSequentially), not a single fill().",
        "Address requires SELECTING a structured autocomplete suggestion; free text fails validation.",
    ],
    "hirebridge": [
        "Account email-gate first: enter the email, then RE-TYPE it to confirm (not an emailed code); proceeds to QuickApply.",
        "ASP.NET postback cascade: Country onchange reloads State options; set fields by stable element id and dispatch a change event.",
        "FormValidation.io gates Submit on real input events; after programmatic fills, revalidate the form, and when isValid() is true but the button stays disabled, clear its disabled attribute and click.",
    ],
    "workday": [
        "Account wall plus date-spinbuttons; fill what you can, then park for account verification and the date control.",
    ],
}

# Tactics that apply across every ATS.
GENERAL_GOTCHAS: list[str] = [
    "A remote flag is not proof: always read the JD body for an in-office requirement (for example '4 days/week').",
    "Phone fields can have a hidden raw value plus a formatted display variant; set both.",
    "EEO self-identify questions are declined; an acknowledgment 'type your full name' field takes the candidate name.",
    "Browser-driver tooling often sandboxes file uploads to an allowed root; stage the resume PDF inside an allowed directory before uploading or setInputFiles errors with 'outside allowed roots'.",
    "An emailed verification code is an email-ownership check (the applicant owns the inbox and authorizes the agent), distinct from a captcha: it can be completed by reading the code from the applicant's own inbox via a scoped, read-only IMAP reader authenticated with a revocable app password, then entering it. A captcha, an 'are you a robot' check, or a 'no AI was used' attestation is NOT this and is never bypassed: those are filled-and-parked for the human.",
]


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
    if "workable.com" in u:
        return "workable"
    if "hirebridge.com" in u:
        return "hirebridge"
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
    # Attach the accreted per-ATS tactics plus the cross-ATS ones (the system's
    # form-handling memory), so the agent driving Playwright knows the gotchas.
    plan.notes.extend(ATS_GOTCHAS.get(ats, []))
    plan.notes.extend(GENERAL_GOTCHAS)
    if action == "fill_and_park":
        plan.notes.append("Park one role at a time; log to NEEDS_YOUR_ATTENTION.md with the resume staged.")
    return plan
