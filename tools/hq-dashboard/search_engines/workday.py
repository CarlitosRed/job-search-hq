"""Read employer Workday APIs with the profile's country scopes."""

LABEL, PACE, CAP, TIMEOUT = 'Workday', 1.5, 150, 12


def read(execute, queries, settings):
    return execute('workday', queries)
