# Fictional fixtures and settings-aware expectations.
"""Fictional, offline acceptance fixtures; not posting/evidence verification.


Reviewer: run with Python 3.12, e.g. python -I tools/hq-dashboard/test_lead_ranker.py
The builder did not execute Python. The module is loaded only in this isolated
test harness; the activation adapter now imports lead_ranker. Updated by
Codex - Activation Builder, 2026-11-07; execution remains maintainer-owned.

F11: the design supplies counts and summaries, NOT immutable original postings
or six auditable clause rows. Every clause/rationale below is labelled fictional;
these are arithmetic/policy fixtures and must never clear a real packet.
Historical table checks use explicit Yes where v3.1 formerly derived Yes. The
accepted F4 benchmark separately uses blanks and asserts corrected Q values.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone, date
from fractions import Fraction
from pathlib import Path
import runpy
import json
import os
import shutil
import tempfile
import unittest

# Use this suite's fictional fixture folder instead of an external data folder.
os.environ.pop("JOB_SEARCH_HQ_DATA", None)


# Arithmetic fixtures own their sourced tenure settings.
with tempfile.TemporaryDirectory(prefix='fictional-ranker-settings-') as folder:
    root = Path(folder)
    dashboard = root / 'tools' / 'hq-dashboard'
    dashboard.mkdir(parents=True)
    for name in ('lead_ranker.py', 'hq_settings.py'):
        shutil.copyfile(Path(__file__).with_name(name), dashboard / name)
    shutil.copyfile(Path(__file__).resolve().parents[2] / 'settings.example.json', root / 'settings.example.json')
    fictional_years = {'analyst': 2, 'operations': 2, 'consulting': 2, 'support': 3, 'agent': 0}
    (root / 'settings.json').write_text(json.dumps({
        'documented_relevant_years': fictional_years,
        'documented_years_sources': {domain: 'Fictional documented same-scope tenure' for domain in fictional_years},
        'documented_years_labels': {'agent': 'under 1'},
    }), encoding='utf-8')
    api = runpy.run_path(str(dashboard / 'lead_ranker.py'))
Lead = api["Lead"]
Requirement = api["Requirement"]
Exposure = api["Exposure"]
F = Fraction
NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def groups(names, ratings):
    """Fictional six-row packets; summaries reproduce the design's S/P/T."""
    return tuple(Requirement(name, rating, clause=f"Fictional clause: {name}",
                             rationale=f"Fictional {rating} scope assessment",
                             permissible_evidence="Fictional stated/verified example",
                             unsupported_scope="" if rating == "S" else f"Fictional gap: {name}")
                 for name, rating in zip(names, ratings))


def fictional_lead(key="fictional", ratings="SSSSPT", work="2", growth="2", **changes):
    """Fictional cleared baseline. Real callers must establish every gate."""
    fields = dict(key=key, employer="Fictional employer " + key,
                  groups=groups(tuple("Fictional task " + str(i) for i in range(6)), ratings),
                  work=None if work is None else F(work),
                  growth=None if growth is None else F(growth), owner_check_complete=True,
                  want_actor="Fictional Alex", want_at=NOW,
                  source_open=True, source_checked_at=NOW, eligibility_clear=True,
                  requirements_complete=True, packet_assessed=True,
                  base_cv_version="fictional-base-business-v1", cv_keyword_passed=True,
                  preparation_clear=True, discovered_at=NOW - timedelta(days=1))
    fields.update(changes)
    return Lead(**fields)


def benchmark(historical_want=False, historical_placement=False):
    """All TEN records are FICTIONAL analogues, never live recommendations.

    Current Example Placement uses requested English with adjudication pending. The
    historical_placement overlay exists only for the labelled historical test.
    Pre-application Example Cloud/Example Database are intentional arithmetic snapshots.
    """
    rows = (
        ("Technical BA", "SSSPPT", "4", "4.5",
         ("discovery", "QA", "client coordination", "integration", "AI-assisted work", "ERP/accounting")),
        ("Example Placement", "SSSPPT", "2.5", "3",
         ("follow-up", "issues", "documentation", "coordination", "improvement", "workforce scheduling")),
        ("AI Workflow", "SSPPTT", "5", "4.5",
         ("discovery", "validation", "AI direction", "user docs", "business rollout", "integration")),
        ("Example Helpdesk", "SSPPPT", "2.5", "3",
         ("issues", "docs", "reporting", "improvement", "bilingual operations", "creator/fraud")),
        ("Example Recruiting", "SSSPPT", "2", "2.5",
         ("records", "follow-up", "docs", "reporting", "recruiting coordination", "sourcing/founder-voice writing")),
        ("Example Lab", "SSSPTT", "3", "3.5",
         ("reporting", "requirements", "coordination", "proposals", "clinical budgeting", "negotiation")),
        ("Example Cloud", "SSPPPT", "5", "5",
         ("requirements", "process work", "agent direction", "prompt quality", "data", "production platform/RAG")),
        ("Example Database", "SSPPPT", "5", "5",
         ("governance", "docs", "workflow", "evaluation", "spend", "API/pipeline/cloud")),
        ("Example Revenue", "SSSPPT", "4.5", "5",
         ("requirements", "validation", "operations", "Salesforce", "AI implementation", "Workato/Copilot Studio")),
        ("Example Ratings", "SSSSPT", "2", "2",
         ("updates", "quality", "inquiries", "deadlines", "AI awareness", "ratings/instruments/disclosures")),
    )
    result = []
    for name, ratings, work, growth, names in rows:
        lead = fictional_lead(name, ratings, work, growth, groups=groups(names, ratings))
        if name in ("Example Placement", "Example Helpdesk", "Example Recruiting"):
            lead = replace(lead, english_clause="Fictional requirement: excellent written and verbal English",
                           english_force="required")
        if name in ("Technical BA", "AI Workflow"):
            lead = replace(lead, eligibility_clear=False,
                           verify_action="Check the application form's country list")
        if name == "Example Cloud":
            lead = replace(lead, want="Dream", want_actor="Fictional Alex", want_at=NOW)
        elif historical_want and name in ("Technical BA", "AI Workflow", "Example Database", "Example Revenue"):
            lead = replace(lead, want="Yes", want_actor="Fictional explicit sensitivity input", want_at=NOW)
        if name == "Example Revenue":
            lead = replace(lead, preparation_clear=False,
                           preparation_holds=("Implementation-scope packet review",))
        if name == "Example Helpdesk":
            lead = replace(lead, preparation_clear=False, preparation_holds=("Equipment/network unknown",),
                           source_type="provisional-placement")
        if name == "Example Placement":
            lead = replace(lead, source_type="placement", placing_agency="Fictional Example Placement")
            if not historical_placement:
                lead = replace(lead, english_force="requested", english_adjudication_pending=True,
                               english_clause="Fictional clause: excellent written and verbal English is requested")
        result.append(lead)
    return result


class BenchmarkTests(unittest.TestCase):
    def test_ten_case_exact_arithmetic_and_historical_sensitivity(self):
        # Fictional historical input overlays reproduce EVERY design number.
        c = ("2/3", "2/3", "1/2", "7/12", "2/3", "7/12", "7/12", "7/12", "2/3", "3/4")
        raw = ("4", "4", "3.5", "3.75", "4", "3.75", "3.75", "3.75", "4", "4.25")
        adjusted = ("4", "4", "3.5", "3.5", "3.75", "3.75", "3.75", "3.75", "4", "4.25")
        d = ("4.15", "2.65", "4.85", "2.65", "2.15", "3.15", "5", "5", "4.65", "2")
        expected = (
            ("3.895", "3.9055", "3.916"), ("3.445", "3.3505", "3.256"),
            ("3.805", "3.8245", "3.844"), ("3.145", "3.0805", "3.016"),
            ("3.145", "3.0805", "3.016"), ("3.445", "3.3505", "3.256"),
            ("4", "4.1", "4.2"), ("4", "4", "4"),
            ("4.045", "4.0405", "4.036"), ("3.4", "3.31", "3.22"))
        lanes = ("Verify", "Stretch", "Verify", "Stretch", "Stretch", "Stretch", "Ready", "Stretch", "Stretch", "Ready")
        for i, lead in enumerate(benchmark(historical_want=True)):
            with self.subTest(fictional=lead.key):
                self.assertEqual(api["compute_c"](lead), F(c[i]))
                self.assertEqual(api["compute_a"](lead), (F(raw[i]), F(adjusted[i])))
                self.assertEqual(api["compute_d"](lead), F(d[i]))
                self.assertEqual(api["assess"](lead, NOW).lane, lanes[i])
                for weight, q in zip((F(0), F(".1"), F(".2")), expected[i]):
                    actual = api["compute_q"](lead, NOW, weight)
                    self.assertIsInstance(actual, F)
                    self.assertEqual(actual, F(q))

    def test_corrected_blank_want_benchmark_and_order(self):
        # Fictional accepted F4 inputs: old derived Yes is now blank Neutral.
        corrected = ("3.7555", "3.3505", "3.6745", "3.0805", "3.0805", "3.3505", "4.1", "3.85", "3.8905", "3.31")
        corrected_twenty = ("3.616", "3.256", "3.544", "3.016", "3.016", "3.256", "4.2", "3.7", "3.736", "3.22")
        for lead, ten, twenty in zip(benchmark(), corrected, corrected_twenty):
            self.assertEqual(api["compute_q"](lead, NOW), F(ten))
            self.assertEqual(api["compute_q"](lead, NOW, F(".2")), F(twenty))
        expected = ["Example Cloud", "Example Ratings", "Example Revenue", "Example Database", "Example Lab", "Example Placement", "Example Helpdesk", "Example Recruiting", "Technical BA", "AI Workflow"]
        for weight in (F(0), F(".1"), F(".2")):
            self.assertEqual([i.lead.key for i in api["ordered"](benchmark(), NOW, weight)], expected)
        ready = [i for i in api["ordered"](benchmark(), NOW) if i.lane == "Ready"]
        self.assertEqual(ready[0].tags, ("Dream",))
        self.assertEqual(ready[1].tags, ("Safe bet",))
        self.assertIn("Work model: Unknown", ready[1].why)
        self.assertIn("tailoring optional", ready[1].why)

    def test_historical_placement_required_english_assumption(self):
        historical = benchmark(historical_placement=True)[1]
        self.assertEqual(api["compute_a"](historical), (F(4), F("3.75")))
        for weight, expected in (("0", "3.295"), (".1", "3.2155"), (".2", "3.136")):
            self.assertEqual(api["compute_q"](historical, NOW, F(weight)), F(expected))

    def test_current_pavago_thermo_exact_tie_at_every_weight(self):
        pavago, thermo = benchmark()[1], benchmark()[5]
        for weight in (F(0), F(".1"), F(".2")):
            self.assertEqual(api["compute_q"](pavago, NOW, weight), api["compute_q"](thermo, NOW, weight))
            self.assertEqual(api["ordered"]([pavago, thermo], NOW, weight)[0].lead.key, thermo.key)
        self.assertEqual(api["compute_q"](pavago, NOW), F(6701, 2000))

    def test_requested_english_is_not_required_and_needs_adjudication(self):
        # Fictional exact-clause alternative to historical Example Placement assumption.
        pavago = replace(benchmark()[1], english_clause="Excellent written and verbal English is requested",
                         english_force="requested", english_adjudication_pending=True)
        self.assertEqual(api["compute_a"](pavago), (F(4), F(4)))
        self.assertEqual(api["assess"](pavago, NOW).lane, "Stretch")
        self.assertEqual(api["assess"](replace(pavago, eligibility_clear=False), NOW).lane, "Verify")
        for force in ("negated", "preferred", "none", "unknown"):
            self.assertEqual(api["english_flag"](replace(pavago, english_force=force)), 0)
        self.assertEqual(api["english_flag"](replace(pavago, english_force="required")), 1)

    def test_benchmark_primary_excludes_applied_and_weekly_prepares(self):
        # Fictional post-application snapshot; no live tracker read.
        pool = [replace(i, applied=True, applied_at=date(2026, 11, 6))
                if i.key in ("Example Cloud", "Example Database") else i for i in benchmark()]
        selection = api["daily_five"](pool, NOW)
        self.assertEqual([i.lead.key for i in selection.primary],
                         ["Example Ratings", "Example Revenue", "Example Lab", "Example Placement", "Example Helpdesk"])
        self.assertEqual(selection.follow_up[0].lead.key, "Example Cloud")
        self.assertEqual(selection.follow_up[0].strip, "Follow-up")
        self.assertEqual(selection.follow_up[0].label, "Applied 2026-11-06")
        advice = api["weekly_advice"](pool, NOW).entries
        self.assertEqual([i.lead.key for i, label in advice], ["Example Ratings", "Example Revenue", "Example Lab"])
        self.assertEqual([label for i, label in advice], ["apply now", "prepare/review", "prepare/review"])


class PolicyTests(unittest.TestCase):
    def test_half_up_display_and_no_rounding_admission(self):
        self.assertEqual(api["display"](F("3.595")), "3.60")
        self.assertEqual(api["display"](F("2.975")), "2.98")
        self.assertEqual(api["display"](F("3.995")), "4.00")
        self.assertEqual(api["display"](None), "Unknown")
        # Fictional finer denominators construct exact threshold counterexamples.
        stretch = fictional_lead("fictional-3.595", groups=groups(["fictional"] * 600, "S" * 319 + "T" * 281))
        monitor = fictional_lead("fictional-2.975", groups=groups(["fictional"] * 40, "S" * 13 + "T" * 27))
        just_under = fictional_lead("fictional-3.995", groups=groups(["fictional"] * 200, "S" * 133 + "T" * 67))
        self.assertEqual(api["compute_a"](stretch)[1], F("3.595"))
        self.assertEqual(api["compute_a"](monitor)[1], F("2.975"))
        self.assertEqual(api["assess"](stretch, NOW).lane, "Stretch")
        self.assertEqual(api["assess"](monitor, NOW).lane, "Monitor")
        self.assertEqual(api["assess"](just_under, NOW).lane, "Stretch")
        self.assertEqual(api["assess"](fictional_lead(ratings="SSSPPT"), NOW).lane, "Ready")

    def test_exact_tie_before_direction(self):
        a, b = benchmark()[3], benchmark()[4]  # Fictional SYA/Example Recruiting analogues
        self.assertEqual(api["compute_q"](a, NOW), F("3.0805"))
        self.assertEqual(api["compute_q"](a, NOW), api["compute_q"](b, NOW))
        self.assertEqual(api["ordered"]([b, a], NOW)[0].lead.key, a.key)

    def test_blank_equals_neutral_at_every_weight(self):
        blank = fictional_lead(work="5", growth="5")
        neutral = replace(blank, want="Neutral")
        self.assertEqual(api["want_label"](blank), "Not set")
        self.assertEqual(api["want_value"](blank), F("2.5"))
        for weight in (F(0), F(".1"), F(".2")):
            self.assertEqual(api["compute_q"](blank, NOW, weight), api["compute_q"](neutral, NOW, weight))
        self.assertEqual(api["order_key"](api["assess"](blank, NOW), NOW),
                         api["order_key"](api["assess"](neutral, NOW), NOW))

    def test_numeric_want_sensitivity_separate_from_dream_band_and_admission(self):
        # Fictional X/Y from the design: explicit Yes for Y, no derived default.
        x = fictional_lead("fictional-X", "SSSSSS", "2.2", "2.2")
        y = fictional_lead("fictional-Y", "SSSPPT", "4", "4", want="Yes")
        for weight, qx, qy in ((F(0), "3.91", "3.85"), (F(".1"), "3.769", "3.865"), (F(".2"), "3.628", "3.88")):
            self.assertEqual(api["compute_q"](x, NOW, weight), F(qx))
            self.assertEqual(api["compute_q"](y, NOW, weight), F(qy))
        dream = fictional_lead("fictional-lower-dream", "SSSPTT", "0", "0", want="Dream")
        self.assertEqual(api["assess"](replace(dream, want=""), NOW).lane, "Stretch")
        self.assertEqual(api["assess"](dream, NOW).lane, "Ready")
        self.assertEqual(api["ordered"]([x, dream], NOW, F(0))[0].lead.key, dream.key)
        self.assertEqual(api["ordered"]([x, dream], NOW, F(0), dream_band=False)[0].lead.key, x.key)
        # F1 eliminates the old 3.75 floor, retaining C>=half and shared gates.
        low_dream = fictional_lead("fictional-low-dream", "SSSPTT", want="Dream",
                                   english_clause="Fluent English required", english_force="required")
        self.assertEqual(api["compute_a"](low_dream)[1], F("3.5"))
        self.assertEqual(api["assess"](low_dream, NOW).lane, "Ready")

    def test_dream_never_overrides_packet_hard_or_owner_gates(self):
        dream = fictional_lead(want="Dream")
        for change in (dict(packet_assessed=False), dict(base_cv_version=""),
                       dict(cv_keyword_passed=False), dict(preparation_clear=False),
                       dict(adjudication_pending=True), dict(owner_check_complete=False),
                       dict(preparation_holds=("Fictional required equipment unknown",))):
            with self.subTest(fictional_hold=change):
                self.assertNotEqual(api["assess"](replace(dream, **change), NOW).lane, "Ready")
        hard = replace(dream, hard_blockers=("Fictional mandatory licence absent; no equivalent accepted",),
                       owner_hold=True, eligibility_clear=False)
        self.assertEqual(api["assess"](hard, NOW).lane, "Archive")
        self.assertEqual(api["assess"](replace(dream, owner_hold=True, eligibility_clear=False), NOW).lane, "Monitor")
        self.assertEqual(api["assess"](replace(dream, want="No"), NOW).lane, "Monitor")
        self.assertEqual(api["assess"](replace(dream, groups=()), NOW).lane, "Verify")
        self.assertEqual(api["assess"](replace(dream, groups=groups(["fictional"] * 6, "SPTTTT")), NOW).lane, "Monitor")

    def test_source_due_dates_adjudication_and_human_why(self):
        lead = fictional_lead("fictional-verify", eligibility_clear=False,
                               verify_action="Check application country list")
        item = api["assess"](lead, NOW)
        self.assertEqual(item.due_at, NOW + timedelta(hours=24))
        self.assertEqual(item.next_action, "Check application country list")
        self.assertEqual(api["assess"](replace(lead, eligibility_clear=True, source_checked_at=NOW - timedelta(hours=25)), NOW).lane, "Verify")
        self.assertEqual(api["assess"](replace(lead, eligibility_clear=True, source_checked_at=NOW - timedelta(hours=24)), NOW).lane, "Ready")
        provisional = fictional_lead("fictional-disagreement", "SSTTTT", adjudication_pending=True,
                                     plausible_half_coverage=True)
        self.assertEqual(api["assess"](provisional, NOW).lane, "Stretch")
        for item in api["ordered"](benchmark(), NOW):
            self.assertIn("Fits:", item.why)
            self.assertIn("Teachable gaps:", item.why)
            self.assertNotRegex(item.why, r"E-\d+")

    def test_age_preferred_groups_and_unknown_direction(self):
        lead = fictional_lead()
        extra = Requirement("Fictional preferred platform", "T", "preferred")
        old = replace(lead, age_days=500, groups=lead.groups + (extra,))
        self.assertEqual(api["compute_c"](old), api["compute_c"](lead))
        self.assertEqual(api["compute_q"](old, NOW), api["compute_q"](lead, NOW))
        self.assertIsNone(api["compute_q"](replace(lead, work=None), NOW))
        self.assertIsNone(api["compute_a"](replace(lead, groups=()))[0])
        with self.assertRaises(TypeError):
            api["compute_q"](lead, NOW, 0.1)

    def test_urgency_boundaries(self):
        lead = fictional_lead()
        for delta, value in ((timedelta(0), "5"), (timedelta(hours=48), "5"),
                             (timedelta(hours=49), "4"), (timedelta(days=7), "4"),
                             (timedelta(days=8), "3"), (timedelta(days=14), "3"),
                             (timedelta(days=15), "2.5")):
            self.assertEqual(api["compute_u"](replace(lead, deadline_verified=True, deadline=NOW + delta), NOW), F(value))
        self.assertEqual(api["compute_u"](replace(lead, deadline=NOW + timedelta(hours=1)), NOW), F("2.5"))
        self.assertEqual(api["compute_u"](replace(lead, imminent_filling_verified=True), NOW), F(3))
        self.assertEqual(api["assess"](replace(lead, deadline_verified=True, deadline=NOW - timedelta(seconds=1)), NOW).lane, "Verify")


class GradedYearsTests(unittest.TestCase):
    """F12 fictional scope inputs; reviewer executes, builder did not run Python."""

    def lead(self, required, relevant, **changes):
        return fictional_lead(required_years=required, relevant_years=relevant,
                               relevant_years_source="Fictional documented same-scope tenure",
                               **changes)

    def test_real_analyst_gap_tiers_without_ratio(self):
        for required, classification, penalty in ((2, "TEACHABLE", F(0)),
                                                   (4, "TEACHABLE", F(".25")),
                                                   (5, "HEAVY", F(".75")),
                                                   (6, "HEAVY", F(".75")),
                                                   (7, "SENIORITY MISMATCH", F(0)),
                                                   (10, "SENIORITY MISMATCH", F(0))):
            lead = fictional_lead(required_years=required, years_domain="analyst")
            with self.subTest(required=required):
                self.assertEqual(api["years_rule"](lead)[:3],
                                 (classification, F(required - 2), penalty))
                self.assertEqual(api["compute_a"](lead), (F("4.25"), F("4.25") - penalty))
        self.assertEqual(api["years_rule"](self.lead("4.5", 2))[0], "HEAVY")
        self.assertEqual(api["years_rule"](self.lead("6.5", 2))[0], "HEAVY")
        self.assertEqual(api["years_rule"](self.lead(2, 3))[:3], ("MET", F(-1), F(0)))
        self.assertEqual(api["years_rule"](self.lead(4, 2))[3],
                         "asks for 4 years; you have about 2 in the relevant work")

    def test_labelled_generic_candidate_five_relevant_years(self):
        # Generic candidate only: this is NOT the fictional candidate's tenure.
        self.assertEqual(api["years_rule"](self.lead(8, 5))[:3], ("HEAVY", F(3), F(".75")))

    def test_real_support_and_agent_tiers(self):
        for domain, required, classification in (("support", 5, "TEACHABLE"),
                                                  ("support", 6, "HEAVY"),
                                                  ("support", 8, "SENIORITY MISMATCH"),
                                                  ("agent", 1, "TEACHABLE"),
                                                  ("agent", 2, "TEACHABLE"),
                                                  ("agent", 3, "HEAVY"),
                                                  ("agent", 5, "SENIORITY MISMATCH")):
            self.assertEqual(api["years_rule"](fictional_lead(required_years=required,
                                                              years_domain=domain))[0], classification)

    def test_title_extraction_and_title_gap_boundaries(self):
        for title, expected in (("Senior Analyst", True), ("AI Workflow Lead", True),
                                 ("Principal Analyst", True), ("Director", True), ("Manager", True),
                                 ("Leadership Analyst", False), ("Data Analyst", False),
                                 ("Management Consultant", False)):
            flag = api["seniority_title"](title)
            self.assertIs(flag, expected)
            # Same 3-year gap; distinct extracted True/False values change triage.
            lead = self.lead(5, 2, title_seniority=flag)
            self.assertEqual(api["years_rule"](lead)[0], "SENIORITY MISMATCH" if expected else "HEAVY")
        senior = self.lead(3, 2, title_seniority=api["seniority_title"]("Senior Analyst"))
        self.assertEqual(api["years_rule"](senior)[0], "TEACHABLE")
        self.assertEqual(api["assess"](senior, NOW).lane, "Ready")
        workflow = fictional_lead(required_years=8, years_domain="agent", want="Dream",
                                  title_seniority=api["seniority_title"]("AI Workflow Lead"))
        item = api["assess"](workflow, NOW)
        self.assertEqual(item.lane, "Monitor")
        self.assertIn("Seniority mismatch:", item.why)
        self.assertNotIn("8 years", item.why.split("Teachable gaps:")[1].split(".")[0])
        self.assertEqual(api["daily_five"]([workflow], NOW).primary, ())
        self.assertEqual(api["years_rule"](fictional_lead(title_seniority=True))[0], "NONE")

    def test_heavy_dream_and_high_direction_never_ready(self):
        heavy = self.lead(5, 2)
        self.assertEqual(api["assess"](heavy, NOW).lane, "Monitor")
        dream = replace(heavy, want="Dream")
        self.assertEqual(api["assess"](dream, NOW).lane, "Stretch")
        self.assertEqual(api["compute_a"](dream)[1], F("3.5"))
        for direction, lane in (("3.49", "Monitor"), ("3.5", "Stretch")):
            self.assertEqual(api["assess"](replace(heavy, work=direction, growth=direction), NOW).lane, lane)
        thin = replace(dream, groups=groups(["fictional"] * 6, "SPTTTT"))
        self.assertEqual(api["assess"](thin, NOW).lane, "Monitor")
        low_a = replace(dream, groups=groups(["fictional"] * 6, "SSSPTT"))
        self.assertEqual(api["assess"](low_a, NOW).lane, "Stretch")
        self.assertEqual(api["assess"](replace(dream, required_years=7), NOW).lane, "Monitor")

    def test_posting_equivalence_is_not_blanket_clearance(self):
        mismatch = self.lead(10, 2, want="Dream")
        equivalent = replace(mismatch, equivalent_experience_accepted=True)
        self.assertEqual(api["years_rule"](equivalent)[0], "HEAVY")
        self.assertEqual(api["assess"](equivalent, NOW).lane, "Stretch")
        # A small gap is teachable even with a seniority title.
        small = self.lead(4, 2, title_seniority=True, equivalent_experience_accepted=True)
        self.assertEqual(api["years_rule"](small)[0], "TEACHABLE")
        self.assertEqual(api["assess"](small, NOW).lane, "Ready")
        self.assertEqual(api["assess"](replace(equivalent, eligibility_clear=False), NOW).lane, "Verify")
        self.assertNotEqual(api["assess"](replace(small, packet_assessed=False), NOW).lane, "Ready")

    def test_legal_minimum_and_other_precedence(self):
        legal = self.lead(3, 2, legal_minimum=True, equivalent_experience_accepted=True,
                          legal_conflict_confirmed=True,
                          want="Dream", owner_hold=True, eligibility_clear=False)
        self.assertEqual(api["years_rule"](legal)[0], "HARD")
        self.assertEqual(api["assess"](legal, NOW).lane, "Archive")
        self.assertEqual(api["assess"](replace(legal, relevant_years=3, owner_hold=False,
                                               eligibility_clear=True), NOW).lane, "Ready")
        heavy = self.lead(5, 2, want="Dream", eligibility_clear=False)
        self.assertEqual(api["assess"](heavy, NOW).lane, "Verify")
        self.assertEqual(api["assess"](replace(heavy, owner_hold=True), NOW).lane, "Monitor")
        self.assertEqual(api["assess"](replace(heavy, hard_blockers=("Fictional closure confirmed",)), NOW).lane, "Archive")

    def test_documented_defaults_unknowns_and_no_inflation(self):
        expected = {"analyst": 2, "operations": 2, "consulting": 2, "support": 3, "agent": 0}
        self.assertEqual(api["DOCUMENTED_RELEVANT_YEARS"], expected)
        for domain, relevant in expected.items():
            lead = fictional_lead(required_years=4, years_domain=domain)
            self.assertEqual(api["years_rule"](lead)[1], F(4 - relevant))
            self.assertTrue(api["DOCUMENTED_YEARS_SOURCES"][domain])
        agent = fictional_lead(required_years=1, years_domain="agent")
        self.assertIn("you have under 1", api["assess"](agent, NOW).why)
        unknown = fictional_lead(required_years=3)
        self.assertEqual(api["assess"](unknown, NOW).lane, "Verify")
        self.assertIn("Confirm documented years", api["assess"](unknown, NOW).next_action)
        self.assertEqual(api["assess"](fictional_lead(legal_minimum=True), NOW).lane, "Verify")
        for change in (dict(required_years=-1), dict(relevant_years=-1),
                       dict(required_years=2.0), dict(relevant_years=2, relevant_years_source="")):
            with self.assertRaises((TypeError, ValueError)):
                fictional_lead(**change)

    def test_f12_preserves_all_ten_exact_benchmark_inputs(self):
        # No years clause was supplied in the original arithmetic fixtures.
        # Explicitly met clauses must also be neutral: compare ALL scores/lanes,
        # both historical and F4 overlays, and all three Want weights exactly.
        for historical in (False, True):
            for lead in benchmark(historical_want=historical):
                met = replace(lead, required_years=2, years_domain="analyst")
                self.assertEqual(api["years_rule"](lead)[0], "NONE")
                for function in ("compute_c", "compute_a", "compute_d"):
                    self.assertEqual(api[function](lead), api[function](met))
                self.assertEqual(api["assess"](lead, NOW),
                                 replace(api["assess"](met, NOW), lead=lead))
                for weight in (F(0), F(".1"), F(".2")):
                    self.assertEqual(api["compute_q"](lead, NOW, weight),
                                     api["compute_q"](met, NOW, weight))


class SelectionTests(unittest.TestCase):
    def dreams(self, count):
        """Fictional distinct-employer Dream-only paths, A3.75."""
        return [fictional_lead(f"fictional-dream-{i}", "SSSPTT", "5", "5", want="Dream") for i in range(count)]

    def safe(self, count):
        """Fictional distinct-employer ordinary Ready paths, A4.25."""
        return [fictional_lead(f"fictional-safe-{i}") for i in range(count)]

    def test_all_dream(self):
        result = api["daily_five"](self.dreams(6), NOW)
        self.assertEqual(len(result.primary), 2)
        self.assertEqual(len(result.deferred), 4)
        self.assertEqual(result.active_dream_count, 6)
        self.assertEqual(result.prompt, "Rank your Dreams")

    def test_many_dream_with_ordinary_reservation(self):
        result = api["daily_five"](self.dreams(7) + self.safe(4), NOW)
        self.assertEqual(len(result.primary), 5)
        self.assertEqual(sum("Dream" in i.tags for i in result.primary), 2)
        self.assertGreaterEqual(sum("Safe bet" in i.tags for i in result.primary), 2)
        self.assertEqual(result.primary[0].tags, ("Dream",))

    def test_urgent_safe_bet_advances_cleared_ready_only(self):
        urgent = replace(self.safe(1)[0], deadline=NOW + timedelta(hours=48), deadline_verified=True)
        result = api["daily_five"](self.dreams(5) + [urgent] + self.safe(3)[1:], NOW)
        self.assertEqual(result.primary[0].lead.key, urgent.key)
        uncleared = replace(urgent, packet_assessed=False)
        self.assertNotEqual(api["daily_five"](self.dreams(2) + [uncleared], NOW).primary[0].lead.key, urgent.key)

    def test_empty_bucket_and_single_safe(self):
        self.assertEqual(api["daily_five"]([], NOW).primary, ())
        self.assertEqual(len(api["daily_five"](self.safe(6), NOW).primary), 5)
        result = api["daily_five"](self.dreams(6) + self.safe(1), NOW)
        self.assertEqual(len(result.primary), 3)
        self.assertEqual(sum("Safe bet" in i.tags for i in result.primary), 1)

    def test_overlap_counts_once_without_consuming_dream_cap(self):
        both = [replace(i, want="Dream") for i in self.safe(4)]
        result = api["daily_five"](both, NOW)
        self.assertEqual(len(result.primary), 4)
        self.assertTrue(all(i.tags == ("Dream", "Safe bet") for i in result.primary))
        self.assertEqual(len({i.lead.key for i in result.primary}), 4)

    def test_two_dream_only_plus_three_ordinary_including_dual(self):
        ordinary = self.safe(3)
        ordinary[0] = replace(ordinary[0], want="Dream")
        result = api["daily_five"](self.dreams(2) + ordinary, NOW)
        self.assertEqual(len(result.primary), 5)
        self.assertEqual(sum(i.tags == ("Dream",) for i in result.primary), 2)
        self.assertEqual(sum("Safe bet" in i.tags for i in result.primary), 3)
        self.assertIsNone(result.shortfall)

    def test_diversity_daily_weekly_placement_and_canonical_dedup(self):
        pool = [replace(i, source_type="placement") for i in self.safe(6)]
        self.assertEqual(len(api["daily_five"](pool, NOW).primary), 2)
        same = [replace(i, end_employer="Fictional same client") for i in self.safe(4)]
        self.assertEqual(len(api["daily_five"](same, NOW).primary), 1)
        unknown_clients = [replace(i, source_type="placement", placing_agency="Fictional agency") for i in self.safe(4)]
        self.assertEqual(len(api["daily_five"](unknown_clients, NOW).primary), 1)
        candidate = self.safe(1)[0]
        recent = [Exposure(candidate.employer, NOW - timedelta(days=1)), Exposure(candidate.employer, NOW - timedelta(days=6))]
        self.assertEqual(api["daily_five"]([candidate], NOW, recent).primary, ())
        boundary = [Exposure(candidate.employer, NOW - timedelta(days=7)), recent[0]]
        self.assertEqual(len(api["daily_five"]([candidate], NOW, boundary).primary), 1)
        self.assertEqual(len(api["daily_five"]([candidate, candidate], NOW).primary), 1)
        applied_alias = replace(candidate, applied=True, applied_at=date(2026, 11, 6))
        self.assertEqual(api["daily_five"]([candidate, applied_alias], NOW).primary, ())

    def test_tie_breaks_and_input_order_independence(self):
        # Fictional equal scores; last tie-break is canonical key.
        a, b = fictional_lead("fictional-a"), fictional_lead("fictional-b")
        self.assertEqual([i.lead.key for i in api["ordered"]([b, a], NOW)], [a.key, b.key])
        deadline = replace(b, deadline=NOW + timedelta(days=30), deadline_verified=True)
        self.assertEqual(api["ordered"]([a, deadline], NOW)[0].lead.key, b.key)
        effort = replace(b, preparation_effort=F(1))
        self.assertEqual(api["ordered"]([a, effort], NOW)[0].lead.key, b.key)
        old = replace(b, discovered_at=NOW - timedelta(days=2))
        self.assertEqual(api["ordered"]([a, old], NOW)[0].lead.key, b.key)
        pool = self.dreams(5) + self.safe(4)
        self.assertEqual(api["daily_five"](pool, NOW), api["daily_five"](list(reversed(pool)), NOW))


class AcceptedReviewFixTests(unittest.TestCase):
    """G1-G9 acceptance cases; maintainer must execute these offline."""

    def test_validation_rejects_malformed_inputs(self):
        boolean_names = [field.name for field in api["fields"](Lead) if field.type is bool]
        for name in boolean_names:
            for bad in ("false", 0, 1, None):
                with self.subTest(gate=name, bad=bad), self.assertRaisesRegex(ValueError, "real boolean"):
                    fictional_lead(**{name: bad})
        for changes in (dict(source_type="placements"), dict(source_type=""),
                        dict(key=" "), dict(employer=""), dict(key=" edge"),
                        dict(end_employer=" "), dict(placing_agency=" edge"),
                        dict(applied_at="yesterday"),
                        dict(preparation_effort=-1), dict(preparation_effort="-.01"),
                        dict(preparation_effort=0.1), dict(preparation_effort=True),
                        dict(want="Dream", want_actor=""), dict(want="Dream", want_at=None)):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                fictional_lead(**changes)
        for want in ("Dream", "Yes", "Neutral", "No"):
            with self.assertRaisesRegex(ValueError, "Explicit Want"):
                Lead("fictional", "Fictional employer", want=want)
        self.assertEqual(api["exact"](fictional_lead(preparation_effort="0").preparation_effort), F(0))

    def test_all_datetimes_require_timezones_and_normalize_offsets(self):
        naive = datetime(2026, 11, 8)
        for name in ("deadline", "source_checked_at", "discovered_at", "want_at", "applied_at"):
            with self.subTest(field=name), self.assertRaisesRegex(ValueError, name + ".*timezone-aware"):
                fictional_lead(**{name: naive})
        with self.assertRaisesRegex(ValueError, "now.*timezone-aware"):
            api["assess"](fictional_lead(), naive)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            Exposure("Fictional", naive)
        offset = timezone(timedelta(hours=-6))
        lead = fictional_lead(source_checked_at=NOW.astimezone(offset))
        self.assertEqual(api["assess"](lead, NOW).lane, "Ready")
        self.assertEqual(api["order_key"](api["assess"](lead, NOW), NOW),
                         api["order_key"](api["assess"](replace(lead, source_checked_at=NOW), NOW), NOW))

    def test_legal_floor_is_verify_until_conflict_confirmed(self):
        lower_bound = fictional_lead(required_years=3, years_domain="analyst", legal_minimum=True)
        self.assertEqual(api["years_rule"](lower_bound)[0], "UNKNOWN")
        self.assertEqual(api["assess"](lower_bound, NOW).lane, "Verify")
        self.assertIn("confirm exact tenure", api["assess"](lower_bound, NOW).next_action)
        # A source string establishes a lower bound, not confirmed absence.
        sourced = replace(lower_bound, relevant_years=2,
                          relevant_years_source="Fictional approved 2+ analyst years")
        self.assertEqual(api["assess"](sourced, NOW).lane, "Verify")
        confirmed = replace(sourced, legal_conflict_confirmed=True,
                            relevant_years_source="Fictional exact tenure confirmed against legal clause")
        self.assertEqual(api["assess"](confirmed, NOW).lane, "Archive")
        for flag in (False, True):
            self.assertEqual(api["assess"](replace(sourced, relevant_years=3,
                legal_conflict_confirmed=flag), NOW).lane, "Ready")
        Clause = api["YearsClause"]
        for relevant, confirmed_flag, lane in ((None, False, "Verify"), (2, False, "Verify"),
                                                (2, True, "Archive"), (3, False, "Ready")):
            clause = Clause(3, "analyst", relevant,
                            "Fictional sourced lower bound" if relevant is not None else "",
                            legal_minimum=True, legal_conflict_confirmed=confirmed_flag)
            self.assertEqual(api["assess"](fictional_lead(years_clauses=(clause,)), NOW).lane, lane)
        for bad in ("false", 0, 1, None):
            with self.assertRaisesRegex(ValueError, "real boolean"):
                fictional_lead(years_clauses=(Clause(3, "analyst", legal_conflict_confirmed=bad),))
        self.assertEqual(api["assess"](replace(lower_bound, hard_blockers=(
            "Fictional independently confirmed legal-tenure conflict",)), NOW).lane, "Archive")

    def test_distinct_years_scopes_take_most_restrictive_once(self):
        Clause = api["YearsClause"]
        clauses = (Clause(4, "analyst"), Clause(6, "support"), Clause(2, "agent"))
        lead = fictional_lead(want="Dream", years_clauses=clauses)
        self.assertEqual(api["years_rule"](lead)[:3], ("HEAVY", F(3), F(".75")))
        self.assertEqual(api["compute_a"](lead)[1], F("3.5"))
        self.assertEqual(api["assess"](lead, NOW).lane, "Stretch")
        self.assertEqual(api["years_rule"](replace(lead, years_clauses=tuple(reversed(clauses)))),
                         api["years_rule"](lead))
        unknown = replace(lead, years_clauses=clauses + (Clause(3, "speciality"),))
        self.assertEqual(api["assess"](unknown, NOW).lane, "Verify")
        mismatch = replace(lead, years_clauses=clauses + (Clause(7, "analyst"),))
        self.assertEqual(api["assess"](mismatch, NOW).lane, "Monitor")
        legal = Clause(3, "analyst", 2, "Fictional confirmed exact tenure", legal_minimum=True,
                       legal_conflict_confirmed=True)
        self.assertEqual(api["assess"](replace(lead, years_clauses=clauses + (legal,)), NOW).lane, "Archive")
        with self.assertRaisesRegex(ValueError, "OR scalar"):
            replace(lead, required_years=4)
        with self.assertRaisesRegex(ValueError, "real boolean"):
            replace(lead, years_clauses=(Clause(4, "analyst", legal_minimum="false"),))

    def test_reviewed_range_preference_and_tool_scope_inputs(self):
        # Input-contract fixtures, NOT a claim that this module parses prose.
        # Reviewed "3-5 analyst years" supplies lower endpoint 3, not 5.
        ranged = fictional_lead(required_years=3, years_domain="analyst")
        self.assertEqual(api["years_rule"](ranged)[:3], ("TEACHABLE", F(1), F(".25")))
        # "5 years preferred" supplies no required minimum.
        preferred = fictional_lead()
        self.assertEqual(api["years_rule"](preferred)[0], "NONE")
        # General support tenure cannot establish years in a particular tool.
        Clause = api["YearsClause"]
        tool = fictional_lead(years_clauses=(Clause(3, "support"), Clause(2, "specific-tool")))
        self.assertEqual(api["assess"](tool, NOW).lane, "Verify")
        self.assertEqual(api["assess"](replace(ranged, requirements_complete=False), NOW).lane, "Verify")

    def test_owner_check_completion_never_silently_demotes_stretch(self):
        low = fictional_lead(ratings="SSSTTT", required_years=4, years_domain="analyst",
                             english_force="required", english_clause="Fluent English required")
        self.assertEqual(api["compute_a"](low)[1], F(3))
        # Ordinary low coverage is already Monitor before the owner check.
        for complete in (False, True):
            self.assertEqual(api["assess"](replace(low, owner_check_complete=complete), NOW).lane, "Monitor")
        # The same half-supported, teachable Dream reaches Ready at A3.
        dream = replace(low, want="Dream")
        self.assertEqual(api["assess"](replace(dream, owner_check_complete=False), NOW).lane, "Stretch")
        self.assertEqual(api["assess"](dream, NOW).lane, "Ready")
        supported = fictional_lead(ratings="SSSPTT", owner_check_complete=False)
        self.assertEqual(api["assess"](supported, NOW).lane, "Stretch")
        self.assertEqual(api["assess"](replace(supported, owner_check_complete=True), NOW).lane, "Stretch")

    def test_explicit_dream_survives_unknown_direction_blank_stays_neutral(self):
        dream = fictional_lead(want="Dream", work=None)
        self.assertEqual(api["want_value"](dream), F(5))
        self.assertEqual(api["want_value"](replace(dream, want="")), F("2.5"))
        self.assertIsNone(api["compute_q"](dream, NOW))
        self.assertIn("Direction Unknown", api["assess"](dream, NOW).why)

    def test_verify_state_is_carried_and_explicitly_renewed(self):
        lead = fictional_lead(eligibility_clear=False)
        first = api["assess"](lead, NOW)
        carried = replace(lead, verify_state=first.verify_state)
        later = api["assess"](carried, NOW + timedelta(hours=25))
        self.assertEqual(later.due_at, first.due_at)
        self.assertEqual(later.verify_state, first.verify_state)
        renewed = api["renew_verify"](later.verify_state, NOW + timedelta(hours=25),
                                       "Country list still unavailable; retry exact source")
        item = api["assess"](replace(carried, verify_state=renewed), NOW + timedelta(hours=25))
        self.assertEqual(item.verify_state.previous_due_at, first.due_at)
        self.assertEqual(item.due_at, NOW + timedelta(hours=49))
        self.assertIn("Verify renewed: Country list still unavailable", item.why)
        with self.assertRaises(ValueError):
            api["renew_verify"](first.verify_state, NOW, "premature")
        with self.assertRaises(ValueError):
            api["renew_verify"](first.verify_state, first.due_at, " ")
        with self.assertRaises(ValueError):
            api["VerifyState"](NOW + timedelta(days=2), NOW + timedelta(days=1), "")

    def test_structured_daily_deferrals_reservation_and_active_count(self):
        a, b = fictional_lead("fictional-a"), fictional_lead("fictional-b")
        b = replace(b, employer=a.employer)
        result = api["daily_five"]([a, b], NOW)
        self.assertEqual(result.reservation_shortfall.requested, 2)
        self.assertEqual(result.reservation_shortfall.selected, 1)
        self.assertIn("One recommendation per employer daily", result.deferrals[0].reasons)
        self.assertEqual(result.shortfall.selected, 1)
        dreams = SelectionTests().dreams(4)
        held = replace(dreams[0], owner_hold=True)
        result = api["daily_five"]([held] + dreams[1:], NOW)
        self.assertEqual(result.active_dream_count, 3)
        self.assertEqual(result.prompt, "")
        self.assertIn("Two Dream-band daily cap", result.deferrals[0].reasons)
        self.assertEqual(api["daily_five"]([], NOW).shortfall.selected, 0)

    def test_ordered_deduplicates_and_verifies_conflicting_aliases(self):
        lead = fictional_lead()
        self.assertEqual(len(api["ordered"]([lead, lead], NOW)), 1)
        applied = replace(lead, applied=True, applied_at=date(2026, 11, 6))
        for pool in ([lead, applied], [applied, lead]):
            self.assertEqual(api["ordered"](pool, NOW)[0].strip, "Follow-up")
            self.assertEqual(api["daily_five"](pool, NOW).primary, ())
        alias = replace(lead, source_checked_at=NOW - timedelta(hours=1))
        for pool in ([lead, alias], [alias, lead]):
            item, = api["ordered"](pool, NOW)
            self.assertEqual(item.lane, "Verify")
            self.assertFalse(item.lead.requirements_complete)
            self.assertEqual(item.next_action, "Adjudicate conflicting aliases for this posting")
        self.assertEqual(api["ordered"]([lead, alias], NOW), api["ordered"]([alias, lead], NOW))

    def test_conflicting_family_leaves_other_leads_rankable(self):
        lead = fictional_lead()
        alias = replace(lead, source_checked_at=NOW - timedelta(hours=1))
        others = SelectionTests().safe(5)
        for pool in ([lead, alias] + others, list(reversed([lead, alias] + others))):
            items = api["ordered"](pool, NOW)
            self.assertEqual(len(items), 6)
            self.assertEqual(sum(i.lane == "Ready" for i in items), 5)
            selection = api["daily_five"](pool, NOW)
            self.assertEqual(len(selection.primary), 5)
            self.assertEqual(selection.verify[0].lead.key, lead.key)

    def test_heavy_example_cloud_and_ratings_analogues(self):
        leads = {lead.key: lead for lead in benchmark()}
        dream = replace(leads["Example Cloud"], required_years=5, years_domain="analyst")
        safe = replace(leads["Example Ratings"], required_years=5, years_domain="analyst")
        self.assertEqual(api["compute_a"](leads["Example Cloud"])[1], F("3.75"))
        self.assertEqual(api["assess"](dream, NOW).lane, "Stretch")
        self.assertEqual(api["compute_d"](safe), F(2))
        self.assertNotEqual(safe.want, "Dream")
        self.assertEqual(api["assess"](safe, NOW).lane, "Monitor")

    def test_source_clock_tolerance_and_future_error(self):
        for delta in (timedelta(microseconds=1), timedelta(minutes=5)):
            lead = fictional_lead(source_checked_at=NOW + delta)
            self.assertTrue(api["fresh"](lead, NOW))
            self.assertEqual(api["assess"](lead, NOW).lane, "Ready")
        lead = fictional_lead(source_checked_at=NOW + timedelta(minutes=5, microseconds=1))
        for check in (lambda: api["fresh"](lead, NOW), lambda: api["assess"](lead, NOW),
                      lambda: api["daily_five"]([lead], NOW)):
            with self.assertRaisesRegex(ValueError, "source_checked_at.*future"):
                check()

    def test_known_seniority_title_false_positives(self):
        # Known word-detector false positives; harmless at gaps <=2 because
        # only a gap above 2 can escalate based on the reviewed title flag.
        for title in ("Lead Generation Specialist", "Account Manager"):
            self.assertTrue(api["seniority_title"](title))
            lead = fictional_lead(required_years=4, years_domain="analyst",
                                  title_seniority=api["seniority_title"](title))
            self.assertEqual(api["years_rule"](lead)[0], "TEACHABLE")
            self.assertEqual(api["assess"](lead, NOW).lane, "Ready")

    def test_shortfall_reasons_are_strings_and_deferrals_are_separate(self):
        lead = fictional_lead()
        alias_employer = fictional_lead("other", employer=lead.employer)
        daily = api["daily_five"]([lead, alias_employer], NOW)
        weekly = api["weekly_advice"]([lead, alias_employer], NOW)
        for shortfall in (daily.shortfall, daily.reservation_shortfall, weekly.shortfall,
                          api["daily_five"]([], NOW).shortfall,
                          api["weekly_advice"]([], NOW).shortfall):
            self.assertTrue(shortfall.reasons)
            self.assertTrue(all(isinstance(reason, str) for reason in shortfall.reasons))
            self.assertTrue(all(isinstance(d, api["Deferral"]) for d in shortfall.deferrals))
        self.assertEqual(daily.shortfall.deferrals, daily.deferrals)
        self.assertEqual(weekly.shortfall.deferrals, weekly.deferrals)

    def test_weekly_full_pool_ready_fallback_and_stretch_shortfall(self):
        dreams = SelectionTests().dreams(3)
        self.assertEqual(len(api["daily_five"](dreams, NOW).primary), 2)
        advice = api["weekly_advice"](dreams, NOW)
        self.assertEqual(len(advice.entries), 3)
        self.assertTrue(all(label == "apply now" for _, label in advice.entries))
        self.assertIsNone(advice.shortfall)
        stretch = fictional_lead("fictional-stretch", ratings="SSSPTT")
        advice = api["weekly_advice"]([stretch], NOW)
        self.assertEqual(advice.entries[0][1], "prepare/review")
        self.assertEqual(advice.shortfall.selected, 1)
        self.assertEqual(api["weekly_advice"]([], NOW).shortfall.selected, 0)
        self.assertTrue(api["weekly_advice"]([], NOW).shortfall.reasons)

    def test_weekly_exposure_diversity_caps_and_exclusions(self):
        pool = SelectionTests().safe(4)
        same = replace(pool[1], employer=pool[0].employer)
        advice = api["weekly_advice"]([pool[0], same, pool[2], pool[3]], NOW)
        self.assertEqual(len(advice.entries), 3)
        self.assertEqual(len(advice.deferrals), 1)
        self.assertIn("One recommendation per employer in weekly advice", advice.deferrals[0].reasons)
        history = [Exposure(pool[0].employer, NOW - timedelta(days=i)) for i in (1, 6)]
        exhausted = api["weekly_advice"]([pool[0]], NOW, history)
        self.assertEqual(exhausted.entries, ())
        self.assertIn("rolling seven days", exhausted.deferrals[0].reasons[0])
        self.assertIsNotNone(exhausted.shortfall)
        placements = [replace(i, source_type="placement") for i in pool]
        self.assertEqual(len(api["weekly_advice"](placements, NOW).entries), 2)
        excluded = [replace(pool[0], applied=True), replace(pool[1], owner_hold=True),
                    replace(pool[2], owner_check_complete=False),
                    replace(pool[3], source_checked_at=NOW - timedelta(days=2))]
        self.assertEqual(api["weekly_advice"](excluded, NOW).entries, ())

    def test_q_direction_and_attainability_tie_stages(self):
        # Q dominates Direction within the same ordinary Ready band.
        high_q = fictional_lead("fictional-z", ratings="SSSSSS", work="0", growth="0")
        high_d = fictional_lead("fictional-a", ratings="SSSPPT", work="1", growth="1")
        self.assertEqual(api["ordered"]([high_d, high_q], NOW)[0].lead.key, high_q.key)
        # Equal Q; Direction wins despite lower A (SYA/Example Recruiting fixture).
        sya, whippy = benchmark()[3:5]
        self.assertEqual(api["ordered"]([whippy, sya], NOW)[0].lead.key, sya.key)
        # Equal Q and D: A4.25/U2.5 beats A4/U4, despite later/unknown deadline.
        high_a = fictional_lead("fictional-z")
        pressure = fictional_lead("fictional-a", ratings="SSSPPT", deadline_verified=True,
                                  deadline=NOW + timedelta(days=4))
        self.assertEqual(api["compute_q"](high_a, NOW), api["compute_q"](pressure, NOW))
        self.assertEqual(api["ordered"]([pressure, high_a], NOW)[0].lead.key, high_a.key)

    def test_exact_deadline_effort_discovery_freshness_and_key_stages(self):
        a, b = fictional_lead("fictional-a"), fictional_lead("fictional-z")
        def winner(left, right):
            return api["ordered"]([left, right], NOW)[0].lead.key
        # Far-future microseconds can collapse in float timestamps; all U=2.5.
        distant = datetime(9998, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(winner(replace(a, deadline=distant + timedelta(microseconds=1), deadline_verified=True),
                                replace(b, deadline=distant, deadline_verified=True)), b.key)
        self.assertEqual(winner(a, replace(b, deadline=distant, deadline_verified=True)), b.key)
        self.assertEqual(winner(replace(a, preparation_effort=F(1, 3)),
                                replace(b, preparation_effort=F(1, 4))), b.key)
        self.assertEqual(winner(a, replace(b, preparation_effort=F(0))), b.key)
        self.assertEqual(winner(a, replace(b, discovered_at=NOW - timedelta(days=2))), b.key)
        self.assertEqual(winner(replace(a, seen=True), b), b.key)
        self.assertEqual(winner(replace(a, source_checked_at=NOW - timedelta(microseconds=1)), b), b.key)
        self.assertEqual(winner(a, b), a.key)
        # Isolate missing source-check key after fixing the assessed lane.
        item = api["assess"](a, NOW)
        missing = replace(item, lead=replace(a, source_checked_at=None))
        self.assertLess(api["order_key"](item, NOW), api["order_key"](missing, NOW))


class ActivationContract(unittest.TestCase):
    """"""
    def test_active_version_and_under_one_agent_scope_boundary(self):
        self.assertEqual(api['RUBRIC_VERSION'],'v3.2')
        small = fictional_lead(required_years=2,years_domain='agent',title_seniority=True)
        self.assertEqual(api['years_rule'](small)[:3],('TEACHABLE',F(2),F('0.25')))
        self.assertIn('under 1',api['years_rule'](small)[3])
        # A senior title only escalates when the relevant years gap exceeds two.
        larger=replace(small,required_years=3)
        self.assertEqual(api['assess'](larger,NOW).lane,'Monitor')


if __name__ == "__main__":
    unittest.main()
