"""Use the Jobicy feed with its existing durable pacing limit."""

LABEL = 'Jobicy'


def read(execute, queries, settings):
    return execute('jobicy', queries)
