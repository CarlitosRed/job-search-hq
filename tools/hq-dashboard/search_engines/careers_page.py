"""Read public employer pages and identify supported applicant tracking boards."""
from datetime import date
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

LABEL, PACE, CAP, TIMEOUT = 'Employer careers pages', 0.35, 140, 10
ATS = {'boards.greenhouse.io': 'greenhouse', 'job-boards.greenhouse.io': 'greenhouse',
       'boards-api.greenhouse.io': 'greenhouse', 'jobs.lever.co': 'lever', 'api.lever.co': 'lever',
       'jobs.ashbyhq.com': 'ashby', 'api.ashbyhq.com': 'ashby', 'apply.workable.com': 'workable',
       'jobs.smartrecruiters.com': 'smartrecruiters', 'careers.smartrecruiters.com': 'smartrecruiters'}
LEAD_HOSTS = ('adzuna.com', 'jooble.org', 'careerjet.com', 'jobviewtrack.com', 'remotive.com', 'remoteok.com',
              'arbeitnow.com', 'linkedin.com', 'himalayas.app', 'jobicy.com', 'jobs.workable.com',
              'indeed.com', 'glassdoor.com', 'ziprecruiter.com', 'talent.com')
CHALLENGE = re.compile(r'\bcaptcha\b|verify (?:that )?you are human|checking your browser|'
                       r'just a moment|access denied|sign in to (?:view|continue)|'
                       r'log in to (?:view|continue)|enable javascript and cookies', re.I)


def host(url):
    try:
        return (urlsplit(url).hostname or '').lower().rstrip('.')
    except (ValueError, TypeError):
        return ''


def is_lead(url):
    domain = host(url)
    return (any(domain == name or domain.endswith('.' + name) for name in LEAD_HOSTS)
            or bool(re.search(r'(^|\.)(?:adzuna|careerjet|indeed|glassdoor)\.(?:co\.)?[a-z]{2}$', domain)))


def board_for(url):
    domain = host(url)
    if not domain:
        return None
    parsed = urlsplit(url)
    parts = parsed.path.strip('/').split('/')
    if re.fullmatch(r'[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com', domain):
        if re.fullmatch(r'[a-z]{2}-[a-z]{2}', parts[0], re.I):
            parts = parts[1:]
        if parts and re.fullmatch(r'[\w-]{1,80}', parts[0], re.ASCII):
            return dict(provider='workday', board=domain.split('.')[0], host=domain, site=parts[0])
    provider = ATS.get(domain)
    token = parts[0]
    if provider == 'greenhouse' and token == 'embed':
        token = parse_qs(parsed.query).get('for', [''])[0]
    elif domain == 'boards-api.greenhouse.io' and parts[:2] == ['v1', 'boards']:
        token = parts[2] if len(parts) > 2 else ''
    elif domain == 'api.lever.co' and parts[:2] == ['v0', 'postings']:
        token = parts[2] if len(parts) > 2 else ''
    elif domain == 'api.ashbyhq.com' and parts[:2] == ['posting-api', 'job-board']:
        token = parts[2] if len(parts) > 2 else ''
    elif provider == 'workable' and token in ('j', 'view', 'api'):
        token = ''
    if provider and re.fullmatch(r'[\w-]{1,80}', token, re.ASCII):
        return dict(provider=provider, board=token)
    return None
    return None


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.structured, self.links, self.visible = [], [], []
        self.script = None
        self.quiet = False
        self.password_field = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('a', 'iframe', 'script'):
            link = attrs.get('href') or attrs.get('src')
            if link:
                self.links.append(link)
        if tag == 'input' and (attrs.get('type') or '').lower() == 'password':
            self.password_field = True
        if tag in ('script', 'style'):
            self.quiet = True
        if tag == 'script' and (attrs.get('type') or '').lower() == 'application/ld+json':
            self.script = []

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            try:
                self.structured.append(json.loads(''.join(self.script)))
            except (ValueError, RecursionError):
                pass
            self.script = None
        if tag in ('script', 'style'):
            self.quiet = False

    def handle_data(self, text):
        if self.script is not None:
            self.script.append(text)
        elif not self.quiet and len(self.visible) < 120:
            self.visible.append(text.strip())


def records(value):
    if isinstance(value, list):
        for item in value:
            yield from records(item)
    elif isinstance(value, dict):
        kinds = value.get('@type', [])
        if kinds == 'JobPosting' or isinstance(kinds, list) and 'JobPosting' in kinds:
            yield value
        yield from records(value.get('@graph'))


def values(value):
    return value if isinstance(value, list) else [value]


def field(value, limit=300):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def posting(raw, url, today):
    organization = raw.get('hiringOrganization')
    company = field(organization.get('name')) if isinstance(organization, dict) else ''
    title = field(raw.get('title'))
    if not (company and title):
        return None
    locations = []
    for location in values(raw.get('jobLocation')):
        address = location.get('address') if isinstance(location, dict) else None
        if isinstance(address, dict):
            country = address.get('addressCountry')
            if isinstance(country, dict):
                country = country.get('name')
            locations += [field(address.get('addressLocality')), field(address.get('addressRegion')), field(country)]
    remote = raw.get('jobLocationType') == 'TELECOMMUTE' or any(
        isinstance(part, str) and 'remote' in part.casefold() for part in locations)
    if remote:
        locations.append('Remote')
        for region in values(raw.get('applicantLocationRequirements')):
            if isinstance(region, dict):
                locations.append(field(region.get('name')))
    canonical = field(raw.get('url'), 2000)
    if canonical:
        try:
            proposed = urljoin(url, canonical)
            if host(proposed) == host(url) and urlsplit(proposed).scheme == 'https':
                url = proposed
        except ValueError:
            pass
    expiry = field(raw.get('validThrough'))[:10]
    posted = field(raw.get('datePosted'))[:10]
    return dict(provider='careers_page', date_source='careers_page', company=company, title=title,
                link=url, posting_id=url, place_text=', '.join(dict.fromkeys(filter(None, locations))),
                posted_on=posted, remote=remote, open=not expiry or expiry >= today.isoformat(),
                description=field(re.sub(r'<[^>]*>', ' ', unescape(field(raw.get('description'), 24000))), 20000))


def read(fetch, urls, today=None, blocked=None, closed=lambda text: False):
    today = today or date.today()
    blocked = {} if blocked is None else blocked
    jobs, boards, errors, visited = [], [], [], set()
    pending = list(dict.fromkeys(urls))
    while pending and len(visited) < CAP:
        link = pending.pop(0)
        domain = host(link)
        if (not domain or link in visited or domain in blocked or is_lead(link)
                or urlsplit(link).scheme != 'https'):
            continue
        visited.add(link)
        try:
            response = fetch(link)
        except OSError:
            errors.append('An employer page could not be read.')
            continue
        if not response:
            errors.append('An employer page did not return a readable response.')
            continue
        status, kind, body = response[:3]
        final = response[3] if len(response) > 3 else link
        if not isinstance(status, int) or status in (401, 403, 429):
            blocked[domain] = (f'The site blocked access (HTTP {status}).' if isinstance(status, int)
                               else 'The site requires a bot check or disallows automatic reading.')
            blocked[host(final)] = blocked[domain]
            continue
        if status != 200 or is_lead(final):
            continue
        page = Page()
        try:
            page.feed(body)
            structured = [row for item in page.structured for row in records(item)]
        except (ValueError, RecursionError):
            errors.append('An employer page returned invalid structured data.')
            continue
        visible = ' '.join(filter(None, page.visible))[:2000]
        if (CHALLENGE.search(visible) or visible.lower() in ('sign in', 'log in', 'login')
                or page.password_field and not structured):
            blocked[domain] = 'The site requires a CAPTCHA, bot check or sign-in.'
            blocked[host(final)] = blocked[domain]
            continue
        board = board_for(final)
        if board:
            boards.append(board)
        for raw in structured:
            job = posting(raw, final, today)
            if job:
                if closed(visible):
                    job['open'] = False
                jobs.append(job)
        for href in page.links[:300]:
            try:
                child = urljoin(final, href)
                candidate = board_for(child)
            except ValueError:
                continue
            if candidate and host(child) not in blocked:
                boards.append(candidate)
            elif (host(child) == host(final) and re.search(r'/(?:jobs?|careers?|vacanc\w*)/', urlsplit(child).path, re.I)
                  and child not in visited and len(pending) < CAP):
                pending.append(child)
    return jobs, blocked, boards, errors
