"""Arbeitnow job leads."""

LABEL, PACE, CAP, TIMEOUT = 'Arbeitnow', 2, 1, 12


def enabled(settings):
    return True


def read(fetch, queries, settings):
    data = fetch('GET', 'https://www.arbeitnow.com/api/job-board-api')
    return [dict(url=row.get('url', ''), title=row.get('title', ''), company=row.get('company_name', ''))
            for row in data.get('data', [])]
