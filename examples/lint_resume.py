#!/usr/bin/env python3
"""Demo style/AI-tell lint gate (a small stand-in for the private lint_resume.py).

Usage: lint_resume.py <resume.html>

Exit 0 when clean; exit 1 and print one finding per line otherwise. The real
gate also enforces layout rules; this demo version checks the tells that most
often mark a resume as machine-written:

  * em dashes (U+2014)
  * inflated resume-speak ("spearheaded", "synergy", ...)
  * the rule-of-three tic ("X, Y, and Z" repeated in every bullet)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BANNED_WORDS = (
    "spearheaded", "synergy", "results-driven", "passionate", "dynamic",
    "leveraged cutting-edge", "seamlessly", "delve", "honed",
)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: lint_resume.py <resume.html>")
        return 2
    html = Path(sys.argv[1]).read_text(encoding="utf-8")
    text = re.sub(r"<[^>]+>", " ", html)
    findings: list[str] = []

    if "—" in text:
        findings.append("em dash (U+2014) found; use plain punctuation")
    low = text.lower()
    for w in BANNED_WORDS:
        if w in low:
            findings.append(f"banned resume-speak: {w!r}")
    triples = re.findall(r"\b\w+, \w+, and \w+\b", text)
    if len(triples) >= 3:
        findings.append(f"rule-of-three tic: {len(triples)} 'X, Y, and Z' constructions")

    if findings:
        print("\n".join(findings))
        return 1
    print("PASS lint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
