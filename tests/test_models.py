"""Unit tests for the core dataclasses and the de-duplication normalizers."""

from __future__ import annotations

from claude_works.models import (
    Application,
    Job,
    Resume,
    Score,
    SearchAngle,
    _slug,
)


def test_slug_strips_suffixes_punctuation_and_trailing_counter():
    assert _slug("Addepar, Inc.") == "addepar"
    assert _slug("addepar1") == "addepar"
    assert _slug("The Foo Co") == "foo"
    assert _slug("Samsara") == "samsara"
    # A word boundary keeps "co" inside a real name intact.
    assert _slug("Coinbase") == "coinbase"


def test_job_role_key_per_ats():
    ashby = Job(title="X", company="Acme",
                url="https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef")
    assert ashby.role_key == "ashby:acme:12345678-90ab-cdef-1234-567890abcdef"

    gh = Job(title="X", company="Acme",
             url="https://boards.greenhouse.io/acme/jobs/4567890")
    assert gh.role_key == "gh:acme:4567890"

    lever = Job(title="X", company="Acme",
                url="https://jobs.lever.co/acme/abcdef12-3456-7890-abcd-ef1234567890")
    assert lever.role_key == "lever:acme:abcdef12-3456-7890-abcd-ef1234567890"

    raw = Job(title="X", company="Acme", url="https://example.com/careers/42")
    assert raw.role_key == "raw:https://example.com/careers/42:"


def test_job_role_key_ignores_query_string():
    a = Job(title="X", company="Acme",
            url="https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef?utm=x")
    b = Job(title="X", company="Acme",
            url="https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef")
    assert a.role_key == b.role_key


def test_job_to_dict_includes_role_key_and_company_slug():
    job = Job(title="Engineer", company="Acme, Inc.",
              url="https://boards.greenhouse.io/acme/jobs/1")
    d = job.to_dict()
    assert d["role_key"] == "gh:acme:1"
    assert d["title"] == "Engineer"
    assert job.company_slug == "acme"


def test_job_from_dict_accepts_alternate_keys():
    job = Job.from_dict({
        "title": "AI Engineer",
        "org": "Acme",
        "apply_url": "https://example.com/x",
        "src": "harvest",
        "loc": "Remote",
        "remote": True,
    })
    assert job.company == "Acme"
    assert job.url == "https://example.com/x"
    assert job.source == "harvest"
    assert job.location == "Remote"
    assert job.remote is True


def test_score_to_dict_round_trip():
    s = Score(value=8.5, pursue=True, reasons=["core-stack overlap"], hard_cap=None, angle="fde")
    d = s.to_dict()
    assert d == {
        "value": 8.5,
        "pursue": True,
        "reasons": ["core-stack overlap"],
        "hard_cap": None,
        "angle": "fde",
    }


def test_application_to_dict_drops_none_tier_keeps_set_tier():
    bare = Application(company="Acme", role="AI Engineer")
    assert "tier" not in bare.to_dict()

    tiered = Application(company="Acme", role="AI Engineer", tier="A")
    assert tiered.to_dict()["tier"] == "A"


def test_application_round_trip_from_dict():
    row = {
        "company": "Acme",
        "role": "AI Engineer",
        "status": "submitted",
        "ats": "ashby",
        "date": "2026-01-01",
        "apply_url": "https://example.com/x",
        "note": "clean channel",
        "tier": "A",
    }
    app = Application.from_dict(row)
    assert app.to_dict() == row


def test_resume_passed_only_when_all_automated_gates_pass():
    ok = Resume(name="r", one_page=True, lint_ok=True, verify_ok=True)
    assert ok.passed is True
    assert ok.to_dict()["passed"] is True

    for bad in (
        Resume(name="r", one_page=False, lint_ok=True, verify_ok=True),
        Resume(name="r", one_page=True, lint_ok=None, verify_ok=True),
        Resume(name="r", one_page=True, lint_ok=True, verify_ok=False),
    ):
        assert bad.passed is False


def test_search_angle_to_dict():
    a = SearchAngle(name="FDE", trigger="that kind of search",
                    definition="forward deployed lane", target_titles=("FDE", "SE"),
                    is_default=True)
    d = a.to_dict()
    assert d["name"] == "FDE"
    assert d["target_titles"] == ("FDE", "SE")
    assert d["is_default"] is True
