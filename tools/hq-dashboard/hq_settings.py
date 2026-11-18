"""Owner configuration."""
import json
import os
import re
from pathlib import Path


def app_root():
    """Return the folder containing the bundled settings and tools."""
    return Path(__file__).resolve().parents[2]


def data_root():
    """Resolve the data folder using the current environment and marker."""
    override = os.environ.get('JOB_SEARCH_HQ_DATA')
    if override is not None:
        return Path(override).resolve()
    app = app_root()
    if (app.parent / '.job-search-hq-data').is_file():
        return app.parent
    return app


def load(root=None, defaults_root=None):
    """Read neutral defaults and optional private overrides on every call."""
    defaults = Path(defaults_root) if defaults_root is not None else Path(root) if root is not None else app_root()
    root = Path(root) if root is not None else data_root()
    values = json.loads((defaults / 'settings.example.json').read_text(encoding='utf-8'))
    values = {key: value for key, value in values.items() if not key.startswith('_')}
    private = root / 'settings.json'
    if private.is_file():
        overrides = json.loads(private.read_text(encoding='utf-8'))
        for key, value in overrides.items():
            if key.startswith('_'):
                continue
            if key not in values:
                raise ValueError(f'Unknown setting: {key}')
            values[key] = value
    links = values['owner_links']
    if not isinstance(links, list) or any(
            not isinstance(link, dict) or set(link) != {'label', 'text', 'url'}
            or any(not isinstance(link[k], str) for k in ('label', 'text', 'url'))
            or not re.fullmatch(r'[\w +./-]{1,80}', link['label'])
            or not re.fullmatch(r'[\w +@.:/()-]{0,200}', link['text'])
            or not re.fullmatch(r'https?://[^\s<>\[\]()"\x00-\x1f]{1,2000}', link['url'])
            for link in links):
        raise ValueError('owner_links needs label, text and an http(s) URL for each link.')
    if values['cv_skills_placement'] not in ('tools_tested', 'end'):
        raise ValueError('cv_skills_placement must be tools_tested or end.')
    if type(values['cv_tools_tested_min']) is not int or values['cv_tools_tested_min'] < 1:
        raise ValueError('cv_tools_tested_min must be a positive integer.')
    if not isinstance(values['cv_tool_groups'], list) or any(
            not isinstance(name, str) or not name.strip() for name in values['cv_tool_groups']):
        raise ValueError('cv_tool_groups must be a list of skill group names.')
    sections = values['cv_fixed_sections']
    if not isinstance(sections, list):
        raise ValueError('cv_fixed_sections must be a list.')
    names = {'skills', 'technical skills', 'summary', 'experience', 'projects', 'selected projects'}
    for section in sections:
        if (not isinstance(section, dict) or set(section) not in ({'name', 'entries', 'from'}, {'name', 'lines'})
                or not isinstance(section['name'], str)
                or not re.fullmatch(r'\w[\w ()/&+.\-]*:?', section['name'].strip())):
            raise ValueError('cv_fixed_sections items need a plain name, an entries list of E-### IDs, and from text or skills.')
        lines = section.get('lines', [section])
        if not isinstance(lines, list):
            raise ValueError('cv_fixed_sections lines must be a list.')
        for line in lines:
            if (not isinstance(line, dict)
                    or ('lines' in section and set(line) != {'entries', 'from', 'join'})
                    or line.get('from') not in ('text', 'skills')
                    or not isinstance(line.get('entries'), list)
                    or any(not isinstance(key, str) or not re.fullmatch(r'E-\d{3}', key) for key in line['entries'])
                    or ('lines' in section and (not isinstance(line['join'], str)
                        or not re.fullmatch(r'[ |,;/+\-]{1,20}', line['join'])))):
                raise ValueError('cv_fixed_sections lines need entries, from text or skills, and a plain join separator.')
        name = section['name'].strip().rstrip(':').strip().casefold()
        if name in names:
            raise ValueError('cv_fixed_sections names must be unique and cannot replace Skills.')
        names.add(name)
    return values


def browser(root=None):
    """Only page-facing configuration crosses into generated HTML."""
    values = load(root)
    return {key: values[key] for key in (
        'owner_name', 'owner_short_name', 'document_prefix', 'home_places_pattern', 'home_country_label',
        'home_country_icon', 'home_place_example', 'documented_relevant_years', 'timezone_offset',
        'prompt_sources', 'prompt_evidence', 'browser_bank_rule', 'browser_role_rule',
        'page_text', 'recommendation_hold_reason', 'analysis_role', 'documented_years_labels')}


def render_page(text, root=None):
    """Resolve trusted text tokens and inject script-safe page settings."""
    values = load(root)
    values['page_text'] = dict(values['page_text'], SCAN_TIME=values['scan_time_label'])
    import html
    import re
    parts = re.split(r'(<script\b[^>]*>.*?</script>)', text, flags=re.S | re.I)
    for index, part in enumerate(parts):
        script = part.lower().startswith('<script')
        for token, value in values['page_text'].items():
            if script:
                value = ''.join(f'\\u{ord(c):04x}' if c in "\\\"'`$<>&\n\r" else c for c in value)
            else:
                value = html.escape(value, quote=True)
            part = part.replace('{{' + token + '}}', value)
        parts[index] = part
    text = ''.join(parts)
    payload = json.dumps(browser(root), ensure_ascii=True).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return text.replace('/*HQ_SETTINGS*/{}', payload)
