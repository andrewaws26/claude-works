"""The built-in boards source and JD fetch, tested against canned API payloads
(the fetcher is injectable, so no test touches the network)."""

from __future__ import annotations

import pytest

from claude_works import boards

ASHBY_BOARD = {
    "name": "Acme Agents",
    "jobs": [
        {"id": "11111111-2222-3333-4444-555555555555", "title": "Forward Deployed Engineer",
         "location": "Remote, US", "isRemote": True,
         "applyUrl": "https://jobs.ashbyhq.com/acme-agents/11111111-2222-3333-4444-555555555555",
         "compensation": {"compensationTierSummary": "$150K - $200K"},
         "descriptionHtml": "<p>Build <b>agents</b> with MCP.</p><ul><li>Own evals</li></ul>"},
        {"id": "no-url", "title": "Ghost Role"},
    ],
}
GH_BOARD = {
    "jobs": [
        {"title": "Applied AI Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/7010001",
         "location": {"name": "Remote (United States)"}},
    ],
}
GH_JOB = {
    "title": "Applied AI Engineer", "location": {"name": "Remote (United States)"},
    "content": "&lt;p&gt;Ship LLM features. Kubernetes required.&lt;/p&gt;",
}
LEVER_BOARD = [
    {"text": "Solutions Engineer", "hostedUrl": "https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
     "categories": {"location": "Remote, US"}, "workplaceType": "remote"},
]
LEVER_JOB = {
    "text": "Solutions Engineer", "categories": {"location": "Remote, US"}, "workplaceType": "remote",
    "descriptionPlain": "Work with customers.",
    "lists": [{"text": "Requirements", "content": "<li>Python</li><li>APIs</li>"}],
}


def test_ashby_board_parses_jobs_and_skips_incomplete():
    jobs = boards.ashby_board_jobs("acme-agents", fetch=lambda url: ASHBY_BOARD)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Forward Deployed Engineer" and j.company == "Acme Agents"
    assert j.remote is True and j.ats == "Ashby" and "$150K" in j.comp
    assert not j.role_key.startswith("raw:")


def test_greenhouse_and_lever_boards_parse():
    gh = boards.greenhouse_board_jobs("acme", fetch=lambda url: GH_BOARD)
    assert gh[0].remote is True and gh[0].ats == "Greenhouse"
    lv = boards.lever_board_jobs("acme", fetch=lambda url: LEVER_BOARD)
    assert lv[0].remote is True and lv[0].ats == "Lever"


def test_boards_sweep_skips_failing_boards_without_dying():
    def fetch(url):
        if "ashbyhq" in url:
            raise OSError("HTTP 404 (org disabled the public API)")
        if "greenhouse" in url:
            return GH_BOARD
        return LEVER_BOARD

    jobs, skipped = boards.boards_sweep(
        seeds={"ashby": ("deadorg",), "greenhouse": ("acme",), "lever": ("acme",)}, fetch=fetch
    )
    assert len(jobs) == 2
    assert len(skipped) == 1 and "deadorg" in skipped[0]


def test_seed_boards_policy_override():
    seeds = boards.seed_boards(policy={"seed_boards": {"ashby": ["MyCo"]}})
    assert seeds == {"ashby": ("myco",)}
    assert boards.seed_boards(policy={}) == boards.DEFAULT_SEED_BOARDS


def test_html_to_text_flattens_blocks_and_entities():
    text = boards.html_to_text("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;li&gt;One&lt;/li&gt;")
    assert "Hello & welcome" in text and "One" in text and "<" not in text


@pytest.mark.parametrize("url,expect_title,expect_in_text", [
    ("https://jobs.ashbyhq.com/acme-agents/11111111-2222-3333-4444-555555555555",
     "Forward Deployed Engineer", "Own evals"),
    ("https://boards.greenhouse.io/acme/jobs/7010001", "Applied AI Engineer", "Kubernetes required"),
    ("https://jobs.lever.co/acme/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "Solutions Engineer", "Python"),
])
def test_fetch_job_description_all_ats(url, expect_title, expect_in_text):
    def fetch(u):
        if "ashbyhq" in u:
            return ASHBY_BOARD
        if "greenhouse" in u:
            return GH_JOB
        return LEVER_JOB

    jd = boards.fetch_job_description(url, fetch=fetch)
    assert jd.get("error") is None or "error" not in jd
    assert jd["title"] == expect_title
    assert expect_in_text in jd["text"]


def test_fetch_job_description_honest_failures():
    missing = boards.fetch_job_description(
        "https://jobs.ashbyhq.com/acme-agents/99999999-9999-9999-9999-999999999999",
        fetch=lambda u: ASHBY_BOARD)
    assert "closed" in missing["error"]
    disabled = boards.fetch_job_description(
        "https://jobs.ashbyhq.com/acme-agents/11111111-2222-3333-4444-555555555555",
        fetch=lambda u: (_ for _ in ()).throw(OSError("404")))
    assert "disabled" in disabled["error"]
    assert "unrecognized" in boards.fetch_job_description("https://example.com/careers")["error"]


def test_ashby_workplace_type_beats_is_remote():
    """A Hybrid posting sets isRemote=True; workplaceType is the ruling field."""
    board = {"name": "Acme Agents", "jobs": [
        {"id": "hybrid-1", "title": "Forward Deployed Engineer",
         "location": "Foster City, CA", "isRemote": True, "workplaceType": "Hybrid",
         "applyUrl": "https://jobs.ashbyhq.com/acme-agents/hybrid-1"},
        {"id": "remote-1", "title": "Applied AI Engineer",
         "location": "Remote, US", "isRemote": True, "workplaceType": "Remote",
         "applyUrl": "https://jobs.ashbyhq.com/acme-agents/remote-1"},
        {"id": "onsite-1", "title": "Platform Engineer",
         "location": "San Francisco", "isRemote": True, "workplaceType": "OnSite",
         "applyUrl": "https://jobs.ashbyhq.com/acme-agents/onsite-1"},
    ]}
    jobs = boards.ashby_board_jobs("acme-agents", fetch=lambda url: board)
    assert [j.remote for j in jobs] == [False, True, False]


def test_ashby_falls_back_to_is_remote_without_workplace_type():
    jobs = boards.ashby_board_jobs("acme-agents", fetch=lambda url: ASHBY_BOARD)
    assert jobs[0].remote is True


def _board_jobs(count: int, locations: list[str]) -> list:
    from claude_works.models import Job
    return [Job(title=f"Founding Engineer {i}", company="Marketplace",
                url=f"https://jobs.ashbyhq.com/marketplace/{i}", source="boards/ashby/marketplace",
                location=locations[i % len(locations)], ats="Ashby")
            for i in range(count)]


def test_aggregator_board_needs_both_scale_and_spread():
    cities = [f"City {i}" for i in range(25)]
    assert boards.looks_like_aggregator_board(_board_jobs(300, cities)) is True
    # Big employer, few offices: not a marketplace.
    assert boards.looks_like_aggregator_board(_board_jobs(300, ["Austin", "Berlin"])) is False
    # Distributed startup across many cities, but a plausible number of reqs.
    assert boards.looks_like_aggregator_board(_board_jobs(30, cities)) is False
