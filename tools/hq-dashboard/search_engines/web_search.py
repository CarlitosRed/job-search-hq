"""Web search providers share a read(transport, queries, settings) interface."""
from urllib.parse import urlencode

LABEL, PACE, CAP, TIMEOUT = 'Web search', 1.1, 53, 10


def brave(fetch, queries, settings):
    hits = []
    for query in queries:
        params = urlencode(dict(q=f"{query['words']} {query['place']}".strip(), count=10))
        data = fetch('GET', 'https://api.search.brave.com/res/v1/web/search?' + params,
                     headers={'X-Subscription-Token': settings['key']})
        hits.extend(dict(title=row.get('title', ''), url=row.get('url', ''))
                    for row in data.get('web', {}).get('results', []))
    return hits


PROVIDERS = {'brave': brave}


def enabled(settings):
    return bool(settings.get('key')) and settings.get('provider', 'brave') in PROVIDERS


def read(fetch, queries, settings):
    return PROVIDERS[settings.get('provider', 'brave')](fetch, queries, settings)
