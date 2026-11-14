"""Read and check the portable, version-one search profile."""
from datetime import date
import json
from pathlib import Path
import re


DEFAULTS = {'daily_land_limit': 30, 'judge_budget': 20,
            'max_per_company': 3, 'wild_cards': 3}
PROVIDERS = ('greenhouse', 'lever', 'ashby', 'workday')


class ProfileError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('Please fix the profile: ' + '; '.join(self.problems))


def load(path):
    """Return a checked profile with defaults; report all field problems together."""
    try:
        profile = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        raise ProfileError(['The profile could not be read as a JSON file.']) from None
    problems = []

    def obj(value, name):
        if not isinstance(value, dict):
            problems.append(name + ' must be an object.')
            return {}
        return value

    def text(value, name):
        if not isinstance(value, str) or not value.strip():
            problems.append(name + ' must be nonempty text.')

    def items(value, name, nonempty=False):
        if not isinstance(value, list):
            problems.append(name + ' must be a list.')
            return []
        if nonempty and not value:
            problems.append(name + ' must have at least one entry.')
        return value

    def words(value, name):
        for index, word in enumerate(items(value, name)):
            text(word, f'{name}[{index}]')

    def day(value, name):
        try:
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError()
            date.fromisoformat(value)
        except ValueError:
            problems.append(name + ' must be a real date written YYYY-MM-DD.')

    profile = obj(profile, 'profile')
    text(profile.get('id'), 'id')
    if type(profile.get('version')) is not int or profile['version'] != 1:
        problems.append('version must be 1.')
    if type(profile.get('revision')) is not int or profile['revision'] < 1:
        problems.append('revision must be a positive whole number.')
    day(profile.get('updated'), 'updated')
    goals = obj(profile.get('goals'), 'goals')
    lane_ids = set()
    for index, value in enumerate(items(goals.get('lanes'), 'goals.lanes', True)):
        name = f'goals.lanes[{index}]'
        lane = obj(value, name)
        for key in ('id', 'label'):
            text(lane.get(key), name + '.' + key)
        lane_id = lane.get('id')
        if isinstance(lane_id, str):
            if lane_id in lane_ids:
                problems.append(name + '.id must be different from the other lanes.')
            lane_ids.add(lane_id)
        for key in ('titles', 'duties'):
            words(lane.get(key), name + '.' + key)
        if lane.get('titles') == [] and lane.get('duties') == []:
            problems.append(name + ' needs a title or duty phrase.')
    seniority = obj(goals.get('seniority'), 'goals.seniority')
    for key in ('allowed', 'stretch'):
        words(seniority.get(key), 'goals.seniority.' + key)
    words(goals.get('stretch_interests'), 'goals.stretch_interests')
    proven = obj(profile.get('proven'), 'proven')
    for index, value in enumerate(items(proven.get('skills'), 'proven.skills')):
        name = f'proven.skills[{index}]'
        skill = obj(value, name)
        text(skill.get('name'), name + '.name')
        words(skill.get('evidence'), name + '.evidence')
        if skill.get('status') not in ('verified', 'stated'):
            problems.append(name + '.status must be verified or stated.')
    rules = obj(profile.get('work_rules'), 'work_rules')
    text(rules.get('home_country'), 'work_rules.home_country')
    for key in ('authorized_countries', 'remote_regions', 'work_modes'):
        words(rules.get(key), 'work_rules.' + key)
    for key in ('relocation_wish', 'sponsorship_needed'):
        if not isinstance(rules.get(key), bool):
            problems.append('work_rules.' + key + ' must be true or false.')
    search = obj(profile.get('search'), 'search')
    companies = items(search.get('companies'), 'search.companies', True)
    if len(companies) > 300:
        problems.append('search.companies may have at most 300 boards.')
    for index, value in enumerate(companies):
        name = f'search.companies[{index}]'
        company = obj(value, name)
        for key in ('label', 'board', 'found_by'):
            text(company.get(key), name + '.' + key)
        if company.get('provider') not in PROVIDERS:
            problems.append(name + '.provider must be greenhouse, lever, ashby or workday.')
        if not isinstance(company.get('board'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', company['board']):
            problems.append(name + '.board must be a public board token using letters, numbers, underscores or hyphens.')
        if company.get('provider') == 'workday':
            host, site = company.get('host'), company.get('site')
            if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9-]+\.wd\d+\.myworkdayjobs\.com', host):
                problems.append(name + '.host must be a public Workday jobs host.')
            if not isinstance(site, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', site):
                problems.append(name + '.site must be a public Workday site token.')
    for key, field in (('exclude_titles', 'pattern'), ('exclude_companies', 'name')):
        for index, value in enumerate(items(search.get(key), 'search.' + key)):
            name = f'search.{key}[{index}]'
            exclusion = obj(value, name)
            text(exclusion.get(field), name + '.' + field)
            text(exclusion.get('reason'), name + '.reason')
    for key, default in DEFAULTS.items():
        search.setdefault(key, default)
        minimum = 1 if key in ('daily_land_limit', 'max_per_company') else 0
        if type(search[key]) is not int or search[key] < minimum:
            problems.append('search.' + key + f' must be a whole number of at least {minimum}.')
    if type(search.get('wild_cards')) is int and type(search.get('daily_land_limit')) is int and search['wild_cards'] > search['daily_land_limit']:
        problems.append('search.wild_cards cannot exceed daily_land_limit.')
    review = obj(profile.get('review'), 'review')
    text(review.get('reviewed_by'), 'review.reviewed_by')
    day(review.get('reviewed_on'), 'review.reviewed_on')
    words(review.get('open_questions'), 'review.open_questions')
    if problems:
        raise ProfileError(problems)
    return profile
