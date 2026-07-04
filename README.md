# Claude Works

[![CI](https://github.com/andrewaws26/claude-works/actions/workflows/ci.yml/badge.svg)](https://github.com/andrewaws26/claude-works/actions/workflows/ci.yml)

An MCP server that turns a perpetual, fit-first job-application pipeline into typed, honest tools an LLM agent can call.

## What it is

Claude Works is a [Model Context Protocol](https://modelcontextprotocol.io) server built on FastMCP. It exposes a working, autonomous job-application loop (discovery sweeps, fit scoring, a verified resume builder, a fill-and-park submission planner, and an application ledger) as a small set of typed tools.

The point is not to wrap a chatbot around a job board. The point is to put the policy where it cannot drift: in code. An agent calling these tools cannot quietly inflate a fit score, claim a resume passed gates it never ran, or report a submission that did not happen. The honesty lives in the modules, not in a system prompt that the next conversation might forget.

The core (everything except the FastMCP wiring) imports with the standard library only. That keeps the domain logic fast to test, easy to read, and reusable outside the MCP runtime.

## Try it in two minutes

The repo ships a [demo mode](./examples/README.md): sanitized sample data for a fictional persona, shaped exactly like the private production files, so every tool works from a fresh clone with zero network access.

```bash
git clone https://github.com/andrewaws26/claude-works.git
cd claude-works
pip install -e .

claude mcp add claude-works \
  -e JOBSEARCH_DATA_DIR="$PWD/examples" \
  -e JOBSEARCH_RESUMES_DIR="$PWD/examples/resumes" \
  -- python -m claude_works
```

Then ask Claude to score a role. This is what "honesty in the modules" looks like on the wire; the agent cannot argue with it:

```jsonc
// score_job(title="Principal Engineer, ML Infrastructure", company="Massive Scale Co", ...)
{
  "value": 5.0,
  "pursue": false,
  "reasons": ["hard cap: over-level title ('principal')"],
  "hard_cap": "over-level title ('principal')"
}

// score_job(title="Forward Deployed Engineer", ..., jd_text="...agentic workflows on
//           Claude with MCP tools, own evals, ... first technical hire.")
{
  "value": 8.5,
  "pursue": true,
  "reasons": [
    "core-stack overlap: agent, agentic, claude, eval, mcp (+4.0)",
    "rare-edge match: forward deployed (+1.0)",
    "level fit: mid / IC / first-hire (+2.0)",
    "clean channel: Ashby/Greenhouse autonomous-submit (+1.0)",
    "remote (+0.5)"
  ],
  "hard_cap": null
}
```

The [examples README](./examples/README.md) walks through the rest: the offline `demo` discovery source, queue curation with auditable park reasons, and the resume gates (try sneaking `"Holds a PhD"` into the demo persona's summary and watch `verify_ok` flip to false).

## Architecture

![The application loop: 1 sweep the public ATS posting APIs, 2 score fresh jobs 0-10 under the policy.json rails and hard caps, 3 triage the queue (fit-rank or park), 4 take the best match, 5 build a resume from verified claims through 4 fail-closed gates, 6 execute the fill-and-park submit plan (Playwright drives the browser; IMAP handles email codes, never captchas), 7 record to the append-only ledger. The agent only makes typed tool calls; JobDataLake is the optional wide net.](https://raw.githubusercontent.com/andrewaws26/claude-works/main/docs/architecture.png?v=2)

The system is a five-stage pipeline. Each stage is a frozen or plain `@dataclass` defined in `models.py`, and each crosses the MCP boundary as a JSON-serializable dict:

```
SearchAngle  ->  Job  ->  Score  ->  Resume  ->  Application
```

- **SearchAngle** is one reusable search lens (FDE, IoT, and so on). It biases discovery and scoring toward a lane without changing the rules.
- **Job** is a single discovered role, normalized across every source. Its `role_key` (ATS plus org slug plus job id, parsed from the apply URL) is the canonical identity used for de-duplication. De-dup is by role, never by company, so the same company with a different role is allowed.
- **Score** is the fit-rubric verdict for a job: a value from 0 to 10, a `pursue` boolean, one reason line per signal, and a `hard_cap` field set when a disqualifying signal applies.
- **Resume** is a built artifact plus the verdicts of a four-gate verification pipeline (one-page render, style lint, anti-fabrication verify, and an agent claim-trace check).
- **Application** is one row in the durable ledger.

Module layout:

| Module | Responsibility |
| --- | --- |
| `models.py` | The five dataclasses, the de-dup `role_key`, and the slug normalizer. Standard library only. |
| `config.py` | Paths, the comp floor, the rails (exclusions, over-level terms, hard-gap skills), and environment-only credential reads. |
| `discovery.py` | Source sweeps, search-angle parsing, and the scoring function with its hard caps. |
| `curation.py` | Queue triage: park poor-fit roles (off-lane, over-level, non-US, excluded, hard-skill-gap, already-applied) with an auditable reason, and fit-rank the rest so the loop applies to the strongest open match first instead of whatever is next in line. |
| `resume.py` | The resume builder and the static verification gates. |
| `submission.py` | ATS classification and the deterministic fill-and-park plan builder. |
| `tracker.py` | Reading and appending the ledger and the discovery queue, de-duped by role. |
| `server.py` | The FastMCP wiring. The only module that imports `mcp`. |

Because `__init__.py` imports only `models`, `import claude_works` never requires the MCP runtime, and the unit tests run against the pure core with no network and no third-party dependencies.

## Tools

Every tool returns JSON-serializable structures so results flow straight back into the model.

| Tool | Contract |
| --- | --- |
| `discover_jobs` | Find fresh roles, ranked by fit and de-duped by role. The default `boards` source queries the public Ashby/Greenhouse/Lever posting APIs over your `seed_boards` list and works from a bare install. Hard-capped roles always rank below clean ones. |
| `fetch_job_description` | Pull a posting's title, location, and plain-text JD from its ATS URL (headless, via the public posting APIs), so `score_job` scores on substance. |
| `curate_queue` | Triage the discovery queue: keep and fit-rank the genuine fits, park the rest with an auditable reason. Nothing is discarded. |
| `score_job` | Score one role 0 to 10 against the fit rubric and return the pursue verdict (with any hard cap). |
| `fetch_verification_code` | Read the newest ATS emailed-verification code from the applicant's own inbox (scoped, read-only IMAP): email-ownership verification, never captchas. |
| `get_search_angle` | Look up one search lens by name or trigger, or the default lane when the name is empty. |
| `list_search_angles` | List every defined search lens (name, trigger, definition). |
| `list_claim_fragments` | List the verified resume building blocks (roles, bullets, projects) that trace to the claims bank. |
| `build_resume` | Build a tailored one-page resume from verified fragments and run the static gates. |
| `render_resume` | Render a resume to PDF and report whether it is exactly one page. |
| `verify_resume` | Run the two static gates (style lint plus anti-fabrication verify) on any resume HTML. |
| `submit_application` | Build the fill-and-park submission plan for a role (no browser is driven here). |
| `record_application` | Append one row to the ledger, de-duped by company and role. |
| `list_queue` | List roles in the discovery queue by queue status (the queue-first gate). |
| `list_applications` | List ledger rows, optionally filtered by status. |
| `ledger_summary` | Return a count of ledger rows by status. |

## Install

```bash
git clone https://github.com/andrewaws26/claude-works.git
cd claude-works
pip install -e ".[dev]"
```

Run the checks CI runs (no network needed):

```bash
ruff check . && mypy && pytest
```

## Quickstart

Start the server over stdio:

```bash
python -m claude_works
```

Register it with Claude Desktop or Claude Code by adding it to your MCP server config:

```json
{
  "mcpServers": {
    "claude-works": {
      "command": "python",
      "args": ["-m", "claude_works"],
      "env": {
        "JOBSEARCH_APPLY_NAME": "Your Name",
        "JOBSEARCH_APPLY_EMAIL": "you@example.com",
        "JOBSEARCH_APPLY_LOCATION": "City, ST",
        "JOBSEARCH_COMP_FLOOR": "120000",
        "JOBSEARCH_PURSUE_THRESHOLD": "7.0"
      }
    }
  }
}
```

### Configuration

All configuration is environment driven. Nothing sensitive is stored in the repo.

| Variable | Purpose |
| --- | --- |
| `JOBSEARCH_DATA_DIR` | Directory holding the ledger, queue, and policy documents. Defaults to the package parent. Readers degrade gracefully when files are absent. |
| `JOBSEARCH_RESUMES_DIR` | Directory holding the resume generator and render pipeline. |
| `JOBSEARCH_COMP_FLOOR` | Base compensation floor in USD per year. |
| `JOBSEARCH_PURSUE_THRESHOLD` | The 0 to 10 score at or above which a role is pursued. |
| `JOBSEARCH_APPLY_NAME`, `JOBSEARCH_APPLY_EMAIL`, `JOBSEARCH_APPLY_PHONE`, `JOBSEARCH_APPLY_LOCATION` | Identity and contact fields, read at submission time only. No PII of any kind is hard-coded in the repo; an unset field is omitted from the plan. |
| `JOBSEARCH_APPLY_WEBSITE`, `JOBSEARCH_APPLY_LINKEDIN`, `JOBSEARCH_APPLY_GITHUB` | Profile links for application forms, read the same way. |
| `JOBSEARCH_APPLY_USERNAME`, `JOBSEARCH_APPLY_PASSWORD` | Portal credentials, read from the environment only and never stored. A missing credential fails loudly instead of silently mis-filling a form. |
| `JOBSEARCH_GMAIL_APP_PASSWORD`, `JOBSEARCH_IMAP_HOST` | Optional, for `fetch_verification_code`: a revocable app password (never the account password) and the IMAP host (defaults to Gmail). Missing credentials return a status; those submits simply park. |

## Running it for your own search

The defaults encode one candidate's policy: his excluded companies (active interview tracks), his skill gaps, his scoring vocabulary. None of that transfers, so none of it requires editing code to change. Drop a `policy.json` next to your trackers (in `JOBSEARCH_DATA_DIR`) and any key you define replaces the corresponding default wholesale; [examples/policy.sample.json](./examples/policy.sample.json) shows every supported key:

| Key | Controls |
| --- | --- |
| `comp_floor`, `pursue_threshold` | The comp floor and the 0-10 pursue gate (env vars still win). |
| `hard_gap_skills` | Skills you lack that, when hard-required, disqualify a role. |
| `overlevel_terms`, `level_ok_signals` | What counts as over-level vs level-fit for you. |
| `excluded_domains`, `excluded_companies` | Rails: domains you refuse and companies you must not (re-)apply to. |
| `core_signals`, `edge_signals` | Your scoring vocabulary: daily-stack terms and rare-differentiator terms. |
| `lane_points`, `off_lane_titles` | Curation's title lanes and what gets parked as off-lane. |

A policy file that exists but does not parse fails loudly at import; running silently on someone else's exclusion list is exactly the kind of quiet wrongness this codebase refuses.

**The fastest path: clone the repo, open Claude Code, type `/setup`.** The bundled command interviews you for example jobs you want and your resume, then derives all of the below from those examples (policy, search angles, seed boards, resume fragments), registers the MCP stack, and smoke-tests the pipeline before handing it over.

What the personalization consists of (all derivable by `/setup`, all editable by hand):

- **Identity**: the `JOBSEARCH_APPLY_*` environment variables (name, contact, profile links, portal credentials). Nothing personal is in the repo.
- **A queue and ledger**: start with empty files; `record_application` creates the ledger on first append.
- **Search angles**: your own `SEARCH_ANGLES.md` (the demo one shows the format).
- **Resume fragments**: the resume tools drive a claims-bank generator; copy `examples/resumes/_genlib.py` and replace the fragments with claims that are true of you. The gates then hold you to them.
- **Discovery**: the built-in `boards` source works out of the box over your `seed_boards` orgs. The `newsource`/`board_harvest` sources wrap private harvest scripts not in this repo and fail loudly without them.

## The full agent stack

This package is the policy brain. A complete, working system is three MCP servers plus the playbooks in this repo:

```bash
claude mcp add claude-works -e JOBSEARCH_DATA_DIR=... -- claude-works   # scoring, curation, plans, gates, ledger
claude mcp add playwright -- npx @playwright/mcp@latest                 # executes the submission plans in a real browser
claude mcp add --transport http jobdatalake https://mcp.jobdatalake.com # optional: 1M+ indexed roles, free tier, wide-net discovery
```

The division of labor: discovery comes from the built-in `boards` source and/or JobDataLake's `search_jobs`; every candidate role goes through `fetch_job_description` and `score_job`; `curate_queue` picks the strongest open fit; `build_resume`/`render_resume`/`verify_resume` produce a gated one-pager; `submit_application` emits the deterministic plan; the agent executes that plan with Playwright following [PLAYBOOK.md](./PLAYBOOK.md) (every hard-won per-ATS lesson, sanitized); the real outcome lands in the ledger via `record_application`. [OPERATING.md](./OPERATING.md) is the loop's operating model: queue-first, honest walls, de-dup semantics, and the standing self-improvement mandate.

## Design principles

**Honesty is enforced in the modules, not the prompt.** Three concrete mechanisms:

1. **Score hard caps.** An over-level title (Director, Principal, Staff, and the like), a required-skill gap, a non-US-only role, an excluded domain (defense, surveillance, and so on), or an active interview track caps the score and forces `pursue=False`. The agent cannot score its way past a disqualifier.
2. **Gate findings, not claims.** The resume tools return the actual results of the lint and anti-fabrication gates with the specific findings attached. A resume is reported as passing only when every automated gate ran and passed.
3. **Fill-and-park plans, not faked submits.** `submit_application` returns a deterministic plan (the ATS, the action, the field values, the honest screening answers, and the one human step left when parked). It never drives a browser and never reports a submission that did not happen. The real outcome is recorded afterward through `record_application`.

**A self-improving ATS playbook.** `submission.py` carries an `ATS_GOTCHAS` table: hard-won, per-ATS form-handling tactics (Ashby labeled-radio focus-plus-Space, Lever's hidden resume input behind an hCaptcha, Workable masked-date sequential typing, Hirebridge's ASP.NET postback cascade and FormValidation-gated submit, and so on). Every plan carries the relevant tactics so the browsing agent does not relearn them each run. When a better way to fill or submit a form is found, it is appended here and committed, so the knowledge persists across instances the way a person remembers a shortcut.

**Typed dataclass boundaries.** Every value that crosses a tool boundary is one of the five core dataclasses with an explicit `to_dict`. The schema is the contract, and the contract is the same whether a record was written by this server or by the underlying loop. The package ships a `py.typed` marker, and CI type-checks it with mypy.

**A ledger that survives concurrency.** Parallel loop instances share one `applications.json`. Every append holds an exclusive lock for the whole read-modify-write and lands via an atomic temp-file replace, so concurrent writers cannot drop each other's rows and a crash mid-write cannot corrupt the file. De-dup runs on the (company, role) pair and on the canonical `role_key` parsed from the apply URL, which catches the same role re-entering under a differently spelled company name.

**Zero-dependency, testable core.** The domain logic depends on nothing but the standard library. The tests cover the slug and role-key normalization, the dataclass round-trips, the de-dup-by-role behavior, the scoring hard caps, the curation park reasons, the submission planner, the demo fixtures, and the server's tool registry, all without touching the network. CI runs ruff, mypy, and pytest on Python 3.10 through 3.13.

## License

MIT. See [LICENSE](./LICENSE).
