"""Demo resume generator (a small stand-in for the private _genlib.py).

Exposes the same surface the MCP's resume module expects:

  * ``ROLES``: canonical employer keys -> (title, company, dates)
  * UPPERCASE string constants: verified bullet fragments (the claims bank)
  * ``P_*`` constants: verified project fragments
  * ``build(name, tagline, summary, experience, projects, skills)``: writes
    ``<name>.html`` next to this file

Every fragment below belongs to a fictional persona, "Jordan Example". In the
real system each fragment traces to a reviewed CLAIMS_BANK.md entry; building
only from these named fragments is what keeps generated resumes honest.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

# Canonical employer keys for the experience list.
ROLES: dict[str, tuple[str, str, str]] = {
    "acme": ("Automation Engineer", "Acme Agents (demo)", "2024 - present"),
    "widgetco": ("Software Engineer", "Widget Intelligence (demo)", "2021 - 2024"),
}

# Verified bullet fragments (the demo claims bank).
ACME_MCP = "Built an MCP server exposing a 5-stage application pipeline as 14 typed tools with enforced honesty gates."
ACME_EVAL = "Added an evaluation harness that scores every candidate role 0-10 with auditable per-signal reasons."
WIDGET_ETL = "Maintained a nightly ETL that normalized postings from four ATS vendors into one queue."
WIDGET_API = "Shipped a REST API consumed by three internal teams; p95 latency held under 200 ms."

# Verified project fragments.
P_DEMO_PIPE = "Demo Pipeline: discovery, scoring, curation, and ledger tooling with a zero-dependency core."
P_DEMO_GATES = "Resume Gates: one-page render, style lint, and anti-fabrication verification wired into CI."

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{name}</title>
<style>body{{font-family:Georgia,serif;max-width:7.5in;margin:24px auto;font-size:11pt}}
h1{{margin:0}} .tag{{color:#345;margin:2px 0 10px}} h2{{font-size:12pt;border-bottom:1px solid #999}}
li{{margin:2px 0}} .role b{{display:block}}</style></head><body>
<h1>Jordan Example</h1><div class="tag">{tagline}</div>
<p>{summary}</p>
<h2>Experience</h2>{experience}
<h2>Projects</h2><ul>{projects}</ul>
<h2>Skills</h2><ul>{skills}</ul>
</body></html>
"""


def build(name, tagline, summary, experience, projects, skills):
    """Render the demo one-page HTML from the given fragments."""
    exp_html = ""
    for role_key, bullets in experience:
        title, company, dates = ROLES[role_key]
        lis = "".join(f"<li>{b}</li>" for b in bullets)
        exp_html += f'<div class="role"><b>{title} | {company} | {dates}</b><ul>{lis}</ul></div>'
    proj_html = "".join(f"<li>{p}</li>" for p in projects)
    skill_html = "".join(f"<li><b>{label}:</b> {text}</li>" for label, text in skills)
    html = _PAGE.format(name=name, tagline=tagline, summary=summary,
                        experience=exp_html, projects=proj_html, skills=skill_html)
    (HERE / f"{name}.html").write_text(html, encoding="utf-8")
    return str(HERE / f"{name}.html")
