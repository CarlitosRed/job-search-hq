"""Pure v3.2 policy used by the activation adapter; no I/O or extraction.


Implements v3.2 plus accepted algo34 G1-G9 and algo35 H1-H6 fixes, including the revised F12 years
rule without a ratio trigger. Thresholds are hypotheses chosen
against the hand benchmark, not validated hiring probabilities. Callers supply
reviewed clauses, scope, eligibility and packet decisions; this module cannot
establish those facts. Activation wiring: Codex - Activation Builder, 2026-11-07.
"""

from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
import re

RUBRIC_VERSION = 'v3.2'


def exact(value):
    """Reject binary floats rather than silently manufacture exact precision."""
    if isinstance(value, (float, bool)):
        raise TypeError("Use Fraction, integer or decimal text")
    return Fraction(value)


def aware(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def seniority_title(title):
    """Word extraction only; callers still review the actual requirement scope."""
    return bool(re.search(r"\b(senior|lead|principal|director|manager)\b", title, re.I))


def display(value):
    """Exact decimal half-up, independent of Decimal context precision."""
    if value is None:
        return "Unknown"
    value = exact(value)
    sign = "-" if value < 0 else ""
    value = abs(value) * 100
    cents = value.numerator // value.denominator
    if 2 * (value.numerator % value.denominator) >= value.denominator:
        cents += 1
    return f"{sign}{cents // 100}.{cents % 100:02d}"


# No personal tenure defaults in the public copy. Supply sourced relevant_years.
DOCUMENTED_RELEVANT_YEARS = {}
DOCUMENTED_YEARS_SOURCES = {}
# ASSUMED implementation choices; accepted F12 did not set numeric penalties
# or a high-D threshold. Preserve quarter-point arithmetic, avoid an A cap.
TEACHABLE_YEARS_PENALTY = Fraction(1, 4)
HEAVY_YEARS_PENALTY = Fraction(3, 4)
HIGH_DIRECTION = Fraction(7, 2)


@dataclass(frozen=True)
class Requirement:
    name: str
    rating: str
    classification: str = "core"
    clause: str = ""
    rationale: str = ""
    permissible_evidence: str = ""
    unsupported_scope: str = ""

    def __post_init__(self):
        if self.rating not in ("S", "P", "T"):
            raise ValueError("rating must be S/P/T")
        if self.classification not in ("core", "preferred"):
            raise ValueError("classification must be core/preferred")


@dataclass(frozen=True)
class YearsClause:
    required_years: object
    years_domain: str = ""
    relevant_years: object = None
    relevant_years_source: str = ""
    equivalent_experience_accepted: bool = False
    legal_minimum: bool = False
    legal_conflict_confirmed: bool = False


@dataclass(frozen=True)
class VerifyState:
    due_at: datetime
    previous_due_at: object = None
    renewal_reason: str = ""

    def __post_init__(self):
        aware(self.due_at, "Verify due_at")
        if self.previous_due_at is not None:
            aware(self.previous_due_at, "Verify previous_due_at")
            if self.due_at <= self.previous_due_at or not self.renewal_reason.strip():
                raise ValueError("Verify renewal needs a later due time and visible reason")
        elif self.renewal_reason:
            raise ValueError("Verify renewal needs previous_due_at")


def renew_verify(state, now, reason):
    """Explicit caller-persisted renewal; assessment never silently resets it."""
    aware(now, "now")
    if now < state.due_at or not reason.strip():
        raise ValueError("Renew only a due Verify item with a visible reason")
    return VerifyState(now + timedelta(hours=24), state.due_at, reason)


@dataclass(frozen=True)
class Lead:
    key: str
    employer: str
    groups: tuple = ()
    work: object = None
    growth: object = None
    want: str = ""
    want_actor: str = ""
    want_at: object = None
    english_clause: str = ""
    english_force: str = "unknown"  # required/requested/preferred/negated/none
    english_adjudication_pending: bool = False
    required_years: object = None  # exact lower endpoint of an adjudicated clause
    relevant_years: object = None  # explicit documented scope override, never inferred
    relevant_years_source: str = ""
    years_domain: str = ""  # reviewed matching scope; blank is Unknown, not analyst
    years_clauses: tuple = ()  # distinct scopes; scalar years fields are an alternative
    title_seniority: bool = False  # reviewed senior/lead/principal/director/manager
    equivalent_experience_accepted: bool = False  # posting itself, not our preference
    legal_minimum: bool = False  # independently confirmed legal/contractual clause
    legal_conflict_confirmed: bool = False  # separately confirmed tenure conflict
    hard_blockers: tuple = ()  # reviewed exact clause plus confirmed conflict
    owner_hold: bool = False
    owner_check_complete: bool = False
    source_open: bool = False
    source_checked_at: object = None
    eligibility_clear: bool = False
    requirements_complete: bool = False
    packet_assessed: bool = False
    base_cv_version: str = ""
    cv_keyword_passed: bool = False
    tailored_cv: str = ""  # optional; never an admission gate
    preparation_clear: bool = False
    preparation_holds: tuple = ()
    adjudication_pending: bool = False
    plausible_half_coverage: bool = False
    deadline: object = None
    deadline_verified: bool = False
    imminent_filling_verified: bool = False
    urgency_conflict: bool = False
    preparation_effort: object = None
    discovered_at: object = None
    seen: bool = False
    age_days: object = None  # information only; no score penalty
    source_type: str = "direct"  # placement / provisional-placement / direct / contact
    placing_agency: str = ""
    end_employer: str = ""
    applied: bool = False
    applied_at: object = None
    work_model: str = "Unknown"
    verify_action: str = "Check the exact source, eligibility and requirement clauses"
    verify_state: object = None  # persist the returned Assessment.verify_state

    def __post_init__(self):
        for name in ("key", "employer"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be a nonblank canonical identity without edge whitespace")
        for name in ("end_employer", "placing_agency"):
            value = getattr(self, name)
            if not isinstance(value, str) or (value and (not value.strip() or value != value.strip())):
                raise ValueError(f"{name} must be blank or a nonblank canonical identity without edge whitespace")
        if self.source_type not in ("direct", "contact", "placement", "provisional-placement"):
            raise ValueError("Unknown source_type")
        for field in fields(self):
            if field.type is bool and type(getattr(self, field.name)) is not bool:
                raise ValueError(f"{field.name} must be a real boolean")
        for name in ("want_at", "source_checked_at", "deadline", "discovered_at"):
            value = getattr(self, name)
            if value is not None:
                aware(value, name)
        if isinstance(self.applied_at, datetime):
            aware(self.applied_at, "applied_at")
        elif self.applied_at is not None and not isinstance(self.applied_at, date):
            raise ValueError("applied_at must be a date or timezone-aware datetime")
        if self.verify_state is not None and not isinstance(self.verify_state, VerifyState):
            raise ValueError("verify_state must be VerifyState")
        if self.preparation_effort is not None and exact(self.preparation_effort) < 0:
            raise ValueError("preparation_effort must be nonnegative")
        if self.want not in ("", "Dream", "Yes", "Neutral", "No"):
            raise ValueError("Unknown Want label")
        if self.want and (not isinstance(self.want_actor, str) or not self.want_actor.strip() or self.want_at is None):
            raise ValueError("Explicit Want needs actor and timezone-aware time")
        for value in (self.work, self.growth):
            if value is not None and not 0 <= exact(value) <= 5:
                raise ValueError("Direction inputs must be 0..5")
        if self.english_force not in ("required", "requested", "preferred", "negated", "none", "unknown"):
            raise ValueError("Unknown English clause force")
        for value in (self.required_years, self.relevant_years):
            if value is not None and exact(value) < 0:
                raise ValueError("Years must be nonnegative")
        if self.relevant_years is not None and (not isinstance(self.relevant_years_source, str)
                                              or not self.relevant_years_source.strip()):
            raise ValueError("Explicit relevant years need a documented source")
        if self.years_clauses:
            if (self.required_years is not None or self.relevant_years is not None or self.years_domain
                    or self.legal_minimum or self.legal_conflict_confirmed
                    or self.equivalent_experience_accepted):
                raise ValueError("Use distinct years_clauses OR scalar years fields")
            for clause in self.years_clauses:
                if not isinstance(clause, YearsClause):
                    raise ValueError("years_clauses must contain YearsClause records")
                replace(self, years_clauses=(), **{f.name: getattr(clause, f.name) for f in fields(clause)})


def years_rule(lead):
    """Return (class, gap, A deduction, human reason). No title-only exclusion.

    Evaluate distinct supplied scopes and retain the most restrictive result;
    never sum overlapping years or reuse broad support
    tenure for an unsupported speciality. None means no supplied years clause;
    requirements_complete remains the caller's exact-text review gate.
    """
    if lead.years_clauses:
        results = [years_rule(replace(lead, years_clauses=(), **{
            f.name: getattr(clause, f.name) for f in fields(clause)})) for clause in lead.years_clauses]
        priority = {"NONE": 0, "MET": 0, "TEACHABLE": 1, "HEAVY": 2,
                    "UNKNOWN": 3, "SENIORITY MISMATCH": 4, "HARD": 5}
        return max(results, key=lambda r: (priority[r[0]], r[2], r[1] is not None,
                                          r[1] if r[1] is not None else Fraction(0), r[3]))
    if lead.required_years is None:
        return ("UNKNOWN" if lead.legal_minimum else "NONE", None, Fraction(0),
                "Confirm the legal or contractual minimum" if lead.legal_minimum else "")
    relevant = (exact(lead.relevant_years) if lead.relevant_years is not None
                else DOCUMENTED_RELEVANT_YEARS.get(lead.years_domain))
    if relevant is None:
        return "UNKNOWN", None, Fraction(0), "Confirm documented years in the required work"
    required = exact(lead.required_years)
    gap = required - relevant
    # Sources remain private inputs/constants; display human-readable decimals.
    required_text = display(required).rstrip("0").rstrip(".")
    relevant_text = display(relevant).rstrip("0").rstrip(".")
    known = "under 1" if lead.relevant_years is None and lead.years_domain == "agent" else "about " + relevant_text
    reason = f"asks for {required_text} years; you have {known} in the relevant work"
    if gap <= 0:
        return "TEACHABLE" if gap == 0 else "MET", gap, Fraction(0), ""
    if lead.legal_minimum:
        if lead.relevant_years is None or not lead.legal_conflict_confirmed:
            return "UNKNOWN", gap, Fraction(0), reason + "; confirm exact tenure against the legal minimum"
        return "HARD", gap, Fraction(0), reason + "; confirmed legal or contractual minimum"
    mismatch = gap >= 5 or (gap > 2 and lead.title_seniority)
    if mismatch and not lead.equivalent_experience_accepted:
        return "SENIORITY MISMATCH", gap, Fraction(0), reason
    # Accepted equivalents remove the mismatch veto, not the gap or packet gates.
    if gap > 2:
        return "HEAVY", gap, HEAVY_YEARS_PENALTY, reason
    return "TEACHABLE", gap, TEACHABLE_YEARS_PENALTY, reason


def compute_c(lead):
    core = [g for g in lead.groups if g.classification == "core"]
    if not core:
        return None
    return sum(({"S": Fraction(1), "P": Fraction(1, 2), "T": Fraction(0)}[g.rating]
                for g in core), Fraction(0)) / len(core)


def english_flag(lead):
    # Force is independently adjudicated: text matching alone cannot decide
    # whether a mixed/negated/preferred clause establishes a requirement.
    triggers = r"\b(?:C[12]|near[ -]native|native|fluent|fluency)\b|excellent\s+(?:written|verbal)\b"
    return int(lead.english_force == "required" and
               bool(re.search(triggers, lead.english_clause, re.IGNORECASE)))


def compute_a(lead):
    """Return (raw, adjusted), retaining exact quarter-points for n=6."""
    c = compute_c(lead)
    if c is None:
        return None, None
    raw = 2 + 3 * c
    return raw, max(Fraction(0), min(Fraction(5), raw - Fraction(english_flag(lead), 4)
                                   - years_rule(lead)[2]))


def compute_d(lead):
    if lead.work is None or lead.growth is None:
        return None
    return Fraction(7, 10) * exact(lead.work) + Fraction(3, 10) * exact(lead.growth)


def want_value(lead):
    return {"": Fraction(5, 2), "Neutral": Fraction(5, 2),
            "Dream": Fraction(5), "Yes": Fraction(4), "No": Fraction(0)}[lead.want]


def want_label(lead):
    return lead.want or "Not set"


def compute_u(lead, now):
    aware(now, "now")
    u = Fraction(5, 2)
    if lead.deadline_verified and lead.deadline is not None:
        delta = lead.deadline - now
        # Past dates without confirmed closure need Verify, not invented U5.
        if timedelta(0) <= delta <= timedelta(hours=48):
            u = Fraction(5)
        elif timedelta(hours=48) < delta <= timedelta(days=7):
            u = Fraction(4)
        elif timedelta(days=7) < delta <= timedelta(days=14):
            u = Fraction(3)
    return max(u, Fraction(3)) if lead.imminent_filling_verified else u


def compute_q(lead, now, want_weight=Fraction(1, 10)):
    w = exact(want_weight)
    if not 0 <= w <= 1:
        raise ValueError("Want weight must be 0..1")
    a, d = compute_a(lead)[1], compute_d(lead)
    if a is None or d is None:
        return None
    # The sole canonical Q expression; no independently rounded expansion.
    return (1 - w) * (Fraction(3, 5) * a + Fraction(3, 10) * d +
                      Fraction(1, 10) * compute_u(lead, now)) + w * want_value(lead)


def fresh(lead, now):
    aware(now, "now")
    if lead.source_checked_at is None:
        return False
    age = now - lead.source_checked_at
    if age < -timedelta(minutes=5):
        raise ValueError("source_checked_at is more than five minutes in the future relative to now")
    return age <= timedelta(hours=24)


@dataclass(frozen=True)
class Assessment:
    lead: Lead
    lane: str
    tags: tuple
    why: str
    strip: str = ""
    label: str = ""
    next_action: str = ""
    due_at: object = None
    verify_state: object = None


def assess(lead, now):
    """Single first-match disposition procedure; Applied is an orthogonal strip."""
    aware(now, "now")
    source_fresh = fresh(lead, now)
    c, a = compute_c(lead), compute_a(lead)[1]
    years_class, _, _, years_reason = years_rule(lead)
    holds = list(lead.preparation_holds)
    if not lead.packet_assessed:
        holds.append("packet unassessed")
    if not lead.base_cv_version or not lead.cv_keyword_passed:
        holds.append("versioned base CV keyword check pending")
    if not lead.preparation_clear:
        holds.append("preparation clearance pending")
    if lead.adjudication_pending:
        holds.append("requirement adjudication pending")
    if lead.english_adjudication_pending:
        holds.append("English clause adjudication pending")
    if lead.hard_blockers or years_class == "HARD":
        lane, reason = "Archive", "Confirmed restriction: " + "; ".join(
            lead.hard_blockers + ((years_reason,) if years_class == "HARD" else ()))
    elif lead.owner_hold or lead.want == "No":
        lane, reason = "Monitor", "the owner's recommendation hold"
    elif years_class == "SENIORITY MISMATCH":
        lane, reason = "Monitor", "Seniority mismatch: " + years_reason
    elif (not lead.source_open or not source_fresh or not lead.eligibility_clear
          or not lead.requirements_complete or c is None or lead.urgency_conflict
          or years_class == "UNKNOWN"
          or (lead.deadline_verified and lead.deadline is not None and lead.deadline < now)):
        lane, reason = "Verify", "Resolve source freshness, availability, eligibility or requirements"
    elif years_class == "HEAVY":
        high_direction = compute_d(lead) is not None and compute_d(lead) >= HIGH_DIRECTION
        allowed = ((lead.want == "Dream" and c >= Fraction(1, 2)) or
                   (high_direction and c >= Fraction(1, 2) and a >= Fraction(13, 4)) or
                   ((lead.want == "Dream" or high_direction) and lead.adjudication_pending
                    and lead.plausible_half_coverage))
        lane, reason = ("Stretch" if allowed else "Monitor"), years_reason
        if holds:
            reason += "; prepare/review: " + "; ".join(holds)
    elif not lead.owner_check_complete:
        supported = ((c >= Fraction(1, 2) and a >= Fraction(13, 4)) or
                     (lead.adjudication_pending and lead.plausible_half_coverage))
        # A packet-cleared Dream would pass Ready after this check, even at A3.
        dream_ready = not holds and lead.want == "Dream" and c >= Fraction(1, 2)
        lane = "Stretch" if supported or dream_ready else "Monitor"
        reason = "Owner-hold check pending" + ("; low supported coverage" if lane == "Monitor" else "")
    elif not holds and c >= Fraction(1, 2) and (a >= 4 or lead.want == "Dream"):
        lane, reason = "Ready", "Shared checks passed; honest application can go now; tailoring optional"
    elif (c >= Fraction(1, 2) and a >= Fraction(13, 4)) or (lead.adjudication_pending and lead.plausible_half_coverage):
        lane, reason = "Stretch", "Prepare/review: " + ("; ".join(holds) or "teachable coverage gaps")
    else:
        lane, reason = "Monitor", "Low supported coverage; reassess in weekly review"
    tags = ()
    if lane == "Ready":
        tags = (("Dream",) if lead.want == "Dream" else ()) + (("Safe bet",) if a >= 4 else ())
    fits = ", ".join(g.name for g in lead.groups if g.rating in ("S", "P")) or "coverage not established"
    gaps = ", ".join(g.unsupported_scope or g.name for g in lead.groups if g.rating != "S") or "none identified"
    why = f"Fits: {fits}. Teachable gaps: {gaps}. {lane}: {reason}. Work model: {lead.work_model}."
    if years_reason:
        why += f" {'Seniority mismatch' if years_class == 'SENIORITY MISMATCH' else 'Years'}: {years_reason}."
    if compute_d(lead) is None:
        why += " Direction Unknown; review duties and growth before numeric ranking."
    # These fields are human prose; keep evidence identifiers out of the card.
    why = re.sub(r"\bE-\d+\b", "private evidence", why)
    label = "Applied " + (str(lead.applied_at) if lead.applied_at is not None else "date Unknown") if lead.applied else ""
    state = (lead.verify_state or VerifyState(now + timedelta(hours=24))) if lane == "Verify" else None
    action = (years_reason if years_class == "UNKNOWN" else lead.verify_action) if lane == "Verify" else ""
    if state is not None and state.renewal_reason:
        why += " Verify renewed: " + state.renewal_reason + "."
    if state is not None and state.due_at <= now:
        why += " Verify overdue; resolve or explicitly renew with a reason."
    clean = lambda value: re.sub(r"\bE-\d+\b", "private evidence", value)
    return Assessment(lead, lane, tags, clean(why), "Follow-up" if lead.applied else "", label,
                      clean(action), state.due_at if state else None, state)


def order_key(item, now, want_weight=Fraction(1, 10), dream_band=True):
    lead = item.lead
    q, d, a = compute_q(lead, now, want_weight), compute_d(lead), compute_a(lead)[1]
    epoch = datetime(1, 1, 1, tzinfo=timezone.utc)
    def temporal(value, newest=False):
        normalized = aware(value, "tie-break datetime") if value is not None else epoch
        return (value is None, -(normalized - epoch) if newest else normalized)
    deadline = temporal(lead.deadline if lead.deadline_verified else None)
    effort = (lead.preparation_effort is None,
              exact(lead.preparation_effort) if lead.preparation_effort is not None else Fraction(0))
    discovery = temporal(lead.discovered_at if not lead.seen else None)
    checked = temporal(lead.source_checked_at, newest=True)
    return ((0 if item.strip else 1),
            (0 if item.strip and lead.want == "Dream" else 1),
            {"Ready": 0, "Stretch": 1, "Verify": 2, "Monitor": 3, "Archive": 4}[item.lane],
            0 if dream_band and item.lane == "Ready" and lead.want == "Dream" else 1,
            q is None, -q if q is not None else Fraction(0),
            -d if d is not None else Fraction(0), -a if a is not None else Fraction(0),
            deadline, effort, discovery, checked, lead.key)


def ordered(leads, now, want_weight=Fraction(1, 10), dream_band=True):
    """One record per canonical key; applied alias wins, conflicts need Verify.

    Conflicting aliases retain a deterministic record with an adjudication hold,
    leaving unrelated leads rankable. Identical aliases are harmless.
    """
    aware(now, "now")
    if type(dream_band) is not bool:
        raise ValueError("dream_band must be a real boolean")
    canonical = {}
    for lead in leads:
        previous = canonical.get(lead.key)
        if previous is None or (lead.applied and not previous.applied):
            canonical[lead.key] = lead
        elif previous.applied and not lead.applied:
            continue
        elif previous != lead:
            canonical[lead.key] = replace(min(previous, lead, key=repr),
                requirements_complete=False,
                verify_action="Adjudicate conflicting aliases for this posting")
    return tuple(sorted((assess(lead, now) for lead in canonical.values()),
                        key=lambda item: order_key(item, now, want_weight, dream_band)))


@dataclass(frozen=True)
class Exposure:
    employer: str
    at: datetime

    def __post_init__(self):
        if not isinstance(self.employer, str) or not self.employer.strip():
            raise ValueError("Exposure needs a nonblank employer")
        aware(self.at, "exposure at")


@dataclass(frozen=True)
class Deferral:
    item: Assessment
    reasons: tuple


@dataclass(frozen=True)
class Shortfall:
    requested: int
    selected: int
    reasons: tuple
    deferrals: tuple = ()


@dataclass(frozen=True)
class Selection:
    primary: tuple
    verify: tuple
    follow_up: tuple
    deferred: tuple
    active_dream_count: int
    prompt: str
    deferrals: tuple = ()
    shortfall: object = None
    reservation_shortfall: object = None


def employer_key(lead):
    # Unknown clients never merge by title; use agency provisionally.
    return (lead.end_employer or lead.placing_agency or lead.employer).strip().casefold()


def urgent(item, now):
    lead = item.lead
    return (item.lane == "Ready" and lead.deadline_verified and lead.deadline is not None
            and timedelta(0) <= lead.deadline - now <= timedelta(hours=48))


def eligible_pool(items, now):
    return [i for i in items if not i.strip and i.lane in ("Ready", "Stretch")
            and not i.lead.owner_hold and i.lead.want != "No"
            and i.lead.owner_check_complete and fresh(i.lead, now)]


def exposure_counts(exposures, now):
    aware(now, "now")
    counts = {}
    for exposure in exposures:
        if now - timedelta(days=7) < exposure.at <= now:
            key = exposure.employer.strip().casefold()
            counts[key] = counts.get(key, 0) + 1
    return counts


def daily_five(leads, now, exposures=(), want_weight=Fraction(1, 10)):
    """Reserve ordinary Ready first; caps defer exposure, never lane or score.

    Dual Dream/Safe-bet roles fill the ordinary reservation once and do not
    consume a Dream-band slot. Exposure input is actual delivered history;
    this pure function never records exposure or changes caller-owned state.
    """
    items = ordered(leads, now, want_weight)
    candidates = eligible_pool(items, now)
    chosen, employers, keys = [], set(), set()
    placements = dreams = 0
    weekly = exposure_counts(exposures, now)

    def failures(item):
        lead = item.lead
        emp = employer_key(lead)
        reasons = []
        if len(chosen) >= 5:
            reasons.append("Daily five is full")
        if emp in employers:
            reasons.append("One recommendation per employer daily")
        if weekly.get(emp, 0) >= 2:
            reasons.append("Employer has two exposures in rolling seven days")
        if lead.source_type in ("placement", "provisional-placement") and placements >= 2:
            reasons.append("Two-placement daily cap")
        dream = lead.want == "Dream" and "Safe bet" not in item.tags
        if dream and dreams >= 2:
            reasons.append("Two Dream-band daily cap")
        return tuple(reasons)

    def take(item):
        nonlocal placements, dreams
        if item.lead.key in keys or failures(item):
            return False
        lead = item.lead
        chosen.append(item)
        keys.add(lead.key)
        employers.add(employer_key(lead))
        placements += int(lead.source_type in ("placement", "provisional-placement"))
        dreams += int(lead.want == "Dream" and "Safe bet" not in item.tags)
        return True

    ordinary = sorted((i for i in candidates if "Safe bet" in i.tags),
                      key=lambda i: (not urgent(i, now), order_key(i, now, want_weight)))
    for item in ordinary:
        if sum("Safe bet" in i.tags for i in chosen) >= 2:
            break
        take(item)
    reservation_target = min(2, len(ordinary))
    reservation_selected = sum("Safe bet" in i.tags for i in chosen)
    reservation_reasons = tuple(Deferral(i, failures(i)) for i in ordinary if i.lead.key not in keys)
    reservation_shortfall = (Shortfall(reservation_target, reservation_selected,
                                      tuple(reason for d in reservation_reasons for reason in d.reasons),
                                      reservation_reasons)
                             if reservation_selected < reservation_target else None)
    fill = sorted(candidates, key=lambda i: (not urgent(i, now), order_key(i, now, want_weight)))
    for item in fill:
        take(item)
    chosen.sort(key=lambda i: (not urgent(i, now), order_key(i, now, want_weight)))
    deferred = tuple(i for i in candidates if i.lead.key not in keys)
    deferrals = tuple(Deferral(i, failures(i)) for i in deferred)
    active = sum(i.lead.want == "Dream" and not i.strip and i.lane != "Archive"
                 and not i.lead.owner_hold for i in items)
    shortfall = (Shortfall(5, len(chosen),
                          tuple(reason for d in deferrals for reason in d.reasons) or
                          ("Fewer than five eligible unsubmitted leads",), deferrals)
                 if len(chosen) < 5 else None)
    return Selection(tuple(chosen), tuple(i for i in items if i.lane == "Verify" and not i.strip)[:3],
                     tuple(i for i in items if i.strip), deferred, active,
                     "Rank your Dreams" if active > 3 else "", deferrals, shortfall, reservation_shortfall)


@dataclass(frozen=True)
class WeeklyAdvice:
    entries: tuple
    deferrals: tuple = ()
    shortfall: object = None


def weekly_advice(leads, now, exposures=(), want_weight=Fraction(1, 10)):
    """Full eligible pool, independent of daily truncation/reservations/Dream cap.

    Advice retains one employer per list, two placements per list and at most
    two delivered exposures per employer in rolling seven days. Merely previewing
    advice does not consume exposure. One preferred Dream, then two Safe bets,
    other Ready, then Stretch, each at most once; at most three total.
    """
    items = eligible_pool(ordered(leads, now, want_weight), now)
    items.sort(key=lambda i: (not urgent(i, now), order_key(i, now, want_weight)))
    result, keys, employers = [], set(), set()
    weekly = exposure_counts(exposures, now)

    def failures(item):
        reasons = []
        if len(result) >= 3:
            reasons.append("Weekly advice three is full")
        if employer_key(item.lead) in employers:
            reasons.append("One recommendation per employer in weekly advice")
        if weekly.get(employer_key(item.lead), 0) >= 2:
            reasons.append("Employer has two exposures in rolling seven days")
        placements = sum(i.lead.source_type in ("placement", "provisional-placement") for i, _ in result)
        if item.lead.source_type in ("placement", "provisional-placement") and placements >= 2:
            reasons.append("Two-placement weekly advice cap")
        return tuple(reasons)

    for predicate, limit in ((lambda i: "Dream" in i.tags, 1),
                             (lambda i: "Safe bet" in i.tags, 2),
                             (lambda i: i.lane == "Ready", 3),
                             (lambda i: i.lane == "Stretch", 3)):
        count = 0
        for item in items:
            if len(result) == 3:
                break
            if item.lead.key not in keys and predicate(item) and count < limit and not failures(item):
                result.append((item, "prepare/review" if item.lane == "Stretch" else "apply now"))
                keys.add(item.lead.key)
                employers.add(employer_key(item.lead))
                count += 1
    deferrals = tuple(Deferral(i, failures(i)) for i in items if i.lead.key not in keys)
    shortfall = (Shortfall(3, len(result),
                          tuple(reason for d in deferrals for reason in d.reasons) or
                          ("Fewer than three eligible unsubmitted leads",), deferrals)
                 if len(result) < 3 else None)
    return WeeklyAdvice(tuple(result), deferrals, shortfall)
