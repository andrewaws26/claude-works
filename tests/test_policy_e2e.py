"""A policy.json in the data dir must repoint the whole system: rails, scoring
vocabulary, and curation lanes. Exercised in a subprocess because the policy is
resolved at import, exactly like a real server launch."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def test_policy_json_personalizes_rails_scoring_and_lanes(tmp_path):
    (tmp_path / "policy.json").write_text(json.dumps({
        "excluded_companies": ["initech"],
        "core_signals": ["fortran"],
        "lane_points": {"actuary": 6},
        "off_lane_titles": [],
    }), encoding="utf-8")

    code = """
import json
from claude_works import curation, discovery
from claude_works.config import RAILS
from claude_works.models import Job

assert RAILS.excluded_companies == ("initech",), RAILS.excluded_companies
assert discovery.excluded_company_match(Job("X", "Samsara", "")) is None
assert discovery.excluded_company_match(Job("X", "Initech", "")) == "initech"

s = discovery.score_job(Job("Fortran Systems Engineer", "Acme", ""))
assert any("fortran" in r for r in s.reasons), s.reasons

assert curation.LANE_POINTS == {"actuary": 6}, curation.LANE_POINTS
assert curation.OFF_LANE == ()
print("POLICY-OK")
"""
    env = dict(os.environ)
    env["JOBSEARCH_DATA_DIR"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "POLICY-OK" in proc.stdout
