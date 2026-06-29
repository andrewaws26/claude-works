# Claude Works

An MCP server that turns a perpetual, fit-first job-application pipeline into typed, honest tools an LLM agent can call.

## What it is

Claude Works is a [Model Context Protocol](https://modelcontextprotocol.io) server built on FastMCP. It exposes a working, autonomous job-application loop (discovery sweeps, fit scoring, a verified resume builder, a fill-and-park submission planner, and an application ledger) as a small set of typed tools.

The point is not to wrap a chatbot around a job board. The point is to put the policy where it cannot drift: in code. An agent calling these tools cannot quietly inflate a fit score, claim a resume passed gates it never ran, or report a submission that did not happen. The honesty lives in the modules, not in a system prompt that the next conversation might forget.

The core (everything except the FastMCP wiring) imports with the standard library only. That keeps the domain logic fast to test, easy to read, and reusable outside the MCP runtime.

## Architecture

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
| `discover_jobs` | Find fresh roles from a discovery source, ranked by fit and de-duped by role. |
| `score_job` | Score one role 0 to 10 against the fit rubric and return the pursue verdict (with any hard cap). |
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

Run the test suite (pure core, no network):

```bash
pytest
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
| `JOBSEARCH_APPLY_EMAIL`, `JOBSEARCH_APPLY_PHONE`, `JOBSEARCH_APPLY_LOCATION` | Contact fields, read at submission time only. |
| `JOBSEARCH_APPLY_USERNAME`, `JOBSEARCH_APPLY_PASSWORD` | Portal credentials, read from the environment only and never stored. A missing credential fails loudly instead of silently mis-filling a form. |

## Design principles

**Honesty is enforced in the modules, not the prompt.** Three concrete mechanisms:

1. **Score hard caps.** An over-level title (Director, Principal, Staff, and the like), a required-skill gap, a non-US-only role, an excluded domain (defense, surveillance, and so on), or an active interview track caps the score and forces `pursue=False`. The agent cannot score its way past a disqualifier.
2. **Gate findings, not claims.** The resume tools return the actual results of the lint and anti-fabrication gates with the specific findings attached. A resume is reported as passing only when every automated gate ran and passed.
3. **Fill-and-park plans, not faked submits.** `submit_application` returns a deterministic plan (the ATS, the action, the field values, the honest screening answers, and the one human step left when parked). It never drives a browser and never reports a submission that did not happen. The real outcome is recorded afterward through `record_application`.

**A self-improving ATS playbook.** `submission.py` carries an `ATS_GOTCHAS` table: hard-won, per-ATS form-handling tactics (Ashby labeled-radio focus-plus-Space, Lever's hidden resume input behind an hCaptcha, Workable masked-date sequential typing, Hirebridge's ASP.NET postback cascade and FormValidation-gated submit, and so on). Every plan carries the relevant tactics so the browsing agent does not relearn them each run. When a better way to fill or submit a form is found, it is appended here and committed, so the knowledge persists across instances the way a person remembers a shortcut.

**Typed dataclass boundaries.** Every value that crosses a tool boundary is one of the five core dataclasses with an explicit `to_dict`. The schema is the contract, and the contract is the same whether a record was written by this server or by the underlying loop.

**Zero-dependency, testable core.** The domain logic depends on nothing but the standard library. The included tests cover the slug and role-key normalization, the dataclass round-trips, the de-dup-by-role behavior, the scoring hard caps, and the submission planner, all without touching the network.

## License

MIT. See [LICENSE](./LICENSE).
