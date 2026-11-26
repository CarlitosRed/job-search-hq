"""Jooble search leads."""
from urllib.parse import quote

LABEL, PACE, CAP, TIMEOUT = 'Jooble', 1, 12, 10


def enabled(settings):
    return bool(settings.get('key'))


def read(fetch, queries, settings):
    hits = []
    for query in queries:
        data = fetch('POST', 'https://jooble.org/api/' + quote(settings['key'], safe=''),
                     dict(keywords=query['words'], location=query['place'], page='1'))
        hits.extend(dict(url=row.get('link', ''), title=row.get('title', ''), company=row.get('company', ''))
                    for row in data.get('jobs', []))
    return hits
