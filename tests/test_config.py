"""Unit tests for environment-only credential reads and the rails policy."""

from __future__ import annotations

import pytest

from claude_works import config


def test_get_credential_reads_from_environment(monkeypatch):
    monkeypatch.setenv("JOBSEARCH_APPLY_EMAIL", "you@example.com")
    assert config.get_credential("email") == "you@example.com"


def test_get_credential_raises_loudly_when_unset(monkeypatch):
    monkeypatch.delenv("JOBSEARCH_APPLY_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        config.get_credential("password")


def test_get_credential_rejects_unknown_field():
    with pytest.raises(RuntimeError):
        config.get_credential("not_a_real_field")


def test_env_var_mapping_is_namespaced():
    assert config.RAILS.env_var_for("password") == "JOBSEARCH_APPLY_PASSWORD"
    assert config.RAILS.env_var_for("email") == "JOBSEARCH_APPLY_EMAIL"
    assert config.RAILS.env_var_for("bogus") == ""


def test_rails_policy_contains_expected_guards():
    assert config.RAILS.pursue_threshold > 0
    assert "director" in config.RAILS.overlevel_terms
    assert "defense" in config.RAILS.excluded_domains


def test_rails_from_env_reads_overrides_at_call_time(monkeypatch):
    monkeypatch.setenv("JOBSEARCH_COMP_FLOOR", "99000")
    monkeypatch.setenv("JOBSEARCH_PURSUE_THRESHOLD", "8.5")
    rails = config.Rails.from_env()
    assert rails.comp_floor == 99000
    assert rails.pursue_threshold == 8.5


def test_load_policy_absent_returns_empty(tmp_path):
    assert config.load_policy(tmp_path / "policy.json") == {}


def test_load_policy_invalid_fails_loudly(tmp_path):
    bad = tmp_path / "policy.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        config.load_policy(bad)
    not_obj = tmp_path / "policy2.json"
    not_obj.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(RuntimeError):
        config.load_policy(not_obj)


def test_rails_from_policy_replaces_lists_wholesale(monkeypatch):
    monkeypatch.delenv("JOBSEARCH_COMP_FLOOR", raising=False)
    rails = config.Rails.from_env(policy={
        "comp_floor": 90000,
        "excluded_companies": ["Initech"],
        "hard_gap_skills": [],
    })
    assert rails.comp_floor == 90000
    assert rails.excluded_companies == ("initech",)
    assert rails.hard_gap_skills == ()          # empty list turns the check off
    assert "director" in rails.overlevel_terms  # omitted key keeps the default


def test_env_beats_policy_for_numbers(monkeypatch):
    monkeypatch.setenv("JOBSEARCH_COMP_FLOOR", "150000")
    rails = config.Rails.from_env(policy={"comp_floor": 90000})
    assert rails.comp_floor == 150000
