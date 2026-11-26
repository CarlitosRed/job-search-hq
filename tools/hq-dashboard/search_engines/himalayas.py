"""Use the Himalayas feed and the shared employer confirmation rules."""

LABEL = 'Himalayas'


def read(execute, queries, settings):
    return execute('himalayas', queries)
