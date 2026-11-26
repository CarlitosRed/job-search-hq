"""Remotive remote job leads."""
from urllib.parse import urlencode

LABEL, PACE, CAP, TIMEOUT = 'Remotive', 2, 1, 12


def enabled(settings):
    return True


def read(fetch, queries, settings):
    data = fetch('GET', 'https://remotive.com/api/remote-jobs?' + urlencode(dict(limit=100)))
    return [dict(url=row.get('url', ''), title=row.get('title', ''), company=row.get('company_name', ''))
            for row in data.get('jobs', [])]
