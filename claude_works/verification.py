"""Fetch the latest ATS email-verification code from the applicant's own inbox.

Some ATSes (Greenhouse especially, against headless sessions) gate the final
submit behind a short code emailed to the applicant. That is email-OWNERSHIP
verification of the applicant's own application, not a captcha and not a no-AI
attestation, so completing it is inside the honesty rails. hCaptcha, "are you a
robot" checks, and no-AI attestations are NEVER handled here or anywhere else:
those park for the human.

Safety properties, in order of importance:

  * READ-ONLY: IMAP with ``BODY.PEEK``; never marks mail read, never deletes or
    moves anything.
  * SCOPED: only recent messages whose SENDER matches a known ATS domain are
    considered, so personal mail is never read. Generic no-reply senders are
    deliberately not matched (they catch account-security alerts and produce
    false codes).
  * NO STORED SECRET: the app password (never the account password) comes from
    ``JOBSEARCH_GMAIL_APP_PASSWORD`` or a chmod-600 dotfile, both outside the
    repo. Missing credentials return a status, never a crash mid-submit.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any

IMAP_HOST_ENV = "JOBSEARCH_IMAP_HOST"          # default imap.gmail.com
ADDRESS_ENVS = ("JOBSEARCH_GMAIL_ADDR", "JOBSEARCH_APPLY_EMAIL")
PASSWORD_ENV = "JOBSEARCH_GMAIL_APP_PASSWORD"
PASSWORD_FILES = ("~/.gmail_app_password", "~/.config/jobsearch/gmail_app_password")

# Specific ATS sender domains only. NOT generic no-reply / notifications (those
# match account security alerts, calendar mail, etc. and produce false codes).
ATS_SENDER_HINTS = (
    "greenhouse", "greenhouse-mail", "ashby", "ashbyhq", "lever.co", "workable",
    "hirebridge", "gem.com", "smartrecruiters", "icims", "jobvite", "myworkday",
)

# Context patterns; the captured token is still validated by _looks_like_code so
# a word like 'for'/'into' after the word 'code' is rejected.
CONTEXT_PATTERNS = (
    r"(?:security code|verification code|one[- ]time code|your code|the following code|"
    r"code is|enter (?:the |this )?code)[\s:\-]*\*{0,2}([A-Za-z0-9]{4,8})\b",
    r"\b([A-Za-z0-9]{6,8})\b\s*(?:is your (?:security |verification )?code|to (?:confirm|verify))",
)
_DIGIT_RE = re.compile(r"\b(\d{6})\b")
_TOKEN_RE = re.compile(r"\b([A-Za-z0-9]{6,8})\b")
_COMMON_WORDS = frozenset({
    "address", "company", "various", "engineer", "received", "applying",
    "greenhouse", "applicant", "position", "schedule", "interview", "yourself",
    "minutes", "expires", "account", "support", "confirm", "verify", "please",
})


def _address() -> str | None:
    for env in ADDRESS_ENVS:
        v = os.environ.get(env)
        if v:
            return v
    return None


def _password() -> str | None:
    p = os.environ.get(PASSWORD_ENV)
    if p:
        return p.replace(" ", "")
    for path in PASSWORD_FILES:
        fp = os.path.expanduser(path)
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as fh:
                return fh.read().strip().replace(" ", "")
    return None


def _body_text(msg: Message) -> str:
    parts: list[str] = []
    payloads = msg.walk() if msg.is_multipart() else [msg]
    for part in payloads:
        if part.get_content_type() in ("text/plain", "text/html"):
            try:
                raw = part.get_payload(decode=True)
                if isinstance(raw, (bytes, bytearray)):
                    parts.append(raw.decode(part.get_content_charset() or "utf-8", "ignore"))
            except Exception:  # noqa: BLE001 - skip undecodable parts
                pass
    return re.sub(r"<[^>]+>", " ", " ".join(parts))


def looks_like_code(tok: str) -> bool:
    """Codes carry digits or mixed case; plain dictionary words never do."""
    if tok.lower() in _COMMON_WORDS or len(set(tok)) == 1:
        return False
    has_digit = any(c.isdigit() for c in tok)
    has_mixed = any(c.islower() for c in tok) and any(c.isupper() for c in tok)
    return has_digit or has_mixed


def extract_code(text: str) -> str | None:
    """Pull the most likely verification code out of subject+body text."""
    for pat in CONTEXT_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            if looks_like_code(m.group(1)):
                return m.group(1)
    for m in _DIGIT_RE.finditer(text):
        if len(set(m.group(1))) > 1:  # reject 000000-style repeated-digit noise
            return m.group(1)
    for m in _TOKEN_RE.finditer(text):
        if looks_like_code(m.group(1)):
            return m.group(1)
    return None


def fetch_verification_code(minutes: int = 15) -> dict[str, Any]:
    """The newest ATS verification code from the applicant's inbox, or a status.

    Returns ``{"status": "OK", "code": ...}`` on success. ``NO_CREDENTIALS``
    means the app password or address is not configured; ``NO_CODE_FOUND``
    means no matching ATS mail carried a code inside the window (the email may
    still be in transit: retry up to 3 times with ~8 second waits before
    parking the application).
    """
    addr, pw = _address(), _password()
    if not (addr and pw):
        return {"status": "NO_CREDENTIALS",
                "detail": f"set {ADDRESS_ENVS[1]} and {PASSWORD_ENV} (a revocable app password)"}

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    try:
        box = imaplib.IMAP4_SSL(os.environ.get(IMAP_HOST_ENV, "imap.gmail.com"))
        box.login(addr, pw)
        box.select("INBOX", readonly=True)
        # IMAP SINCE is date-granular; fetch the day's mail, filter by time + sender.
        data = box.search(None, f'(SINCE "{cutoff.strftime("%d-%b-%Y")}")')[1]
        candidates: list[tuple[datetime, str]] = []
        for num in reversed(data[0].split()[-40:]):  # newest first, cap the work
            typ, msg_data = box.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            frm = (msg.get("From") or "").lower()
            if not any(h in frm for h in ATS_SENDER_HINTS):
                continue
            try:
                dt = parsedate_to_datetime(msg.get("Date") or "")
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:  # noqa: BLE001
                dt = None
            if dt and dt < cutoff:
                continue
            code = extract_code((msg.get("Subject") or "") + "  " + _body_text(msg))
            if code:
                candidates.append((dt or cutoff, code))
        box.logout()
    except imaplib.IMAP4.error as e:
        return {"status": "NO_CODE_FOUND", "detail": f"IMAP error: {e}"}
    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        return {"status": "OK", "code": candidates[0][1]}
    return {"status": "NO_CODE_FOUND", "detail": f"no ATS verification mail in the last {minutes} min"}
