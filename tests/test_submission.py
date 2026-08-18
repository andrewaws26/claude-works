"""Unit tests for ATS classification and the fill-and-park plan builder."""

from __future__ import annotations

from claude_works import submission
from claude_works.models import Job


def test_classify_ats_from_url():
    assert submission.classify_ats(Job("X", "Acme", "https://jobs.ashbyhq.com/acme/x")) == "ashby"
    assert submission.classify_ats(Job("X", "Acme", "https://boards.greenhouse.io/acme/jobs/1")) == "greenhouse"
    assert submission.classify_ats(Job("X", "Acme", "https://jobs.lever.co/acme/x")) == "lever"
    assert submission.classify_ats(Job("X", "Acme", "https://acme.myworkdayjobs.com/x")) == "workday"
    assert submission.classify_ats(Job("X", "Acme", "https://example.com/careers")) == "unknown"
    assert submission.classify_ats(Job("X", "Acme", "https://acme.pinpointhq.com/en/postings/1")) == "pinpointhq"


def test_ashby_plan_auto_submits_without_human_step():
    plan = submission.plan_submission(
        Job("AI Engineer", "Acme", "https://jobs.ashbyhq.com/acme/x")
    )
    assert plan.action == "auto_submit"
    assert plan.human_step is None
    assert any("Ashby labeled-radio" in n for n in plan.notes)


def test_plan_never_leaks_credentials_into_fields_by_default():
    plan = submission.plan_submission(
        Job("AI Engineer", "Acme", "https://jobs.ashbyhq.com/acme/x")
    )
    assert "portal_password" not in plan.fields
    assert "portal_username" not in plan.fields
    # Honest screening answers are present and self-identify questions are declined.
    assert plan.screening_answers["authorized_to_work_us"] == "Yes"
    assert plan.screening_answers["require_sponsorship"] == "No"


def test_lever_plan_fills_and_parks_at_a_human_step():
    plan = submission.plan_submission(
        Job("AI Engineer", "Acme", "https://jobs.lever.co/acme/x")
    )
    assert plan.action == "fill_and_park"
    assert plan.human_step is not None
    assert "captcha" in plan.human_step.lower()


def test_excluded_company_plan_is_blocked():
    plan = submission.plan_submission(
        Job("AI Engineer", "Samsara", "https://jobs.ashbyhq.com/samsara/x")
    )
    assert plan.action == "blocked"
    assert plan.rail_block is not None


def test_unknown_ats_defaults_to_fill_and_park():
    plan = submission.plan_submission(
        Job("AI Engineer", "Acme", "https://example.com/careers")
    )
    assert plan.action == "fill_and_park"
    assert plan.human_step is not None


def test_classify_workable_and_hirebridge():
    assert submission.classify_ats(Job("X", "Acme", "https://apply.workable.com/acme/j/ABC123/")) == "workable"
    assert submission.classify_ats(Job("X", "Acme", "https://recruit.hirebridge.com/v3/Jobs/JobDetails.aspx?jid=1")) == "hirebridge"


def test_classify_jazzhr_and_parks_at_recaptcha():
    job = Job("AI Engineer", "Acme", "https://acme.applytojob.com/apply/abc123/AI-Engineer")
    assert submission.classify_ats(job) == "jazzhr"
    plan = submission.plan_submission(job)
    assert plan.action == "fill_and_park"
    assert plan.human_step is not None


def test_workable_auto_submits_with_date_and_address_gotchas():
    plan = submission.plan_submission(
        Job("AI Engineer", "Acme", "https://apply.workable.com/acme/j/ABC123/")
    )
    assert plan.action == "auto_submit"
    assert any("pressSequentially" in n for n in plan.notes)
    assert any("autocomplete suggestion" in n for n in plan.notes)


def test_hirebridge_auto_submits_with_email_gate_and_formvalidation_gotchas():
    plan = submission.plan_submission(
        Job("Agentic Engineer", "Acme", "https://recruit.hirebridge.com/v3/Jobs/JobDetails.aspx?jid=1")
    )
    assert plan.action == "auto_submit"
    assert any("RE-TYPE it" in n for n in plan.notes)
    assert any("FormValidation" in n for n in plan.notes)


def test_brightmove_auto_submits_with_account_creation_gotchas():
    job = Job("Applied AI Engineer", "Acme", "https://portal.brightmove.com/jb.do?reqGK=1&companyGK=2&portalGK=3")
    assert submission.classify_ats(job) == "brightmove"
    plan = submission.plan_submission(job)
    assert plan.action == "auto_submit"
    assert any("account creation" in n for n in plan.notes)
    assert any("Application Received" in n for n in plan.notes)


def test_successfactors_auto_submits_with_account_creation_gotchas():
    job = Job("AI Data Engineer", "Acme", "https://career41.sapsf.com/career?company=acmeinc")
    assert submission.classify_ats(job) == "successfactors"
    plan = submission.plan_submission(job)
    assert plan.action == "auto_submit"
    assert any("Account Already Exists" in n for n in plan.notes)
    assert any("Your application has been sent" in n for n in plan.notes)


def test_bamboohr_parks_at_recaptcha():
    job = Job("AI Platform Engineer", "Acme", "https://acme.bamboohr.com/careers/246")
    assert submission.classify_ats(job) == "bamboohr"
    plan = submission.plan_submission(job)
    assert plan.action == "fill_and_park"
    assert plan.human_step is not None and "reCAPTCHA" in plan.human_step
    assert any("reCAPTCHA" in n for n in plan.notes)
    assert any("client-rendered" in n for n in plan.notes)


def test_comeet_parks_at_session_verification_bot_check():
    job = Job("Forward Deployed AI Engineer", "Acme", "https://www.comeet.com/jobs/acme/1.234/role/5.678")
    assert submission.classify_ats(job) == "comeet"
    plan = submission.plan_submission(job)
    assert plan.action == "fill_and_park"
    assert plan.human_step is not None and "session-verification" in plan.human_step
    assert any("session verification failed" in n for n in plan.notes)
    assert any("applynow.io" in n for n in plan.notes)


def test_every_plan_carries_the_general_gotchas_memory():
    plan = submission.plan_submission(
        Job("AI Engineer", "Acme", "https://jobs.ashbyhq.com/acme/x")
    )
    assert any("remote flag is not proof" in n for n in plan.notes)


def test_identity_fields_come_from_env_only(monkeypatch):
    # No env set: the plan carries no identity PII at all (nothing is hard-coded).
    for var in ("JOBSEARCH_APPLY_NAME", "JOBSEARCH_APPLY_EMAIL", "JOBSEARCH_APPLY_PHONE",
                "JOBSEARCH_APPLY_LOCATION", "JOBSEARCH_APPLY_WEBSITE",
                "JOBSEARCH_APPLY_LINKEDIN", "JOBSEARCH_APPLY_GITHUB"):
        monkeypatch.delenv(var, raising=False)
    bare = submission.plan_submission(Job("AI Engineer", "Acme", "https://jobs.ashbyhq.com/acme/x"))
    assert bare.fields == {}

    monkeypatch.setenv("JOBSEARCH_APPLY_NAME", "Jordan Example")
    monkeypatch.setenv("JOBSEARCH_APPLY_GITHUB", "github.com/jordan-example")
    filled = submission.plan_submission(Job("AI Engineer", "Acme", "https://jobs.ashbyhq.com/acme/x"))
    assert filled.fields["name"] == "Jordan Example"
    assert filled.fields["github"] == "github.com/jordan-example"


def test_rail_block_is_whole_word_not_substring():
    # 'axon' excluded must not block 'Axonius'.
    plan = submission.plan_submission(
        Job("AI Engineer", "Axonius", "https://jobs.ashbyhq.com/axonius/x")
    )
    assert plan.action != "blocked"
