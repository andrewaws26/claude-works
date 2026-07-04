# OPERATING.md - how an agent runs this system

The operating model for any agent (Claude Code or otherwise) driving this
pipeline for a candidate. The personal specifics (rails, scoring vocabulary,
lanes, identity) come from `policy.json` and the `JOBSEARCH_*` environment;
this file is the behavior that stays the same for everyone.

## The loop, one firing at a time

Each firing: check the queue, apply to the strongest open fit, record the real
outcome, replenish discovery only when the queue runs thin, repeat. Durable
state lives in the ledger and the queue, so a firing can stop cleanly at any
point (low tokens, rate limit) and the next one resumes exactly where it left
off.

1. **Queue-first gate.** If enough fit-scored, in-lane roles sit in the queue
   (todo), apply from the queue; run discovery only to replenish when the
   active pool drops low (~20). Discovery without application is motion, not
   progress.
2. **Curate before applying.** `curate_queue` parks poor fits with an
   auditable reason and ranks the rest; apply best-fit-first, not
   next-in-line.
3. **Read the JD before committing.** Titles under- and over-sell. Fetch it
   headlessly (`fetch_job_description`) and score with the text. Confirm the
   remote reality per PLAYBOOK.md's remote rule before spending a resume
   build.
4. **Verified resumes only (the 4-gate).** Build from the claims bank
   fragments; every resume must pass: render to exactly 1 page, style lint
   clean, anti-fabrication verify pass, plus your own check that every claim
   traces to the claims bank at the right level. Never submit a resume that
   fails a gate.
5. **Submit via the Playwright MCP,** guided by the plan from
   `submit_application` and PLAYBOOK.md. Verify success by URL/text, never by
   the click returning.
6. **Record the real outcome** (`record_application`) in the same firing:
   submitted, submitted-verified, deferred-captcha, skipped-rail,
   closed-expired. Recording skips is what lets the queue self-prune.
7. **Draft outreach for exceptional fits** (a founder/CTO note under 80
   words) as a file for the candidate to send. NEVER auto-send outbound
   communication of any kind.
8. **Deep-read QA every ~10th resume:** read the full text and confirm
   genuine quality and honesty beyond what the automated gates check.

## Hard walls: defer, never cross

Captchas (hCaptcha, reCAPTCHA challenges), "are you a robot" checks, "I did
not use AI" attestations, SSO/social-login walls, and knockout questions that
cannot be answered honestly. For each: fill everything fillable, stage the
resume, park with a note telling the human the one step left, and move on.
Emailed verification codes are the one gate that is NOT a wall (email-ownership
verification of the candidate's own application): auto-complete via
`fetch_verification_code` per PLAYBOOK.md.

Never fabricate a claim, answer a knockout dishonestly (answer truthfully and
let the chips fall, or skip the role), solve a captcha, sign a false
attestation, or report a submission that did not confirm.

## De-duplication (two layers, both on purpose)

- **Record layer: by ROLE.** The ledger and discovery de-dup on `role_key`
  (ATS + org + job id). The same company with a different role is allowed;
  real interviews come from second-role applications.
- **Curation layer: by COMPANY.** Queue triage parks roles at companies
  already in the ledger (matched by name slug AND by apply-URL org slug, which
  catches missing or differently spelled names). Check the URL org slug on any
  shortlist before spending JD fetches.

## Discovery

Any source is fair game; the filter is FIT, not channel.

- **Built-in `boards` source:** the public Ashby/Greenhouse/Lever posting APIs
  over the `seed_boards` orgs in `policy.json`. Free, keyless, works from a
  bare install.
- **JobDataLake MCP** (`claude mcp add --transport http jobdatalake
  https://mcp.jobdatalake.com`): 1M+ indexed roles with filters and vector
  similarity; the wide net. Feed its results to `score_job` /
  `curate_queue`; its free tier is ~500 calls/day, so sweep in batches.
- **Aggregators and WebSearch** for anything the APIs miss (HN Who-is-hiring
  threads, niche AI boards, VC portfolio pages).
- **"Dry" requires more than one dry source.** A seeded board sweep going dry
  is not market-dry: check a second, wider source before declaring a firing
  dry. Strong fits routinely sit in the wider aggregators while the seed list
  reads "0 new".
- **Self-improving filter:** when you skip a role, the reason is a pattern
  (a skip means more like it are queued). Add the learned pattern to the
  curation filters and re-curate so the queue prunes proactively. Patterns
  learned this way so far: model-training/RLHF-required, onsite/hybrid in the
  body, lead-duties behind an IC title, clearance, advanced-degree
  requirements, non-US regions in the title.

## Autonomous cadence (when run on a schedule)

- One firing at a time: guard with a lockfile so runs never overlap. Parallel
  instances must partition the work (for example by ATS) and serialize all
  tracker writes (the package's ledger writes are flock-locked for this).
- Adaptive cadence: dry firings are cheap but not free. After several
  consecutive dry firings, throttle the schedule; speed back up when fresh
  supply lands. The expensive work should only trigger on net-new roles.
- On macOS, schedule with a launchd LaunchAgent in the GUI session, not cron:
  cron-spawned processes cannot read the keychain credentials headless agents
  need.

## Continuous improvement (standing mandate)

The system must get better at its own job automatically, the way a person
remembers a shortcut once they find it. Every session, interactive or
scheduled:

1. A better way to fill or submit a form goes into `PLAYBOOK.md` AND (terse,
   generic form) into `submission.py`'s `ATS_GOTCHAS`, with tests green, in
   the same session it was learned.
2. A durable preference, lane, or rail learned from the candidate goes into
   `policy.json` or their search-angles doc.
3. A skip pattern goes into the curation filters, then re-curate.

Do not wait to be asked; improving the playbook is part of every session.
