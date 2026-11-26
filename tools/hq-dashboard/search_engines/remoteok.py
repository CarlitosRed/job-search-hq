"""Remote OK remote job leads."""

LABEL, PACE, CAP, TIMEOUT = 'Remote OK', 2, 1, 12


def enabled(settings):
    return True


def read(fetch, queries, settings):
    return [dict(url=row.get('apply_url') or row.get('url', ''), title=row.get('position', ''),
                 company=row.get('company', ''))
            for row in fetch('GET', 'https://remoteok.com/api') if row.get('position')]
