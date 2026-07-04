#!/usr/bin/env python3
"""Demo anti-fabrication gate (a small stand-in for the private verify_resume.py).

Usage: verify_resume.py <resume.html>

Exit 0 when clean; exit 1 and print one finding per line otherwise. The real
gate cross-checks every claim against a claims bank; this demo version enforces
a blocklist of claims the sample persona ("Jordan Example") must never make,
which is the same mechanism at smaller scale: the resume builder CANNOT report
a pass unless this script actually ran and found nothing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Claims the demo persona does not hold and must never appear.
BLOCKLIST = (
    ("c/c++", "claims C/C++ (not in the demo claims bank)"),
    ("arduino", "claims Arduino experience (not in the demo claims bank)"),
    ("patent", "claims a patent (the demo persona holds none)"),
    ("ph.d", "claims a PhD (the demo persona holds none)"),
    ("phd", "claims a PhD (the demo persona holds none)"),
    ("google deepmind", "claims an employer not in the demo claims bank"),
)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_resume.py <resume.html>")
        return 2
    html = Path(sys.argv[1]).read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", " ", html).lower()
    findings = [msg for term, msg in BLOCKLIST if term in text]
    if findings:
        print("\n".join(sorted(set(findings))))
        return 1
    print("PASS verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
