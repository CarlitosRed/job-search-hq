"""Use the existing Workable search and employer confirmation reader."""

LABEL = 'Workable'


def read(execute, queries, settings):
    return execute('workable_search', queries)
