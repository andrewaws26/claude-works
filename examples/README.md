# Demo mode

Everything in this directory is sanitized sample data for a fictional persona
("Jordan Example") and fictional companies, shaped exactly like the private
production files. It exists so a fresh clone can exercise every tool in the MCP
server with zero network access and zero private scripts.

## Run it

```bash
pip install -e .
JOBSEARCH_DATA_DIR="$PWD/examples" \
JOBSEARCH_RESUMES_DIR="$PWD/examples/resumes" \
python -m claude_works
```

Or register it with Claude Code from the repo root:

```bash
claude mcp add claude-works \
  -e JOBSEARCH_DATA_DIR="$PWD/examples" \
  -e JOBSEARCH_RESUMES_DIR="$PWD/examples/resumes" \
  -- python -m claude_works
```

## What to try

| Call | What it demonstrates |
| --- | --- |
| `discover_jobs(source="demo")` | Canned roles ranked by fit; the Promptline role is absent because the sample ledger already holds it (de-dup by role). |
| `score_job(title="Principal Engineer, ML Infrastructure")` | A hard cap: `pursue=false`, reason `over-level title ('principal')`. |
| `curate_queue()` | The sample queue triaged: strong fits ranked first; the Staff / Design / Benelux / defense / Kubernetes rows parked with auditable reasons; companies already in the ledger parked as `already-applied`. |
| `list_search_angles()` | The three sample lenses parsed from `SEARCH_ANGLES.md`. |
| `build_resume(...)` then `render_resume(...)` | The gates in action; try sneaking `"Holds a PhD"` into the summary and watch `verify_ok` flip to false. |
| `submit_application(...)` | A fill-and-park plan with the per-ATS gotcha notes attached. |
| `record_application(...)` | Ledger append with role-level de-dup (locked, atomic write). |

## Files

- `SEARCH_ANGLES.md` - three sample search lenses in the parsed format.
- `applications.json` - a three-row sample ledger.
- `top300_jobs.json` - an eleven-row sample discovery queue.
- `lint_resume.py`, `verify_resume.py` - working demo gate scripts (style lint and anti-fabrication blocklist).
- `resumes/_genlib.py` - a demo claims-bank generator with named, verified fragments.
- `resumes/_render.sh` - render stub: real Chrome headless render when Chrome is present, a placeholder single-page PDF otherwise; the reported page count is always read back from the produced PDF.
