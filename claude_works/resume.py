"""Resume build + the 4-gate verification pipeline.

This wraps the existing resume toolchain rather than reimplementing it:

    _genlib.py    builds one-page HTML from CLAIMS_BANK fragments (build())
    _render.sh    Chrome headless HTML -> PDF, asserts exactly 1 page
    lint_resume.py    style / AI-tell / em-dash gate
    verify_resume.py  anti-fabrication blocklist (the layer that was missing)

The 4-gate contract (from ``ARCHITECTURE.md``): render -> 1 page, lint -> 0
findings, verify -> PASS, and an agent claim-trace check the caller performs.
``verify_resume_file`` runs the two static gates (lint + verify) on any HTML so a
resume can be checked without re-rendering. The build helper exposes the
generator's catalog of verified bullet fragments so a caller assembles a tailored
resume from vetted claims only, never free text.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import PATHS
from .models import Resume


def _load_genlib():
    """Import ``_genlib.py`` from the resumes dir as a module."""
    path = PATHS.genlib
    if not path.exists():
        raise FileNotFoundError(f"_genlib.py not found at {path}; set JOBSEARCH_RESUMES_DIR")
    spec = importlib.util.spec_from_file_location("_jobsearch_genlib", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def list_claim_fragments() -> dict[str, list[str]]:
    """Return the generator's verified building blocks, grouped for selection.

    Keys: ``roles`` (canonical employer keys for the experience list), ``bullets``
    (reusable verified experience bullets), and ``projects`` (verified project
    bullets). Every fragment traces to ``CLAIMS_BANK.md``; building only from these
    is what keeps the output honest.
    """
    g = _load_genlib()
    roles = sorted(getattr(g, "ROLES", {}).keys())
    bullets = sorted(
        k for k, v in vars(g).items()
        if isinstance(v, str) and k.isupper() and not k.startswith("P_") and "<b>" not in v and k not in ("HDR", "EDU")
    )
    projects = sorted(k for k, v in vars(g).items() if isinstance(v, str) and k.startswith("P_"))
    return {"roles": roles, "bullets": bullets, "projects": projects}


def build_resume(
    name: str,
    tagline: str,
    summary: str,
    experience: list[tuple[str, list[str]]],
    projects: list[str],
    skills: list[tuple[str, str]],
) -> Resume:
    """Build a tailored one-page resume HTML from verified fragments via ``_genlib``.

    ``experience`` is a list of ``(role_key, [bullet, ...])`` where role_key is one
    of the canonical employers (``bnb``, ``twinspires``, ``upwork``, ``humana``,
    ``tesla``, ``dojo``, ``lifespring``). ``projects`` and the experience bullets
    should be drawn from ``list_claim_fragments`` (verified HTML) or written to
    trace to ``CLAIMS_BANK.md``. ``skills`` is a list of ``(label, text)`` rows.

    Resolves bare fragment NAMES to their text, writes ``<name>.html`` into the
    resumes dir, then runs the static gates (lint + verify) and returns a
    ``Resume`` with the findings. The render-to-PDF 1-page gate runs separately via
    ``render_resume`` because it needs Chrome.
    """
    g = _load_genlib()

    def resolve(token: str) -> str:
        # Allow callers to pass either a fragment NAME (BNB_AI) or literal HTML.
        return getattr(g, token) if isinstance(token, str) and token.isupper() and hasattr(g, token) else token

    exp = [(rk, [resolve(b) for b in bullets]) for rk, bullets in experience]
    proj = [resolve(p) for p in projects]
    g.build(name, tagline, summary, exp, proj, skills)
    html_path = PATHS.resumes / f"{name}.html"
    res = Resume(name=name, html_path=str(html_path))
    res.lint_ok, lint_findings = _run_lint(html_path)
    res.verify_ok, verify_findings = _run_verify(html_path)
    res.findings = lint_findings + verify_findings
    return res


def render_resume(name: str) -> Resume:
    """Render ``<name>.html`` to PDF via ``_render.sh`` and report the page count.

    Returns a ``Resume`` with ``one_page`` set. Requires Google Chrome and qpdf
    (the existing pipeline's dependencies). Raises if the HTML is missing.
    """
    html_path = PATHS.resumes / f"{name}.html"
    if not html_path.exists():
        raise FileNotFoundError(f"{html_path} not found; build the resume first")
    proc = subprocess.run(
        ["/bin/zsh", str(PATHS.render_sh), name],
        capture_output=True, text=True, cwd=str(PATHS.resumes), timeout=120,
    )
    out = proc.stdout + proc.stderr
    m = re.search(r"pages:\s*(\d+)", out)
    pages = int(m.group(1)) if m else None
    return Resume(
        name=name,
        html_path=str(html_path),
        pdf_path=str(PATHS.resumes / f"{name}.pdf"),
        one_page=(pages == 1),
        findings=[] if pages == 1 else [f"render produced {pages} pages (want 1)"],
    )


def verify_resume_file(html_path: str) -> Resume:
    """Run the two static gates (lint + verify) on an existing resume HTML.

    Use this to check any resume on disk without rebuilding or rendering. Returns a
    ``Resume`` whose ``lint_ok`` / ``verify_ok`` reflect the gates and whose
    ``findings`` lists every violation.
    """
    p = Path(html_path)
    if not p.exists():
        raise FileNotFoundError(html_path)
    res = Resume(name=p.stem, html_path=str(p))
    res.lint_ok, lint_findings = _run_lint(p)
    res.verify_ok, verify_findings = _run_verify(p)
    res.findings = lint_findings + verify_findings
    return res


def _run_lint(html_path: Path) -> tuple[bool, list[str]]:
    return _run_gate(PATHS.lint_resume, html_path)


def _run_verify(html_path: Path) -> tuple[bool, list[str]]:
    return _run_gate(PATHS.verify_resume, html_path)


def _run_gate(script: Path, html_path: Path) -> tuple[bool, list[str]]:
    """Run a gate script on a file; return (passed, findings).

    Gate scripts exit 0 on pass, non-zero on findings, and print the findings to
    stdout. We run them with the same interpreter that runs the MCP.
    """
    if not script.exists():
        return False, [f"gate script missing: {script}"]
    proc = subprocess.run(
        [sys.executable, str(script), str(html_path)],
        capture_output=True, text=True, timeout=60,
    )
    passed = proc.returncode == 0
    findings: list[str] = []
    if not passed:
        for line in (proc.stdout + proc.stderr).splitlines():
            line = line.strip()
            if line and not line.startswith(("PASS", "OK", "usage")):
                findings.append(line)
    return passed, findings
