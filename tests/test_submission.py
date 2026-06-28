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


def test_every_plan_carries_the_general_gotchas_memory():
    plan = submission.plan_submission(
        Job("AI Engineer", "Acme", "https://jobs.ashbyhq.com/acme/x")
    )
    assert any("remote flag is not proof" in n for n in plan.notes)
