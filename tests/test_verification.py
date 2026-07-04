"""Code extraction for the emailed-verification gate (pure functions, no IMAP)."""

from __future__ import annotations

from claude_works import verification


def test_extracts_context_pattern_codes():
    assert verification.extract_code("Your verification code is: J3HfidxT today") == "J3HfidxT"
    assert verification.extract_code("enter the code 481923 to continue") == "481923"


def test_rejects_dictionary_words_and_repeated_digits():
    # 'confirm' follows 'code is' syntactically but is a common word, not a code.
    assert verification.extract_code("the code is confirm") is None
    assert verification.extract_code("codes like 000000 are noise") is None


def test_bare_token_fallback_needs_digits_or_mixed_case():
    assert verification.extract_code("random text kVslYwYN more text") == "kVslYwYN"
    assert verification.extract_code("nothing here but plain words") is None


def test_missing_credentials_is_a_status_not_a_crash(monkeypatch):
    for env in ("JOBSEARCH_GMAIL_ADDR", "JOBSEARCH_APPLY_EMAIL", "JOBSEARCH_GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(verification, "PASSWORD_FILES", ())
    out = verification.fetch_verification_code()
    assert out["status"] == "NO_CREDENTIALS"
