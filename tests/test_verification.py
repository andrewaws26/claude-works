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


class _FakeBox:
    """Minimal IMAP stand-in: two messages, one newsletter and one ATS code mail."""

    MAIL = {
        b"1": b"From: Store <news@example-shop.com>\r\nSubject: Big sale\r\n\r\nhuge body",
        b"2": (b"From: Greenhouse <no-reply@us.greenhouse-mail.io>\r\n"
               b"Subject: Security code for your application\r\n\r\n"
               b"Your security code is: Ab3dEf7H"),
    }

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.full_fetches: list[bytes] = []

    def login(self, *a):
        return "OK", [b""]

    def select(self, *a, **k):
        return "OK", [b"2"]

    def search(self, *a):
        return "OK", [b"1 2"]

    def fetch(self, num, spec):
        raw = self.MAIL[num]
        if "HEADER.FIELDS" in spec:
            return "OK", [(b"hdr", raw.split(b"\r\n\r\n")[0] + b"\r\n\r\n")]
        self.full_fetches.append(num)
        return "OK", [(b"body", raw)]

    def logout(self):
        return "BYE", [b""]


def _creds(monkeypatch):
    monkeypatch.setenv("JOBSEARCH_GMAIL_ADDR", "applicant@example.com")
    monkeypatch.setenv("JOBSEARCH_GMAIL_APP_PASSWORD", "x" * 16)


def test_full_body_fetched_only_for_ats_sender_mail(monkeypatch):
    _creds(monkeypatch)
    boxes: list[_FakeBox] = []

    def factory(*a, **k):
        boxes.append(_FakeBox(*a, **k))
        return boxes[-1]

    monkeypatch.setattr(verification.imaplib, "IMAP4_SSL", factory)
    out = verification.fetch_verification_code(minutes=60 * 24 * 365 * 50)
    assert out == {"status": "OK", "code": "Ab3dEf7H"}
    assert boxes[0].full_fetches == [b"2"]  # the newsletter body is never downloaded
    assert boxes[0].kwargs.get("timeout") == verification.IMAP_TIMEOUT_S


def test_socket_errors_become_a_status_not_a_hang_or_crash(monkeypatch):
    _creds(monkeypatch)

    def boom(*a, **k):
        raise ConnectionResetError(54, "Connection reset by peer")

    monkeypatch.setattr(verification.imaplib, "IMAP4_SSL", boom)
    assert verification.fetch_verification_code()["status"] == "NO_CODE_FOUND"
