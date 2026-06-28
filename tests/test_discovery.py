"""Unit tests for scoring, hard caps, and the offline discovery guard."""

from __future__ import annotations

from claude_works import discovery
from claude_works.config import RAILS
from claude_works.models import Job


def _ashby(title: str, company: str = "Acme") -> Job:
    return Job(
        title=title,
        company=company,
        url="https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef",
        ats="ashby",
    )


def test_over_level_title_is_hard_capped():
    score = discovery.score_job(_ashby("Director of Engineering"))
    assert score.hard_cap is not None
    assert "over-level" in score.hard_cap
    assert score.pursue is False
    assert score.value <= 5.0


def test_principal_title_is_hard_capped():
    score = discovery.score_job(_ashby("Principal AI Engineer"))
    assert score.hard_cap is not None
    assert score.pursue is False


def test_excluded_domain_in_jd_is_hard_capped():
    score = discovery.score_job(_ashby("AI Engineer"), jd_text="Build defense systems for the field.")
    assert score.hard_cap is not None
    assert "excluded domain" in score.hard_cap
    assert score.pursue is False


def test_excluded_company_is_hard_capped():
    job = Job(title="AI Engineer", company="Samsara",
              url="https://jobs.ashbyhq.com/samsara/12345678-90ab-cdef-1234-567890abcdef")
    score = discovery.score_job(job)
    assert score.hard_cap is not None
    assert "excluded company" in score.hard_cap
    assert score.pursue is False


def test_strong_in_lane_role_scores_high_and_is_pursued():
    job = Job(
        title="Forward Deployed AI Engineer",
        company="Acme",
        url="https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef",
        ats="ashby",
        remote=True,
    )
    score = discovery.score_job(job, jd_text="We build with Claude, the MCP, and agentic eval pipelines.")
    assert score.hard_cap is None
    assert score.value >= RAILS.pursue_threshold
    assert score.pursue is True
    assert score.reasons  # at least one signal recorded


def test_bare_title_with_no_signals_does_not_pursue():
    score = discovery.score_job(Job(title="Widget Operator", company="Acme",
                                    url="https://example.com/x"))
    assert score.hard_cap is None
    assert score.pursue is False
    assert score.reasons


def test_discover_jobs_offline_returns_empty():
    assert discovery.discover_jobs(network_ok=False) == []


def test_angle_bias_lookup_is_case_insensitive():
    assert discovery._angle_bias_terms("FDE")
    assert discovery._angle_bias_terms("fde") == discovery._angle_bias_terms("FDE")
    assert discovery._angle_bias_terms(None) == ()
