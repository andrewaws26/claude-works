"""Unit tests for queue curation: parking reasons and fit ranking."""

from __future__ import annotations

from claude_works import curation
from claude_works.models import Job


def _job(title: str, company: str = "Acme", location: str = "Remote, US", remote: bool = True) -> Job:
    return Job(
        title=title,
        company=company,
        url="https://jobs.ashbyhq.com/acme/12345678-90ab-cdef-1234-567890abcdef",
        location=location,
        remote=remote,
        ats="ashby",
    )


def test_forward_deployed_is_kept_and_ranks_highest():
    res = curation.curate([
        _job("Backend Software Engineer"),
        _job("Forward Deployed Engineer"),
        _job("AI Engineer"),
    ])
    assert res.parked == []
    # Forward Deployed should be the top-ranked active role.
    top_job, top_fit = res.active[0]
    assert top_job.title == "Forward Deployed Engineer"
    assert top_fit >= res.active[1][1]


def test_design_engineer_is_parked_off_lane():
    res = curation.curate([_job("Design Engineer")])
    assert res.active == []
    assert res.parked[0][1] == "off-lane"


def test_over_level_title_is_parked():
    for title in ("Staff Software Engineer", "Director of AI", "Founding Engineer"):
        res = curation.curate([_job(title)])
        assert res.parked and res.parked[0][1] == "over-level", title


def test_excluded_domain_is_parked():
    res = curation.curate([_job("AI Engineer", company="Acme Defense Systems")])
    assert res.parked and res.parked[0][1] == "excluded-domain"


def test_already_applied_company_is_parked():
    job = _job("AI Engineer", company="Acme")
    res = curation.curate([job], applied_slugs={job.company_slug})
    assert res.parked and res.parked[0][1] == "already-applied"


def test_scientist_and_phd_are_parked_advanced_degree():
    # "Scientist" titles and PhD-required JDs are a credential knockout, even in-lane.
    res = curation.curate([_job("Applied AI/ML Scientist")])
    assert res.parked and res.parked[0][1] == "advanced-degree"
    res2 = curation.curate([_job("AI Engineer", company="Acme (PhD required)")])
    assert res2.parked and res2.parked[0][1] == "advanced-degree"


def test_non_us_only_is_parked():
    res = curation.curate([_job("AI Engineer", location="London, United Kingdom", remote=False)])
    assert res.parked and res.parked[0][1] == "non-us-only"


def test_hard_skill_gap_is_parked():
    res = curation.curate([_job("Kubernetes Platform Engineer", company="Acme Spark Kafka")])
    assert res.parked and res.parked[0][1] == "hard-skill-gap"


def test_remote_us_role_with_no_location_still_kept():
    # Absent location must NOT trigger the non-US park (only a present, non-US one does).
    res = curation.curate([_job("AI Engineer", location="")])
    assert res.active and res.parked == []


def test_counts_histogram_and_sorting():
    jobs = [
        _job("Design Engineer"),          # off-lane
        _job("Forward Deployed Engineer"),  # keep, fit 6+
        _job("Staff Engineer"),           # over-level
        _job("AI Engineer"),              # keep, fit 5+
    ]
    res = curation.curate(jobs)
    assert res.counts.get("off-lane") == 1
    assert res.counts.get("over-level") == 1
    assert len(res.active) == 2
    # active sorted by fit descending
    fits = [f for _, f in res.active]
    assert fits == sorted(fits, reverse=True)
