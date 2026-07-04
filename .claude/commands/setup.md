---
description: Install claude-works and personalize the whole pipeline from example jobs + the user's resume
---

You are onboarding a new candidate onto this job-application system. When you
are done, the system must run for THEM: their policy, their search lanes,
their resume claims, their identity. Never leave them running on the repo's
default (someone else's) policy. Work autonomously; only stop to ask for the
inputs listed below and for confirmations that involve their personal facts.

## 1. Collect the two inputs

Ask the user for:

1. **Example jobs they want** (3-10 job posting URLs, or titles + companies).
   ATS URLs (Ashby/Greenhouse/Lever) are best; anything works.
2. **Their resume** (a file path, pasted text, or a LinkedIn export). Also ask
   for: name, email, phone, location, LinkedIn/GitHub/website URLs, whether
   they are authorized to work in the US and need sponsorship, their base
   compensation floor, any companies they must not apply to (current
   employer, active interview processes), and any domains they refuse
   (defense, gambling, whatever they name).

## 2. Install

```bash
pip install -e .        # from this clone; or: pip install claude-works
ruff check . && mypy && pytest   # confirm the install is healthy (all green)
mkdir -p ~/jobsearch-data
```

## 3. Derive the personalization (from their examples, not the defaults)

Fetch each example job's JD (`fetch_job_description` or WebFetch). From the
JDs + resume, derive and write:

- **`~/jobsearch-data/policy.json`** (start from `examples/policy.sample.json`):
  - `core_signals`: the stack terms that appear in BOTH their resume and the
    example JDs (their daily work).
  - `edge_signals`: rare differentiators from their resume (domain experience,
    unusual skill pairings) that few candidates share.
  - `lane_points`: the example jobs' title patterns, strongest first.
  - `overlevel_terms` / `level_ok_signals`: from their seniority.
  - `hard_gap_skills`: required-skill terms they lack (ask them to confirm).
  - `excluded_companies` / `excluded_domains` / `comp_floor`: from step 1.
  - `seed_boards`: the example jobs' org slugs plus 10-20 similar companies
    (same space, size, stack) that use Ashby/Greenhouse/Lever.
- **`~/jobsearch-data/SEARCH_ANGLES.md`** (format per `examples/SEARCH_ANGLES.md`):
  2-4 lanes distilled from the example jobs; mark the strongest `(PRIMARY /
  default lane)`.
- **`~/jobsearch-data/resumes/_genlib.py`**: copy `examples/resumes/_genlib.py`
  and replace ROLES + every fragment with entries built from THEIR resume.
  Each fragment must be a claim the user confirms is true; read the list back
  to them before writing it. Never invent, inflate, or extrapolate a claim.
  Copy `examples/resumes/_render.sh` alongside it (it uses Chrome when
  available).

## 4. Register the MCP stack

```bash
# The pipeline brain (this package)
claude mcp add claude-works \
  -e JOBSEARCH_DATA_DIR="$HOME/jobsearch-data" \
  -e JOBSEARCH_RESUMES_DIR="$HOME/jobsearch-data/resumes" \
  -e JOBSEARCH_APPLY_NAME="<name>" \
  -e JOBSEARCH_APPLY_EMAIL="<email>" \
  -e JOBSEARCH_APPLY_PHONE="<phone>" \
  -e JOBSEARCH_APPLY_LOCATION="<City, ST>" \
  -e JOBSEARCH_APPLY_LINKEDIN="<linkedin url>" \
  -e JOBSEARCH_APPLY_GITHUB="<github url>" \
  -- claude-works

# The hands (executes submission plans in a real browser)
claude mcp add playwright -- npx @playwright/mcp@latest

# The wide discovery net (1M+ indexed roles, free tier ~500 calls/day)
claude mcp add --transport http jobdatalake https://mcp.jobdatalake.com
```

Optional, for the emailed-verification-code gate (Greenhouse): have the user
create a revocable Gmail app password and set `JOBSEARCH_GMAIL_APP_PASSWORD`
in the claude-works env. Skip it if they prefer; those submits just park.

## 5. Smoke test (prove it works before handing over)

1. `list_search_angles` returns their lanes; `score_job` on one example job
   (with its JD text) scores high and on a Director title returns a hard cap.
2. `discover_jobs(source="boards")` returns live roles from their seeds.
3. `build_resume` from their fragments passes lint + verify; `render_resume`
   reports one page; deliberately add a false claim to a scratch build and
   confirm `verify_resume` FAILS it (then delete the scratch).
4. `submit_application` on an example job returns a plan (not a submission)
   with the ATS gotchas attached.
5. `record_application` + `ledger_summary` round-trip.

## 6. Hand over

Show the user: their policy.json (ask them to sanity-check the exclusions),
where the ledger lives, and how to run a session: "find and apply to jobs"
uses discover/score/curate to pick the best open role, builds + gates a
resume, executes the submission plan via Playwright per PLAYBOOK.md, and
records the outcome per OPERATING.md. Remind them: captchas and attestations
always park for them; nothing outbound is ever auto-sent; every resume claim
traces to fragments they approved.
