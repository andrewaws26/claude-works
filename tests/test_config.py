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
