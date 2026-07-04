# PLAYBOOK.md - the living operator manual for driving ATS forms

This is the accumulated field knowledge of the system: every lesson learned
driving real application forms with the Playwright MCP, sanitized of personal
data. It is a LIVING document. **Whenever you find a better way to fill or
submit a form, append it here and commit, in the same session.** Treat it like
muscle memory the system keeps so no future instance relearns a gotcha. The
machine-readable subset ships in `claude_works/submission.py` (`ATS_GOTCHAS`),
which every submission plan attaches; keep the two in sync (this file holds the
long-form detail, the code holds the terse per-plan notes).

Standard answers, identity, and credentials come from the environment and
`policy.json`, never from this file. See the README's configuration table.

## The remote rule (critical)

An ATS `isRemote=true` flag is NOT proof of remote. Boards set it while the JD
body says "N days/week in office." ALWAYS read the body for an in-office
requirement and an in-office knockout question before building anything.

- AUTHORITATIVE check on Ashby: the application-page header has a structured
  **"Location Type"** field that reads literally Remote / Hybrid / Onsite.
  Trust THIS over the posting-api `isRemote` flag, which is unreliable in both
  directions. Only a header of "Remote" clears a remote-first filter.
- A required knockout button like "Are you willing to join us in office
  Mon/Tue/Thu?" cannot be honestly answered Yes by a remote-only candidate:
  skip the role (record it with a rail reason), never answer dishonestly.
- A queue fit score can be inflated because it trusted the same bad flag, so
  re-screen on the live page before spending a resume build.

## Headless screening via the posting APIs (before opening any browser)

- Ashby: the per-job endpoint returns Unauthorized, but the BOARD endpoint
  `api.ashbyhq.com/posting-api/job-board/<org>?includeCompensation=true`
  returns the whole board as `{jobs:[...]}`; filter by job id. Each job carries
  title, location, isRemote, isListed, secondaryLocations, compensation,
  applyUrl, and descriptionHtml. Use `secondaryLocations[].address` country
  fields for a fast non-US screen.
- USER-AGENT REQUIRED: the Ashby posting API returns 403 for default
  urllib/curl user agents on every board; send a real browser UA header.
- BOARD-LEVEL 404 IS NOT "JOB CLOSED": a 404 on the whole board endpoint means
  the org DISABLED the public posting API, not that the role is gone; the live
  job page can still be open and submittable. The "missing from the board =
  closed" rule only applies when the board returns 200 with a jobs list. Before
  recording closed-expired, fetch the live job page and check its `<title>` (a
  live posting renders "Role @ Company"). JD fallback when the API is off: the
  job page HTML embeds `"descriptionHtml":"..."` plus locationName /
  workplaceType keys.
- Greenhouse: `boards-api.greenhouse.io/v1/boards/<org>/jobs/<id>` returns the
  JD (`content`), fresh and headless. Lever:
  `api.lever.co/v0/postings/<org>/<id>` returns descriptionPlain + lists.
- The `fetch_job_description` MCP tool wraps all three.

## Ashby (auto-submit)  jobs.ashbyhq.com/org/id/application

- Resume = the LAST `input[type=file]` (id `_systemfield_resume`); the FIRST
  file input is "autofill from resume". `setInputFiles` directly.
- Location is a typeahead combobox: type the city, wait, click the
  `[role=option]` matching "City, State, Country". A value that reads empty
  afterward means the option click missed; re-type and click from a fresh
  snapshot.
- Yes/No questions render as `<button>` (not radios) and get an `_act` class
  when selected (newer boards: `_active_*`). Clicking an already-selected
  button TOGGLES IT OFF. Check the selected class; only click if not selected.
- CRITICAL: set those Yes/No buttons with a REAL Playwright click, never
  `element.click()` inside `page.evaluate`. A JS click sets the visual state
  but NOT the React form value, so the field reads "missing required field" on
  submit. Recovery for a desynced button: real-click the opposite answer, then
  real-click the intended one, and verify exactly one selected class per
  question before submit. The a11y snapshot may only mark the LAST group
  active, so verify each group's class via the DOM, not the snapshot.
- Required TEXT inputs also want a real fill: a synthetic native-setter value
  can be silently dropped by React validation on required fields.
- Labeled radios that ignore label clicks, coordinate clicks, AND
  `.check({force})`: the reliable fix is `locator.focus()` then
  `keyboard.press('Space')` on the target radio input. Do not rename radio
  element ids (it breaks React tracking). For stubborn Yes/No buttons an
  OS-level coordinate click also works.
- Watch for a required "Exercise" / "shared URL" field and in-office knockouts.
- SPAM-FLAG WALL: some boards run server-side bot detection that rejects a
  fully-valid headless submit with an alert like "Your application submission
  was flagged as possible spam. Please submit your application again." The form
  clears; re-submitting from the same automated browser gets flagged again (it
  fingerprints the automation, not the data). This is a robot wall, NOT a
  captcha to defeat: do not hammer it. Fill-and-park with everything prepared
  and have the human submit once from an ordinary browser. Seen on plain
  backend roles too, so no role type is exempt; the alert wording varies.
- Success signal: literal text "successfully submitted".

## Greenhouse (auto-submit)  job-boards.greenhouse.io

- EEO fields use numeric ids: `[id="1101"]`-style attribute selectors. Match
  auth/sponsorship questions by EXACT label, never fuzzy (a fuzzy match once
  picked "No" for work authorization).
- Screening + EEO dropdowns are React-Select comboboxes: get the combobox by
  name, click to open, type the option, press Enter (type-and-Enter filters
  then selects).
- The phone "Country" React-Select is REQUIRED and the #1 silent
  submit-blocker; a submit failing with an aria-invalid "country" means set it
  and resubmit.
- After `setInputFiles`, Greenhouse REMOVES the file input and shows the
  filename near "Resume/CV": do not treat the missing input as a failed upload
  or try to re-upload.
- An invisible reCAPTCHA badge does not block a legit submit. Success = the
  `/confirmation` "Thank you for applying" redirect.
- Custom dropdowns are not always Yes/No: a consent question's only option may
  be literally "I consent" (typing "Yes" silently fails). Read the actual
  option text scoped to that field's own control; a global `[role=option]`
  query is polluted by the always-present phone-country listbox.
- A question that LOOKS like a Yes/No dropdown can be a plain text input:
  check the DOM, fill the word. Conditional questions appear/disappear based
  on earlier answers, so re-enumerate fields before the final verify.
- Location (City) and other geocode autocompletes need REAL keystrokes:
  `.fill()` sets the value but fires no lookup. Type slowly (~55-90ms delay),
  wait ~3s for results, then click the option by EXACT text ("City, State,
  Country"). These options render as `[class*=option]`, NOT `[role=option]`.
  If a prior `.fill()` left text, clear it first (focus, select-all,
  Backspace) or slow typing APPENDS and corrupts the value.
- Company-branded career pages embed the whole form in an IFRAME: find the
  frame holding the file inputs / comboboxes and operate within it. The
  `/embed/job_app?for=<org>&token=<jobid>` URL reaches the raw form directly
  for any posting wrapped in a marketing site.
- Required cover letter with no file: click "Enter manually" to reveal a
  textarea and type a genuine tailored letter into the VISIBLE textarea,
  excluding the hidden `g-recaptcha-response` one.
- Some boards expose file inputs with direct ids (`#resume`,
  `#cover_letter`): `setInputFiles` works without clicking anything.
- EXPORT-CONTROL / US-PERSON question (aerospace and defense-adjacent
  manufacturers, ITAR/EAR): a required React-Select asking about
  export-controlled information access with passport / green-card /
  protected-status / none-of-the-above options. It is a work-authorization
  attestation, NOT a security clearance, so an export-control line alone is
  not a clearance rail. Answer with the option that is true of the candidate;
  never guess.
- Long forms may add in-office-percentage, AI-policy, relocation,
  interviewed-before, and a 200-400 word "Why us?" essay: write a genuine one.
- EMAILED-CODE GATE: see "Emailed verification-code gates" below.

## Lever (fill-and-park, hCaptcha)  jobs.lever.co/org/id/apply

- hCaptcha is embedded: fill everything, then PARK for the human.
- Upload the resume FIRST: Lever's parser auto-fills name/email/phone/
  location/company from it, correctly. Leave the auto-filled values unless
  wrong.
- Resume input: the hidden `input#resume-upload-input` via `setInputFiles`
  (clicking the visible attach control is intercepted by the captcha overlay).
- Standard fields are plain `name=` inputs; custom questions live under
  `cards[<uuid>][fieldN]` names, so enumerate by name. EEO is native selects.
- The required consent checkbox sits under the hCaptcha widget: once the
  challenge renders, the captcha iframe subtree intercepts pointer events and
  a normal click times out. Set it programmatically (checked=true, dispatch
  input+change+click) before parking so the human only solves the captcha and
  clicks Submit.
- The captcha is risk-based per submission: some submits pass with zero
  challenge, and a submission can LAND even after a puzzle pops (a re-submit
  returning "application already received" is the proof). After a challenge,
  verify with a fresh submit attempt before assuming the application is
  blocked.
- Success = the `/thanks` redirect.

## Workable (usually auto-submit)  apply.workable.com/org/j/id/apply

- reCAPTCHA is usually disabled, so usually auto-submittable; if an hCaptcha
  appears, park instead.
- Masked DATE inputs (MM/DD/YYYY) IGNORE `.fill()`: sequential typing
  (`pressSequentially`) only. This is the #1 silent submit-blocker here.
- Address requires SELECTING a structured autocomplete suggestion; free text
  leaves it aria-invalid and blocks submit.
- Success = the URL gains `?success`.

## Hirebridge (auto-submit after an email gate)  recruit.hirebridge.com

- The apply link lands on a login page: enter the email, then RE-TYPE it to
  confirm (not an emailed code), which proceeds to QuickApply.
- ASP.NET WebForms: the Country select's onchange does a `__doPostBack` that
  reloads the State options and shifts every element ref. Set fields by stable
  element id via evaluate and dispatch input+change; do Country first.
- Phone has a hidden raw field plus a formatted display variant; set both.
- Submit is gated by FormValidation.io, which tracks validity on REAL input
  events: after programmatic fills, revalidate the form; when it reports valid
  but the button stays disabled, clear the disabled attribute and click.
- Success = the "Application Submitted" acknowledgment page.

## Gem (fill-and-park, hCaptcha)  jobs.gem.com

- No field ids or labels: map inputs by their visually preceding label and
  fill by index. File dropzones: JS-click the hidden `input[type=file]`.
- Prefer "Apply without saving" over the account-creating submit button.
- Gates submissions behind hCaptcha shape puzzles; treat it as a
  human-in-the-loop checkpoint, never a thing to beat.

## Workday / UltiPro / SuccessFactors / Taleo / Oracle / iCIMS (account walls)

- These need an account and often email verification. Create the account where
  possible (per-tenant; save which tenants have accounts), fill what you can,
  and defer with the resume staged.
- Workday resume autofill parses BADLY (it has put a city into the name
  fields): always re-verify the My Information page after autofill.
- Workday dropdowns are `button[aria-haspopup=listbox]` then `[role=option]`;
  date fields are spinbuttons a human usually must set; "How did you hear" is
  a two-level menu. It is an 8-step wizard; the Review step is the natural
  verification checkpoint.

## Emailed verification-code gates (auto-complete, in-rails)

Some ATSes (Greenhouse especially, against headless sessions) gate the final
submit behind a short code emailed to the applicant. This is email-OWNERSHIP
verification of the applicant's own application, NOT a captcha, so reading the
code from the applicant's own inbox (scoped, read-only, revocable app password)
and finishing the submit is inside the honesty rails. The
`fetch_verification_code` MCP tool implements it.

- Order of operations: submit, wait ~8s, fetch the code (retry up to 3x with
  ~8s waits; mail can be in transit), enter it, finish, verify /confirmation.
  Park only on NO_CODE_FOUND / NO_CREDENTIALS after retries.
- The gate appears ONLY after a clean field-validation pass, so seeing the
  code boxes is positive confirmation the form was otherwise complete.
- Box mechanics: the field can render as 8 separate single-char boxes (only
  the first has an accessible name). Click the first box and type the full
  code; the widget auto-distributes one char per box. Submit stays disabled
  until all boxes fill, then re-enables. The code is case-sensitive
  alphanumeric; type it verbatim.
- The gate is session-bound anti-bot: a headless fire that triggers it may not
  see it at all from an interactive browser session.
- STILL never bypass: hCaptcha, "are you a robot", or "no AI was used"
  attestations. Those always park for the human.

## General submit-debug checklist (any ATS)

1. After filling, look for inline errors and any `aria-invalid="true"` field;
   that one field is usually the blocker (often a date or autocomplete).
2. If Submit is disabled but everything looks filled, the client validator did
   not register programmatic fills: revalidate, then force-enable and click
   only if the validator reports valid.
3. Verify success by URL or text (`/thanks`, `?success`, `/confirmation`,
   "Application Submitted"), never by the click returning.
4. Record the real outcome to the ledger; never report a submit that did not
   confirm.

## Cross-cutting operational traps

- Coordinates trap: the browser viewport width differs from the screenshot
  render width (scale factor ~1.26). Never click at screenshot pixel
  coordinates; screenshot the target element (1:1) or use locators.
- Tab fragility: the Playwright MCP browser can reset tabs between operations.
  Do not park filled-but-unsubmitted forms in background tabs expecting them
  to survive; finish or hand off promptly and record state the moment a form
  reaches "ready".
- File-upload sandbox: browser-driver tooling restricts uploads to allowed
  roots. Stage the resume PDF inside a gitignored allowed directory; a PDF
  copied to the repo root can get committed as PII on the next push. Remove
  any stray copy before committing.
- A `browser_snapshot` with a `filename` saves to the CWD (not a scratch dir),
  so it can get committed by the next push, same risk as a stray PDF: omit the
  filename or delete it before recording.
- Phone fields can have a hidden raw value plus a formatted display variant;
  set both.
- An acknowledgment "type your full name" field takes the candidate's name; it
  is a signature, not a screening question.

## Queue-screening lessons

- Dedupe by URL org slug BEFORE spending JD fetches: queue rows whose display
  text parses to company "?" (or a name spelled differently from the ledger)
  sail past name-based dedup. The ATS URL org is the authoritative company
  identity; `curate_queue` applies this, and a shortlist should re-check it in
  case applications landed after the last curation.
- Role families that share one JD across many location variants (the same
  title posted per-city) need ONE JD read, not one per variant; a knockout in
  the shared JD parks the whole family.
- Domain-specific hard requirements hide behind agent-flavored titles (for
  example deep EHR/FHIR integration experience behind an "AI deployment
  architect" title): the JD's must-have list decides, not the title.
