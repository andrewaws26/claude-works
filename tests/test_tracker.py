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


def test_dedupe_jobs_drops_same_role_re_harvested_under_a_new_url(tmp_path):
    """A decided role coming back from a different source must not re-enter the queue."""
    ledger = tmp_path / "applications.json"
    tracker.record_application(
        Application(company="The Agency Fund", role="Forward Deployed Engineer",
                    status="skipped-rail",
                    apply_url="https://example.com/aggregator/listing/1"),
        path=ledger,
    )

    # Same role, this time straight off the company board: a brand new role_key.
    reharvested = Job(title="Forward Deployed Engineer", company="the agency fund",
                      url="https://jobs.ashbyhq.com/the%20agency%20fund/"
                          "700573a9-3c7c-4257-86ba-94561211e5e6")
    other_role = Job(title="AI Engineer", company="The Agency Fund",
                     url="https://jobs.ashbyhq.com/the%20agency%20fund/"
                         "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    out = tracker.dedupe_jobs([reharvested, other_role], path=ledger)
    assert [j.title for j in out] == ["AI Engineer"]


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


def test_record_application_dedupes_by_role_key_across_company_spellings(tmp_path):
    ledger = tmp_path / "applications.json"
    url = "https://jobs.ashbyhq.com/bland-ai/12345678-90ab-cdef-1234-567890abcdef"
    first = tracker.record_application(
        Application(company="Bland AI", role="AI Engineer", apply_url=url), path=ledger
    )
    assert first["recorded"] is True
    # Same role re-entering under a differently spelled company name: caught by
    # the canonical role_key parsed from the apply URL.
    dup = tracker.record_application(
        Application(company="Bland", role="AI Engineer (Remote)", apply_url=url), path=ledger
    )
    assert dup["recorded"] is False
    assert "role_key" in dup["reason"]


def test_record_application_write_is_atomic_no_tmp_left_behind(tmp_path):
    ledger = tmp_path / "applications.json"
    tracker.record_application(Application(company="Acme", role="AI Engineer"), path=ledger)
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert json.loads(ledger.read_text())["applications"][0]["company"] == "Acme"


def test_applied_company_slugs_includes_name_and_url_org(tmp_path):
    ledger = tmp_path / "applications.json"
    tracker.record_application(
        Application(company="Acme, Inc.", role="AI Engineer",
                    apply_url="https://jobs.ashbyhq.com/acme-widgets/12345678-90ab-cdef-1234-567890abcdef"),
        path=ledger,
    )
    slugs = tracker.applied_company_slugs(path=ledger)
    assert "acme" in slugs          # from the company name
    assert "acmewidgets" in slugs   # from the apply-URL org


def _queue_row(company, role, url, status="parked-poorfit"):
    return {"n": 1, "status": status, "ats": "Ashby", "fit": 9,
            "text": f"{role} - {company}", "url": url, "_company": company, "_role": role}


def test_dedupe_jobs_drops_a_role_already_in_the_queue_at_any_status(tmp_path):
    # A role parked at a submit wall never reaches the ledger, so a ledger-only
    # check re-queues it forever. This is the real failure that motivated the
    # queue check: the parked role came back as the top-scoring candidate.
    ledger = tmp_path / "applications.json"
    ledger.write_text(json.dumps({"applications": []}), encoding="utf-8")
    queue = tmp_path / "queue.json"
    url = "https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef"
    queue.write_text(json.dumps([_queue_row("Acme", "Applied AI Engineer", url)]), encoding="utf-8")

    parked = Job(title="Applied AI Engineer", company="Acme", url=url)
    fresh = Job(title="Agent Engineer", company="Initech",
                url="https://jobs.ashbyhq.com/initech/abcdef12-3456-7890-abcd-ef1234567890")

    out = tracker.dedupe_jobs([parked, fresh], path=ledger, queue_path=queue)
    assert [j.title for j in out] == ["Agent Engineer"]


def test_dedupe_jobs_drops_a_queued_role_reposted_under_a_new_url(tmp_path):
    ledger = tmp_path / "applications.json"
    ledger.write_text(json.dumps({"applications": []}), encoding="utf-8")
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps([_queue_row(
        "Acme", "Applied AI Engineer",
        "https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef")]), encoding="utf-8")

    repost = Job(title="Applied AI Engineer", company="Acme",
                 url="https://jobs.ashbyhq.com/acme/99999999-90ab-cdef-1234-567890abcdef")
    out = tracker.dedupe_jobs([repost], path=ledger, queue_path=queue)
    assert out == []


def test_dedupe_jobs_keeps_a_different_role_at_a_queued_company(tmp_path):
    # Role-level de-dup is the point: companies may repeat, the same role never.
    ledger = tmp_path / "applications.json"
    ledger.write_text(json.dumps({"applications": []}), encoding="utf-8")
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps([_queue_row(
        "Acme", "Applied AI Engineer",
        "https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef")]), encoding="utf-8")

    other = Job(title="Support Engineer", company="Acme",
                url="https://jobs.ashbyhq.com/acme/abcdef12-3456-7890-abcd-ef1234567890")
    out = tracker.dedupe_jobs([other], path=ledger, queue_path=queue)
    assert [j.title for j in out] == ["Support Engineer"]
