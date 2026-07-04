"""The demo kit must keep working: the offline source, the sample fixtures, and
the demo gate scripts are what a fresh clone exercises, so they are tested."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from claude_works import curation, discovery, tracker

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_demo_source_works_offline_and_ranks_by_fit():
    jobs = discovery.discover_jobs(source="demo", network_ok=False)
    assert len(jobs) >= 6
    titles = [j.title for j in jobs]
    # A strong in-lane role outranks the hard-capped ones.
    assert titles.index("Forward Deployed Engineer") < titles.index(
        "Principal Engineer, ML Infrastructure"
    )
    # Every demo URL parses to a real ATS role key (no raw fallbacks).
    assert all(not j.role_key.startswith("raw:") for j in jobs)


def test_demo_source_is_listed():
    assert "demo" in discovery.available_sources()


def test_examples_search_angles_parse():
    angles = discovery.list_search_angles(EXAMPLES / "SEARCH_ANGLES.md")
    assert len(angles) == 3
    default = discovery.get_search_angle("", EXAMPLES / "SEARCH_ANGLES.md")
    assert default is not None and "FDE" in default.name
    assert default.target_titles


def test_examples_queue_curates_with_auditable_reasons():
    jobs = tracker.queue_jobs(status="todo", path=EXAMPLES / "top300_jobs.json")
    applied = tracker.applied_company_slugs(EXAMPLES / "applications.json")
    res = curation.curate(jobs, applied_slugs=applied)

    assert res.counts.get("over-level") == 1        # Staff Software Engineer
    assert res.counts.get("off-lane") == 1          # Design Engineer
    assert res.counts.get("non-us-region") == 1     # Solutions Engineer, Benelux
    assert res.counts.get("advanced-degree") == 1   # Applied AI/ML Scientist
    assert res.counts.get("excluded-domain") == 1   # Warfront Defense Analytics
    assert res.counts.get("hard-skill-gap") == 1    # Kubernetes Platform Engineer
    assert res.counts.get("already-applied") == 3   # Promptline, Fleetly, Widget Intelligence

    active_titles = [j.title for j, _ in res.active]
    assert "Forward Deployed Engineer" in active_titles
    fits = [f for _, f in res.active]
    assert fits == sorted(fits, reverse=True)


def test_demo_ledger_dedupes_demo_discovery():
    # The sample ledger already holds the Promptline role, so the demo sweep
    # must not resurface it.
    jobs = discovery.discover_jobs(source="demo", network_ok=False)
    deduped = tracker.dedupe_jobs(jobs, path=EXAMPLES / "applications.json")
    assert all(j.company != "Promptline" for j in deduped)
    assert len(deduped) == len(jobs) - 1


def _run_gate(script: Path, html: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(script), str(html)], capture_output=True, text=True, timeout=30
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_demo_gates_catch_fabrication_and_style(tmp_path):
    bad = tmp_path / "bad.html"
    bad.write_text(
        "<html><body><p>Spearheaded synergy — holds a PhD in C/C++.</p></body></html>",
        encoding="utf-8",
    )
    code, out = _run_gate(EXAMPLES / "lint_resume.py", bad)
    assert code == 1 and "em dash" in out and "spearheaded" in out
    code, out = _run_gate(EXAMPLES / "verify_resume.py", bad)
    assert code == 1 and "PhD" in out and "C/C++" in out


def test_demo_gates_pass_clean_resume(tmp_path):
    good = tmp_path / "good.html"
    good.write_text(
        "<html><body><p>Built an MCP server with typed tools and tests.</p></body></html>",
        encoding="utf-8",
    )
    assert _run_gate(EXAMPLES / "lint_resume.py", good)[0] == 0
    assert _run_gate(EXAMPLES / "verify_resume.py", good)[0] == 0
