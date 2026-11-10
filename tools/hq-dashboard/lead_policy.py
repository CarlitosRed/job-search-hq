"""v3.2 production boundary for supplied posting assessments.


Extraction is model work; arithmetic and disposition are ranker work. Private
Want and packet clearance are joined by the caller, never accepted from a model.
"""
import importlib.util
import json
import re
from pathlib import Path
import sys
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
RUBRIC_VERSION = 'v3.2'


def load_ranker():
    name = 'hq_lead_ranker_v32'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE / 'lead_ranker.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses needs the module registered
        spec.loader.exec_module(module)
    return sys.modules[name]


ranker = load_ranker()
if ranker.RUBRIC_VERSION != RUBRIC_VERSION:
    raise ValueError('Rubric version mismatch: ranker and adapter; nothing scored')

#
SCORING_PROMPT = """RUBRIC_VERSION: v3.2
Extract and group distinct core task clauses BEFORE reading evidence or historical decisions.
Retain original clauses and alternatives; preferred requirements stay preferred. Do not
multiply one platform into several tasks or count years as another core task.
Each match importance is exactly "must" for core, required or requested requirements
and "nice" for preferred requirements. Put free-text nuance in rationale.
Rate each match strong (direct stated/verified support), partial (adjacent support,
name unsupported scope), or gap. No usable evidence means gap. No invented skills.
Return analysis inputs, not a computed score or lane. Extract the exact English clause
and whether it is required, requested, preferred, negated or unknown.
Years: preserve each distinct original clause, lower endpoint of required range;
omit preferred-only years. Use only documented relevant years with a source;
missing tenure stays Unknown. Never infer an applicant history or borrow general
support years for a specialist tool. Private tenure defaults are joined locally.
Extract equivalent-experience alternatives and legal minima faithfully. A legal
conflict needs independent confirmation and exact documented tenure; never infer it.
Only confirmed geography/authorization, non-substitutable credential, genuine non-English
language conflict, closed posting or established fraud can be hard blockers; quote conflict.
403/timeouts/redirects or unknown eligibility require verification, never a hard blocker.
D=.70 work+.30 growth, each 0-5 from actual duties (AI/agent work 5, implementation 4,
analytics 3, repetitive operations 2); explain half points. Unknown is null.
Extract verified deadline and source-check facts; unknown dates stay null.
Private Want joins locally. Never infer or return Want, owner holds or packet clearance.
No pay or contract questions. Copy advertised pay faithfully; Unknown is no penalty.
No applications, outreach, unsolicited CVs or letters. Never hide teachable-gap leads.
"""


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def extraction_schema():
    text = {'type': 'string'}
    boolean = {'type': 'boolean'}
    exact = {'type': ['string', 'null']}
    clause = obj(dict(clause=text, required_years=exact, years_domain=text,
                      relevant_years=exact, relevant_years_source=text,
                      equivalent_experience_accepted=boolean, legal_minimum=boolean,
                      legal_conflict_confirmed=boolean))
    return obj(dict(work=exact, growth=exact, direction_reason=text,
                    english_clause=text, english_force={'type': 'string', 'enum': ['required','requested','preferred','negated','none','unknown']},
                    title_seniority=boolean, years_clauses={'type':'array','items':clause},
                    hard_blockers={'type':'array','items':obj(dict(
                        kind={'type':'string','enum':['geography','credential','other_language','closed','fraud']},
                        clause=text, conflict=text, confirmed=boolean))}, source_open=boolean,
                    source_checked_at={'type':['string','null']}, eligibility_clear=boolean,
                    requirements_complete=boolean, deadline={'type':['string','null']},
                    deadline_verified=boolean, imminent_filling_verified=boolean,
                    urgency_conflict=boolean))


def instant(value):
    if value is None:
        return None
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    ranker.aware(result, 'assessment timestamp')
    return result


def read_wants(path):
    """Private sidecar, keyed by exact canonical posting URL, never part of bank."""
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def validate_shape(value, schema):
    """Validate CLI/fake-runner output too, not just the provider's schema promise."""
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['required']):
            raise ValueError('Missing or unexpected extraction fields')
        for key, spec in schema['properties'].items():
            validate_shape(value[key], spec)
    elif kind == 'array':
        if not isinstance(value, list):
            raise ValueError('Expected extraction array')
        for item in value:
            validate_shape(item, schema['items'])
    else:
        allowed = kind if isinstance(kind, list) else [kind]
        actual = 'null' if value is None else 'boolean' if type(value) is bool else 'string' if isinstance(value, str) else 'invalid'
        if actual not in allowed or 'enum' in schema and value not in schema['enum']:
            raise ValueError('Invalid extraction type or value')


#
def normalize_importance(value):
    if isinstance(value, str):
        token = value.split(';', 1)[0].strip().lower()
        if token in ('core', 'required', 'requested', 'must', 'mandatory'):
            return 'must'
        if token in ('preferred', 'nice', 'bonus', 'plus', 'advantageous'):
            return 'nice'
    raise ValueError('Each requirement needs its original clause and scope rationale: invalid importance')


def rank_result(out, key, employer, now=None, context=None, want=None, return_lead=False):
    if out.get('rubric_version') != RUBRIC_VERSION:
        raise ValueError('Rubric version mismatch: expected v3.2; nothing scored or written')
    now = now or datetime.now(timezone.utc)
    data = out.get('analysis')
    validate_shape(data, extraction_schema())
    data = dict(data)
    data.pop('direction_reason')
    data['years_clauses'] = tuple(ranker.YearsClause(**{k:v for k,v in c.items() if k != 'clause'}) for c in data['years_clauses'])
    blockers = []
    for b in data['hard_blockers']:
        if not b['confirmed'] or not b['clause'].strip() or not b['conflict'].strip():
            raise ValueError('Hard blocker requires an exact clause and confirmed conflict')
        if b['kind'] == 'other_language' and re.search(r'\bEnglish\b|\bC[12]\b', b['clause'], re.I):
            raise ValueError('English is a soft flag only, never a hard blocker')
        blockers.append(b['clause'] + '; ' + b['conflict'])
    data['hard_blockers'] = tuple(blockers)
    for name in ('source_checked_at', 'deadline'):
        data[name] = instant(data[name])
    groups = []
    for m in out['matches']:
        rating = {'strong':'S','partial':'P','gap':'T'}.get(m.get('level'))
        if rating is None:
            raise ValueError('Invalid requirement evidence level')
        m['importance'] = normalize_importance(m.get('importance'))
        for field in ('clause', 'rationale'):
            if not isinstance(m.get(field), str) or not m[field].strip():
                raise ValueError('Each requirement needs its original clause and scope rationale: invalid ' + field)
        if rating != 'T' and not m.get('evidence'):
            rating = 'T'
        groups.append(ranker.Requirement(m['requirement'], rating,
                      'preferred' if m.get('importance') == 'nice' else 'core',
                      m['clause'], m['rationale'], m.get('evidence',''), m.get('unsupported_scope','')))
    # Caller-owned packet/provenance only. Model output cannot clear these gates.
    context = dict(context or {})
    permitted = {'owner_hold','owner_check_complete','packet_assessed','base_cv_version',
                 'cv_keyword_passed','preparation_clear','preparation_holds','adjudication_pending',
                 'plausible_half_coverage','english_adjudication_pending','applied','applied_at',
                 'work_model','verify_state'}
    if set(context) - permitted:
        raise ValueError('Unexpected private policy context')
    context['preparation_holds'] = tuple(context.get('preparation_holds', ()))
    if context.get('applied_at'):
        context['applied_at'] = instant(context['applied_at'])
    if context.get('verify_state'):
        state = context['verify_state']
        context['verify_state'] = ranker.VerifyState(instant(state['due_at']), instant(state.get('previous_due_at')), state.get('renewal_reason',''))
    private = dict(want or {})
    if set(private) - {'want','actor','at'}:
        raise ValueError('Invalid private Want record')
    lead = ranker.Lead(key=key, employer=employer, groups=tuple(groups), **data, **context,
                       want=private.get('want',''), want_actor=private.get('actor',''), want_at=instant(private.get('at')))
    item = ranker.assess(lead, now)
    value = lambda v: None if v is None else str(v)
    state = item.verify_state
    result = dict(rubric_version=RUBRIC_VERSION, A=value(ranker.compute_a(lead)[1]),
                D=value(ranker.compute_d(lead)), V=value(ranker.want_value(lead)),
                U=value(ranker.compute_u(lead, now)), Q=value(ranker.compute_q(lead, now)),
                want=ranker.want_label(lead), lane=item.lane, tags=list(item.tags),
                why=item.why, next_action=item.next_action, strip=item.strip, label=item.label,
                years_class=ranker.years_rule(lead)[0],
                years_reason=ranker.years_rule(lead)[3],
                verify_state=None if state is None else dict(due_at=state.due_at.isoformat(),
                    previous_due_at=state.previous_due_at.isoformat() if state.previous_due_at else None,
                    renewal_reason=state.renewal_reason))
    return (result, lead) if return_lead else result
