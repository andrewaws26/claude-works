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


def test_evergreen_posting_is_parked():
    res = curation.curate([_job("Forward Deployed Engineer (Evergreen)")])
    assert res.parked and res.parked[0][1] == "evergreen-posting"


def test_intern_title_is_parked_but_internal_is_not():
    res = curation.curate([_job("Software Engineering Intern")])
    assert res.parked and res.parked[0][1] == "over-level"

    res = curation.curate([_job("AI Engineer, Internal Agents & Workflow Automation")])
    assert res.parked == []
    assert res.active


def test_excluded_domain_is_parked():
    res = curation.curate([_job("AI Engineer", company="Acme Defense Systems")])
    assert res.parked and res.parked[0][1] == "excluded-domain"


def test_negated_clearance_is_not_parked():
    # A posting that advertises the ABSENCE of a clearance requirement ("no
    # clearance") must not trip the bare "clearance" excluded-domain signal.
    job = _job("Forward Deployed AI Engineer")
    job.comp = "no clearance required"
    res = curation.curate([job])
    assert res.parked == []
    assert res.active


def test_required_clearance_is_parked():
    job = _job("AI Engineer")
    job.comp = "active secret clearance required"
    res = curation.curate([job])
    assert res.parked and res.parked[0][1] == "excluded-domain"


def test_already_applied_company_is_parked():
    job = _job("AI Engineer", company="Acme")
    res = curation.curate([job], applied_slugs={job.company_slug})
    assert res.parked and res.parked[0][1] == "already-applied"


def test_already_applied_matches_url_org_when_name_is_missing():
    # Discovery rows sometimes lack a parseable company name; the ATS URL org is
    # the authoritative identity and must still hit the applied-ledger de-dup.
    job = Job(
        title="AI Engineer",
        company="?",
        url="https://jobs.ashbyhq.com/acme-widgets/12345678-90ab-cdef-1234-567890abcdef",
        location="Remote, US",
        remote=True,
        ats="ashby",
    )
    assert job.url_org_slug == "acmewidgets"
    res = curation.curate([job], applied_slugs={"acmewidgets"})
    assert res.parked and res.parked[0][1] == "already-applied"


def test_already_screened_role_is_parked_before_burning_a_screen_slot():
    # A role a prior run screened and rejected re-enters via a board harvest as a
    # fresh row (often with an unparseable company name); the role-level screened
    # ledger must catch it through the URL org so runs stop re-screening it.
    job = Job(
        title="Enterprise Solutions Engineer",
        company="?",
        url="https://jobs.ashbyhq.com/acme-widgets/12345678-90ab-cdef-1234-567890abcdef",
        location="Remote, US",
        remote=True,
        ats="ashby",
    )
    screened = {curation.role_key("acmewidgets", "Enterprise Solutions Engineer")}
    res = curation.curate([job], screened_keys=screened)
    assert res.parked and res.parked[0][1] == "already-screened"
    # A different role at the same screened company stays live.
    other = _job("AI Engineer", company="Acme Widgets")
    res2 = curation.curate([other], screened_keys=screened)
    assert res2.active


def test_already_screened_matches_company_embedded_as_hyphen_tail():
    # Harvest titles sometimes carry the company as a hyphen tail after a title
    # that itself contains hyphens ("Software Engineer, Full Stack - GTM - Acme").
    # The screened ledger stores ("acme", "Software Engineer, Full Stack - GTM");
    # every split point must be tried or the re-queued dup burns a screen slot.
    job = Job(
        title="Software Engineer, Full Stack - GTM - Acme Widgets",
        company="?",
        url="https://example.com/careers/123",
        location="Remote, US",
        remote=True,
    )
    screened = {curation.role_key("acmewidgets", "Software Engineer, Full Stack - GTM")}
    res = curation.curate([job], screened_keys=screened)
    assert res.parked and res.parked[0][1] == "already-screened"


def test_scientist_and_phd_are_parked_advanced_degree():
    # "Scientist" titles and PhD-required JDs are a credential knockout, even in-lane.
    res = curation.curate([_job("Applied AI/ML Scientist")])
    assert res.parked and res.parked[0][1] == "advanced-degree"
    res2 = curation.curate([_job("AI Engineer", company="Acme (PhD required)")])
    assert res2.parked and res2.parked[0][1] == "advanced-degree"


def test_non_us_only_is_parked():
    res = curation.curate([_job("AI Engineer", location="London, United Kingdom", remote=False)])
    assert res.parked and res.parked[0][1] == "non-us-only"


def test_explicit_non_us_location_beats_remote_label():
    # A location like "Remote - Australia" contains "remote", which satisfies
    # US_SIGNALS and used to slip past the non-us-only rule even though the
    # location names a non-US country outright. The explicit country wins.
    for location in ("Remote - Australia", "Remote, United Kingdom", "Remote, KSA; Remote, UAE"):
        res = curation.curate([_job("AI Engineer", location=location)])
        assert res.parked and res.parked[0][1] == "non-us-location", location
    # A location that names both a non-US country and the US stays kept.
    res = curation.curate([_job("AI Engineer", location="Remote - US or Canada")])
    assert not res.parked


def test_region_in_title_is_parked_non_us_region():
    # Regional roles often carry a bare "Hybrid" location, so the location rule
    # never fires; the title itself is the signal (incl. language requirements).
    for title in (
        "Solutions Engineer, Benelux",
        "Solutions Engineer, Nordics",
        "Solutions Engineer, Central & Eastern Europe - Hebrew Speaking",
        "Solutions Engineer, EMEA",
    ):
        res = curation.curate([_job(title, location="Hybrid", remote=False)])
        assert res.parked and res.parked[0][1] == "non-us-region", title


def test_hard_skill_gap_is_parked():
    res = curation.curate([_job("Kubernetes Platform Engineer", company="Acme Spark Kafka")])
    assert res.parked and res.parked[0][1] == "hard-skill-gap"


def test_comp_below_floor_is_parked():
    # A range topping under the floor can never clear the comp bar; park it
    # before a run spends a screen slot. Concatenated discovery text ("$110,0003+
    # years") and k-suffix ranges must both parse to the true ceiling.
    job = _job("AI Engineer")
    job.comp = "$93,500 - $110,0003+ years in a customer-facing role"
    res = curation.curate([job])
    assert res.parked and res.parked[0][1] == "comp-below-floor"

    ok = _job("AI Engineer")
    ok.comp = "$100k - $150k"
    assert curation.curate([ok]).active

    # Funding amounts and hourly rates are not annual salary signals: keep.
    funded = _job("AI Engineer")
    funded.comp = "$160M raised, $45.00 per hour contract track"
    assert curation.curate([funded]).active
    assert curation.comp_ceiling("no salary present") is None


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


def test_channel_bonus_breaks_ties_toward_ashby():
    ashby = Job(title="AI Engineer", company="A", url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef", ats="ashby", remote=True)
    lever = Job(title="AI Engineer", company="B", url="https://jobs.lever.co/b/12345678-90ab-cdef-1234-567890abcdef", ats="lever", remote=True)
    assert curation.fit_score(ashby) > curation.fit_score(lever)


def test_learned_filters_park_model_training_onsite_lead():
    # model-training (research engineering)
    r1 = curation.curate([Job(title="AI Engineer", company="Acme", url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef", ats="ashby", comp="RLHF and fine-tuning reward models")])
    assert r1.parked and r1.parked[0][1] == "model-training"
    # onsite/hybrid without remote
    r2 = curation.curate([Job(title="AI Engineer", company="Acme", url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef", ats="ashby", location="New York", comp="3 days a week in office", remote=False)])
    assert r2.parked and r2.parked[0][1] == "onsite-hybrid"
    # lead hiding behind an IC title
    r3 = curation.curate([Job(title="Forward Deployed Engineer", company="Acme", url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef", ats="ashby", comp="you will be the technical lead mentoring engineers")])
    assert r3.parked and r3.parked[0][1] == "lead-in-body"
