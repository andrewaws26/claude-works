"""Unit tests for the ledger: record, de-dup by role, and queue parsing."""

from __future__ import annotations

import json

from claude_works import tracker
from claude_works.models import Application, Job


def test_record_application_dedupes_by_company_and_role(tmp_path):
    ledger = tmp_path / "applications.json"

    first = tracker.record_application(
        Application(company="Acme", role="AI Engineer", status="submitted"), path=ledger
    )
    assert first["recorded"] is True
    assert first["total"] == 1

    dup = tracker.record_application(
        Application(company="Acme", role="AI Engineer", status="submitted"), path=ledger
    )
    assert dup["recorded"] is False
    assert "duplicate" in dup["reason"]

    # Same company, different role is NOT a duplicate.
    other = tracker.record_application(
        Application(company="Acme", role="Solutions Engineer", status="submitted"), path=ledger
    )
    assert other["recorded"] is True
    assert other["total"] == 2


def test_record_application_fills_today_date(tmp_path):
    ledger = tmp_path / "applications.json"
    tracker.record_application(Application(company="Acme", role="AI Engineer"), path=ledger)
    rows = tracker.list_applications(path=ledger)
    assert rows[0].date  # non-empty ISO date filled in


def test_already_applied_normalizes_company(tmp_path):
    ledger = tmp_path / "applications.json"
    tracker.record_application(Application(company="Acme, Inc.", role="AI Engineer"), path=ledger)
    # Normalized slug means the punctuated and bare forms match.
    assert tracker.already_applied("Acme", "AI Engineer", path=ledger) is True
    assert tracker.already_applied("Acme", "Other Role", path=ledger) is False


def test_status_counts(tmp_path):
    ledger = tmp_path / "applications.json"
    tracker.record_application(Application(company="A", role="r1", status="submitted"), path=ledger)
    tracker.record_application(Application(company="B", role="r2", status="submitted"), path=ledger)
    tracker.record_application(Application(company="C", role="r3", status="deferred-captcha"), path=ledger)
    counts = tracker.status_counts(path=ledger)
    assert counts == {"submitted": 2, "deferred-captcha": 1}


def test_dedupe_jobs_drops_roles_already_in_ledger(tmp_path):
    ledger = tmp_path / "applications.json"
    applied_url = "https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef"
    tracker.record_application(
        Application(company="Acme", role="AI Engineer", apply_url=applied_url), path=ledger
    )

    already = Job(title="AI Engineer", company="Acme", url=applied_url)
    fresh = Job(title="AI Engineer II", company="Acme",
                url="https://jobs.ashbyhq.com/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    out = tracker.dedupe_jobs([already, fresh], path=ledger)
    assert [j.role_key for j in out] == [fresh.role_key]


def test_queue_jobs_parses_label_and_status(tmp_path):
    queue = tmp_path / "top300_jobs.json"
    queue.write_text(json.dumps([
        {"n": 1, "text": "[BH][R] AI Engineer - Acme",
         "url": "https://jobs.ashbyhq.com/acme/x", "ats": "ashby", "status": "todo", "remote": True},
        {"n": 2, "text": "[BH] Backend Engineer - Beta",
         "url": "https://example.com/y", "ats": "lever", "status": "done", "remote": False},
    ]), encoding="utf-8")

    todo = tracker.queue_jobs(status="todo", path=queue)
    assert len(todo) == 1
    assert todo[0].title == "AI Engineer"
    assert todo[0].company == "Acme"
    assert todo[0].remote is True
