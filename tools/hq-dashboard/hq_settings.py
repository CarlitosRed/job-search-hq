"""Owner configuration."""
import json
import os
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


def load(root=None):
    """Read neutral defaults and optional private overrides on every call."""
    defaults = Path(root) if root is not None else app_root()
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
