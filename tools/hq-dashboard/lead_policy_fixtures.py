"""Fictional extraction packets for offline tests only.

"""
from copy import deepcopy


def fictional_analysis():
    return dict(work='2', growth='2', direction_reason='Fictional operational duties.',
                english_clause='', english_force='none', title_seniority=False,
                years_clauses=[], hard_blockers=[], source_open=True,
                source_checked_at='2026-11-07T12:00:00+00:00', eligibility_clear=True,
                requirements_complete=True, deadline=None, deadline_verified=False,
                imminent_filling_verified=False, urgency_conflict=False)


def fictional_result(ratings='SSSSPT'):
    return dict(rubric_version='v3.2', is_job_posting=True, readable=True,
                company='Fictional employer', role='Fictional operations role',
                link='https://example.com/fictional', posting_text='',
                summary='Fictional match.', location_ok='Fictional eligible location',
                gaps=[], flags=[], analysis=fictional_analysis(), matches=[
                    dict(requirement=f'Fictional task {i}', importance='must',
                         evidence='E-999' if r != 'T' else '',
                         level={'S':'strong','P':'partial','T':'gap'}[r],
                         clause=f'Fictional requirement {i}', rationale='Fictional scope only',
                         unsupported_scope='' if r=='S' else 'Fictional unsupported task scope')
                    for i,r in enumerate(ratings)])


def cleared_context():
    return dict(owner_check_complete=True, packet_assessed=True,
                base_cv_version='fictional-base-v1', cv_keyword_passed=True,
                preparation_clear=True)
