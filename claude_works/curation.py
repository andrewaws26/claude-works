"""Queue curation: triage discovered roles into a fit-ranked active set and a
parked set, so the autonomous loop never wastes a firing walking past poor fits.

Discovery yields many ``Job`` records, but only a fraction are genuine fits. Left
unsorted, the loop picks whatever role is next in line, which is often a Design
Engineer, a Consultant, an over-level title, or a non-US posting. Curation triages
the whole queue once: every job is either KEPT with a fit score (so the loop applies
to the strongest open match first) or PARKED with an auditable reason. Parked roles
are never discarded, only set aside, so a human can review or restore them.

Curation reuses the same ``RAILS`` the scorer enforces, so triage and per-job scoring
disqualify the same roles. Standard library only, so it imports with zero third-party
dependencies and the unit tests stay fast.

    curate(jobs, applied_slugs) -> CurationResult(active=[(Job, fit)], parked=[(Job, reason)])
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .config import POLICY, RAILS, matched_excluded_domain, policy_tuple
from .discovery import excluded_company_match
from .models import Job, _slug

# Role-title lanes the candidate converts in, with fit points (strongest first).
# These bias the active queue so the loop applies to the best-matching role first.
# Per-candidate: a "lane_points" object in policy.json replaces this table.
_DEFAULT_LANE_POINTS: dict[str, int] = {
    "forward deployed": 6,
    "applied ai": 5,
    "ai engineer": 5,
    "ai developer": 5,
    "agent": 5,
    "solutions engineer": 5,
    "automation engineer": 5,
    "automation": 4,
    "developer experience": 4,
    "developer advocate": 4,
    "integration engineer": 4,
    "implementation": 4,
    "full stack": 3,
    "software engineer": 3,
    "backend": 3,
    "platform engineer": 3,
    "support engineer": 3,
}
LANE_POINTS: dict[str, int] = (
    {str(k).lower(): int(v) for k, v in POLICY["lane_points"].items()}
    if "lane_points" in POLICY else _DEFAULT_LANE_POINTS
)

# Off-lane titles to park (design / sales / consulting / research / non-software).
# "GTM engineer" and kin are growth/sales-ops automation seats, not builder roles;
# board harvests of AI companies queue them in bulk, so the title itself is the rail.
OFF_LANE: tuple[str, ...] = policy_tuple("off_lane_titles", (
    "gtm engineer", "gtm operations", "growth engineer",
    "design engineer", "designer", "ux ", "ui/ux", "consultant", "value engineer",
    "pre-sales", "presales", "sales engineer", "recruiter", "sourcer", " sales",
    "account executive", "account manager", "marketing", "copywriter",
    "research scientist", "researcher", "data scientist", "hardware engineer",
    "mechanical", "electrical engineer", "firmware", "embedded ", "product manager",
    "program manager", "project manager", "strategist", "controller", "accountant",
    "technician", "strategic client", "strategic account",
))

# Extra over-level / wrong-level signals beyond RAILS.overlevel_terms.
EXTRA_LEVEL: tuple[str, ...] = ("founding", "founder", "apprentice")

# "intern"/"internship" need a word-boundary match: a plain substring check
# false-positives on "Internal" (e.g. an "Internal Tools Engineer" or "Internal
# Agents" title), which would wrongly park a legitimate IC role.
EXTRA_LEVEL_WORD = re.compile(r"\bintern(?:ship)?\b")

# Advanced-degree knockout: "Scientist" titles (Research/Applied/ML/Data Scientist)
# and JDs that require a PhD or Master's are a hard credential gap for a candidate
# without an advanced degree, regardless of how well the lane otherwise scores.
ADVANCED_DEGREE: tuple[str, ...] = (
    "phd", "ph.d", "doctorate", "doctoral", "master's degree", "masters degree",
    "ms or phd", "graduate degree", "advanced degree", "requires a phd",
)

# A row noting the ABSENCE of a PhD ask (e.g. a summariser writing "no PhD gate"
# as a positive signal) can false-positive on the bare "phd" substring above.
# These are plain substring tests, so a summariser must not paste "phd" into a
# row's text even to negate it - this regex is a backstop, not a license to.
ADVANCED_DEGREE_NEGATED = re.compile(
    r"\bno\b\W+(?:\w+\W+){0,2}phd|phd\W+(?:\w+\W+){0,2}not required")

# Learned from runtime skips (each skip means more like it are queued): model-training
# / research engineering (the candidate builds ON models, not trains them), onsite/hybrid
# requirements (candidate is remote-only), and lead/over-level roles hiding behind an IC
# title (caught from the JD body).
MODEL_TRAINING: tuple[str, ...] = (
    "fine-tun", "rlhf", "rlaif", "reward model", "model training", "pretrain",
    "pre-train", "training large language", "train llms", "models from scratch",
    "coding agent evaluation", "ai coding agent evaluation",
)
ONSITE: tuple[str, ...] = (
    "on-site", "onsite", "in-office", "in office", "in person", "in-person",
    "days a week in", "days/week in", "days per week in", "relocate to",
    "must be located in", "hybrid work", "hybrid role", "hybrid schedule",
    "hybrid - ", "(hybrid", "in an office", "in an office every day",
)
# An explicit in-office MANDATE outranks any remote label. Harvest rows are often
# hand-stamped "(Remote US)" in bulk without verification, and that single word in
# the blob disables the ONSITE rule below (which requires "remote" to be absent).
# These phrases are mandates, not location noise, so they park a row even when it
# also claims remote. Learned from a batch where every hand-labeled "Remote US" row
# turned out to be a city-anchored seat.
STRONG_ONSITE: tuple[str, ...] = (
    "in-office mandate", "in office mandate", "is in-person in", "is in person in",
    "role is in-person", "role is in person", "teams are in-person",
    "teams are in person", "all teams are in", "requires you to work",
    "days a week in", "days/week in", "days per week in",
    # A mandate is usually stated once, in the closing paragraph, and the exact
    # wording varies more than the list above assumed. Two misses worth encoding:
    # "we work together in person five days a week FROM our offices" (the
    # preposition is "from", so the "days a week in" entries never matched) and
    # "a fully in-person team in <city>" (not "teams are in-person"). Relocation
    # support offered as a perk is the same signal phrased as a benefit.
    "in person five days", "in-person five days",
    "in person 5 days", "in-person 5 days",
    "days a week from", "days/week from", "days per week from",
    "fully in-person", "fully in person",
    "work together in person", "in-person team in", "in person team in",
    "relocation support for those moving",
    # The mandate can also hide in the "you will like working here if" culture
    # bullets and carry none of the keywords above. One posting said "you like
    # being in an office every day": the article "an" defeats both "in office"
    # and "in-office", and the description never used the words onsite, hybrid,
    # or remote at all, so the only other signal was the board API remote flag.
    # Match the cadence phrase rather than the preposition.
    "office every day", "office every single day",
    "every day in the office", "every day in an office",
    "in an office every", "in the office every day",
)

# A location string that names an OFFICE while the row claims remote is a
# contradiction ("NYC Office" with a remote flag set). The office token is the
# structured field the ATS controls, so it wins over the boolean.
OFFICE_LOCATION: tuple[str, ...] = (
    "office", "hq", "headquarters", "onsite", "on-site", "in-office",
)


def office_anchored(location: str) -> bool:
    """True when the location field itself names an office rather than a region."""
    return any(tok in (location or "").lower() for tok in OFFICE_LOCATION)


# A leveling code in the title (IC4, L5, E5) carries no scope information: a
# posting titled "Agent Engineer [IC4]" can still say, three paragraphs down,
# "we are hiring a technical leader, not just a strong individual contributor"
# and "you operate at staff scope". The scope sentence in the body is the real
# signal, so the phrases below are matched against the JD text, not the title.
# These are plain substring tests, so a summariser must not paste the literal
# phrases into a row's text even to negate them.
LEAD_BODY: tuple[str, ...] = (
    "technical lead", "team lead", "tech lead", "engineering lead", "lead engineer",
    "lead a team of", "mentor the team", "mentoring engineers", "drive engineering excellence",
    "staff scope", "not just a strong individual contributor", "technical leader, not just",
)

# A "United States - Remote" location label can still hide a TIME-ZONE knockout in
# the JD body ("Open to candidates located in the Central, Mountain, and Pacific
# time zones"). The ONSITE list cannot catch it, because the posting really does
# say "remote". Match the allowed-zone sentence itself and only park when the
# candidate's own zone is absent from the zones the posting lists.
TZ_SENTENCE = re.compile(
    r"(?:located|based|reside|residing|work(?:ing)?|candidates?|open to|hours|"
    r"available)[^.]{0,140}?\btime\s?zones?\b"
)
TZ_NAMED = re.compile(
    r"\b(pacific|mountain|central|eastern|west coast|east coast|"
    r"pst|pdt|mst|cst|est|edt|pt|mt|ct|et)\b"
)
CANDIDATE_TZ: tuple[str, ...] = ("eastern", "east coast", "est", "edt", "et")


def is_time_zone_restricted(blob: str, allowed: tuple[str, ...] = CANDIDATE_TZ) -> bool:
    """True when the posting names allowed time zones and none of them is ours."""
    for match in TZ_SENTENCE.finditer(blob):
        segment = match.group(0)
        named = {m.group(0) for m in TZ_NAMED.finditer(segment)}
        if not named:
            continue
        if not named & set(allowed):
            return True
    return False


# A "Remote - US" label can also hide a RESIDENCY knockout: the posting is
# genuinely remote, but only for people who already live in an enumerated set of
# states. Seen in both the ATS location field ("Remote - CO, FL, GA, MA, ...")
# and one line of the JD body ("Employees may live in the following locations:
# Texas, Colorado, ..."). Neither the onsite list nor the time-zone rule catches
# it, and the screen slot is spent proving the role was never applicable.
# Two narrow paths, both tied to a residency claim, so a company merely naming
# its office states or its pay-transparency states stays kept.
CANDIDATE_STATES: tuple[str, ...] = ("kentucky", "indiana")
CANDIDATE_STATE_CODES: frozenset[str] = frozenset({"KY", "IN"})
US_STATE_NAMES: tuple[str, ...] = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
)
# Postal codes are matched on the RAW string, uppercase only, so the ordinary
# words "in" and "or" can never pose as Indiana and Oregon.
STATE_CODE_RUN = re.compile(
    r"(?i:remote)[^A-Za-z0-9]{0,12}((?:[A-Z]{2}\s*,\s*){2,}[A-Z]{2})"
)
RESIDENCY_CUE = re.compile(
    r"(?:may live in|must (?:live|reside)|employees? (?:may|must) (?:live|reside)|"
    r"residents? of|reside in|open to candidates (?:in|living in)|"
    r"hiring (?:only )?in|located in) (?:the following [a-z ]{0,20})?",
    re.I,
)
# A pay-transparency notice ("For residents of California, Colorado and New
# York, the base pay range is ...") reuses the same cue words without being a
# residency rail, so a comp word inside the window disarms the spelled-out path.
PAY_WORD = re.compile(r"salary|base pay|pay range|compensation|pay transparency", re.I)
STATE_LIST_MIN = 3
_RESIDENCY_WINDOW = 260


def is_state_restricted(
    raw: str,
    home_states: tuple[str, ...] = CANDIDATE_STATES,
    home_codes: frozenset[str] = CANDIDATE_STATE_CODES,
) -> bool:
    """True when a posting enumerates allowed-residency states and ours is absent.

    ``raw`` must keep its original casing: the postal-code path depends on it.
    """
    match = STATE_CODE_RUN.search(raw or "")
    if match:
        codes = [c.strip() for c in match.group(1).split(",")]
        if len(codes) >= STATE_LIST_MIN and not home_codes & set(codes):
            return True
    for cue in RESIDENCY_CUE.finditer(raw or ""):
        window = (raw or "")[cue.end():cue.end() + _RESIDENCY_WINDOW]
        if PAY_WORD.search(window):
            continue
        lowered = window.lower()
        named = {st for st in US_STATE_NAMES if st in lowered}
        if len(named) >= STATE_LIST_MIN and not any(h in lowered for h in home_states):
            return True
    return False

# Pre-sales "Solutions/Sales Engineer" roles dressed as builder titles: the lane
# table gives "solutions engineer" high points on title alone, but a real chunk
# of postings under that title are pure pre-sales (POV/RFP/deal-closing, reporting
# into a Sales department) with zero hands-on building. Only fires when the title
# already looks like solutions/sales engineering AND the body also carries an
# explicit deal-cycle marker, so a genuine builder role that happens to mention
# "customer" isn't caught. Learned from runtime triage: title alone gives no signal.
PRESALES_SIGNALS: tuple[str, ...] = (
    "account executive", "proof of value", " pov ", "rfp", "sales cycle",
    "quota-carrying", "quota carrying", "pre-sales", "presales", "win rate",
    "deal desk", "closing deals", "closes deals", "close deals",
)
PRESALES_TITLE = re.compile(r"solutions? engineer|sales engineer")

# Title-only pre-sales catch for board-summary rows that carry NO body text. The
# body-gated rule above cannot reach them, so a go-to-market seat sails through:
# two consecutive runs burned their only queued row on exactly this shape, one a
# partner seat and one an enterprise pre-sales seat with an on-target-earnings
# range and a single-metro location requirement. The qualifier words below are
# sales territory and segment terms, and a role that has the candidate building
# AI is never titled "Enterprise Solutions Engineer", so matching the title alone
# is safe here and needs no body text.
PRESALES_SEGMENT_TITLE = re.compile(
    r"\b(enterprise|partner|channel|field|strategic|named|commercial|"
    r"mid[ -]?market|smb|corporate|territory|pre-?sales|presales|"
    r"customer-?facing|customer)\b[\w ,/&-]{0,24}\bsolutions? engineers?\b"
)

# Same gap, SUFFIX shape: a regional territory tag trailing the title instead of
# leading it (e.g. "Solutions Engineer - Central"). The leading-qualifier regex
# above only checks text BEFORE "solutions engineer", so a trailing region tag
# sails through. Compass-direction and region words after the title carry the
# same territory-segment signal as the leading list, just on the other side of
# the noun phrase.
PRESALES_REGION_SUFFIX = re.compile(
    r"\bsolutions? engineers?\b[\w ,/&-]{0,24}\b(central|east|west|north|south|"
    r"northeast|northwest|southeast|southwest)\b"
    r"(?![\w ,&-]{0,20}(?:europe|asia|africa|america|pacific|atlantic))"
)

# Contact-center / CCaaS architect roles: "Solutions Architect" postings whose body
# is really telephony or contact-center platform consulting (Amazon Connect, Genesys,
# Five9, CCaaS). Deep contact-center platform expertise is a hard domain gap, and
# these postings cluster, so one missed pattern burns several screen slots in a row.
# Gated on an architect/solutions-engineer role shape so a builder role at a
# customer-experience company is never caught. Learned from runtime triage: three
# of eight screen slots in one run fell to this exact knockout.
CONTACT_CENTER_SIGNALS: tuple[str, ...] = (
    "amazon connect", "aws connect", "genesys", "five9", "ccaas",
    "contact center", "contact-center", "call center",
)
CONTACT_CENTER_TITLE = re.compile(r"architect|solutions? engineer|sales engineer")

# Enterprise-delivery architect postings: partner and channel architects,
# professional-services implementation architects, and packaged-platform
# consulting seats. The title reads like engineering, but the work is rolling out
# someone else's platform, so a candidate whose lane is building AI systems is
# off-lane for all of them. Board harvests queue these in bulk (one run found them
# filling nearly an entire ATS partition), which is what makes the pattern worth
# a rail rather than a case-by-case screen. Gated on BOTH the architect or
# field-engineer title shape AND an explicit partner / professional-services /
# packaged-platform marker, so a genuine builder role that merely mentions
# consulting is never caught.
# The title may say "solutions ENGINEER" rather than architect: a partner
# enablement seat under a sales department reads as a builder title until the
# body shows the travel load and the packaged-platform stack. Board-summary
# rows carry no body text, so the pre-sales body filter cannot reach them and
# the title shape has to. Widening it stays safe because the signal gate below
# still requires an explicit partner or professional-services marker.
DELIVERY_ARCHITECT_TITLE = re.compile(
    r"solutions? architect|solutions? engineer|field engineer"
)
DELIVERY_ARCHITECT_SIGNALS: tuple[str, ...] = (
    "partner solution", "partner solutions", "channel partner", "gsi",
    "system integrator", "systems integrator", "professional services",
    "implementation partner", "appian", "anaplan", "salesforce", "servicenow",
    "netsuite", " sap ", " erp ",
)

# Demo and sandbox tenants on public ATS hosts (for example a vendor's own
# "demo" board). The postings parse like real reqs and survive every other rail,
# but no employer is hiring against them, so each one costs a screen slot for
# nothing. Matched on the exact URL org slug.
SANDBOX_ORGS: frozenset[str] = frozenset({"leverdemo", "demo"})

# Orgs whose ENTIRE public board was verified hybrid or onsite. Screening a board
# is cheaper than screening its reqs one at a time: an ATS board endpoint returns
# every open posting in the same fetch that answers the one you asked about, so a
# single scan for `workplaceType == "Remote"` decides the whole org. When no US
# posting on the board carries that value, every future row the harvesters produce
# from that org is a location rail regardless of its title or its remote flag, and
# re-screening each sibling req costs one wasted fire apiece.
# Populate at runtime from verified boards; a single genuinely-remote posting
# disqualifies an org from this set. Matched on the exact URL org slug.
HYBRID_ONLY_ORGS: frozenset[str] = frozenset()

# Orgs that are not excluded-industry vendors themselves, but publish an excluded
# industry as a named customer and vertical in their About copy. These are missed by
# body-term rails for a structural reason: harvested rows usually carry only a short
# title, so no term from the posting body is ever in the text the rail sees, and the
# company reads clean on its title, stack, and product. The call is company-level, so
# it belongs at the org rather than the req: one board can publish a dozen otherwise
# clean rows that each cost a screen slot.
# Kept separate from the hard company exclusion list on purpose. That list is for
# vendors whose whole mission is the excluded work; this one is a weaker, reversible
# judgment about a dual-use vendor, and removing a slug here reverses the decision for
# that org alone with no other effect. Populate at runtime from screened boards, and
# record why in the run notes so the call can be audited later.
# Matched on the exact URL org slug or company slug, never as a substring: a substring
# match would also catch unrelated orgs whose names merely start with the same letters.
EXCLUDED_VERTICAL_ORGS: frozenset[str] = frozenset()

# Orgs whose ENTIRE board is non-US-only, in a way no other rail can see. The title
# carries no region token, so the region rail never fires; the row often carries a
# bare remote flag, which is true (remote within that region) and so passes the
# location rail; and the residency requirement lives only in the ATS location field
# or in the benefits block at the bottom of the posting body. Harvested rows that
# carry just a title therefore reach a screen slot every time, and one board can
# publish a dozen of them.
# Matched on the RAW url org segment, never the normalized slug: the slug helper
# strips generic corporate suffixes and trailing counters, so two boards whose names
# differ only by one of those tokens collapse to a single key, and an org-level park
# keyed on the slug would hit the wrong employer. Populate at runtime from verified
# boards.
# Sibling signal worth recording when adding an entry: a studio or agency that places
# builders with an unnamed partner client also hides the real employer, which is an
# independent skip on its own.
NONUS_ORGS: frozenset[str] = frozenset()

# Non-US region tokens in the title. Regional roles ("Solutions Engineer, Benelux",
# "SE, Nordics", "SE, EMEA") often carry a bare "Hybrid" or empty location, so the
# location rule never fires; the title itself is the reliable signal. Also catches
# "<language> Speaking" requirements. Learned from runtime skips of regional roles.
REGION_TITLE = re.compile(
    r"\b(benelux|nordics?|emea|apac|dach|latam|anz|iberia|europe|european|"
    r"united kingdom|ireland|germany|france|spain|italy|poland|netherlands|"
    r"japan|singapore|australia|new zealand|brazil|mexico|canada|korea|israel|"
    r"mena|ksa|uae|saudi arabia|india|philippines|portugal|romania|vietnam|"
    r"indonesia|colombia|argentina|ukraine|ukrainian|czech|czechia|hungary|bulgaria|serbia|croatia|lithuania|latvia|estonia|pakistan|bangladesh|egypt|nigeria|kenya|turkey|taiwan|thailand|malaysia|chile|peru|uruguay|costa rica|"
    r"middle east|africa|eu|uk)\b|[a-z]+[- ]speaking",
)

# US-location signals. When a location is present but shows none of these, the
# posting is treated as non-US-only and parked.
US_SIGNALS: tuple[str, ...] = (
    "united states", "usa", "u.s", "u.s.a", "remote", " us", "us-", "us ", ", us",
    "california", "new york", "texas", "washington", "massachusetts", "colorado",
    "illinois", "georgia", "florida", "san francisco", "seattle", "boston",
    "austin", "denver", "chicago", "los angeles", "atlanta", "remote, us", "us remote",
)

# Bare "remote" is NOT a US signal for the explicit-location rule below: harvester
# rows sometimes carry a wrong "REMOTE US" label while the true ATS location reads
# "Remote - Australia". That location contains "remote", which kept the non-us-only
# rule from ever firing on it, and a run burned screen slots re-discovering the
# same knockout. When the location itself names a non-US country, trust it over
# any remote label.
US_SIGNALS_TIGHT: tuple[str, ...] = tuple(s for s in US_SIGNALS if s != "remote")

# The reasons curate can assign (stable vocabulary for summaries and tests).
# Channel bonus: bias the active ranking toward ATSes that auto-submit cleanly, so
# more fires land as confirmed rather than parked. Ashby has no anti-bot gate; Greenhouse
# often email-gates an automated submit; Lever is captcha-walled; Custom varies.
# Zero-interaction preference: captcha walls (lever/gem) and account walls
# (workday/icims and kin) always end as human handoffs, so they carry negative
# bonuses - queue them only when the underlying fit is exceptional.
CHANNEL_BONUS: dict[str, int] = {
    "ashby": 2, "workable": 2, "greenhouse": 2,
    "lever": -2, "gem": -2, "workday": -3, "icims": -3,
}

PARK_REASONS: tuple[str, ...] = (
    "already-applied", "already-screened", "excluded-company", "excluded-domain", "over-level",
    "evergreen-posting", "advanced-degree", "lead-in-body", "model-training",
    "onsite-hybrid", "office-anchored-location", "off-lane", "non-us-region", "non-us-location", "non-us-only",
    "hard-skill-gap",
    "comp-below-floor", "pre-sales", "contact-center", "delivery-architect",
    "demo-board", "railed-role-family",
)

# Compensation floor: when the TOP of a posting's salary range is an annual
# figure below the floor, the role can never clear the candidate's comp bar, so
# park it before a run spends a screen slot on it. Parsing notes: discovery text
# often concatenates the salary with the next sentence ("$295,0007+ years"), so
# take exactly three digits after the comma and drop the remainder; funding
# amounts ("$160M raised") have no comma and no k-suffix, so they never match;
# values under $30k (hourly rates, equity fragments) are ignored as non-annual.
COMP_FLOOR = 120_000
_SALARY = re.compile(r"\$\s?(\d{2,3}),(\d{3})|\$(\d{2,3})k(?!\d)", re.I)


def comp_ceiling(text: str) -> int | None:
    """Largest annual salary figure found in ``text``, or ``None``."""
    vals = [int(a + b) if a else int(k) * 1000 for a, b, k in _SALARY.findall(text)]
    annual = [v for v in vals if v >= 30_000]
    return max(annual) if annual else None


@dataclass
class CurationResult:
    """The triage outcome for one queue.

    ``active`` is the fit-ranked list of ``(job, fit)`` to pursue, highest fit first.
    ``parked`` pairs each set-aside ``(job, reason)``. ``counts`` is the reason
    histogram for a one-line summary. Nothing is discarded.
    """

    active: list[tuple[Job, int]] = field(default_factory=list)
    parked: list[tuple[Job, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": [{**j.to_dict(), "fit": f} for j, f in self.active],
            "parked": [{**j.to_dict(), "park_reason": r} for j, r in self.parked],
            "counts": dict(self.counts),
        }


def _blob(job: Job) -> str:
    return " ".join([job.title, job.company, job.location, job.comp]).lower()


def role_key(company_slug: str, title: str) -> tuple[str, str]:
    """Role-level identity: (company slug, normalized title prefix)."""
    return (company_slug or "", re.sub(r"[^a-z0-9]", "", (title or "").lower())[:40])


# A company that posts one job under many segment suffixes generates a new role
# key per suffix, so exact-key screening never catches the next one. The family
# is the title with its suffix removed.
RAILED_FAMILY_MIN = 3


# Level qualifiers that prefix a title without changing the job family. Stripping
# them keeps level variants of one family on the SAME knockout key, so a board
# that posts "Senior X" and "X" and "X, Segment" reaches the threshold instead of
# spreading three rejections across three keys that each stay under it.
_LEVEL_PREFIX = re.compile(
    r"^(?:sr\.?|senior|staff|principal|lead|junior|jr\.?|associate|"
    r"entry[- ]level|mid[- ]level)\s+(?=\S)",
    re.I,
)


def role_family(title: str) -> str:
    """Title minus its segment suffix and level qualifier: both "Applied
    Architect, Startups" and "Senior Applied Architect" give the family key for
    "Applied Architect". Empty when the base is a single word, which is too
    coarse to knock out a whole family on."""
    base = re.split(r"\s*[,(]", (title or "").strip())[0].strip()
    while True:
        stripped = _LEVEL_PREFIX.sub("", base)
        if stripped == base:
            break
        base = stripped
    return _slug(base) if len(base.split()) >= 2 else ""


def park_reason(
    job: Job,
    applied_slugs: set[str],
    screened_keys: set[tuple[str, str]] | frozenset = frozenset(),
    railed_families: Mapping[tuple[str, str], int] | None = None,
) -> str | None:
    """Return why a job should be parked, or ``None`` to keep it.

    Checks run cheapest-and-most-decisive first and reuse ``RAILS`` so curation and
    the scorer agree on what disqualifies a role.
    """
    title = job.title.lower()
    blob = _blob(job)
    if job.company_slug and job.company_slug in applied_slugs:
        return "already-applied"
    if job.url_org_slug and job.url_org_slug in applied_slugs:
        return "already-applied"
    # Roles a prior run already screened and rejected re-enter through board
    # harvests as fresh rows and burn whole runs re-screening them; a rejection
    # is durable at role level, so park duplicates before they cost a screen slot.
    # Match on both the parsed company and the URL org, since harvest rows often
    # lack a parseable company name.
    if screened_keys:
        cand_keys = {
            role_key(job.company_slug, job.title),
            role_key(job.url_org_slug, job.title),
        }
        # Harvest titles sometimes embed the company as a hyphen tail
        # ("Software Engineer, Full Stack - GTM - Acme"), and a parser that
        # splits at the first hyphen leaves the real company buried in the
        # remainder, so neither plain key matches the ledger row
        # "Acme | Software Engineer, Full Stack - GTM". Try every split point,
        # treating the tail as the company and the head as the title.
        segs = re.split(r"\s+[\u2014\u2013-]\s+", job.title)
        for i in range(1, len(segs)):
            cand_keys.add(role_key(_slug(" - ".join(segs[i:])), " - ".join(segs[:i])))
            cand_keys.add(role_key(_slug(segs[i]), " - ".join(segs[:i])))
        if cand_keys & set(screened_keys):
            return "already-screened"
    # Exact-key screening only catches the SAME title. Some orgs post one job
    # under many segment suffixes ("Applied Architect, {Partnerships, Commercial,
    # Industries, ...}"); each rejection is a distinct role key, so the next
    # suffix arrives unscreened and burns another slot. Once a company has
    # RAILED_FAMILY_MIN rejected siblings sharing a title family, the family
    # itself is the rail. Parked jobs stay in the result, so an outlier is
    # still recoverable by hand.
    if railed_families:
        fam = role_family(job.title)
        if fam:
            for org in (job.company_slug, job.url_org_slug):
                if org and railed_families.get((org, fam), 0) >= RAILED_FAMILY_MIN:
                    return "railed-role-family"
    if job.url_org_slug and job.url_org_slug in SANDBOX_ORGS:
        return "demo-board"
    if job.url_org_slug and job.url_org_slug in HYBRID_ONLY_ORGS:
        return "hybrid-only-org"
    if job.url_org_raw and job.url_org_raw in NONUS_ORGS:
        return "non-US-only-org"
    if (job.url_org_slug and job.url_org_slug in EXCLUDED_VERTICAL_ORGS) or (
        job.company_slug and job.company_slug in EXCLUDED_VERTICAL_ORGS
    ):
        return "excluded-vertical-org"
    if excluded_company_match(job) is not None:
        return "excluded-company"
    if matched_excluded_domain(blob) is not None:
        return "excluded-domain"
    if (
        any(t in title for t in RAILS.overlevel_terms)
        or any(t in title for t in EXTRA_LEVEL)
        or EXTRA_LEVEL_WORD.search(title)
    ):
        return "over-level"
    # Evergreen postings are proactive talent-pipeline posts, not open seats; an
    # automated run would spend a screen slot on a req nobody is hiring against.
    if "evergreen" in title:
        return "evergreen-posting"
    if "scientist" in title or any(
        d in blob for d in ADVANCED_DEGREE if not (d == "phd" and ADVANCED_DEGREE_NEGATED.search(blob))
    ):
        return "advanced-degree"
    if any(p in blob for p in LEAD_BODY):
        return "lead-in-body"
    if any(m in blob for m in MODEL_TRAINING):
        return "model-training"
    if any(o in blob for o in STRONG_ONSITE):
        return "onsite-hybrid"
    if job.remote and office_anchored(job.location):
        return "office-anchored-location"
    if any(o in blob for o in ONSITE) and "remote" not in blob:
        return "onsite-hybrid"
    if is_time_zone_restricted(blob):
        return "time-zone-restricted"
    if is_state_restricted(" ".join([job.location, job.title, job.comp])):
        return "state-restricted-remote"
    if any(t in title for t in OFF_LANE):
        return "off-lane"
    if PRESALES_TITLE.search(title) and any(s in blob for s in PRESALES_SIGNALS):
        return "pre-sales"
    if PRESALES_SEGMENT_TITLE.search(title) or PRESALES_REGION_SUFFIX.search(title):
        return "pre-sales"
    if CONTACT_CENTER_TITLE.search(title) and any(s in blob for s in CONTACT_CENTER_SIGNALS):
        return "contact-center"
    if DELIVERY_ARCHITECT_TITLE.search(title) and any(
        s in title or s in blob for s in DELIVERY_ARCHITECT_SIGNALS
    ):
        return "delivery-architect"
    if REGION_TITLE.search(title):
        return "non-us-region"
    if job.location and not any(s in job.location.lower() for s in US_SIGNALS):
        return "non-us-only"
    loc = (job.location or "").lower()
    if (loc and REGION_TITLE.search(loc)
            and not any(s in loc for s in US_SIGNALS_TIGHT)
            and not re.search(r"\bus\b|\busa\b", loc)):
        return "non-us-location"
    if any(s in blob for s in RAILS.hard_gap_skills):
        return "hard-skill-gap"
    ceiling = comp_ceiling(job.comp)
    if ceiling is not None and ceiling < COMP_FLOOR:
        return "comp-below-floor"
    return None


def fit_score(job: Job) -> int:
    """A small integer fit score so the active queue ranks best-match first."""
    title = job.title.lower()
    blob = _blob(job)
    score = 0
    for kw, pts in LANE_POINTS.items():
        if kw in title:
            score = max(score, pts)
    # QA and test titles are floor lanes: "QA Automation Engineer" substring
    # matches "automation engineer" and "QA ... AI Agents" matches "agent",
    # which would rank a floor QA role above builder lanes. Cap the lane
    # component so builder lanes always outrank QA in best-first ordering.
    if re.search(r"\bqa\b|quality assurance|test engineer", title):
        score = min(score, 3)
    if job.remote or "remote" in blob:
        score += 2
    if "python" in blob:
        score += 1
    if "typescript" in blob or "react" in blob:
        score += 1
    if "llm" in blob or "rag" in blob or "generative" in blob:
        score += 1
    if "senior" in title:
        score += 1
    if "junior" in title or "associate" in title:
        score -= 1
    score += CHANNEL_BONUS.get((job.ats or "").lower(), 0)
    return score


def curate(
    jobs: Iterable[Job],
    applied_slugs: Iterable[str] | None = None,
    screened_keys: Iterable[tuple[str, str]] | None = None,
    railed_families: Mapping[tuple[str, str], int] | None = None,
) -> CurationResult:
    """Partition ``jobs`` into a fit-ranked active set and a reasoned parked set.

    ``applied_slugs`` are normalized company slugs already in the ledger; matching
    jobs are parked as ``already-applied``. ``screened_keys`` are ``role_key``
    identities of roles a prior run screened and rejected; matching jobs are
    parked as ``already-screened``. ``railed_families`` counts rejected siblings
    per ``(company slug, role_family)``; a job whose family is at or above
    ``RAILED_FAMILY_MIN`` is parked as ``railed-role-family``. The active list is sorted by fit
    descending so the caller can pop the strongest open match in O(1).
    """
    applied = set(applied_slugs or ())
    screened = set(screened_keys or ())
    result = CurationResult()
    for job in jobs:
        reason = park_reason(job, applied, screened, railed_families)
        if reason:
            result.parked.append((job, reason))
            result.counts[reason] = result.counts.get(reason, 0) + 1
        else:
            result.active.append((job, fit_score(job)))
    result.active.sort(key=lambda jf: -jf[1])
    return result
