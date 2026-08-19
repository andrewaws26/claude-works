"""Read and append the application ledger and the discovery queue.

The ledger (``applications.json``) and the queue (``top300_jobs.json``) are the
durable state that makes the perpetual loop resumable: every firing picks up from
them. This module reads them and appends to them in place, preserving the exact
on-disk schema the cron loop expects, so the MCP and the loop never disagree.

De-duplication is by ROLE (org + job id), never by company.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from .config import PATHS
from .models import Application, Job, _slug


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@contextlib.contextmanager
def _ledger_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for a read-modify-write on ``path``.

    Multiple loop instances share the ledger; without this, two concurrent
    ``record_application`` calls can each read the same document and the second
    write silently drops the first row. The lock lives on a sidecar ``.lock``
    file so it also serializes with a writer that replaces the ledger itself.
    On platforms without ``fcntl`` (Windows) it degrades to no locking.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX fallback
        yield
        return
    with open(lock_path, "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _write_json_atomic(path: Path, doc: Any, indent: int = 1) -> None:
    """Write JSON to a temp file in the same directory, then ``os.replace``.

    A crash mid-write leaves the old document intact instead of a truncated,
    corrupt one; readers never observe a half-written file.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=indent)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def load_applications(path: Path | None = None) -> dict[str, Any]:
    """Return the full ledger document (``{generated, applicant, applications}``)."""
    p = path or PATHS.applications
    doc = _load_json(p, {"applications": []})
    doc.setdefault("applications", [])
    return doc


def list_applications(
    status: str | None = None, path: Path | None = None
) -> list[Application]:
    """Return ledger rows as ``Application`` objects, optionally filtered by status."""
    doc = load_applications(path)
    rows = [Application.from_dict(a) for a in doc["applications"]]
    if status:
        rows = [a for a in rows if a.status == status]
    return rows


def status_counts(path: Path | None = None) -> dict[str, int]:
    """Count ledger rows by status (handy for a one-line system summary)."""
    counts: dict[str, int] = {}
    for a in load_applications(path)["applications"]:
        s = a.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    return counts


def already_applied(company: str, role: str, path: Path | None = None) -> bool:
    """True if this (company, role) pair is already in the ledger.

    Company is normalized the same way as the harvest scripts; role is matched
    case-insensitively. Same company / different role is NOT a duplicate.
    """
    target = (_slug(company), (role or "").strip().lower())
    for a in load_applications(path)["applications"]:
        if (_slug(a.get("company", "")), (a.get("role", "") or "").strip().lower()) == target:
            return True
    return False


def record_application(app: Application, path: Path | None = None) -> dict[str, Any]:
    """Append one row to the ledger and persist it, de-duped by role.

    De-dup runs two checks: the (company slug, role title) pair, and, when an
    ``apply_url`` is present, the canonical ``role_key`` parsed from it. The
    second catches the same role re-entering under a differently spelled company
    name, which the pair check misses. Fills ``date`` with today if blank.
    Returns a small status dict. The write holds an exclusive lock for the whole
    read-modify-write (parallel loop instances share this file) and is atomic
    (temp file + ``os.replace``), and preserves the document's ``generated`` and
    ``applicant`` keys untouched.
    """
    p = path or PATHS.applications
    if not app.date:
        app.date = date.today().isoformat()
    with _ledger_lock(p):
        doc = load_applications(p)
        if already_applied(app.company, app.role, p):
            return {"recorded": False, "reason": "duplicate (company, role)",
                    "company": app.company, "role": app.role}
        if app.apply_url:
            rk = Job(title=app.role, company=app.company, url=app.apply_url).role_key
            if not rk.startswith("raw:") and rk in applied_role_keys(p):
                return {"recorded": False, "reason": f"duplicate (role_key {rk})",
                        "company": app.company, "role": app.role}
        doc["applications"].append(app.to_dict())
        _write_json_atomic(p, doc)
    return {"recorded": True, "total": len(doc["applications"]), "company": app.company, "role": app.role}


def load_queue(path: Path | None = None) -> list[dict[str, Any]]:
    """Return the raw discovery queue (list of role dicts)."""
    return _load_json(path or PATHS.queue, [])


def queue_jobs(status: str = "todo", path: Path | None = None) -> list[Job]:
    """Return queue entries as ``Job`` objects, filtered by queue status.

    The queue stores entries like ``{'n', 'text', 'url', 'ats', 'status', 'remote'}``.
    The display label (``text``) carries a ``[BH][R]`` tag prefix that we strip to
    recover the human title.
    """
    out: list[Job] = []
    for j in load_queue(path):
        if status and j.get("status") != status:
            continue
        label = j.get("text", "")
        title = label.split("] ", 1)[-1] if "]" in label else label
        company = ""
        if " - " in title:
            title, company = title.rsplit(" - ", 1)
        out.append(
            Job(
                title=title.strip(),
                company=company.strip(),
                url=j.get("url", ""),
                source="queue",
                remote=bool(j.get("remote", False)),
                ats=j.get("ats", ""),
            )
        )
    return out


def applied_company_slugs(path: Path | None = None) -> set[str]:
    """Normalized org identities already in the ledger, for curation's de-dup.

    Includes both the slugged company name and the org slug parsed from each
    row's apply URL, because discovery rows sometimes carry a missing or
    differently spelled company name and the ATS URL org is authoritative then.
    """
    slugs: set[str] = set()
    for a in load_applications(path)["applications"]:
        s = _slug(a.get("company", ""))
        if s:
            slugs.add(s)
        org = Job.from_dict(a).url_org_slug
        if org:
            slugs.add(org)
    return slugs


def applied_role_keys(path: Path | None = None) -> set[str]:
    """The set of role_keys already in the ledger (for cross-source de-dup)."""
    keys: set[str] = set()
    for a in load_applications(path)["applications"]:
        keys.add(Job.from_dict(a).role_key)
    return keys


def applied_role_pairs(path: Path | None = None) -> set[tuple[str, str]]:
    """Ledger roles as ``(company slug, title slug)`` pairs, for URL-independent de-dup."""
    pairs: set[tuple[str, str]] = set()
    for a in load_applications(path)["applications"]:
        pairs.add((_slug(a.get("company", "")), _slug(a.get("role", ""))))
    return pairs


def dedupe_jobs(jobs: Iterable[Job], path: Path | None = None) -> list[Job]:
    """Drop jobs whose role is already in the ledger; de-dup the input by role.

    Two independent identities are checked, because either one alone leaks. The
    ``role_key`` is URL-derived, so the same role re-harvested from a different
    source (an aggregator link one day, the company's own board the next) gets a
    fresh key and re-enters the queue as if it were new, costing a full screening
    pass on a role that was already decided. The ``(company slug, title slug)``
    pair catches that. It stays role-level: the same company with a DIFFERENT
    title still passes through, which is deliberate.
    """
    seen = applied_role_keys(path)
    seen_pairs = applied_role_pairs(path)
    out: list[Job] = []
    for j in jobs:
        rk = j.role_key
        pair = (j.company_slug, _slug(j.title))
        if rk in seen or pair in seen_pairs:
            continue
        seen.add(rk)
        seen_pairs.add(pair)
        out.append(j)
    return out
