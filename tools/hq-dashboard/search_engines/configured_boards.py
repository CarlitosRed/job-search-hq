"""Read the profile's starting Greenhouse, Lever and Ashby boards."""

LABEL, PACE, CAP, TIMEOUT = 'Configured employer boards', 0.3, 300, 10


def read(execute, queries, settings):
    return execute('configured_boards', queries)
