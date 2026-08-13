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
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import RAILS, get_credential
from .discovery import excluded_company_match
from .models import Job

# Standard, honest answers to common screening questions (AUTHORIZATIONS.md).
# These are policy, not secrets, and they contain no PII: every identity field
# (name, email, links) is pulled from the environment at call time, never
# hard-coded here.
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
}

# ATSes whose forms this system can fill and submit without a human step.
# (Workable: recaptcha is usually disabled. Hirebridge: completable after a
# re-type email gate, no emailed code.)
AUTO_SUBMIT_ATS = {"ashby", "greenhouse", "workable", "hirebridge"}
# ATSes / signals that force a fill-and-park (captcha or irreducible human step).
PARK_ATS = {"lever", "workday", "gem", "icims", "rippling"}

# Hard-won, per-ATS form-handling tactics, accreted as the system learns a better
# way (the public mirror of the private ATS_PLAYBOOK.md). This is the "memory" of
# how each ATS behaves. APPEND here whenever a new gotcha is discovered.
ATS_GOTCHAS: dict[str, list[str]] = {
    "ashby": [
        "Ashby labeled-radio gotcha: use locator.focus() then keyboard.press('Space').",
        "Resume is the LAST input[type=file] (id _systemfield_resume); the first file input is autofill-from-resume.",
        "Uploading the resume can trigger an autofill-parse re-render that WIPES already-typed text fields (Name/Email/free-text answers), so upload the resume FIRST, fill text fields after, and re-verify every input value right before submit; a 'Missing entry for required field' error on a field you filled means the parse wiped it.",
        "Location is a typeahead: type the city, then click the [role=option] matching 'City, State, Country'.",
        "Yes/No questions render as <button> with an _act class when selected; clicking an already-selected one TOGGLES IT OFF, so check state instead of re-clicking.",
        "Set those Yes/No buttons with a REAL pointer click, never a scripted element.click(): a scripted click sets the _act visual but not the React form value, so the field reads as a missing required field on submit; recover a mismatched one by clicking the opposite answer then the intended one.",
        "A remote flag can still hide 'N days/week in office' in the body; read the description before treating as remote.",
        "Trust the application-page header 'Location Type' field (Remote / Hybrid / Onsite) over the posting-api isRemote flag, which is unreliable: a posting can report isRemote=true while the page header reads Location Type=Hybrid for a specific city, so only a header of 'Remote' clears a remote-first filter.",
        "Headless JD screening: the per-job posting-api endpoint returns Unauthorized, but the board endpoint posting-api/job-board/<org> returns the whole board as {jobs:[...]}; filter by job id (a missing id means the posting is closed). Each job carries title, location, isRemote, secondaryLocations, compensation, applyUrl, and descriptionHtml, and secondaryLocations[].address country fields give a fast location screen before opening a browser.",
        "The posting API rejects default library user agents (urllib/curl) with 403 Forbidden on every board; send a real browser User-Agent header and the same request succeeds.",
        "A 404 on the whole board endpoint means the org DISABLED the public posting API, not that the role closed: the live job page can still be open and submittable. The missing-id-means-closed rule only applies when the board returns 200 with a jobs list; before recording a role as closed, fetch the live job page and check its title tag (a live posting renders 'Role @ Company'). When the API is off, the job page HTML embeds descriptionHtml plus locationName/workplaceType keys to screen from.",
        "Some boards run server-side bot detection that rejects a fully-valid headless submit with an alert 'We couldn't submit your application. Your application submission was flagged as possible spam. Please submit your application again.' The form clears and re-submitting from the same automated browser gets flagged again, because it is fingerprinting the automation, not validating the data. Treat it as a robot wall, not a captcha to defeat: do not re-submit in a loop (repeat attempts look like the spam being blocked), park the application with the resume and answers prepared, and have a human submit once from an ordinary browser. This wall shows up across role types, including plain backend engineering postings, so do not assume a non-customer-facing role is exempt; the alert wording varies slightly (for example ending 'If you believe this was a mistake, please submit your application again.'). Form complexity is not a predictor either: boards have walled everything from long multi-essay forms down to a minimal name-plus-email-plus-resume form, so always verify the filled state programmatically before the first submit and be ready to park.",
        "On newer boards the selected Yes/No state is the class _active_* rather than _act, and an accessibility snapshot may only mark the last group active, so verify each group's selected class by reading the DOM rather than trusting the snapshot.",
        "Required TEXT inputs also want a real fill: a synthetic native-setter value can be silently dropped by React validation on required fields (a LinkedIn field has silently lost its value this way).",
        "For a stubborn Yes/No button that resists trusted clicks, an OS-level coordinate click on the element works; never rename radio element ids to force state (it breaks React tracking).",
        "Success signal is the literal text 'successfully submitted'.",
        "Some boards enforce per-org application limits and say so in a banner on the form (for example: at most N applications per 90 days across all jobs, and no re-apply to the same role within a year without an offer). When policy allows repeat applications to different roles at the same company, count that org's recent submissions before applying and skip if at the cap.",
        "Not every board uses the Yes/No <button> pattern: some render real input[type=radio] elements, where a normal pointer click works and checked state verifies via input[type=radio]:checked. When verifying filled text fields via the DOM, remember phone is type=tel and email is type=email, so an input[type=text] query misses them; query those types explicitly.",
        "On wall-capable boards, save any long-form answer text to a local file before clicking submit: the spam wall clears the form, and a park is only cheap for the human if the prepared essay answers survive somewhere pasteable.",
        "An org whose board spam-walled an automated submit once stays walled on later postings too: when a target org already has a spam-wall park on record, expect another park and budget the attempt accordingly (the fill is still worth doing, because a prepared park makes the human's manual submit take about two minutes).",
        "Do not pointer-click the raw resume file input even after exposing it: an instructions overlay intercepts the click, and a broken file-chooser sequence can blank the tab and lose the entire fill. Click the resume section's visible upload button (with a fresh element reference, since references go stale after fills) and immediately answer the real file chooser with the file path. Upload the resume before any text fills so a chooser mishap costs nothing to redo.",
        "A queued job id missing from a 200 board response is not always closed: orgs delete and re-post the same role under a fresh id. Before recording closed, scan the board's jobs list for the same title (same location/department); if present under a new id, re-screen that description (it can change) and apply to the new id, noting the id swap so email-based dedupe still matches.",
        "The tab can die to a blank page mid-fill even without a file-chooser mishap (observed after a location-typeahead option click). A cascade of stale element references is the tell: on repeated stale-reference errors, check the page URL immediately instead of hunting fresh references; selector errors on a dead page read like syntax problems when the real problem is that there is no page. Recovery is re-navigate and refill in one clean pass.",
        "Re-render-safe fill order: upload the resume first, then do every button/radio/checkbox click back-to-back (references from one fresh snapshot stay valid across many consecutive clicks if no combobox is touched between them), then plain text fills, then typeahead comboboxes LAST since each option-click blur re-renders the form and stales every reference; verify the whole form via the DOM, then re-find the submit button fresh and submit once.",
    ],
    "greenhouse": [
        "The boards API (boards-api.greenhouse.io/v1/boards/<org>/jobs/<id>) is a free liveness oracle: closed or removed postings return a 404 JSON body while open ones return the full JD, so bulk-check aging queue batches there before spending browser sessions; sweep-sourced queues can go majority-stale within weeks.",
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
        "A question that LOOKS like a Yes/No dropdown can be a plain text input; check the DOM and fill the word. Conditional questions appear/disappear based on earlier answers, so re-enumerate fields before the final verify.",
        "The /embed/job_app?for=<org>&token=<jobid> URL reaches the raw form directly when a posting redirects to a marketing-site wrapper; some boards expose file inputs with direct ids (#resume, #cover_letter) where setInputFiles works without clicking.",
        "Export-control / US-person questions (ITAR/EAR manufacturers) are work-authorization attestations, not clearance requirements: answer with the option that is true of the candidate, never 'None of the above' by default and never a guess.",
        "Match auth/sponsorship by EXACT label, never fuzzy: a fuzzy match has wrongly selected 'No' for work authorization.",
        "The NEW remix-style embed (remix-css classes; the form's FormData serializes EMPTY because all state is React-side) will NOT commit a React-Select from fill()+Enter: use real keystrokes (click, type character-by-character, wait, Enter), and read the aria-live [role=log] beside each select ('<option>, N of M' / '<option> selected') to verify focus and commit.",
        "CRITICAL remix-embed trap: if a submit fails while any react-select is empty, that field is flagged invalid and every later value you commit to it (clicks, keyboard, synthetic events - all of them, even when the selected text visibly renders) REVERTS on each subsequent submit. The field cannot be repaired in place: reload the job page, refill the ENTIRE form in one clean pass (formerly-flagged selects first), and submit exactly once. Corollary: fill everything, especially phone Country, BEFORE the first submit attempt.",
        "Remix-embed option text differs from classic boards (gender options are 'Man'/'Woman', not 'Male'; source options like 'LinkedIn Jobs'), and the cover letter can be FILE-ONLY: the 'enter manually' control may be a button that never reveals a textarea, so write the note to a .txt and setInputFiles it into the cover-letter file input. But option wording ALSO varies per board within the remix style (some remix boards keep the classic 'Male'/'Female' wording), so read the actual option list instead of assuming either vocabulary.",
        "On the remix embed the stable id #country is the PHONE dial-code react-select, not an address country: its options read like 'United States +1' and the committed value displays as just '+1'. Verify it by opening the option list once; do not wait for a country-name display that never appears.",
        "The mid-fill tab-death failure (page dies to about:blank and every element lookup goes stale) happens on the remix embed too, typically around the geocode typeahead. Check the page URL as soon as lookups cascade-fail; recovery is re-navigate and refill the ENTIRE form in one clean pass with the geocode typeahead LAST, verify programmatically, then submit once.",
        "A US boards-api 404 is NOT proof a role is closed when the posting page redirects to a company careers wrapper: the org may live on EU Greenhouse (boards.eu.greenhouse.io), which the US boards-api cannot see (and boards-api.eu.greenhouse.io does not resolve). Check the wrapper HTML for boards.eu.greenhouse.io links first; the same role may be live there under a NEW job id (a repost). The EU form is reachable at job-boards.eu.greenhouse.io/embed/job_app?for=<org>&token=<id> with no validity token and is the remix style.",
        "An HTTP 428 on the submit POST is the emailed-code gate announcing itself, not an error: the form re-renders with the 8-box security-code group and a disabled Submit. Proceed with the standard inbox-read flow; after typing the code, re-find the Submit button fresh because the pre-gate element reference goes stale (on some boards the old reference happens to survive, but a fresh find is always safe).",
        "Some boards run a POST-SUBMIT identity-verification step: the description says a separate email with a unique verification link (a third-party identity service, sometimes biometric) will arrive after applying. It is not a form gate, so the automated submit still completes to /confirmation, but the application is not fully processed until the HUMAN applicant completes that emailed step, which is theirs alone to do. When a description mentions an identity-verification email, still submit, then flag the pending human step in the outcome record so it surfaces instead of the application silently stalling.",
    ],
    "lever": [
        "hCaptcha-walled: fill everything, then PARK at the captcha for the human.",
        "Resume: setInputFiles on the hidden input#resume-upload-input (do not click through the captcha overlay).",
        "Radios: set by clicking the input matched on its label text; the generic fill-form helper malforms non-boolean setChecked values.",
        "Required consent checkbox sits under the hCaptcha widget: once the challenge renders, the captcha iframe subtree intercepts pointer events and a normal click on the checkbox times out. Set it programmatically (checked=true, then dispatch input+change+click) before parking so the form is fully ready for the human.",
        "Lever auto-parses the uploaded resume and may auto-fill current location and current company from it; leave those unless wrong. Upload the resume FIRST so the parser fills the standard fields for you.",
        "Custom questions live under cards[<uuid>][fieldN] input names, so enumerate by name; EEO is native selects.",
        "The captcha is risk-based per submission: a submission can land even after a challenge pops (a re-submit returning 'application already received' proves it), so verify with a fresh submit attempt before assuming a blocked application.",
    ],
    "workable": [
        "recaptcha is usually disabled, so usually auto-submittable; if an hCaptcha appears, park instead.",
        "Masked DATE inputs (MM/DD/YYYY) need sequential typing (pressSequentially), not a single fill().",
        "Address requires SELECTING a structured autocomplete suggestion; free text fails validation. Some orgs render a plain free-text address instead: if no suggestion listbox appears after slow typing, free text is accepted, so do not wait on one.",
        "Some orgs gate the submit POST behind invisible Cloudflare Turnstile: the button sticks on Submitting..., the application POST never fires, and the Turnstile pat request 401s. It will not pass headlessly and a reload retry hits the same wall; it is a robot check, so fill-and-park. Detect early by checking the network log for challenges.cloudflare.com/turnstile.",
        "The whole form (fields, radios, uploaded-resume ref) persists in localStorage per browser profile, so a reload restores everything (accept the beforeunload dialog). When parking, export the long-form answers to a file; the human's own browser starts empty.",
        "Question textareas can carry a tiny maxlength and fill() silently truncates mid-word at the cap; re-read value.length after filling and rewrite a complete answer that fits.",
    ],
    "hirebridge": [
        "Account email-gate first: enter the email, then RE-TYPE it to confirm (not an emailed code); proceeds to QuickApply.",
        "ASP.NET postback cascade: Country onchange reloads State options; set fields by stable element id and dispatch a change event.",
        "FormValidation.io gates Submit on real input events; after programmatic fills, revalidate the form, and when isValid() is true but the button stays disabled, clear its disabled attribute and click.",
    ],
    "workday": [
        "Account wall plus date-spinbuttons; fill what you can, then park for account verification and the date control.",
        "Account creation is per-tenant (save which tenants have accounts); some tenants skip email verification entirely.",
        "Resume autofill parses BADLY (it has put a city into the name fields): always re-verify the My Information page after autofill.",
        "Dropdowns are button[aria-haspopup=listbox] then [role=option]; 'How did you hear' is a two-level menu; it is an 8-step wizard and the Review step is the natural verification checkpoint.",
    ],
    "gem": [
        "hCaptcha shape-puzzle wall: fill everything, then PARK for the human; never attempt the puzzle.",
        "No field ids or labels: map inputs by their visually preceding label and fill by index; file dropzones need a JS click on the hidden input[type=file].",
        "Prefer the 'Apply without saving' submit over the account-creating one.",
    ],
    "icims": [
        "Account wall with email verification; create the account where possible, fill what you can, park with the resume staged.",
    ],
    "breezy": [
        "Fully headless-submittable: no captcha, no robot wall, no account. The apply form lives at the posting URL plus /apply; success is a redirect to /apply/submitted with an 'Application Submitted' heading.",
        "Resume-parse autofill: click the Upload Resume link (a real file chooser opens; answer it with the file path), wait several seconds, and the parse fills Work History, Education, and the experience summary from the PDF, but NOT the personal details (name/email/phone/address), so fill those yourself after.",
        "The parse garbles fields, so verify and fix before submit: it can put a parenthetical into the Company input (losing the real employer name), rewrite ampersands as 'AND' in titles, and split PDF ligatures in summaries ('traffi c', 'verifi able'). Inputs take a real fill; textarea summaries accept a programmatic value set plus a dispatched input event (the form is AngularJS).",
        "Date inputs inside work-history rows share the same placeholder as the Company field, so a verify-by-placeholder pass will show dates under 'Company'; expect it.",
        "HONEYPOT: a hidden unlabeled text input (name like hp_XXXX) sits before the submit button; leave it empty, filling it flags the submission as a bot.",
        "An optional SMS-consent checkbox under the phone field is not required for submit; leave it unchecked unless consent is intended.",
        "Work History and Education are required sections but the resume parse satisfies them; education dates may stay empty. The cover-letter textarea is name=cCoverLetter.",
    ],
    "rippling": [
        "No account needed: Apply opens a single-page form; resume-parse autofill is excellent, so upload the resume FIRST and it fills name/email/phone/location/link/company, leaving only the dropdowns.",
        "Dropdowns (visa question, EEO fields) are custom comboboxes: click the combobox, options render inside a dialog listbox, click the option by text; values verify via the combobox display text and its search input value, and the Apply button enables only when required fields are set.",
        "The submit POST is gated behind invisible Cloudflare Turnstile: clicking Apply disables every field, the Turnstile pat request 401s headlessly, and the application POST never appears in the network log (the page can even blank out after a while). Same wall signature as the Workable variant: it is a robot check, never bypassed, so fill-and-park after ONE clean network-log-confirmed attempt, and do not hammer retries.",
        "The form does not persist for the human's own browser, so a park must list every answer; the parse autofill makes the manual redo about two minutes.",
        "Element references go stale after option clicks and can silently re-resolve to a different element whose click times out on 'subtree intercepts pointer events'; re-snapshot scoped to the submit button's test id for a fresh reference before clicking.",
    ],
}

# Tactics that apply across every ATS.
GENERAL_GOTCHAS: list[str] = [
    "A remote flag is not proof: always read the JD body for an in-office requirement (for example '4 days/week').",
    "Phone fields can have a hidden raw value plus a formatted display variant; set both.",
    "EEO self-identify questions are declined; an acknowledgment 'type your full name' field takes the candidate name.",
    "Browser-driver tooling often sandboxes file uploads to an allowed root; stage the resume PDF inside an allowed directory before uploading or setInputFiles errors with 'outside allowed roots'.",
    "An emailed verification code is an email-ownership check (the applicant owns the inbox and authorizes the agent), distinct from a captcha: it can be completed by reading the code from the applicant's own inbox via a scoped, read-only IMAP reader authenticated with a revocable app password, then entering it (the fetch_verification_code tool). A captcha, an 'are you a robot' check, or a 'no AI was used' attestation is NOT this and is never bypassed: those are filled-and-parked for the human.",
    "Coordinates trap: the browser viewport width differs from the screenshot render width (~1.26x); never click at screenshot pixel coordinates, use locators or an element screenshot (1:1).",
    "Tab fragility: the driven browser can reset tabs between operations; never park a filled-but-unsubmitted form in a background tab, finish or record state promptly.",
    "Verify success by URL or page text (/thanks, ?success, /confirmation, 'Application Submitted'), never by the submit click returning.",
    "After filling, hunt for any aria-invalid=true field; that one field (usually a date or autocomplete) is the silent submit-blocker.",
]


@dataclass
class SubmissionPlan:
    """The deterministic plan a Playwright agent executes for one application.

    ``action`` is either ``auto_submit`` (fill everything and submit) or
    ``fill_and_park`` (fill everything that does not need the candidate, then stop at the
    captcha / human step and log it). ``fields`` is the standard data to enter;
    ``screening_answers`` are the honest answers; ``human_step`` describes the one
    thing left for the candidate when parked. ``resume_path`` is the PDF to upload.
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
    if "jobs.gem.com" in u or "gem.com" in u:
        return "gem"
    if "icims.com" in u:
        return "icims"
    if "ats.rippling.com" in u:
        return "rippling"
    if "breezy.hr" in u:
        return "breezy"
    return job.ats.lower() or "unknown"


def _rail_block(job: Job) -> str | None:
    """Return a rail-violation reason if this job must not be applied to, else None."""
    if (co := excluded_company_match(job)) is not None:
        return f"excluded company / active track: {co}"
    blob = f"{job.title} {job.company} {job.location}".lower()
    for dom in RAILS.excluded_domains:
        if re.search(rf"\b{re.escape(dom)}\b", blob):
            return f"excluded domain: {dom}"
    return None


def _identity_fields(include_credentials: bool) -> dict[str, str]:
    """Assemble the identity/contact fields, pulling PII + creds from the env.

    Every identity field, the name included, comes from ``JOBSEARCH_*`` env vars
    when present; an unset one is simply omitted (the agent fills it from local
    memory). That is what keeps this public file free of anyone's PII.
    """
    import os

    fields: dict[str, str] = {}
    for key, env in (
        ("name", "JOBSEARCH_APPLY_NAME"),
        ("email", "JOBSEARCH_APPLY_EMAIL"),
        ("phone", "JOBSEARCH_APPLY_PHONE"),
        ("location", "JOBSEARCH_APPLY_LOCATION"),
        ("website_portfolio", "JOBSEARCH_APPLY_WEBSITE"),
        ("linkedin", "JOBSEARCH_APPLY_LINKEDIN"),
        ("github", "JOBSEARCH_APPLY_GITHUB"),
    ):
        v = os.environ.get(env)
        if v:
            fields[key] = v
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
        human = {
            "lever": "captcha / hCaptcha (Lever)",
            "gem": "hCaptcha shape puzzle (Gem)",
            "icims": "account creation / email verification (iCIMS)",
        }.get(ats, "Workday date-spinbutton or account verification")
    else:
        action = "fill_and_park"
        human = "unknown ATS: fill everything fillable, park at any captcha/account wall"

    plan = SubmissionPlan(
        job=job.to_dict(),
        ats=ats,
        action=action,
        resume_path=resume_path,
        fields=_identity_fields(include_credentials),
        screening_answers=dict(STANDARD_ANSWERS),
        human_step=human,
    )
    # Attach the accreted per-ATS tactics plus the cross-ATS ones (the system's
    # form-handling memory), so the agent driving Playwright knows the gotchas.
    plan.notes.extend(ATS_GOTCHAS.get(ats, []))
    plan.notes.extend(GENERAL_GOTCHAS)
    if action == "fill_and_park":
        plan.notes.append("Park one role at a time; log to NEEDS_YOUR_ATTENTION.md with the resume staged.")
    return plan
