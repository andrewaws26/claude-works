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


def test_phd_negated_does_not_park():
    # A row noting the ABSENCE of a PhD ask ("no PhD gate") must not trip the
    # bare "phd" substring check the way the affirmative case above does.
    res = curation.curate([_job("AI Engineer", company="Acme (no PhD gate)")])
    assert res.active and res.active[0][0].title == "AI Engineer"


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
    # bare "Hybrid - City" title parenthetical (board-harvest [R] tags can lie about remote)
    r4 = curation.curate([Job(title="AI Engineer - AI Agents (Hybrid - San Francisco)", company="Acme", url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef", ats="ashby", remote=True)])
    assert r4.parked and r4.parked[0][1] == "onsite-hybrid"


def test_ic_leveling_code_does_not_hide_staff_scope():
    """A leveling code in the title says nothing; the body's scope sentence does."""
    url = "https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef"
    for body in (
        "you operate at staff scope on the hardest problems",
        "we are hiring a technical leader, not just a strong individual contributor",
    ):
        res = curation.curate([Job(title="Agent Engineer [IC4]", company="Acme",
                                   url=url, ats="ashby", remote=True, comp=body)])
        assert res.parked and res.parked[0][1] == "lead-in-body", body

    # The same leveling code with an ordinary senior-IC body stays in lane.
    kept = curation.curate([Job(title="Agent Engineer [IC4]", company="Acme", url=url,
                                ats="ashby", remote=True,
                                comp="build multi-step agent loops in typescript and python")])
    assert kept.parked == []
    assert kept.active


def test_delivery_architect_titles_are_parked_but_ai_builder_architect_is_kept():
    partner = Job(title="Partner Solutions Architect", company="Acme",
                  url="https://job-boards.greenhouse.io/acme/jobs/8578847002",
                  ats="greenhouse", location="Remote, United States", remote=True)
    prosvc = Job(title="Solution Architect, Professional Services", company="Acme",
                 url="https://job-boards.greenhouse.io/acme/jobs/8574466002",
                 ats="greenhouse", location="Remote, United States", remote=True)
    packaged = Job(title="Field Engineer", company="Acme",
                   url="https://job-boards.greenhouse.io/acme/jobs/8470951002",
                   ats="greenhouse", location="Remote, United States", remote=True,
                   comp="Appian and ServiceNow rollouts for enterprise clients")
    for job in (partner, prosvc, packaged):
        res = curation.curate([job])
        assert res.parked and res.parked[0][1] == "delivery-architect", job.title
    # A builder role that happens to carry an architect title stays active.
    builder = Job(title="Solutions Architect, AI Platform", company="Acme",
                  url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef",
                  ats="ashby", location="Remote, United States", remote=True,
                  comp="build agentic systems in Python with LLM APIs")
    assert curation.curate([builder]).active
    # The engineer-titled variant of the same seat, with no body text to screen
    # on, is caught by the title shape alone (as pre-sales, the segment-title rule
    # that runs first, which is the more accurate reason for a go-to-market seat).
    partner_se = Job(title="Partner Solutions Engineer", company="Acme",
                     url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef",
                     ats="ashby", location="Remote, United States", remote=True)
    res_se = curation.curate([partner_se])
    assert res_se.parked and res_se.parked[0][1] == "pre-sales"


def test_segment_qualified_solutions_engineer_titles_are_parked_without_body_text():
    # Board-summary rows carry only a title, so the body-gated pre-sales rule
    # cannot reach a go-to-market seat. Segment and territory qualifiers on a
    # solutions-engineer title are enough on their own.
    for title in ("Enterprise Solutions Engineer", "Mid-Market Solutions Engineer",
                  "Field Solutions Engineer, West", "Strategic Solutions Engineer"):
        job = Job(title=title, company="Acme",
                  url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef",
                  ats="ashby", location="Remote, United States", remote=True)
        res = curation.curate([job])
        assert res.parked and res.parked[0][1] == "pre-sales", title
    # A builder-shaped title that merely contains the words stays active.
    builder = Job(title="AI Solutions Engineer", company="Acme",
                  url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef",
                  ats="ashby", location="Remote, United States", remote=True,
                  comp="build agentic systems in Python and TypeScript with LLM APIs")
    assert curation.curate([builder]).active
    # A solutions-engineer title without a partner marker is still a builder role.
    builder_se = Job(title="Solutions Engineer, AI", company="Acme",
                     url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef",
                     ats="ashby", location="Remote, United States", remote=True,
                     comp="build agentic systems in Python with LLM APIs")
    assert curation.curate([builder_se]).active


def test_ats_demo_board_is_parked():
    demo = Job(title="Solutions Architect", company="Lever Demo",
               url="https://jobs.lever.co/leverdemo/12345678-90ab-cdef-1234-567890abcdef",
               ats="lever", location="Remote, United States", remote=True)
    res = curation.curate([demo])
    assert res.parked and res.parked[0][1] == "demo-board"


def test_remote_posting_with_excluding_time_zone_list_is_parked():
    # "United States - Remote" in the location, but the body limits the seat to
    # zones the candidate is not in. The onsite list cannot catch this.
    restricted = Job(title="Solutions Engineer, Mid-Market", company="Acme",
                     url="https://job-boards.greenhouse.io/acme/jobs/7284384002",
                     ats="greenhouse", location="United States - Remote", remote=True,
                     comp="Open to candidates located in the Central, Mountain, and Pacific time zones.")
    r = curation.curate([restricted])
    assert r.parked and r.parked[0][1] == "time-zone-restricted"


def test_time_zone_language_that_does_not_exclude_the_candidate_is_kept():
    assert not curation.is_time_zone_restricted("you will collaborate across many time zones")
    assert not curation.is_time_zone_restricted("must be located in the eastern or central time zone")
    assert not curation.is_time_zone_restricted("remote across all us time zones, including eastern")
    assert curation.is_time_zone_restricted("must reside in the pacific or mountain time zone")


def test_hybrid_only_org_is_parked(monkeypatch):
    # The board fetch showed no posting with workplaceType "Remote", so the org
    # was recorded as hybrid-only. Every later req from it parks without a screen,
    # even when the posting advertises itself as remote.
    monkeypatch.setattr(curation, "HYBRID_ONLY_ORGS", frozenset({"acmeinfra"}))
    sibling = Job(title="Solutions Architect, AI Platform", company="Acme Infra",
                  url="https://jobs.ashbyhq.com/acmeinfra/12345678-90ab-cdef-1234-567890abcdef",
                  ats="ashby", location="Remote, United States", remote=True,
                  comp="build agentic systems in Python with LLM APIs")
    res = curation.curate([sibling])
    assert res.parked and res.parked[0][1] == "hybrid-only-org"
    # An org that is not in the set is unaffected by the rule.
    other = Job(title="Solutions Architect, AI Platform", company="Other",
                url="https://jobs.ashbyhq.com/other/12345678-90ab-cdef-1234-567890abcdef",
                ats="ashby", location="Remote, United States", remote=True,
                comp="build agentic systems in Python with LLM APIs")
    assert curation.curate([other]).active


def test_railed_role_family_parks_the_next_segment_suffix():
    # An org posted the same job under many segment suffixes; each rejection got
    # its own role key, so the next suffix arrived unscreened. Once enough
    # siblings share a title family, the family itself is the rail.
    families = {("acmewidgets", curation.role_family("Applied Architect")): 8}
    job = _job("Applied Architect, Startups", company="Acme Widgets")
    res = curation.curate([job], railed_families=families)
    assert res.parked and res.parked[0][1] == "railed-role-family"
    # An unrelated family at the same org stays live.
    other = _job("Product Engineer, Platform", company="Acme Widgets")
    assert curation.curate([other], railed_families=families).active
    # Below the threshold nothing is knocked out.
    thin = {("acmewidgets", curation.role_family("Applied Architect")): 2}
    assert curation.curate([job], railed_families=thin).active


def test_role_family_ignores_single_word_bases():
    # A one-word base ("Engineer") would knock out unrelated roles, so it never
    # forms a family key.
    assert curation.role_family("Engineer, Platform") == ""
    assert curation.role_family("Applied Architect, Startups") == curation.role_family(
        "Applied Architect, Commercial"
    )


def test_explicit_in_office_mandate_outranks_a_remote_label():
    # Harvest rows get hand-stamped "(Remote US)" in bulk. That word in the blob
    # disables the plain onsite rule, so an explicit mandate has to park on its own.
    mandate = Job(title="Forward Deployed Engineer (Remote US)", company="Acme",
                  url="https://jobs.ashbyhq.com/a/12345678-90ab-cdef-1234-567890abcdef",
                  ats="ashby", location="Remote US", remote=True,
                  comp="This role requires you to work Monday to Thursday in our New York office")
    r = curation.curate([mandate])
    assert r.parked and r.parked[0][1] == "onsite-hybrid"

    in_person = Job(title="Software Engineer, Applied AI (Remote US)", company="Acme",
                    url="https://jobs.ashbyhq.com/a/22345678-90ab-cdef-1234-567890abcdef",
                    ats="ashby", location="Remote US", remote=True,
                    comp="This role is in-person in San Francisco")
    r2 = curation.curate([in_person])
    assert r2.parked and r2.parked[0][1] == "onsite-hybrid"


def test_office_anchored_location_beats_the_remote_flag():
    job = Job(title="Forward Deployed Engineer", company="Acme",
              url="https://jobs.ashbyhq.com/a/32345678-90ab-cdef-1234-567890abcdef",
              ats="ashby", location="NYC Office", remote=True)
    r = curation.curate([job])
    assert r.parked and r.parked[0][1] == "office-anchored-location"


def test_genuine_remote_row_survives_the_new_onsite_rules():
    job = Job(title="Forward Deployed Engineer", company="Acme",
              url="https://jobs.ashbyhq.com/a/42345678-90ab-cdef-1234-567890abcdef",
              ats="ashby", location="Remote, United States", remote=True)
    r = curation.curate([job])
    assert r.active and not r.parked


def test_excluded_vertical_org_is_parked(monkeypatch):
    # A dual-use vendor that names an excluded industry as a customer and vertical
    # in its About copy. The harvested row carries only a short title, so no body
    # term is present for a text rail to match, and the title and stack read clean.
    # The org entry is what parks it, and the match is on the exact slug.
    monkeypatch.setattr(curation, "EXCLUDED_VERTICAL_ORGS", frozenset({"acmesync"}))
    row = Job(title="AI Engineer", company="Acme Sync",
              url="https://jobs.ashbyhq.com/acmesync/12345678-90ab-cdef-1234-567890abcdef",
              ats="ashby", location="Remote, United States", remote=True,
              comp="agent runtime, typed tool surface, MCP servers, evals in Python")
    res = curation.curate([row])
    assert res.parked and res.parked[0][1] == "excluded-vertical-org"
    # A longer slug that merely starts with the same letters is not a match.
    lookalike = Job(title="AI Engineer", company="Acmesync Labs",
                    url="https://jobs.ashbyhq.com/acmesynclabs/12345678-90ab-cdef-1234-567890abcdef",
                    ats="ashby", location="Remote, United States", remote=True,
                    comp="agent runtime, typed tool surface, MCP servers, evals in Python")
    assert curation.curate([lookalike]).active
