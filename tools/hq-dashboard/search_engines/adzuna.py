"""Adzuna search leads."""
from urllib.parse import urlencode

LABEL, PACE, CAP, TIMEOUT = 'Adzuna', 1, 12, 10


def enabled(settings):
    return bool(settings.get('id') and settings.get('key'))


def read(fetch, queries, settings):
    hits = []
    country = settings.get('country', 'us').lower()
    if len(country) != 2 or not country.isascii() or not country.isalpha():
        raise ValueError('Use a two-letter country code for Adzuna.')
    for query in queries:
        params = urlencode(dict(app_id=settings['id'], app_key=settings['key'], results_per_page=20,
                               what=query['words'], where=query['place'], max_days_old=14))
        data = fetch('GET', f'https://api.adzuna.com/v1/api/jobs/{country}/search/1?' + params)
        hits.extend(dict(url=row.get('redirect_url', ''), title=row.get('title', ''),
                         company=(row.get('company') or {}).get('display_name', ''))
                    for row in data.get('results', []))
    return hits
