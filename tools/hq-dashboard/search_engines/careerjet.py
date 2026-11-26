"""Careerjet search leads."""
import base64
from ipaddress import ip_address
from urllib.parse import urlencode

LABEL, PACE, CAP, TIMEOUT = 'Careerjet', 1, 12, 10


def enabled(settings):
    return bool(settings.get('key'))


def read(fetch, queries, settings):
    hits = []
    credentials = base64.b64encode((settings['key'] + ':').encode()).decode()
    caller = str(ip_address(settings.get('user_ip', '127.0.0.1')))
    for query in queries:
        params = urlencode(dict(locale_code=settings.get('locale', 'en_US'),
                                keywords=query['words'], location=query['place'], page_size=20,
                                user_ip=caller, user_agent='JobSearchHQ/1.0'))
        data = fetch('GET', 'https://search.api.careerjet.net/v4/query?' + params,
                     headers={'Authorization': 'Basic ' + credentials})
        hits.extend(dict(url=row.get('url', ''), title=row.get('title', ''), company=row.get('company', ''))
                    for row in data.get('jobs', []))
    return hits
